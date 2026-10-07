from pathlib import Path

import pytest

from editorial_mcp.index import BookIndex
from editorial_mcp.service import EditorialService


def write(root: Path, path: str, text: str) -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


@pytest.fixture
def library(tmp_path):
    one, two = tmp_path / "uno", tmp_path / "dos"
    chapter = """---
libro: Uno
capitulo: 4
titulo: Oscuridad
personajes: ["[[Martha]]", "[[Gómez]]"]
lugares: ["[[Iglesia]]"]
estado: cerrado
---
# Oscuridad

Martha encontró dos mil personas. Gómez no se arrodilló.

***

La semilla no debe madurar. Nadie comprendía a Martha.
"""
    write(one, "01-Manuscrito/Cap-04.md", chapter)
    write(
        one,
        "01-Manuscrito/Cap-04-Interludio.md",
        """---
capitulo: "4.5"
titulo: Interludio
---
# Interludio
Martha vio el cuchillo.
""",
    )
    write(one, "00-Biblia/Personajes/Martha.md", "# Martha\n\n## Arco\n\nFinal desconocido.\n")
    write(one, "00-Biblia/Reglas/Semilla.md", "# Semilla\n\nNunca debe madurar.\n")
    write(
        one,
        "02-Revision/Issues.md",
        """# Issues

## Abiertos
- [ ] Falta una explicación. [[Cap-04]]
  Continuación de la explicación.
- [x] Ya se corrigió. [[Cap-04]]
""",
    )
    write(
        one, "02-Revision/Calidad.md", "# Calidad\n\n### Cap-04 — Oscuridad\n- [ ] Ritmo lento.\n"
    )
    write(
        one,
        "CLAUDE.md",
        "Nunca voseo. Comillas angulares. Puntos suspensivos como un solo carácter: …\n",
    )
    write(
        two,
        "01-Manuscrito/Cap-04.md",
        "---\ncapitulo: 4\n---\n# Otro libro\nMartha nació en Chile.\n",
    )
    index = BookIndex({"uno": one, "dos": two}, tmp_path / "cache/index.sqlite3")
    index.sync()
    return EditorialService(index), one, two
