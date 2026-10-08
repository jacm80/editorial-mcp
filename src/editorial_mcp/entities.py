"""Capa relacional de la biblia: fichas, menciones y vínculos derivados.

Solo escribe en la base derivada. Los Markdown siguen siendo canónicos; esta
capa materializa en SQL el roster de fichas, sus vínculos [[...]] y dónde se
menciona cada entidad, para consultas deterministas de continuidad.

El esquema es común a cualquier libro del harness (convención init-libro):
fichas en 00-Biblia/{Personajes,Lugares}/ y campos personajes/lugares en el
frontmatter de los capítulos. Un libro sin esas carpetas no aporta entidades.
"""

import json
import re
import sqlite3
import unicodedata

from .parsing import links

# Cualquier cambio en estas tablas debe ir acompañado del bump de SCHEMA_VERSION.
DDL = """
CREATE TABLE IF NOT EXISTS entities (
    book TEXT NOT NULL,
    name TEXT NOT NULL,
    name_fold TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    metadata TEXT NOT NULL,
    hash TEXT NOT NULL,
    PRIMARY KEY (book, path)
);
CREATE INDEX IF NOT EXISTS entity_lookup ON entities(book, name_fold);
CREATE TABLE IF NOT EXISTS entity_mentions (
    id INTEGER PRIMARY KEY,
    book TEXT NOT NULL,
    name TEXT NOT NULL,
    name_fold TEXT NOT NULL,
    kind TEXT NOT NULL,
    chunk_id INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('frontmatter', 'mention')),
    UNIQUE (book, name_fold, chunk_id, source),
    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS entity_mention_chunk ON entity_mentions(book, chunk_id);
CREATE INDEX IF NOT EXISTS entity_mention_name ON entity_mentions(book, name_fold);
CREATE TABLE IF NOT EXISTS entity_relations (
    book TEXT NOT NULL,
    source_path TEXT NOT NULL,
    target TEXT NOT NULL,
    target_fold TEXT NOT NULL,
    target_kind TEXT,
    PRIMARY KEY (book, source_path, target),
    FOREIGN KEY (book, source_path) REFERENCES entities(book, path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS entity_relation_target ON entity_relations(book, target_fold);
"""

ENTITY_FOLDERS = (
    ("00-Biblia/Personajes", "personaje"),
    ("00-Biblia/Lugares", "lugar"),
)
FRONTMATTER_FIELDS = (("personajes", "personaje"), ("lugares", "lugar"))

_WIKILINK = re.compile(r"\[\[([^\[\]\n]+)\]\]")


def fold(value: str) -> str:
    """Caso plegado sin diacríticos: 'Gómez' == 'gomez'."""
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char)
    )


def relation_targets(text: str) -> list[str]:
    """Nombres de [[wikilinks]] del cuerpo, sin parte de alias tras '|'."""
    targets = []
    for match in _WIKILINK.finditer(text):
        target = match.group(1).split("|", 1)[0].strip()
        if target:
            targets.append(target)
    return targets


def rebuild(db: sqlite3.Connection, book: str) -> None:
    """Reconstruye la capa de entidades de un libro desde documentos/chunks.

    Se ejecuta dentro del sync de BookIndex, en la misma transacción; nunca
    toca las fuentes Markdown.
    """
    db.execute("DELETE FROM entity_relations WHERE book=?", (book,))
    db.execute("DELETE FROM entity_mentions WHERE book=?", (book,))
    db.execute("DELETE FROM entities WHERE book=?", (book,))

    fichas = db.execute(
        "SELECT path, text, metadata, hash FROM documents WHERE book=? AND kind='biblia'",
        (book,),
    ).fetchall()
    entity_rows = []
    for row in fichas:
        for folder, kind in ENTITY_FOLDERS:
            prefix = folder + "/"
            if not row["path"].startswith(prefix):
                continue
            name = row["path"][len(prefix) : -3]
            entity_rows.append(
                (book, name, fold(name), kind, row["path"], row["metadata"], row["hash"])
            )
            break
    db.executemany("INSERT INTO entities VALUES (?,?,?,?,?,?,?)", entity_rows)

    ficha_paths = {row[4] for row in entity_rows}
    kind_of = {row[2]: row[3] for row in entity_rows}
    for row in fichas:
        if row["path"] not in ficha_paths:
            continue
        for target in relation_targets(row["text"]):
            target_fold = fold(target)
            db.execute(
                "INSERT OR IGNORE INTO entity_relations"
                " (book, source_path, target, target_fold, target_kind) VALUES (?,?,?,?,?)",
                (book, row["path"], target, target_fold, kind_of.get(target_fold)),
            )

    # Roster de menciones: fichas + nombres declarados en frontmatter de capítulos.
    roster = {row[2]: (row[1], row[3]) for row in entity_rows}  # name_fold -> (name, kind)
    declared_by_path = {}
    for row in db.execute(
        "SELECT path, metadata FROM documents WHERE book=? AND kind='manuscrito'", (book,)
    ).fetchall():
        meta = json.loads(row["metadata"])
        names = set()
        for field, kind in FRONTMATTER_FIELDS:
            for name in links(meta.get(field)):
                roster.setdefault(fold(name), (name, kind))
                names.add(fold(name))
        declared_by_path[row["path"]] = names

    patterns = {
        name_fold: re.compile(r"(?<![\w])" + re.escape(name_fold) + r"(?![\w])")
        for name_fold in roster
    }
    chunk_rows = []
    for chunk in db.execute(
        """SELECT c.* FROM chunks c
           JOIN documents d ON d.book = c.book AND d.path = c.path
           WHERE c.book=? AND d.kind='manuscrito' ORDER BY c.id""",
        (book,),
    ).fetchall():
        folded_text = fold(chunk["text"])
        declared = declared_by_path.get(chunk["path"], set())
        for name_fold, (name, kind) in roster.items():
            # Independientes: declarado en frontmatter y/o presente en el texto.
            if name_fold in declared:
                chunk_rows.append((book, name, name_fold, kind, chunk["id"], "frontmatter"))
            if patterns[name_fold].search(folded_text):
                chunk_rows.append((book, name, name_fold, kind, chunk["id"], "mention"))
    db.executemany(
        "INSERT OR IGNORE INTO entity_mentions"
        " (book, name, name_fold, kind, chunk_id, source) VALUES (?,?,?,?,?,?)",
        chunk_rows,
    )
