import pytest
from conftest import write


def test_overview_chapters_entities_and_issues(library):
    service, _, _ = library
    result = service.book_overview("uno")
    # Orden por capitulo numérico: el interludio 4.5 va después del 4.
    assert [c["path"] for c in result["chapters"]] == [
        "01-Manuscrito/Cap-04.md",
        "01-Manuscrito/Cap-04-Interludio.md",
    ]
    first = result["chapters"][0]["frontmatter"]
    assert first["estado"] == "cerrado"
    assert first["personajes"] == ["[[Martha]]", "[[Gómez]]"]
    assert [e["name"] for e in result["entities"]] == ["Martha"]
    assert result["entities"][0]["kind"] == "personaje"
    issues = result["issues"]
    assert issues["open"] == 2
    assert issues["resolved"] == 1
    assert {"path": "02-Revision/Issues.md", "open": 1, "resolved": 1} in issues["files"]
    assert {"path": "02-Revision/Calidad.md", "open": 1, "resolved": 0} in issues["files"]
    excerpts = [item["excerpt"] for item in issues["open_items"]]
    assert any("Falta una explicación" in excerpt for excerpt in excerpts)
    assert any("Ritmo lento" in excerpt for excerpt in excerpts)
    assert result["index"]["fresh"]


def test_overview_syncs_edits_and_isolates_books(library):
    service, root, _ = library
    write(
        root,
        "01-Manuscrito/Cap-05.md",
        "---\ncapitulo: 5\nestado: borrador\n---\n# Capítulo 5\nEscena nueva.\n",
    )
    result = service.book_overview("uno")
    last = result["chapters"][-1]
    assert last["path"] == "01-Manuscrito/Cap-05.md"
    assert last["frontmatter"]["estado"] == "borrador"
    assert result["index"]["fresh"]
    other = service.book_overview("dos")
    assert [c["path"] for c in other["chapters"]] == ["01-Manuscrito/Cap-04.md"]
    assert other["entities"] == []
    assert other["issues"]["open"] == 0


def test_overview_unknown_book(library):
    service, _, _ = library
    with pytest.raises(ValueError, match="Libro desconocido"):
        service.book_overview("tres")
