from conftest import write


def test_entity_graph_ficha_declared_and_untagged(library):
    service, _, _ = library
    result = service.entity_graph("uno", "Martha")
    assert result["found"]
    assert result["kind"] == "personaje"
    assert result["ficha"]["path"] == "00-Biblia/Personajes/Martha.md"
    # Cap-04 la declara en frontmatter; el interludio la menciona sin declararla.
    assert any(d["chapter"] == "4" for d in result["declared"])
    assert any(i["path"] == "01-Manuscrito/Cap-04-Interludio.md" for i in result["untagged"])
    assert result["total_references"] >= 3
    assert any(m["source"] == "mention" for m in result["mentions"])


def test_entity_graph_relations_and_co_mentioned(library):
    service, root, _ = library
    write(
        root,
        "00-Biblia/Lugares/Iglesia.md",
        "# Iglesia\n\nLa parroquia donde oficia [[Martha]].\n",
    )
    result = service.entity_graph("uno", "Iglesia")
    assert result["found"]
    assert result["kind"] == "lugar"
    assert {"target": "Martha", "kind": "personaje"} in result["relations"]["out"]
    incoming = service.entity_graph("uno", "Martha")["relations"]["in"]
    assert any(r["path"] == "00-Biblia/Lugares/Iglesia.md" for r in incoming)
    # Gómez se declara en frontmatter sin ficha: aparece como co-mencionado.
    co = {c["name"]: c for c in service.entity_graph("uno", "Martha")["co_mentioned"]}
    assert co["Gómez"]["chunks"] >= 1
    assert co["Gómez"]["ficha"] is False


def test_entity_graph_frontmatter_only_name(library):
    service, _, _ = library
    result = service.entity_graph("uno", "Gómez")
    assert result["found"]
    assert result["ficha"] is None
    assert result["kind"] == "personaje"
    assert any(d["chapter"] == "4" for d in result["declared"])


def test_entity_graph_unknown_and_book_isolation(library):
    service, _, _ = library
    unknown = service.entity_graph("uno", "Milagro")
    assert not unknown["found"]
    assert "entity_evidence" in unknown["notice"]
    # "dos" menciona a Martha sin biblia ni frontmatter: no es parte del roster.
    other = service.entity_graph("dos", "Martha")
    assert not other["found"]


def test_entity_layer_rebuilds_on_edit(library):
    service, root, _ = library
    path = root / "01-Manuscrito/Cap-04-Interludio.md"
    path.write_text(path.read_text().replace("Martha vio", "Nadie vio"))
    result = service.entity_graph("uno", "Martha")
    assert not any(i["path"] == "01-Manuscrito/Cap-04-Interludio.md" for i in result["untagged"])
    assert service.index.status()["entities"] == 1
