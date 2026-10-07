"""Detector mecánico: coincidencias para revisión, nunca correcciones."""

import re
import unicodedata

from .parsing import frontmatter

PATTERNS = {
    "doble_cierre": r"[?!]\.",
    "puntos_suspensivos": r"\.{3}",
    "comillas_rectas": r'"',
    "guion_dialogo": r"^\s*-\s+\S",
    "espacio_tras_raya": r"^\s*—\s+\S",
}
VOSEO = r"\b(?:vos|querés|sabés|podés|tenés|escuchame|decime|mirá|andá|sos)\b"


def lint_text(text: str, style: str) -> dict:
    _, start = frontmatter(text)
    rules = dict(PATTERNS)
    if re.search(r"nunca\s+(?:\*\*)?voseo", style, re.IGNORECASE):
        rules["voseo"] = VOSEO
    if "angulares" not in style.casefold():
        rules.pop("comillas_rectas")
    if not re.search(r"puntos suspensivos.*(?:carácter|caracter|…)", style, re.IGNORECASE):
        rules.pop("puntos_suspensivos")
    findings, counts = [], {name: 0 for name in (*rules, "cirilicos")}
    for number, line in enumerate(text.splitlines()[start:], start + 1):
        for rule, pattern in rules.items():
            for match in re.finditer(pattern, line, re.IGNORECASE):
                counts[rule] += 1
                findings.append(
                    {
                        "rule": rule,
                        "line": number,
                        "column": match.start() + 1,
                        "match": match[0],
                        "excerpt": line[max(0, match.start() - 80) : match.end() + 120],
                    }
                )
        for column, character in enumerate(line, 1):
            if "CYRILLIC" in unicodedata.name(character, ""):
                counts["cirilicos"] += 1
                findings.append(
                    {
                        "rule": "cirilicos",
                        "line": number,
                        "column": column,
                        "match": character,
                        "excerpt": line[max(0, column - 80) : column + 120],
                    }
                )
    return {
        "counts": counts,
        "findings": findings,
        "notice": "Detección mecánica parcial; requiere revisión. No corrige ni evalúa prosa.",
    }
