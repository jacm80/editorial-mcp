import pytest
from conftest import write


def test_books_are_isolated(library):
    service, _, _ = library
    assert service.search("uno", "Chile")["results"] == []
    assert service.search("dos", "Chile")["total_matching_chunks"] == 1
    with pytest.raises(ValueError, match="desconocido"):
        service.search("tres", "Martha")


def test_accents_literal_and_safe_fts(library):
    service, _, _ = library
    assert service.search("uno", "gomez")["total_matching_chunks"] == 1
    assert service.search("uno", "gomez", mode="literal")["results"] == []
    assert service.search("uno", "GÓMEZ", mode="literal")["total_matching_chunks"] == 1
    assert service.search("uno", 'Martha" OR "inventada')["results"] == []
    assert service.search("uno", '" OR 1=1 --', mode="literal")["results"] == []


def test_budget_and_pagination(library):
    service, _, _ = library
    first = service.search("uno", "Martha", limit=1, max_chars=200)
    assert first["has_more"]
    assert first["excerpt_chars"] <= 200
    second = service.search("uno", "Martha", limit=1, offset=first["next_offset"])
    assert second["results"][0]["chunk_id"] != first["results"][0]["chunk_id"]


@pytest.mark.parametrize(
    "kwargs",
    [{"limit": 0}, {"offset": -1}, {"max_chars": 1}, {"mode": "vector"}, {"kind": "private"}],
)
def test_invalid_search_arguments(library, kwargs):
    with pytest.raises(ValueError):
        library[0].search("uno", "Martha", **kwargs)


@pytest.mark.parametrize("query", ["", " ", "a" * 501, "***"])
def test_invalid_query(library, query):
    with pytest.raises(ValueError):
        library[0].search("uno", query)


def test_chapter_context_excludes_closed_issues_and_prose(library):
    result = library[0].chapter_context("uno", "4")
    assert result["metadata"]["capitulo"] == 4
    assert len(result["scenes"]) == 2
    assert result["open_issue_chunks"] == 2
    assert not any("Ya se corrigió" in r["excerpt"] for r in result["open_issues"])
    assert not any("text" in scene for scene in result["scenes"])
    assert len(result["bible_candidates"]) == 2
    assert library[0].chapter_context("uno", "4.5")["metadata"]["titulo"] == "Interludio"
    assert library[0].chapter_context("uno", "01-Manuscrito/Cap-04.md")["metadata"]["capitulo"] == 4


def test_context_does_not_mix_chapter_and_interlude_headings(library):
    service, root, _ = library
    write(
        root,
        "02-Revision/Interludio.md",
        "# Calidad\n\n### Cap-04-Interludio\n- [ ] Solo interludio.\n",
    )
    context = service.chapter_context("uno", "4")
    assert not any("Solo interludio" in r["excerpt"] for r in context["open_issues"])


def test_read_scene_pagination_and_hash_guard(library):
    service, root, _ = library
    path = root / "01-Manuscrito/Cap-04.md"
    path.write_text(
        path.read_text().replace("Martha encontró", "Sin novedad. " * 30 + "\n\nMartha encontró")
    )
    hit = service.search("uno", "dos mil", limit=1, chapter="4")["results"][0]
    result = service.read_scene("uno", hit["scene_id"], hit["source_hash"], max_chars=100)
    assert result["has_more"]
    combined = result["text"]
    while result["has_more"]:
        result = service.read_scene(
            "uno", hit["scene_id"], hit["source_hash"], offset=result["next_offset"], max_chars=100
        )
        combined += result["text"]
    assert "dos mil" in combined
    assert "semilla" not in combined
    with_neighbors = service.read_scene("uno", hit["scene_id"], hit["source_hash"], neighbors=1)
    assert "semilla" in with_neighbors["text"]
    path = root / hit["path"]
    path.write_text(path.read_text() + "\nCambio nuevo.\n")
    with pytest.raises(ValueError, match="versión cambió"):
        service.read_scene("uno", hit["scene_id"], hit["source_hash"])


def test_literal_phrase_across_chunk_boundary(library):
    service, root, _ = library
    text = (
        "---\ncapitulo: 8\n---\n# Largo\n\n" + "Una palabra. " * 310 + "Martha\n\nEmily apareció.\n"
    )
    write(root, "01-Manuscrito/Cap-08.md", text)
    result = service.search("uno", "Martha\n\nEmily", mode="literal")
    assert result["total_matching_chunks"] == 1
    assert "Martha\n\nEmily" in result["results"][0]["excerpt"]


def test_entity_filter_is_chapter_metadata(library):
    result = library[0].search("uno", "semilla", entity="Gómez")
    assert result["total_matching_chunks"] == 1
    assert "frontmatter" in result["entity_filter_scope"]


def test_resolved_issues_are_opt_in(library):
    service = library[0]
    assert service.search("uno", "corrigió", kind="revision")["results"] == []
    assert service.search("uno", "corrigió", kind="revision", include_resolved=True)["results"]
    open_result = service.search("uno", "explicación", kind="revision")
    assert all("Ya se corrigió" not in hit["excerpt"] for hit in open_result["results"])


def test_no_missing_scene_claim(library):
    result = library[0].search("uno", "Nunca existió", mode="literal")
    assert result["results"] == []
    assert "prueba de ausencia" in result["notice"]


def test_linter_skips_yaml_and_respects_style(library):
    service, root, _ = library
    write(
        root,
        "01-Manuscrito/Cap-09.md",
        """---
capitulo: 9
titulo: "..."
---
- Hola vos?.
— Texto con а cirílica y "comillas"...
""",
    )
    result = service.lint_chapter("uno", "9", limit=2)
    assert result["counts"]["voseo"] == 1
    assert result["counts"]["cirilicos"] == 1
    assert result["counts"]["comillas_rectas"] == 2
    assert result["counts"]["puntos_suspensivos"] == 1
    assert result["counts"]["doble_cierre"] == 1
    assert result["has_more"]
    assert all(f["line"] >= 5 for f in result["findings"])
    (root / "CLAUDE.md").write_text("Registro rioplatense con voseo.")
    result = service.lint_chapter("uno", "9")
    assert "voseo" not in result["counts"]
    assert "comillas_rectas" not in result["counts"]


def test_read_unknown_or_malformed_scene(library):
    service = library[0]
    with pytest.raises(ValueError):
        service.read_scene("uno", "../secreto.md#s001", "x")
    with pytest.raises(ValueError):
        service.read_scene("uno", "malformed", "x")
    hit = service.search("uno", "Martha")["results"][0]
    with pytest.raises(ValueError):
        service.read_scene("uno", hit["scene_id"], hit["source_hash"], offset=999_999)
