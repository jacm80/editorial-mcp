"""Embeddings locales versionados y combinación RRF; no interpreta hechos."""

import math
import re
import threading
from array import array
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from .index import BookIndex

MODEL = "minishlab/potion-multilingual-128M"
REVISION = "73908c3438cf03b6a01bcb9611d62b23d0726f08"
MODEL_FILES = ["model.safetensors", "tokenizer.json", "config.json", "README.md"]


class Embedder(Protocol):
    identity: str

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


class LocalEmbedder:
    """Checkpoint público fijado por revisión; las consultas solo usan caché local."""

    identity = f"model2vec-0.9.0:{MODEL}@{REVISION}:normalize:no-truncation:v1"

    def __init__(self, cache: Path):
        self.cache = cache
        self._model = None

    def prepare(self) -> Path:
        try:
            from huggingface_hub import snapshot_download
        except ImportError as error:
            raise ValueError("Instala el extra semantic: uv sync --extra semantic") from error
        return Path(
            snapshot_download(
                repo_id=MODEL,
                revision=REVISION,
                cache_dir=str(self.cache),
                allow_patterns=MODEL_FILES,
                token=False,
            )
        )

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            try:
                from huggingface_hub import snapshot_download
                from model2vec import StaticModel

                snapshot = snapshot_download(
                    repo_id=MODEL,
                    revision=REVISION,
                    cache_dir=str(self.cache),
                    allow_patterns=MODEL_FILES,
                    local_files_only=True,
                    token=False,
                )
                self._model = StaticModel.from_pretrained(
                    snapshot,
                    normalize=True,
                    force_download=False,
                )
            except (ImportError, OSError) as error:
                raise ValueError(
                    "Modelo local no disponible. Instala el extra semantic y ejecuta "
                    "prepare-model; las consultas no descargan modelos ni usan APIs."
                ) from error
        return self._model.encode(
            list(texts), normalize=True, max_length=None, use_multiprocessing=False
        ).tolist()


def normalize(vector: Sequence[float]) -> list[float]:
    values = [float(value) for value in vector]
    if not values or not all(math.isfinite(value) for value in values):
        raise ValueError("Embedding vacío o no finito")
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]


def pack(vector: Sequence[float]) -> bytes:
    import sys

    values = array("f", vector)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def unpack(data: bytes, dimension: int) -> list[float]:
    import sys

    values = array("f")
    values.frombytes(data)
    if sys.byteorder != "little":
        values.byteswap()
    if len(values) != dimension:
        raise ValueError("Dimensión de embedding incompatible")
    return list(values)


def fuse(lexical, semantic, limit: int = 100):
    """Reciprocal Rank Fusion sobre IDs ya filtrados por libro/tipo/capítulo."""
    ranks = {}
    sources = {}
    for rank, row in enumerate(lexical[:limit], 1):
        sources[row["id"]] = row
        ranks.setdefault(row["id"], {})["lexical_rank"] = rank
    for rank, (row, similarity) in enumerate(semantic[:limit], 1):
        sources[row["id"]] = row
        ranks.setdefault(row["id"], {}).update(semantic_rank=rank, similarity=similarity)
    for entry in ranks.values():
        entry["rrf_score"] = sum(
            1 / (60 + entry[key]) for key in ("lexical_rank", "semantic_rank") if key in entry
        )
    ordered = sorted(sources, key=lambda key: (-ranks[key]["rrf_score"], key))[:limit]
    return [sources[key] for key in ordered], ranks


class SemanticIndex:
    def __init__(self, index: BookIndex, embedder: Embedder):
        self.index = index
        self.embedder = embedder
        self._lock = threading.RLock()
        # El embedding cache no migra/reemplaza tablas de la fuente textual.
        with index.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS vector_cache (
                    model TEXT NOT NULL, text_hash TEXT NOT NULL,
                    dimension INTEGER NOT NULL, vector BLOB NOT NULL,
                    PRIMARY KEY(model, text_hash)
                );
                CREATE TABLE IF NOT EXISTS chunk_vectors (
                    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
                    model TEXT NOT NULL, dimension INTEGER NOT NULL, vector BLOB NOT NULL,
                    PRIMARY KEY(chunk_id, model)
                );
            """)

    def status(self) -> dict:
        with self.index.connect() as db:
            total = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
            count = db.execute(
                "SELECT count(*) FROM chunk_vectors WHERE model=?", (self.embedder.identity,)
            ).fetchone()[0]
        return {
            "enabled": True,
            "model": self.embedder.identity,
            "indexed_chunks": count,
            "pending_chunks": total - count,
            "inference": "local CPU; sin API",
            "fusion": "RRF (k=60)",
        }

    def vectors(self, texts: Sequence[str]) -> list[list[float]]:
        with self._lock:
            hashes = [sha256(text.encode("utf-8")).hexdigest() for text in texts]
            cached = {}
            unique = list(dict.fromkeys(hashes))
            with self.index.connect() as db:
                for start in range(0, len(unique), 500):
                    batch = unique[start : start + 500]
                    parameters = ",".join("?" for _ in batch)
                    for row in db.execute(
                        "SELECT text_hash,dimension,vector FROM vector_cache "
                        f"WHERE model=? AND text_hash IN ({parameters})",
                        [self.embedder.identity, *batch],
                    ):
                        cached[row["text_hash"]] = unpack(row["vector"], row["dimension"])
            missing = {
                digest: text
                for digest, text in zip(hashes, texts, strict=True)
                if digest not in cached
            }
            if missing:
                encoded = self.embedder.encode(list(missing.values()))
                if len(encoded) != len(missing):
                    raise ValueError("El modelo devolvió una cantidad de embeddings incorrecta")
                vectors = [normalize(vector) for vector in encoded]
                if len({len(vector) for vector in vectors}) != 1:
                    raise ValueError("El modelo devolvió dimensiones diferentes")
                with self.index.connect() as db:
                    for digest, vector in zip(missing, vectors, strict=True):
                        encoded_vector = pack(vector)
                        db.execute(
                            "INSERT OR REPLACE INTO vector_cache VALUES (?,?,?,?)",
                            (self.embedder.identity, digest, len(vector), encoded_vector),
                        )
                        # Puntuar siempre en la representación persistida (float32).
                        # Así la primera consulta y las siguientes no alteran el orden
                        # por diferencias de precisión entre float64 y la caché.
                        cached[digest] = unpack(encoded_vector, len(vector))
            return [cached[digest] for digest in hashes]

    def rank(self, rows, query: str):
        if not rows:
            return [], None
        with self._lock:
            query_vector = self.vectors([query])[0]
            if not any(query_vector):
                raise ValueError("Consulta sin señal semántica; usa una búsqueda textual")
            vectors = self.vectors([row["text"] for row in rows])
            scores = []
            with self.index.connect() as db:
                for row, vector in zip(rows, vectors, strict=True):
                    if len(vector) != len(query_vector):
                        raise ValueError("Dimensiones distintas; revisa la identidad del modelo")
                    # No adjuntar un vector a un ID reutilizado por una edición concurrente.
                    current = db.execute(
                        "SELECT book,path,scene,part,text FROM chunks WHERE id=?", (row["id"],)
                    ).fetchone()
                    if not current or any(
                        current[key] != row[key]
                        for key in ("book", "path", "scene", "part", "text")
                    ):
                        raise ValueError("El índice cambió durante la búsqueda; repite la consulta")
                    db.execute(
                        "INSERT OR REPLACE INTO chunk_vectors VALUES (?,?,?,?)",
                        (row["id"], self.embedder.identity, len(vector), pack(vector)),
                    )
                    similarity = sum(a * b for a, b in zip(query_vector, vector, strict=True))
                    scores.append((row, similarity))
            return sorted(scores, key=lambda item: (-item[1], item[0]["id"])), query_vector

    def snippet_position(self, text: str, query_vector: Sequence[float]) -> int:
        paragraphs = list(re.finditer(r"\S[^\n]*(?:\n(?!\n)[^\n]+)*", text))
        if len(paragraphs) < 2:
            return 0
        vectors = self.vectors([paragraph[0] for paragraph in paragraphs])
        winner = max(
            range(len(paragraphs)),
            key=lambda i: sum(a * b for a, b in zip(query_vector, vectors[i], strict=True)),
        )
        return paragraphs[winner].start()
