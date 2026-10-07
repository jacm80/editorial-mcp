# editorial-mcp

Aplicación independiente de los vaults de libros. Mantén el protocolo MCP por
stdio y las herramientas de consulta de solo lectura respecto de las fuentes.
Los libros se registran por ID en `books.json`; no copies manuscritos al código.
La BD, los vectores y los pesos son datos privados regenerables en
`.editorial-cache/`, junto al registro, y nunca se incluyen en Git.

Pruebas: `uv run --locked --extra semantic pytest`.
Modelo real opcional: define `EDITORIAL_MODEL_CACHE` con la ruta del checkpoint
ya preparado; las pruebas y consultas no deben descargar modelos.
Formato: `uv run --locked --extra semantic ruff check .` y
`uv run --locked --extra semantic ruff format --check .`.

Los Markdown y sus reglas editoriales pertenecen a cada libro, no a esta app.
No edites un vault como parte de pruebas o indexación. La búsqueda semántica es
aproximada: no infiere hechos ni prueba ausencia narrativa.
