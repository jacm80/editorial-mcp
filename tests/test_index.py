from hashlib import sha256

import pytest
from conftest import write

from editorial_mcp.index import BookIndex
from editorial_mcp.parsing import frontmatter, segments


def test_incremental_update_and_delete(library):
    service, root, _ = library
    assert service.index.status()["fresh"]
    assert service.index.sync()["updated"] == 0
    path = root / "01-Manuscrito/Cap-04.md"
    path.write_text(path.read_text().replace("dos mil", "tres mil"))
    assert service.index.status()["pending"]["changed"] == 1
    assert service.index.sync()["updated"] == 1
    assert service.search("uno", "dos mil", mode="literal")["total_matching_chunks"] == 0
    assert service.search("uno", "tres mil", mode="literal")["total_matching_chunks"] == 1
    path.unlink()
    assert service.index.sync()["removed"] == 1
    assert service.search("uno", "tres mil")["total_matching_chunks"] == 0


def test_new_file_and_malformed_yaml_atomicity(library):
    service, root, _ = library
    write(root, "01-Manuscrito/Cap-05.md", "---\ncapitulo: 5\n---\nNuevo texto.\n")
    assert service.index.status()["pending"]["added"] == 1
    assert service.index.sync()["updated"] == 1
    good = root / "01-Manuscrito/Cap-04.md"
    good.write_text(good.read_text().replace("dos mil", "tres mil"))
    bad = write(root, "01-Manuscrito/Cap-99.md", "---\ncapitulo: [\n---\nRoto.\n")
    with pytest.raises(ValueError, match="YAML inválido"):
        service.index.sync()
    with service.index.connect() as db:
        assert (
            "dos mil"
            in db.execute(
                "SELECT text FROM documents WHERE book='uno' AND path=?",
                ("01-Manuscrito/Cap-04.md",),
            ).fetchone()[0]
        )
    bad.unlink()


def test_sources_do_not_change(library):
    service, root, _ = library
    before = {p: sha256(p.read_bytes()).digest() for p in root.rglob("*.md")}
    service.search("uno", "Martha")
    service.chapter_context("uno", "4")
    service.lint_chapter("uno", "4")
    assert before == {p: sha256(p.read_bytes()).digest() for p in root.rglob("*.md")}


def test_external_symlink_is_not_indexed(library, tmp_path):
    service, root, _ = library
    outside = tmp_path / "private.md"
    outside.write_text("Secreto exterior.")
    (root / "01-Manuscrito/enlace.md").symlink_to(outside)
    service.index.sync()
    assert service.search("uno", "Secreto")["total_matching_chunks"] == 0


def test_reject_database_inside_sources(library):
    _, root, _ = library
    with pytest.raises(ValueError, match="fuentes"):
        BookIndex({"uno": root}, root / "01-Manuscrito/index.sqlite3")


def test_path_traversal_is_rejected(library):
    service, _, _ = library
    with pytest.raises(ValueError, match="fuera"):
        service.index.source("uno", "../private.md", "x")


def test_remap_same_id_to_different_root(library, tmp_path):
    service, root, _ = library
    relocated = tmp_path / "relocated"
    for path in root.rglob("*.md"):
        write(relocated, path.relative_to(root).as_posix(), path.read_text())
    index = BookIndex({"uno": relocated}, service.index.database)
    assert index.status()["pending"]["changed"] > 0
    assert index.sync()["updated"] > 0
    with index.connect() as db:
        assert db.execute("SELECT root FROM documents WHERE book='uno'").fetchone()[0] == str(
            relocated
        )


def test_frontmatter_missing_or_invalid():
    assert frontmatter("Texto sin YAML")[0] == {}
    with pytest.raises(ValueError, match="cierre"):
        frontmatter("---\na: b\n")
    with pytest.raises(ValueError, match="mapping"):
        frontmatter("---\n- lista\n---\n")


def test_scene_separators_and_preserved_crlf():
    text = "---\r\ncapitulo: 4.5\r\n---\r\n# Título\r\n\r\nUno.\r\n\r\n* * *\r\n\r\nDos.\r\n"
    units = segments(text, "manuscrito")
    assert len(units) == 2
    assert units[0].line_start == 4
    assert units[1].line_start == 10
    assert units[1].text == "Dos.\r\n"
    for unit in units:
        assert text[unit.char_start : unit.char_end] == unit.text


def test_long_scene_is_split_without_loss():
    text = "# Título\n\n" + "Martha vio algo.\n\n" * 10
    units = segments(text, "manuscrito", max_chars=50)
    assert len(units) > 1
    assert {unit.scene for unit in units} == {1}
    assert "".join(unit.text for unit in units) == text.rstrip("\n") + "\n"


def test_empty_sources(tmp_path):
    (tmp_path / "01-Manuscrito").mkdir()
    index = BookIndex({"vacio": tmp_path}, tmp_path / "cache.db")
    assert index.sync() == {"updated": 0, "removed": 0, "unchanged": 0}
    assert index.status()["fresh"]


def test_missing_book_root(tmp_path):
    with pytest.raises(ValueError, match="01-Manuscrito"):
        BookIndex({"uno": tmp_path}, tmp_path / "cache.db")
