import math
from hashlib import sha256

import pytest
from conftest import write

from editorial_mcp.semantic import LocalEmbedder, SemanticIndex, fuse, normalize, pack, unpack
from editorial_mcp.service import EditorialService


class FakeEmbedder:
    """Doble determinista para verificar mecánica; no mide calidad del modelo real."""

    identity = "fixture-semantic-v1"

    def __init__(self):
        self.texts = []

    def encode(self, texts):
        self.texts.extend(texts)
        output = []
        for text in texts:
            text = text.casefold()
            fear = sum(text.count(word) for word in ("miedo", "terror", "temor", "asustad"))
            weather = sum(text.count(word) for word in ("clima", "nieve", "invierno", "frío"))
            output.append([fear, weather, 0.1])
        return output


def semantic_fixture(library):
    base, one, two = library
    write(
        one,
        "01-Manuscrito/Cap-10.md",
        "---\ncapitulo: 10\n---\n# Horror\nEl terror dominaba al pueblo.\n",
    )
    write(
        one,
        "01-Manuscrito/Cap-11.md",
        "---\ncapitulo: 11\n---\n# Clima\nLa nieve traía un invierno frío.\n",
    )
    embedder = FakeEmbedder()
    return EditorialService(base.index, embedder), embedder, one, two


def test_semantic_finds_synonym_without_lexical_match(library):
    service, _, _, _ = semantic_fixture(library)
    assert service.search("uno", "miedo", mode="terms")["results"] == []
    result = service.search("uno", "miedo", mode="semantic", limit=1)
    assert result["results"][0]["chapter"] == "10"
    assert result["results"][0]["ranking"]["semantic_rank"] == 1
    assert result["total_matching_chunks"] is None
    assert "terror" in result["results"][0]["excerpt"]


def test_hybrid_scores_and_filters(library):
    service, _, _, _ = semantic_fixture(library)
    result = service.search("uno", "cómo llega el temor al pueblo", mode="hybrid", limit=1)
    assert result["results"][0]["chapter"] == "10"
    assert result["results"][0]["ranking"]["rrf_score"] > 0
    filtered = service.search("uno", "miedo", mode="hybrid", chapter="11")
    assert all(hit["chapter"] == "11" for hit in filtered["results"])
    assert all(
        hit["book"] == "dos" for hit in service.search("dos", "miedo", mode="semantic")["results"]
    )
    assert service.search("uno", "miedo", mode="semantic", entity="No existe")["results"] == []


def test_embeddings_are_cached_and_survive_unrelated_edits(library):
    service, embedder, root, _ = semantic_fixture(library)
    service.search("uno", "miedo", mode="semantic")
    first = len(embedder.texts)
    service.search("uno", "miedo", mode="semantic")
    assert len(embedder.texts) == first
    path = root / "01-Manuscrito/Cap-10.md"
    path.write_text(path.read_text().replace("terror", "miedo y terror"))
    result = service.search("uno", "miedo", mode="semantic")
    newly_encoded = embedder.texts[first:]
    assert newly_encoded
    assert all("invierno" not in text for text in newly_encoded)
    assert result["results"][0]["source_hash"] == sha256(path.read_bytes()).hexdigest()
    path.unlink()
    result = service.search("uno", "miedo", mode="semantic")
    assert all(hit["chapter"] != "10" for hit in result["results"])


def test_model_identity_invalidates_vectors(library):
    service, embedder, _, _ = semantic_fixture(library)
    service.search("uno", "miedo", mode="semantic")
    other = FakeEmbedder()
    other.identity = "fixture-semantic-v2"
    replacement = EditorialService(service.index, other)
    assert replacement.status()["semantic"]["indexed_chunks"] == 0
    replacement.search("uno", "miedo", mode="semantic")
    assert other.texts
    assert replacement.status()["semantic"]["indexed_chunks"] > 0
    assert embedder.identity != other.identity


def test_semantic_preserves_sources_and_budget(library):
    service, _, root, _ = semantic_fixture(library)
    before = {path: path.read_bytes() for path in root.rglob("*.md")}
    first = service.search("uno", "miedo", mode="hybrid", limit=1, max_chars=200)
    assert first["excerpt_chars"] <= 200
    assert first["has_more"]
    second = service.search("uno", "miedo", mode="hybrid", limit=1, offset=first["next_offset"])
    assert first["results"][0]["chunk_id"] != second["results"][0]["chunk_id"]
    assert before == {path: path.read_bytes() for path in root.rglob("*.md")}


def test_rrf_rewards_agreement_and_deduplicates():
    rows = [{"id": 1}, {"id": 2}, {"id": 3}]
    result, ranks = fuse(rows[:2], [(rows[2], 0.9), (rows[1], 0.8)])
    assert result[0]["id"] == 2
    assert len(result) == 3
    assert ranks[2]["rrf_score"] == pytest.approx(2 / 62)


def test_candidate_pool_is_explicit(library):
    service, _, root, _ = semantic_fixture(library)
    for chapter in range(20, 35):
        write(
            root,
            f"01-Manuscrito/Cap-{chapter}.md",
            f"---\ncapitulo: {chapter}\n---\nMiedo {chapter}.\n",
        )
    result = service.search("uno", "miedo", mode="semantic", candidate_limit=10)
    assert result["candidate_pool_truncated"]
    assert result["ranked_candidates"] == 10
    assert result["total_matching_chunks"] is None


def test_pack_normalize_and_dimensions():
    assert unpack(pack([0.6, 0.8]), 2) == pytest.approx([0.6, 0.8])
    assert normalize([3, 4]) == pytest.approx([0.6, 0.8])
    for vector in ([], [math.inf], [math.nan]):
        with pytest.raises(ValueError):
            normalize(vector)
    with pytest.raises(ValueError, match="Dimensión"):
        unpack(pack([1, 2]), 3)


def test_missing_model_does_not_download(library, tmp_path):
    service = EditorialService(library[0].index, LocalEmbedder(tmp_path / "not-downloaded"))
    with pytest.raises(ValueError, match="prepare-model"):
        service.search("uno", "miedo", mode="semantic")
    assert service.search("uno", "Martha", mode="literal")["results"]


def test_disabled_semantic_is_explicit(library):
    with pytest.raises(ValueError, match="desactivada"):
        library[0].search("uno", "miedo", mode="semantic")


def test_zero_query_rejected_but_zero_document_safe(library):
    class ZeroEmbedder(FakeEmbedder):
        def encode(self, texts):
            return [[0.0, 0.0] if text == "?" else [1.0, 0.0] for text in texts]

    service = EditorialService(library[0].index, ZeroEmbedder())
    with pytest.raises(ValueError, match="sin señal"):
        service.search("uno", "?", mode="semantic")


def test_wrong_count_and_invalid_vectors_fail_atomically(library):
    class BrokenEmbedder(FakeEmbedder):
        def encode(self, texts):
            return []

    index = SemanticIndex(library[0].index, BrokenEmbedder())
    with pytest.raises(ValueError, match="cantidad"):
        index.vectors(["Texto"])
    with index.index.connect() as db:
        assert db.execute("SELECT count(*) FROM vector_cache").fetchone()[0] == 0
