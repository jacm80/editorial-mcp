"""Memoria de sesión por libro: remember/recall/session_context/session_summary."""

import pytest

from editorial_mcp.memory import MemoryStore


@pytest.fixture
def service_with_memory(library, tmp_path):
    service, _, _ = library
    service.memory = MemoryStore(tmp_path / "mem/memory.sqlite3")
    return service


def test_remember_and_recall(service_with_memory):
    service = service_with_memory
    saved = service.remember("uno", "Decisión de registro", "Se usa tuteo en diálogos.", "decision")
    assert saved["status"] == "saved"
    assert saved["topic_key"]
    found = service.recall("uno", "tuteo")
    assert len(found["results"]) == 1
    assert found["results"][0]["type"] == "decision"


def test_remember_idempotent(service_with_memory):
    service = service_with_memory
    first = service.remember("uno", "Nota", "Contenido.", "other", "clave-1")
    second = service.remember("uno", "Nota", "Contenido.", "other", "clave-1")
    assert first["status"] == "saved"
    assert second["status"] == "unchanged"


def test_recall_isolated_by_book(service_with_memory):
    service = service_with_memory
    service.remember("uno", "Nota privada", "secreto del libro uno", "other")
    assert service.recall("dos", "secreto")["results"] == []


def test_session_summary_and_context(service_with_memory):
    service = service_with_memory
    empty = service.session_context("uno")
    assert empty["latest_summary"] is None
    saved = service.session_summary(
        "uno",
        goal="Auditar Cap-04",
        accomplished="Diff aplicado",
        discoveries="Muletilla nueva",
        next_steps="Seguir con Cap-05",
        files="01-Manuscrito/Cap-04.md",
    )
    assert saved["status"] == "saved"
    context = service.session_context("uno")
    assert context["latest_summary"]["topic_key"] == saved["topic_key"]
    assert "Cap-05" in context["latest_summary"]["content"]


def test_validation(service_with_memory):
    service = service_with_memory
    with pytest.raises(ValueError):
        service.remember("uno", "", "algo", "other")
    with pytest.raises(ValueError):
        service.remember("uno", "t", "c", "invento")
    with pytest.raises(ValueError):
        service.recall("uno", "   ")
    with pytest.raises(ValueError):
        service.remember("tres", "t", "c", "other")


def test_memory_disabled(library):
    service, _, _ = library
    with pytest.raises(ValueError, match="desactivada"):
        service.recall("uno", "algo")
