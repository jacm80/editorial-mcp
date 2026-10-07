import argparse
import json
import re
import sys
from pathlib import Path

from .index import BookIndex
from .service import EditorialService


def main() -> None:
    parser = argparse.ArgumentParser(description="MCP editorial local de solo lectura")
    parser.add_argument(
        "--book",
        action="append",
        metavar="ID=RUTA",
        help="Repetible para varios libros; por defecto libro=directorio actual",
    )
    parser.add_argument(
        "--library", type=Path, help="Registro JSON con books: {id: ruta}, relativo al registro"
    )
    parser.add_argument("--database", type=Path, help="Base derivada SQLite")
    parser.add_argument(
        "--semantic", action="store_true", help="Habilitar embeddings locales y RRF"
    )
    parser.add_argument(
        "--model-cache", type=Path, help="Caché local del checkpoint, separable del índice"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="MCP sobre stdio; stdout reservado al protocolo")
    commands.add_parser("index", help="Actualizar índice incremental")
    commands.add_parser("status", help="Comprobar frescura sin sincronizar")
    commands.add_parser("prepare-model", help="Descargar el checkpoint multilingüe fijado")
    query = commands.add_parser("search")
    query.add_argument("query")
    query.add_argument("--book-id")
    query.add_argument("--kind", default="manuscrito")
    query.add_argument(
        "--mode", choices=["terms", "literal", "semantic", "hybrid"], default="terms"
    )
    query.add_argument("--chapter")
    query.add_argument("--limit", type=int, default=5)
    query.add_argument("--offset", type=int, default=0)
    context = commands.add_parser("context")
    context.add_argument("chapter")
    context.add_argument("--book-id")
    lint = commands.add_parser("lint")
    lint.add_argument("chapter")
    lint.add_argument("--book-id")
    lint.add_argument("--limit", type=int, default=30)
    lint.add_argument("--offset", type=int, default=0)
    lint.add_argument("--format", choices=["json", "markdown"], default="json")
    args = parser.parse_args()
    try:
        books = {}
        if args.library:
            library = args.library.expanduser().resolve()
            registry = json.loads(library.read_text(encoding="utf-8"))
            if not isinstance(registry, dict) or not isinstance(registry.get("books"), dict):
                raise ValueError("El registro necesita un objeto books: {id: ruta}")
            for name, path in registry["books"].items():
                if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
                    raise ValueError(f"ID inválido: {name}")
                if not isinstance(path, str) or not path.strip():
                    raise ValueError(f"Ruta inválida para {name}")
                root = Path(path).expanduser()
                books[name] = (library.parent / root).resolve()
        defaults = [] if args.library else [f"libro={Path.cwd()}"]
        for entry in args.book or defaults:
            name, separator, path = entry.partition("=")
            if not separator or not path or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
                raise ValueError("--book debe ser id-en-minusculas=ruta")
            if name in books:
                raise ValueError(f"ID de libro duplicado: {name}")
            books[name] = Path(path).expanduser().resolve()
        if not books:
            raise ValueError("El registro debe contener al menos un libro")
        database = args.database or next(iter(books.values())) / ".editorial-cache/index.sqlite3"
        index = BookIndex(books, database)
        from .semantic import LocalEmbedder

        embedder = (
            LocalEmbedder(args.model_cache or index.database.parent / "models")
            if args.semantic
            else None
        )
        service = EditorialService(index, embedder)
        book = getattr(args, "book_id", None) or next(iter(books))
        if args.command == "serve":
            from .server import create_server

            index.sync()
            create_server(service).run(transport="stdio")
            return
        if args.command == "prepare-model":
            if not embedder:
                raise ValueError("prepare-model requiere --semantic")
            embedder.prepare()
            result = {
                "model_prepared": True,
                "model": embedder.identity,
                "notice": "Modelo descargado; consultas e inferencia locales",
            }
        elif args.command == "index":
            changes = index.sync()
            if service.semantic:
                for registered in index.books:
                    service.semantic.rank(service.rows(registered, include_resolved=True), "índice")
            result = {**changes, **service.status()}
        elif args.command == "status":
            result = service.status()
        elif args.command == "search":
            result = service.search(
                book,
                args.query,
                kind=args.kind,
                mode=args.mode,
                chapter=args.chapter,
                limit=args.limit,
                offset=args.offset,
            )
        elif args.command == "context":
            result = service.chapter_context(book, args.chapter)
        else:
            result = service.lint_chapter(book, args.chapter, args.limit, args.offset)
            if args.format == "markdown":
                print(f"# Hallazgos mecánicos — {result['path']}\n")
                print(f"Fuente: `{result['source_hash']}`\n\n{result['notice']}\n")
                for rule, count in result["counts"].items():
                    print(f"- {rule}: {count}")
                for finding in result["findings"]:
                    print(
                        f"\n- {finding['rule']}, línea {finding['line']}, "
                        f"columna {finding['column']}: {finding['excerpt']}"
                    )
                if result["has_more"]:
                    print(f"\n**Reporte parcial:** continuar con --offset {result['next_offset']}.")
                return
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    except (ValueError, OSError) as error:
        print(f"editorial-mcp: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
