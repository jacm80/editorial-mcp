"""Genera adaptadores V2 sin duplicar el mantenimiento de prompts editoriales."""

import argparse
from pathlib import Path

import yaml


def build(root: Path, check: bool = False) -> list[str]:
    source = root / ".claude/agents"
    output = root / ".opencode/agents"
    stale = []
    for path in sorted(source.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        _, header, body = raw.split("---", 2)
        original = yaml.safe_load(header)
        metadata = {
            "description": original["description"],
            "mode": "subagent",
            # Claude usa alias sonnet/haiku; OpenCode hereda el modelo de la sesión.
            # No inventar IDs de proveedor ni obligar a contratar otro proveedor.
            "permissions": [
                {"action": "edit", "resource": "**/01-Manuscrito/**", "effect": "deny"},
                {"action": "edit", "resource": "**/00-Biblia/**", "effect": "deny"},
                {"action": "edit", "resource": "**/02-Revision/**", "effect": "deny"},
            ],
        }
        if "Bash" not in original.get("tools", []):
            metadata["permissions"].insert(
                0, {"action": "shell", "resource": "*", "effect": "deny"}
            )
        generated = (
            "---\n"
            + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False)
            + "---\n\n<!-- Generado desde .claude/agents/"
            + path.name
            + "; no editar a mano. -->\n"
            + body
        )
        target = output / path.name
        if not target.exists() or target.read_text(encoding="utf-8") != generated:
            stale.append(path.name)
            if not check:
                output.mkdir(parents=True, exist_ok=True)
                target.write_text(generated, encoding="utf-8")
    return stale


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path, nargs="?", default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changes = build(args.root.resolve(), args.check)
    print(
        "Adaptadores "
        + ("desactualizados" if args.check else "generados")
        + ": "
        + (", ".join(changes) or "ninguno")
    )
    raise SystemExit(1 if args.check and changes else 0)
