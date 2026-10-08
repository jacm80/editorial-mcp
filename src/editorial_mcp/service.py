"""Consultas acotadas con procedencia explícita y paginación."""

import json
import re
import unicodedata
from hashlib import sha256

from .entities import fold
from .index import BookIndex
from .lint import lint_text
from .parsing import links
from .semantic import Embedder, SemanticIndex, fuse

NOTICE = "Recuperación textual, no auditoría exhaustiva ni prueba de ausencia narrativa."
STOPWORDS = {
    "el",
    "la",
    "los",
    "las",
    "un",
    "una",
    "unos",
    "unas",
    "de",
    "del",
    "al",
    "a",
    "ante",
    "bajo",
    "con",
    "contra",
    "desde",
    "en",
    "entre",
    "hacia",
    "hasta",
    "para",
    "por",
    "sobre",
    "tras",
    "y",
    "e",
    "o",
    "u",
    "que",
    "como",
    "cual",
    "cuales",
    "quien",
    "quienes",
    "cuando",
    "donde",
    "cuanto",
    "cuantos",
    "es",
    "son",
    "fue",
    "eran",
    "se",
    "su",
    "sus",
    "lo",
    "le",
    "les",
    "the",
    "an",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "and",
    "or",
    "what",
    "who",
    "when",
    "where",
    "how",
    "is",
    "are",
    "was",
    "were",
}


def content_tokens(tokens: list[str]) -> list[str]:
    return [
        token
        for token in tokens
        if "".join(
            character
            for character in unicodedata.normalize("NFKD", token.casefold())
            if not unicodedata.combining(character)
        )
        not in STOPWORDS
    ]


def bounded(value: int, minimum: int, maximum: int, name: str) -> None:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} debe estar entre {minimum} y {maximum}")


def reference(row) -> dict:
    scene_id = f"{row['path']}#s{row['scene']:03d}"
    return {
        "book": row["book"],
        "path": row["path"],
        "kind": row["kind"],
        "chapter": row["chapter"],
        "scene_id": scene_id,
        "chunk_id": f"{scene_id}.p{row['part']:03d}",
        "heading": row["heading"],
        "source_hash": row["hash"],
        "line_start": row["line_start"],
        "line_end": row["line_end"],
    }


def excerpt(text: str, position: int, size: int) -> tuple[str, dict]:
    start = max(0, position - size // 3)
    end = min(len(text), start + size)
    return text[start:end], {
        "line_start": text.count("\n", 0, start) + 1,
        "line_end": text.count("\n", 0, max(start, end - 1)) + 1,
        "char_start": start,
        "char_end": end,
        "truncated": start > 0 or end < len(text),
    }


class EditorialService:
    def __init__(self, index: BookIndex, embedder: Embedder | None = None):
        self.index = index
        self.semantic = SemanticIndex(index, embedder) if embedder is not None else None

    def status(self) -> dict:
        return {
            **self.index.status(),
            "semantic": self.semantic.status() if self.semantic else {"enabled": False},
            "retrieval": "lexical + local semantic + RRF"
            if self.semantic
            else "lexical; no embeddings",
        }

    @staticmethod
    def filter_entity(rows, entity: str | None):
        if not entity:
            return rows
        return [
            row
            for row in rows
            if entity.casefold()
            in [
                name.casefold()
                for field in ("personajes", "lugares")
                for name in links(json.loads(row["metadata"]).get(field))
            ]
        ]

    def check_book(self, book: str) -> None:
        if book not in self.index.books:
            raise ValueError(
                f"Libro desconocido: {book}; disponibles: {', '.join(self.index.books)}"
            )

    def rows(
        self,
        book: str,
        kind: str = "all",
        chapter: str | None = None,
        include_resolved: bool = False,
        match: str | None = None,
    ):
        self.check_book(book)
        if kind not in {"all", "manuscrito", "biblia", "revision"}:
            raise ValueError("kind: all, manuscrito, biblia o revision")
        conditions, args = ["c.book=?"], [book]
        if kind != "all":
            conditions.append("d.kind=?")
            args.append(kind)
        if chapter is not None:
            conditions.append("d.chapter=?")
            args.append(str(chapter))
        if not include_resolved:
            conditions.append("(c.resolved IS NULL OR c.resolved=0)")
        join = ""
        order = "c.path,c.scene,c.part"
        if match:
            join = "JOIN chunks_fts ON chunks_fts.rowid=c.id"
            conditions.append("chunks_fts MATCH ?")
            args.append(match)
            order = "bm25(chunks_fts),c.path,c.scene,c.part"
        with self.index.connect() as db:
            return db.execute(
                f"""SELECT c.*, d.kind, d.chapter, d.title, d.hash,
                d.metadata, d.text AS source_text, d.body_start
                FROM chunks c JOIN documents d ON c.book=d.book AND c.path=d.path
                {join} WHERE {" AND ".join(conditions)} ORDER BY {order}""",
                args,
            ).fetchall()

    def search(
        self,
        book: str,
        query: str,
        kind: str = "manuscrito",
        chapter: str | None = None,
        mode: str = "terms",
        entity: str | None = None,
        include_resolved: bool = False,
        limit: int = 5,
        offset: int = 0,
        max_chars: int = 2000,
        candidate_limit: int = 100,
    ) -> dict:
        bounded(limit, 1, 20, "limit")
        bounded(offset, 0, 1_000_000, "offset")
        bounded(max_chars, 200, 12_000, "max_chars")
        bounded(candidate_limit, 10, 200, "candidate_limit")
        if not query.strip() or len(query) > 500:
            raise ValueError("query debe tener entre 1 y 500 caracteres no vacíos")
        if mode not in {"terms", "literal", "semantic", "hybrid"}:
            raise ValueError("mode: terms, literal, semantic o hybrid")
        if mode in {"semantic", "hybrid"} and self.semantic is None:
            raise ValueError("Búsqueda semántica desactivada; inicia con --semantic")
        self.check_book(book)
        self.index.sync()
        tokens = re.findall(r"\w+", query, re.UNICODE)
        if mode == "terms" and not tokens:
            raise ValueError("La búsqueda terms necesita palabras; usa literal para signos")
        # Nunca aceptar sintaxis FTS arbitraria del cliente.
        match = " AND ".join(f'"{token}"' for token in tokens) if mode == "terms" else None
        rows = self.filter_entity(self.rows(book, kind, chapter, include_resolved, match), entity)
        ranking, query_vector, corpus_count = {}, None, len(rows)
        if mode in {"semantic", "hybrid"}:
            # Filtrar ANTES de calcular similitud; nunca consultar vectores de otro libro.
            corpus = self.filter_entity(self.rows(book, kind, chapter, include_resolved), entity)
            corpus_count = len(corpus)
            scored, query_vector = self.semantic.rank(corpus, query)
            if mode == "semantic":
                rows = [row for row, _ in scored[:candidate_limit]]
                ranking = {
                    row["id"]: {"semantic_rank": rank, "similarity": score}
                    for rank, (row, score) in enumerate(scored[:candidate_limit], 1)
                }
            else:
                keywords = content_tokens(tokens)
                lexical_match = " OR ".join(f'"{token}"' for token in keywords)
                lexical = (
                    self.filter_entity(
                        self.rows(book, kind, chapter, include_resolved, lexical_match), entity
                    )
                    if keywords
                    else []
                )
                rows, ranking = fuse(lexical, scored, candidate_limit)
        ranges = {}
        for row in rows:
            key = (row["path"], row["scene"])
            start, end = ranges.get(key, (row["char_start"], row["char_end"]))
            ranges[key] = (min(start, row["char_start"]), max(end, row["char_end"]))
        positions = {}
        if mode == "literal":
            # Buscar sobre documentos completos permite frases entre dos fragmentos.
            documents = {row["path"]: row for row in rows}
            for path, row in documents.items():
                body_offset = sum(
                    len(line)
                    for line in row["source_text"].splitlines(keepends=True)[: row["body_start"]]
                )
                positions[path] = [
                    m.start()
                    for m in re.finditer(re.escape(query), row["source_text"], re.IGNORECASE)
                    if m.start() >= body_offset
                ]
            rows = [
                row
                for row in rows
                if any(row["char_start"] <= p < row["char_end"] for p in positions[row["path"]])
            ]
        selected, results, remaining = rows[offset : offset + limit], [], max_chars
        for row in selected:
            if remaining < 100:
                break
            if mode == "literal":
                position = next(
                    p for p in positions[row["path"]] if row["char_start"] <= p < row["char_end"]
                )
            elif mode in {"semantic", "hybrid"}:
                position = row["char_start"] + self.semantic.snippet_position(
                    row["text"], query_vector
                )
            else:
                found = re.search(
                    "|".join(re.escape(t) for t in tokens), row["text"], re.IGNORECASE
                )
                position = row["char_start"] + (found.start() if found else 0)
            size = min(400, remaining)
            start, end = ranges[row["path"], row["scene"]]
            text, citation = excerpt(row["source_text"][start:end], position - start, size)
            citation["char_start"] += start
            citation["char_end"] += start
            citation["line_start"] = row["source_text"].count("\n", 0, citation["char_start"]) + 1
            citation["line_end"] = (
                row["source_text"].count(
                    "\n", 0, max(citation["char_start"], citation["char_end"] - 1)
                )
                + 1
            )
            remaining -= len(text)
            results.append(
                {
                    **reference(row),
                    "excerpt": text,
                    "excerpt_range": citation,
                    **({"ranking": ranking[row["id"]]} if ranking else {}),
                }
            )
        next_offset = offset + len(results)
        return {
            "book": book,
            "mode": mode,
            "results": results,
            "total_matching_chunks": len(rows) if mode in {"terms", "literal"} else None,
            "ranked_candidates": len(rows) if mode in {"semantic", "hybrid"} else None,
            "candidate_pool_truncated": corpus_count > candidate_limit
            if mode in {"semantic", "hybrid"}
            else False,
            "has_more": next_offset < len(rows),
            "next_offset": next_offset if next_offset < len(rows) else None,
            "excerpt_chars": max_chars - remaining,
            "entity_filter_scope": "frontmatter del capítulo, no presencia en escena"
            if entity
            else None,
            "notice": "Ranking semántico aproximado, no coincidencias exhaustivas ni prueba "
            "de un hecho o ausencia narrativa."
            if mode in {"semantic", "hybrid"}
            else NOTICE,
        }

    def read_scene(
        self,
        book: str,
        scene_id: str,
        expected_hash: str,
        offset: int = 0,
        max_chars: int = 8000,
        neighbors: int = 0,
    ) -> dict:
        self.check_book(book)
        bounded(offset, 0, 10_000_000, "offset")
        bounded(max_chars, 100, 12_000, "max_chars")
        bounded(neighbors, 0, 1, "neighbors")
        parsed = re.fullmatch(r"(.+\.md)#s([0-9]+)", scene_id)
        if not parsed:
            raise ValueError("scene_id debe provenir de search o chapter_context")
        self.index.sync()
        path, scene = parsed[1], int(parsed[2])
        with self.index.connect() as db:
            document = db.execute(
                "SELECT * FROM documents WHERE book=? AND path=?", (book, path)
            ).fetchone()
            if document is None:
                raise ValueError("Fuente no indexada")
            if document["hash"] != expected_hash:
                raise ValueError("La versión cambió; busca de nuevo antes de leer esta escena")
            units = db.execute(
                "SELECT * FROM chunks WHERE book=? AND path=? AND scene=?", (book, path, scene)
            ).fetchall()
            if not units:
                raise ValueError("Escena no encontrada")
            nearby = db.execute(
                """SELECT min(char_start),max(char_end) FROM chunks
                WHERE book=? AND path=? AND scene BETWEEN ? AND ?""",
                (book, path, scene - neighbors, scene + neighbors),
            ).fetchone()
        source = self.index.source(book, path, expected_hash)
        start, end = nearby[0], nearby[1]
        length = end - start
        if offset > length:
            raise ValueError("offset excede la longitud de la escena")
        stop = min(end, start + offset + max_chars)
        text = source[start + offset : stop]
        citation = {
            "line_start": source.count("\n", 0, start + offset) + 1,
            "line_end": source.count("\n", 0, max(start + offset, stop - 1)) + 1,
        }
        next_offset = offset + len(text)
        return {
            "book": book,
            "path": path,
            "scene_id": scene_id,
            "source_hash": expected_hash,
            "kind": document["kind"],
            "text": text,
            **citation,
            "offset": offset,
            "total_chars": length,
            "has_more": next_offset < length,
            "next_offset": next_offset if next_offset < length else None,
            "neighbors": neighbors,
        }

    def chapter_document(self, book: str, chapter: str):
        self.check_book(book)
        with self.index.connect() as db:
            rows = db.execute(
                """SELECT * FROM documents WHERE book=? AND kind='manuscrito'
                AND (chapter=? OR path=?)""",
                (book, str(chapter), chapter),
            ).fetchall()
        if len(rows) != 1:
            raise ValueError("Capítulo no encontrado o ambiguo; usa su ruta relativa exacta")
        return rows[0]

    def chapter_context(self, book: str, chapter: str, max_chars: int = 3000) -> dict:
        bounded(max_chars, 500, 8000, "max_chars")
        self.check_book(book)
        self.index.sync()
        document = self.chapter_document(book, chapter)
        metadata = json.loads(document["metadata"])
        rows = self.rows(book, "manuscrito", document["chapter"])
        scenes = [
            reference(row) for row in rows if row["part"] == 1 and row["path"] == document["path"]
        ]
        names = {
            name.casefold()
            for field in ("personajes", "lugares")
            for name in links(metadata.get(field))
        }
        with self.index.connect() as db:
            bible = db.execute(
                "SELECT path,title,hash FROM documents WHERE book=? AND kind='biblia'", (book,)
            ).fetchall()
        candidates = [
            {"path": row["path"], "title": row["title"], "source_hash": row["hash"]}
            for row in bible
            if row["path"].rsplit("/", 1)[-1][:-3].casefold() in names
            or row["path"].startswith("00-Biblia/Reglas/")
            or row["path"] == "00-Biblia/Cronologia.md"
        ]
        aliases = [document["path"].rsplit("/", 1)[-1][:-3], document["title"]]
        chapter_rows = [
            row
            for row in self.rows(book, "revision")
            if row["resolved"] == 0
            and any(
                f"[[{alias}]]" in row["text"]
                or re.search(
                    r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])", row["heading"], re.IGNORECASE
                )
                for alias in aliases
            )
        ]
        issues, remaining = [], max_chars
        for row in chapter_rows[:10]:
            size = min(350, remaining)
            if size < 100:
                break
            issues.append(
                {
                    **reference(row),
                    "excerpt": row["text"][:size],
                    "truncated": len(row["text"]) > size,
                }
            )
            remaining -= min(len(row["text"]), size)
        return {
            "book": book,
            "path": document["path"],
            "source_hash": document["hash"],
            "metadata": metadata,
            "scenes": scenes[:40],
            "scenes_omitted": max(0, len(scenes) - 40),
            "bible_candidates": candidates[:30],
            "bible_candidates_omitted": max(0, len(candidates) - 30),
            "open_issues": issues,
            "open_issue_chunks": len(chapter_rows),
            "issues_omitted": len(chapter_rows) - len(issues),
            "notice": "No incluye prosa ni resueltos. Biblia: referencias candidatas, no hechos verificados.",
        }

    def lint_chapter(self, book: str, chapter: str, limit: int = 30, offset: int = 0) -> dict:
        bounded(limit, 1, 100, "limit")
        bounded(offset, 0, 1_000_000, "offset")
        self.check_book(book)
        self.index.sync()
        document = self.chapter_document(book, chapter)
        text = self.index.source(book, document["path"], document["hash"])
        style_path = self.index.books[book] / "CLAUDE.md"
        style = style_path.read_text(encoding="utf-8") if style_path.is_file() else ""
        report = lint_text(text, style)
        findings = report["findings"]
        report["findings"] = findings[offset : offset + limit]
        stop = offset + len(report["findings"])
        return {
            "book": book,
            "path": document["path"],
            "source_hash": document["hash"],
            "style_hash": sha256(style.encode()).hexdigest(),
            **report,
            "total_findings": len(findings),
            "has_more": stop < len(findings),
            "next_offset": stop if stop < len(findings) else None,
        }

    def entity_graph(self, book: str, entity: str, limit: int = 5, offset: int = 0) -> dict:
        bounded(limit, 1, 20, "limit")
        bounded(offset, 0, 1_000_000, "offset")
        if not entity.strip() or len(entity) > 120:
            raise ValueError("entity debe tener entre 1 y 120 caracteres no vacíos")
        self.check_book(book)
        self.index.sync()
        folded = fold(entity)
        empty = {
            "book": book,
            "entity": entity,
            "kind": None,
            "found": False,
            "ficha": None,
            "declared": [],
            "untagged": [],
            "co_mentioned": [],
            "relations": {"out": [], "in": []},
            "mentions": [],
            "total_references": 0,
            "has_more": False,
            "next_offset": None,
            "notice": "No está en la biblia ni en el frontmatter de ningún capítulo. "
            "Para menciones literales libres (p. ej. un alias), usa entity_evidence.",
        }
        with self.index.connect() as db:
            ficha = db.execute(
                "SELECT * FROM entities WHERE book=? AND name_fold=? ORDER BY kind LIMIT 1",
                (book, folded),
            ).fetchone()
            declared_name = None
            if ficha is None:
                row = db.execute(
                    "SELECT DISTINCT name, kind FROM entity_mentions"
                    " WHERE book=? AND name_fold=? LIMIT 1",
                    (book, folded),
                ).fetchone()
                if row is None:
                    return empty
                declared_name = row["name"]
                kind = row["kind"]
            else:
                declared_name = ficha["name"]
                kind = ficha["kind"]
            rows = db.execute(
                """SELECT c.book, c.path, c.scene, c.part, c.heading,
                          c.line_start, c.line_end, m.source,
                          d.kind, d.chapter, d.hash
                   FROM entity_mentions m
                   JOIN chunks c ON c.id = m.chunk_id
                   JOIN documents d ON d.book = c.book AND d.path = c.path
                   WHERE m.book=? AND m.name_fold=?
                   ORDER BY c.path, c.scene, c.part, m.source
                   LIMIT ? OFFSET ?""",
                (book, folded, limit, offset),
            ).fetchall()
            total = db.execute(
                "SELECT count(*) FROM entity_mentions WHERE book=? AND name_fold=?",
                (book, folded),
            ).fetchone()[0]
            by_doc = db.execute(
                """SELECT d.path, d.chapter, d.title,
                          sum(m.source='frontmatter') AS fm,
                          sum(m.source='mention') AS mn,
                          count(*) AS chunks
                   FROM entity_mentions m
                   JOIN chunks c ON c.id = m.chunk_id
                   JOIN documents d ON d.book = c.book AND d.path = c.path
                   WHERE m.book=? AND m.name_fold=?
                   GROUP BY d.path, d.chapter, d.title
                   ORDER BY d.path""",
                (book, folded),
            ).fetchall()
            co_mentioned = [
                {
                    "name": row["name"],
                    "kind": row["kind"],
                    "ficha": bool(row["has_ficha"]),
                    "chunks": row["chunks"],
                }
                for row in db.execute(
                    """SELECT m2.name, m2.kind,
                              (e.path IS NOT NULL) AS has_ficha,
                              count(DISTINCT m2.chunk_id) AS chunks
                       FROM entity_mentions m2
                       LEFT JOIN entities e
                         ON e.book = m2.book AND e.name_fold = m2.name_fold AND e.kind = m2.kind
                       WHERE m2.book=? AND m2.name_fold<>?
                         AND m2.source='mention'
                         AND m2.chunk_id IN (
                             SELECT chunk_id FROM entity_mentions
                             WHERE book=? AND name_fold=? AND source='mention')
                       GROUP BY m2.name, m2.kind
                       ORDER BY chunks DESC, m2.name
                       LIMIT 15""",
                    (book, folded, book, folded),
                ).fetchall()
            ]
            rel_out: list[dict] = []
            rel_in: list[dict] = []
            if ficha is not None:
                rel_out = [
                    {"target": row["target"], "kind": row["target_kind"]}
                    for row in db.execute(
                        "SELECT target, target_kind FROM entity_relations"
                        " WHERE book=? AND source_path=? ORDER BY target",
                        (book, ficha["path"]),
                    ).fetchall()
                ]
                rel_in = [
                    {
                        "path": row["source_path"],
                        "name": row["source_name"],
                        "kind": row["source_kind"],
                    }
                    for row in db.execute(
                        """SELECT r.source_path, e.name AS source_name, e.kind AS source_kind
                           FROM entity_relations r
                           JOIN entities e ON e.book = r.book AND e.path = r.source_path
                           WHERE r.book=? AND r.target_fold=?
                           ORDER BY e.name""",
                        (book, folded),
                    ).fetchall()
                ]
        declared = [
            {
                "path": row["path"],
                "chapter": row["chapter"],
                "title": row["title"],
                "chunks": row["chunks"],
            }
            for row in by_doc
            if row["fm"] > 0
        ]
        untagged = [
            {
                "path": row["path"],
                "chapter": row["chapter"],
                "title": row["title"],
                "chunks": row["chunks"],
            }
            for row in by_doc
            if row["fm"] == 0 and row["mn"] > 0
        ]
        mentions = [{**reference(row), "source": row["source"]} for row in rows]
        next_offset = offset + len(rows)
        return {
            "book": book,
            "entity": declared_name,
            "kind": kind,
            "found": True,
            "ficha": (
                {
                    "path": ficha["path"],
                    "source_hash": ficha["hash"],
                    "kind": ficha["kind"],
                    "metadata": json.loads(ficha["metadata"]),
                }
                if ficha is not None
                else None
            ),
            "declared": declared,
            "untagged": untagged,
            "co_mentioned": co_mentioned,
            "relations": {"out": rel_out, "in": rel_in},
            "mentions": mentions,
            "total_references": total,
            "has_more": next_offset < total,
            "next_offset": next_offset if next_offset < total else None,
            "notice": "Referencia derivada y determinista: co-mención y frontmatter, "
            "no hechos, causalidad ni ausencia narrativa.",
        }

    def book_overview(self, book: str) -> dict:
        """Tablero del libro: capítulos, fichas, issues y frescura, sin prosa."""
        self.check_book(book)
        self.index.sync()
        with self.index.connect() as db:
            chapter_rows = db.execute(
                "SELECT path, title, metadata FROM documents WHERE book=? AND kind='manuscrito'",
                (book,),
            ).fetchall()
            entity_rows = db.execute(
                "SELECT path, name, kind, metadata FROM entities WHERE book=? ORDER BY kind, name",
                (book,),
            ).fetchall()
            issue_rows = db.execute(
                """SELECT c.path, c.line_start, c.heading, c.text, c.resolved
                FROM chunks c JOIN documents d ON d.book=c.book AND d.path=c.path
                WHERE c.book=? AND d.kind='revision' AND c.resolved IS NOT NULL
                ORDER BY c.path, c.scene, c.part""",
                (book,),
            ).fetchall()
        chapters = [
            {"path": row["path"], "title": row["title"], "frontmatter": json.loads(row["metadata"])}
            for row in chapter_rows
        ]

        def chapter_key(item):
            try:
                return (0, float(str(item["frontmatter"].get("capitulo"))), item["path"])
            except (TypeError, ValueError):
                return (1, 0.0, item["path"])

        chapters.sort(key=chapter_key)
        entities = [
            {
                "path": row["path"],
                "name": row["name"],
                "kind": row["kind"],
                "frontmatter": json.loads(row["metadata"]),
            }
            for row in entity_rows
        ]
        files: dict[str, dict[str, int]] = {}
        for row in issue_rows:
            counts = files.setdefault(row["path"], {"open": 0, "resolved": 0})
            counts["open" if row["resolved"] == 0 else "resolved"] += 1
        open_rows = [row for row in issue_rows if row["resolved"] == 0]
        open_items = [
            {
                "path": row["path"],
                "line_start": row["line_start"],
                "heading": row["heading"],
                "excerpt": row["text"][:400].strip(),
                "truncated": len(row["text"]) > 400,
            }
            for row in open_rows[:60]
        ]
        return {
            "book": book,
            "chapters": chapters[:200],
            "chapters_omitted": max(0, len(chapters) - 200),
            "entities": entities[:400],
            "entities_omitted": max(0, len(entities) - 400),
            "issues": {
                "open": len(open_rows),
                "resolved": len(issue_rows) - len(open_rows),
                "files": [{"path": path, **counts} for path, counts in files.items()],
                "open_items": open_items,
                "open_omitted": max(0, len(open_rows) - 60),
            },
            "index": self.status(),
            "notice": "Tablero derivado de frontmatter y checkboxes; sincroniza antes de "
            "responder, así que refleja los archivos actuales. Los issues son decisiones "
            "registradas por el autor, no hechos de la prosa.",
        }
