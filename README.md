# editorial-mcp — aplicación compartida

Recuperación económica para el harness existente. No escribe manuscritos ni
genera prosa. La búsqueda textual y el linter no usan modelos; la búsqueda
semántica utiliza embeddings locales, sin llamadas a un LLM ni a una API.
El protocolo es MCP estándar sobre **stdio**, con el SDK oficial de Python.

Esta app tiene su propio repositorio Git y no vive dentro de ningún libro.
Los vaults son clientes y fuentes de datos; conservan prosa, biblia, revisión y
reglas editoriales. La app conserva servidor, registro y cachés regenerables.
Su historial inicial se extrajo de `terrario/harness/editorial-mcp/` sin importar
el manuscrito ni el resto del historial del libro.

## Qué queda dónde

| Capa | Ubicación | Fuente de verdad |
|---|---|---|
| Texto del autor | En cada vault: `01-Manuscrito/` | Markdown + Git del libro |
| Canon documentado | En cada vault: `00-Biblia/` | Fichas revisadas por el autor |
| Decisiones e issues | En cada vault: `02-Revision/` | Notas editoriales, no prosa canónica |
| Agentes/skills existentes | En cada vault: `.claude/`, más el plugin `taller-editorial` | Contrato editorial de cada libro |
| Adaptadores OpenCode V2 | En cada vault: `.opencode/agents/` | Generados desde sus agentes de `.claude/` |
| Biblioteca | `books.json` | IDs y rutas explícitas |
| Consulta MCP | En cada vault: `.mcp.json` / `opencode.json` | Cliente del servidor compartido |
| Índice derivado | `.editorial-cache/index.sqlite3` | Regenerable, ignorado por Git |

OpenCode descubre las skills de `.claude/skills/` por compatibilidad estándar;
no se duplican bajo `.opencode/skills/`. Los alias `sonnet`/`haiku` de Claude no
son IDs válidos de proveedor OpenCode: los adaptadores heredan el modelo de la
sesión. Si quieres fijarlos, configura un modelo disponible por agente en tu
configuración de usuario. No edites los adaptadores a mano.

## Iniciar desde la raíz de esta app

Requiere `uv` y Python 3.13 (fijado en `.python-version`; el código admite >=3.11).
La primera instalación descarga dependencias; las consultas no envían tus libros
a ningún proveedor externo ni usan APIs. `uv.lock` fija las versiones.

La autenticación de GitHub es independiente del MCP: un token vencido puede
impedir el `git push`, pero no afecta las consultas locales del libro.

```sh
uv sync --locked --extra semantic
uv run --locked --extra semantic editorial-mcp --library books.json --semantic prepare-model
uv run --locked --extra semantic editorial-mcp --library books.json --semantic index
uv run --locked --extra semantic editorial-mcp --library books.json search "dos mil" --mode literal --book-id terrario
uv run --locked --extra semantic editorial-mcp --library books.json context 4 --book-id terrario
```

En cada vault, `opencode.json` registra `editorial` en `mcp.servers`; comprueba con
`opencode mcp list` o `/mcps`. Puede requerir reconectar o abrir una sesión nueva.
Claude Code usa `.mcp.json`; revisa y aprueba el servidor desde `/mcp`.
No se desactiva ninguna protección ni se aprueban servidores automáticamente.

Para un vault hermano de esta app, el comando de conexión es:

```sh
uv run --locked --extra semantic --project ../editorial-mcp editorial-mcp --library ../editorial-mcp/books.json --semantic serve
```

No es necesario copiar el servidor al libro. Mantén la disposición de carpetas
hermanas o ajusta las rutas de su configuración. Clonar el libro no instala esta
app automáticamente: son dos proyectos independientes.

## Registrar los otros libros

Cada libro tiene un ID estable independiente de su título y carpeta. El ID debe
usar minúsculas, números, guion o guion bajo, hasta 64 caracteres, sin espacios.
Todas las herramientas que leen contenido exigen `book`; no hay búsqueda global
implícita. Una misma entidad en dos libros no mezcla sus resultados.

`books.json` empieza solo con `terrario`; agrega las rutas **reales** de los otros
vaults. Las rutas relativas se resuelven desde el archivo del registro, no desde
la sesión. Ejemplo ilustrativo (ajusta las carpetas antes de usarlo):

```json
{
  "books": {
    "terrario": "../terrario",
    "la-nico": "../la-nico",
    "descenso": "../descenso-al-olvido",
    "convergencia": "../la-convergencia"
  }
}
```

El servidor valida que cada ruta tenga `01-Manuscrito/`. No registra otras
carpetas del disco automáticamente. Si modificas el registro, reinicia/reconecta
el servidor. Puedes mantener un registro privado fuera del repo y pasar
`--library /ruta/biblioteca.json`. Usa `--database /ruta/cache.sqlite3` para colocar
el índice donde prefieras; por defecto vive en `.editorial-cache/` junto al
registro, independientemente del primer libro y del directorio del cliente.
Sin `--library`, los comandos ad hoc usan `.editorial-cache/` en su directorio
de ejecución. Los pesos se guardan junto a la BD, salvo `--model-cache` explícito.

No es necesario copiar el servidor a cada libro: desde otro proyecto, apunta su
configuración MCP a este mismo módulo y al mismo registro usando rutas absolutas
locales. Cada proceso puede atender todos los IDs. No modifiques la configuración
global del cliente salvo que quieras cargar este MCP en todos los proyectos.

También puedes registrar raíces directamente:

```sh
uv run --locked --extra semantic editorial-mcp --book terrario=../terrario --book la-nico=/ruta/al/vault --semantic serve
```

La CLI elige el primer libro si omites `--book-id`; el MCP exige siempre un ID.
No reutilices un ID para otro libro distinto. Si cambias la ruta de un mismo ID,
se actualizan sus fuentes por ruta y hash. Quitar un libro del registro retira sus
datos del índice en la próxima sincronización; nunca borra sus archivos.

## Herramientas MCP

| Herramienta | Uso |
|---|---|
| `index_status()` | IDs, cantidades y frescura, sin texto ni sincronización forzada |
| `chapter_context(book, chapter)` | Metadatos, escenas, candidatos de biblia e issues activos |
| `search(book, query, ...)` | Extractos con ruta, líneas, tipo de fuente y hash |
| `read_scene(book, scene_id, expected_hash, ...)` | Texto original acotado, con paginación y vecinos opcionales |
| `entity_evidence(book, entity, ...)` | Menciones literales, no hechos ni cronología inferida |
| `lint_chapter(book, chapter, ...)` | Detector mecánico parcial sin LLM |

Los nombres visibles dependen del cliente: OpenCode agrupa bajo `editorial`
(normalmente en Code Mode); Claude usa `mcp__editorial__<herramienta>`.

### Búsqueda y cobertura

- `mode=terms`: AND entre palabras, FTS5 tolera mayúsculas y tildes. **No es
  búsqueda semántica**, stemming ni expansión de sinónimos.
- `mode=literal`: cadena exacta, sin distinguir mayúsculas; conserva diferencias
  de tildes y signos. Busca sobre el documento para no perder frases entre
  fragmentos. Devuelve fragmentos coincidentes, no cada ocurrencia individual.
- `mode=semantic`: similitud coseno sobre embeddings multilingües locales.
- `mode=hybrid`: combina ranking lexical (OR de palabras de contenido) y semántico
  mediante Reciprocal Rank Fusion, `k=60`. No necesita que la consulta use las
  mismas palabras del texto. Los scores no son probabilidades ni hechos verificados.
- Semántica/híbrida exigen `--semantic` y el extra de dependencias `semantic`.
  Las configuraciones de ambos clientes ya lo habilitan. Literal/terms siguen
  disponibles aunque el modelo falte; no hay fallback semántico silencioso.
- `kind`: `manuscrito` por defecto; `biblia`, `revision` o `all` explícitos.
  Un issue, una hipótesis o una ficha no se convierten en un hecho de la prosa.
- `include_resolved=false` por defecto. Actívalo para decisiones históricas
  específicas; no cargues todo el archivo de issues.
- `entity` filtra personajes/lugares del **frontmatter del capítulo**. No demuestra
  que ese personaje aparezca en cada escena. Busca alias solo si están documentados.
- `max_chars` es un presupuesto de texto, **no tokens ni tamaño total del JSON**.
  Por defecto se devuelven hasta 5 extractos de 400 caracteres. Referencias y
  resultados están acotados; `has_more`, `next_offset` y `*_omitted` señalan recortes.
- `candidate_limit` (10–200, por defecto 100) acota el ranking semántico paginable.
  `ranked_candidates` no cuenta coincidencias reales; `total_matching_chunks=null`
  en estos modos. `candidate_pool_truncated` indica que el ranking omite candidatos.
  Los filtros de libro/tipo/capítulo/entidad se aplican antes de puntuar vectores.
- Cero coincidencias no demuestra que una escena falte. Para continuidad global,
  arcos o ausencia narrativa, combina consultas y recorridos exhaustivos con
  lectura directa cuando sea necesario. Prosa/ritmo requieren capítulo completo.

### Fidelidad y actualización

Indexa solo Markdown de `01-Manuscrito/`, `00-Biblia/` y `02-Revision/`; no logs,
memorias, credenciales, PDFs ni compilados. `CLAUDE.md` se consulta localmente para
las convenciones del linter, pero no entra en la búsqueda de evidencia.

La segmentación usa separadores explícitos de escenas (`***`, `---`, `___`, con
variantes espaciadas), encabezados de biblia y bloques de checkboxes de revisión.
No adivina escenas sin marcar ni hechos del texto. Las escenas largas se subdividen
por párrafos; `read_scene` recupera la escena completa paginada, no solo un chunk.

Antes de cada consulta de contenido se escanean hashes localmente. Solo se
reindexan documentos modificados/nuevos, y se retiran los borrados. Un error de
YAML aborta la transacción, sin dejar un índice parcialmente actualizado. Leer
archivos para computar hashes no los incorpora al contexto del modelo.

Cada resultado lleva SHA-256 de su fuente. `read_scene` comprueba ese hash contra
el archivo actual y rechaza versiones anteriores. Los IDs son posiciones de
escena en **esa versión**; no sobreviven como identidad estable a inserciones o
reordenamientos. Si el archivo cambia, busca otra vez. Para aplicar un diff sigue
verificando el texto actual y pidiendo aprobación: el MCP no tiene herramientas
de escritura y no reemplaza el control editorial.

## Linter y agentes existentes

El flujo `auditar-capitulo` reutiliza `lint_chapter` para los patrones cubiertos:
cirílicos, doble cierre, guion/raya inicial, comillas/puntos suspensivos y voseo
cuando las convenciones están explícitas. `prose-linter` completa por Grep las
reglas aún no implementadas, incluyendo muletillas y errores recurrentes.
El copyeditor verifica falsos positivos y sigue leyendo todo el capítulo.

El registro regional no se infiere; si no hay prohibición explícita de voseo,
el detector no impone tuteo. No corrige nada ni evalúa calidad literaria.

```sh
uv run --locked --extra semantic editorial-mcp --library books.json lint 4 --book-id terrario --format markdown
uv run --locked --extra semantic python scripts/build-opencode-agents.py ../terrario
uv run --locked --extra semantic python scripts/build-opencode-agents.py ../terrario --check
```

## Verificación

```sh
uv run --locked --extra semantic pytest
uv run --locked --extra semantic ruff check .
uv run --locked --extra semantic ruff format --check .
```

Las pruebas usan vaults temporales e incluyen aislamiento por ID, hashes,
actualización/borrado, preservación del texto, paginación, presupuestos, YAML
inválido, rutas exteriores, frases entre fragmentos y handshake MCP real.

## Embeddings locales y límites

Usa `minishlab/potion-multilingual-128M`, derivado de BGE-M3: embeddings estáticos
de 256 dimensiones, español incluido, ejecutados por Model2Vec 0.9.0 en CPU.
El checkpoint está fijado por revisión, no por una rama mutable. Los pesos ocupan
aproximadamente 512 MB y solo se descargan en `prepare-model`; después la carga
usa exclusivamente archivos locales. No se requiere token de Hugging Face.
Solo se descargan Safetensors/tokenizador/configuración, no código remoto.

Es una elección ligera compatible con esta máquina (Mac Intel/Python 3.13), sin
PyTorch ni servidor externo. Al ser embeddings estáticos, **no modela de forma
fiable el orden, las negaciones, la causalidad ni cambios mínimos de cifras**.
Para continuidad, verifica siempre el texto exacto. No existe expansión explícita
de alias ni un reranker neuronal: la combinación de rankings es RRF.

Los vectores se cachean por hash del texto + identidad del modelo/revisión.
Al editar un capítulo, se retiran sus asociaciones anteriores; los fragmentos
idénticos reutilizan su vector. Un cambio de modelo usa otra identidad y no mezcla
dimensiones. La caché retiene vectores sin asociaciones para evitar recalcular al
restaurar una versión; son datos privados derivados, no resultados consultables.
Los pesos y vectores viven bajo `.editorial-cache/`, fuera de Git.

```sh
uv run --locked --extra semantic editorial-mcp --library books.json --semantic search "qué amenaza evita tocar a los muertos" --mode hybrid --book-id terrario
```

`--model-cache /ruta/cache` permite compartir el checkpoint con índices de prueba.
Pruebas técnicas sin descarga automática:

```sh
uv run --locked --extra semantic pytest
EDITORIAL_MODEL_CACHE="$PWD/.editorial-cache/models" uv run --locked --extra semantic pytest tests/test_real_semantic.py
```

## Cómo se consulta `02-Revision/`

Los Markdown de revisión siguen siendo la fuente de verdad del libro. `documents`
guarda archivo/metadatos/hash y `chunks` conserva encabezado, líneas y `resolved`
extraído de checkboxes. FTS5 y los vectores permiten recuperar solo lo pertinente.
`chapter_context` devuelve pendientes del capítulo, no todo el archivo. Los
resueltos se consultan explícitamente con `include_resolved=true`.

Comprobar hashes y leer archivos en el indexador es I/O local, no tokens del
modelo. Solo el contenido que el harness entrega al modelo consume su contexto.
Esta BD no es un gestor independiente de tickets ni almacena transiciones
permanentes por issue: el historial durable sigue en Git del libro. Los IDs de
fragmento pertenecen a una versión, no son IDs permanentes de issues.

## Validación editorial pendiente

Hechos/cronologías estructurados y reranking neuronal no están implementados.
Antes de extenderlos, medir consultas editoriales reales: costo y
tokens totales (incluidos subagentes y caché), latencia, evidencia recuperada y
hallazgos perdidos frente al flujo anterior. Las pruebas técnicas no demuestran
todavía ahorro de tokens ni igual calidad de auditoría. Los casos históricos
corregidos se evalúan sobre su revisión Git correspondiente.
