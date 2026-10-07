import json
import subprocess
import sys


def run(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "editorial_mcp", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def test_registry_relative_paths_and_explicit_id(library, tmp_path):
    _, one, two = library
    registry = tmp_path / "books.json"
    registry.write_text(json.dumps({"books": {"uno": one.name, "dos": two.name}}))
    result = run("--library", str(registry), "search", "Chile", "--book-id", "dos", cwd=two)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["total_matching_chunks"] == 1


def test_empty_or_invalid_registry_fails(tmp_path):
    registry = tmp_path / "books.json"
    for contents in (
        {"books": {}},
        {"books": {"Libro Inválido": "."}},
        {"books": []},
        {"books": {"uno": 42}},
    ):
        registry.write_text(json.dumps(contents))
        result = run("--library", str(registry), "status", cwd=tmp_path)
        assert result.returncode == 1
        assert "editorial-mcp:" in result.stderr
        assert not result.stdout


def test_registry_owns_cache_independent_of_book_and_working_directory(library, tmp_path):
    _, one, two = library
    app = tmp_path / "app"
    app.mkdir()
    registry = app / "books.json"
    registry.write_text(json.dumps({"books": {"uno": "../uno", "dos": "../dos"}}))
    result = run("--library", str(registry), "index", cwd=two)
    assert result.returncode == 0, result.stderr
    assert (app / ".editorial-cache/index.sqlite3").is_file()
    assert not (one / ".editorial-cache").exists()
    assert not (two / ".editorial-cache").exists()


def test_duplicate_ids_fail(library):
    _, one, _ = library
    result = run("--book", f"uno={one}", "--book", f"uno={one}", "status", cwd=one)
    assert result.returncode == 1
    assert "duplicado" in result.stderr


def test_markdown_lint_and_unknown_chapter(library):
    _, one, _ = library
    result = run("--book", f"uno={one}", "lint", "4", "--format", "markdown", cwd=one)
    assert result.returncode == 0
    assert "# Hallazgos mecánicos" in result.stdout
    invalid = run("--book", f"uno={one}", "context", "999", cwd=one)
    assert invalid.returncode == 1
    assert "Capítulo no encontrado" in invalid.stderr


def test_adhoc_commands_use_explicit_execution_directory(library, tmp_path):
    _, one, _ = library
    sandbox = tmp_path / "execution"
    sandbox.mkdir()
    result = run("--book", f"uno={one}", "index", cwd=sandbox)
    assert result.returncode == 0, result.stderr
    assert (sandbox / ".editorial-cache/index.sqlite3").is_file()
    assert not (one / ".editorial-cache").exists()
