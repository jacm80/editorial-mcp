"""SQLite/FTS5 local. Solo escribe en la base derivada, nunca en las fuentes."""

import json
import sqlite3
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from .parsing import frontmatter, segments

SCHEMA_VERSION = 1
SOURCE_DIRS = {"01-Manuscrito": "manuscrito", "00-Biblia": "biblia", "02-Revision": "revision"}


class BookIndex:
    def __init__(self, books: dict[str, Path], database: Path):
        if not books:
            raise ValueError("Se necesita al menos un libro")
        self.books = {book: root.resolve() for book, root in books.items()}
        for book, root in self.books.items():
            if not (root / "01-Manuscrito").is_dir():
                raise ValueError(f"{book}: no existe 01-Manuscrito/ en {root}")
        self.database = database.resolve()
        for root in self.books.values():
            for folder in SOURCE_DIRS:
                if self.database.is_relative_to(root / folder):
                    raise ValueError("La base derivada no puede estar dentro de las fuentes")
        if self.database.suffix not in {".sqlite", ".sqlite3", ".db"}:
            raise ValueError("La base debe usar extensión .sqlite, .sqlite3 o .db")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, SCHEMA_VERSION}:
                raise ValueError("Versión de índice incompatible; usa otra base regenerable")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    book TEXT NOT NULL, path TEXT NOT NULL, root TEXT NOT NULL,
                    kind TEXT NOT NULL, title TEXT NOT NULL, chapter TEXT,
                    metadata TEXT NOT NULL, hash TEXT NOT NULL, text TEXT NOT NULL,
                    body_start INTEGER NOT NULL,
                    PRIMARY KEY(book, path)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY, book TEXT NOT NULL, path TEXT NOT NULL,
                    scene INTEGER NOT NULL, part INTEGER NOT NULL, heading TEXT NOT NULL,
                    line_start INTEGER NOT NULL, line_end INTEGER NOT NULL,
                    char_start INTEGER NOT NULL, char_end INTEGER NOT NULL,
                    text TEXT NOT NULL, resolved INTEGER,
                    FOREIGN KEY(book, path) REFERENCES documents(book, path) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS chunk_document ON chunks(book, path, scene);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    text, content='chunks', content_rowid='id',
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TRIGGER IF NOT EXISTS chunks_insert AFTER INSERT ON chunks BEGIN
                    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS chunks_delete AFTER DELETE ON chunks BEGIN
                    INSERT INTO chunks_fts(chunks_fts, rowid, text)
                    VALUES ('delete', old.id, old.text);
                END;
            """)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def sources(self) -> dict[tuple[str, str], tuple[str, str, str]]:
        result = {}
        for book, root in self.books.items():
            for folder, kind in SOURCE_DIRS.items():
                for path in sorted((root / folder).rglob("*.md")):
                    if not path.resolve().is_relative_to(root) or not path.is_file():
                        continue
                    raw = path.read_bytes()
                    result[book, path.relative_to(root).as_posix()] = (
                        kind,
                        sha256(raw).hexdigest(),
                        raw.decode("utf-8"),
                    )
        return result

    def status(self) -> dict:
        current = self.sources()
        with self.connect() as db:
            indexed = {
                (r["book"], r["path"]): (r["hash"], r["root"])
                for r in db.execute("SELECT book, path, hash, root FROM documents")
            }
            counts = dict(db.execute("SELECT kind, count(*) FROM documents GROUP BY kind"))
            chunks = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        added = sorted(set(current) - set(indexed))
        removed = sorted(set(indexed) - set(current))
        changed = sorted(
            key
            for key in set(current) & set(indexed)
            if current[key][1] != indexed[key][0] or str(self.books[key[0]]) != indexed[key][1]
        )
        return {
            "fresh": not (added or removed or changed),
            "books": list(self.books),
            "documents": counts,
            "chunks": chunks,
            "pending": {"added": len(added), "changed": len(changed), "removed": len(removed)},
            "schema_version": SCHEMA_VERSION,
            "retrieval": "lexical; no embeddings",
        }

    def sync(self) -> dict:
        current = self.sources()
        updated = removed = unchanged = 0
        with self.connect() as db:
            indexed = {
                (r["book"], r["path"]): (r["hash"], r["root"])
                for r in db.execute("SELECT book, path, hash, root FROM documents")
            }
            for key in set(indexed) - set(current):
                db.execute("DELETE FROM documents WHERE book=? AND path=?", key)
                removed += 1
            for (book, path), (kind, digest, text) in current.items():
                if indexed.get((book, path)) == (digest, str(self.books[book])):
                    unchanged += 1
                    continue
                try:
                    metadata, body_start = frontmatter(text)
                    units = segments(text, kind)
                except ValueError as error:
                    raise ValueError(f"{book}:{path}: {error}") from error
                heading = next((u.heading for u in units if u.heading), Path(path).stem)
                title = str(metadata.get("titulo") or heading)
                chapter = str(metadata["capitulo"]) if "capitulo" in metadata else None
                encoded = json.dumps(metadata, ensure_ascii=False, default=str)
                db.execute("DELETE FROM documents WHERE book=? AND path=?", (book, path))
                db.execute(
                    "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        book,
                        path,
                        str(self.books[book]),
                        kind,
                        title,
                        chapter,
                        encoded,
                        digest,
                        text,
                        body_start,
                    ),
                )
                db.executemany(
                    """INSERT INTO chunks
                    (book,path,scene,part,heading,line_start,line_end,char_start,char_end,text,resolved)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    [
                        (
                            book,
                            path,
                            u.scene,
                            u.part,
                            u.heading,
                            u.line_start,
                            u.line_end,
                            u.char_start,
                            u.char_end,
                            u.text,
                            u.resolved,
                        )
                        for u in units
                    ],
                )
                updated += 1
        return {"updated": updated, "removed": removed, "unchanged": unchanged}

    def source(self, book: str, path: str, digest: str) -> str:
        if book not in self.books:
            raise ValueError(f"Libro desconocido: {book}")
        root = self.books[book]
        target = (root / path).resolve()
        if not target.is_relative_to(root) or target == root:
            raise ValueError("Ruta fuera del libro")
        raw = target.read_bytes()
        if sha256(raw).hexdigest() != digest:
            raise ValueError("La fuente cambió durante la consulta; repite la búsqueda")
        return raw.decode("utf-8")
