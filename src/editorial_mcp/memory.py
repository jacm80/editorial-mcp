"""Memoria de sesión por libro (patrón MCP-RAPP, capa MCP).

Guarda decisiones, descubrimientos y resúmenes de sesión en un SQLite local
junto al resto de datos derivados (fuera de Git). Sin vectores: la búsqueda
es léxica (LIKE) sobre título y contenido, acotada como el resto del servicio.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MEMORY_TYPES = {
    "decision",
    "discovery",
    "convention",
    "preference",
    "session_summary",
    "other",
}

SUMMARY_TEMPLATE = """## Goal
{goal}

## Accomplished
{accomplished}

## Discoveries
{discoveries}

## Next Steps
{next_steps}

## Relevant Files
{files}
"""


class MemoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS memories(
                id INTEGER PRIMARY KEY,
                book TEXT NOT NULL,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                topic_key TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(book, topic_key))"""
            )
            db.execute("CREATE INDEX IF NOT EXISTS idx_mem_updated ON memories(book, updated_at)")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def remember(
        self,
        book: str,
        title: str,
        content: str,
        type: str = "other",
        topic_key: str | None = None,
    ) -> dict:
        if type not in MEMORY_TYPES:
            raise ValueError(f"type: {', '.join(sorted(MEMORY_TYPES))}")
        if not title.strip() or not content.strip():
            raise ValueError("title y content no pueden estar vacíos")
        if len(title) > 200 or len(content) > 8000:
            raise ValueError("title hasta 200 caracteres, content hasta 8000")
        key = topic_key or f"note-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self.connect() as db:
            existing = db.execute(
                "SELECT * FROM memories WHERE book=? AND topic_key=?", (book, key)
            ).fetchone()
            if (
                existing
                and existing["title"] == title
                and existing["content"] == content
                and existing["type"] == type
            ):
                return {"book": book, "topic_key": key, "status": "unchanged"}
            db.execute(
                """INSERT INTO memories(book,type,title,content,topic_key,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(book, topic_key) DO UPDATE SET
                type=excluded.type, title=excluded.title,
                content=excluded.content, updated_at=excluded.updated_at""",
                (book, type, title, content, key, now),
            )
        return {"book": book, "topic_key": key, "status": "saved"}

    def recall(self, book: str, query: str, limit: int = 5, type: str | None = None) -> list[dict]:
        if type is not None and type not in MEMORY_TYPES:
            raise ValueError(f"type: {', '.join(sorted(MEMORY_TYPES))}")
        terms = [term for term in query.split() if term.strip()]
        if not terms:
            raise ValueError("query no puede estar vacía")
        like = " OR ".join(["(title LIKE ? OR content LIKE ?)"] * len(terms))
        args: list = []
        for term in terms:
            args += [f"%{term}%", f"%{term}%"]
        conditions, params = [f"({like})"], args
        if type is not None:
            conditions.append("type=?")
            params.append(type)
        with self.connect() as db:
            rows = db.execute(
                f"""SELECT book,type,title,content,topic_key,updated_at FROM memories
                WHERE book=? AND {" AND ".join(conditions)}
                ORDER BY updated_at DESC LIMIT ?""",
                (book, *params, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_summary(self, book: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT book,type,title,content,topic_key,updated_at FROM memories
                WHERE book=? AND type='session_summary'
                ORDER BY updated_at DESC LIMIT 1""",
                (book,),
            ).fetchone()
        return dict(row) if row else None

    def session_context(self, book: str, limit: int = 5) -> dict:
        summary = self.latest_summary(book)
        with self.connect() as db:
            rows = db.execute(
                """SELECT book,type,title,content,topic_key,updated_at FROM memories
                WHERE book=? AND type<>'session_summary'
                ORDER BY updated_at DESC LIMIT ?""",
                (book, limit),
            ).fetchall()
        return {
            "book": book,
            "latest_summary": summary,
            "recent": [dict(row) for row in rows],
            "notice": "Contexto de sesiones previas (decisiones y resúmenes), "
            "no hechos del manuscrito.",
        }

    def session_summary(
        self,
        book: str,
        goal: str,
        accomplished: str,
        discoveries: str,
        next_steps: str,
        files: str = "",
    ) -> dict:
        for name, value in (
            ("goal", goal),
            ("accomplished", accomplished),
            ("discoveries", discoveries),
            ("next_steps", next_steps),
        ):
            if not value.strip():
                raise ValueError(f"{name} no puede estar vacío")
            if len(value) > 4000:
                raise ValueError(f"{name} hasta 4000 caracteres")
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        content = SUMMARY_TEMPLATE.format(
            goal=goal.strip(),
            accomplished=accomplished.strip(),
            discoveries=discoveries.strip(),
            next_steps=next_steps.strip(),
            files=files.strip(),
        )
        return self.remember(
            book,
            title=f"Session summary: {book} ({day})",
            content=content,
            type="session_summary",
            topic_key=f"session-summary-{day}",
        )
