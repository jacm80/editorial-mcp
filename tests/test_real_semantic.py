"""Smoke real opt-in; no descarga modelos ni altera fuentes del autor."""

import os
from pathlib import Path

import pytest
from conftest import write

from editorial_mcp.semantic import LocalEmbedder
from editorial_mcp.service import EditorialService

pytestmark = pytest.mark.skipif(
    not os.environ.get("EDITORIAL_MODEL_CACHE"),
    reason="Requiere checkpoint local preparado (opt-in)",
)


@pytest.fixture(scope="module")
def real_embedder():
    return LocalEmbedder(Path(os.environ["EDITORIAL_MODEL_CACHE"]))


def test_real_checkpoint_has_expected_dimension_and_no_network(real_embedder, monkeypatch):
    import httpx

    def forbid_network(*args, **kwargs):
        raise AssertionError("La inferencia local no debe usar HTTP")

    monkeypatch.setattr(httpx.Client, "send", forbid_network)
    vectors = real_embedder.encode(
        ["El terror dominaba al pueblo.", "El invierno cubrió las calles."]
    )
    assert len(vectors) == 2
    assert all(len(vector) == 256 for vector in vectors)
    assert all(
        sum(value * value for value in vector) == pytest.approx(1, abs=0.001) for vector in vectors
    )


def test_real_spanish_synonym_and_cache(library, real_embedder, monkeypatch):
    base, root, _ = library
    write(
        root, "01-Manuscrito/Cap-10.md", "---\ncapitulo: 10\n---\nEl terror dominaba al pueblo.\n"
    )
    write(
        root,
        "01-Manuscrito/Cap-11.md",
        "---\ncapitulo: 11\n---\nLa nieve traía un invierno frío.\n",
    )
    service = EditorialService(base.index, real_embedder)
    assert service.search("uno", "miedo")["results"] == []
    result = service.search("uno", "miedo", mode="semantic", limit=1)
    assert result["results"][0]["chapter"] == "10"

    def forbid_encoding(*args, **kwargs):
        raise AssertionError("La consulta idéntica debe usar embeddings ya cacheados")

    monkeypatch.setattr(real_embedder, "encode", forbid_encoding)
    assert service.search("uno", "miedo", mode="semantic", limit=1)["results"] == result["results"]
