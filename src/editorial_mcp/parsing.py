"""Segmentación determinista, sin inferir hechos ni modificar la prosa."""

import re
from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class Unit:
    scene: int
    part: int
    heading: str
    line_start: int
    line_end: int
    char_start: int
    char_end: int
    text: str
    resolved: bool | None = None


def frontmatter(text: str) -> tuple[dict, int]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for end in range(1, len(lines)):
        if lines[end].strip() == "---":
            try:
                metadata = yaml.safe_load("".join(lines[1:end])) or {}
            except yaml.YAMLError as error:
                raise ValueError("Frontmatter YAML inválido") from error
            if not isinstance(metadata, dict):
                raise ValueError("El frontmatter debe ser un mapping YAML")
            return metadata, end + 1
    raise ValueError("Frontmatter YAML sin cierre")


def links(value: object) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(v).removeprefix("[[").removesuffix("]]") for v in values]


def segments(text: str, kind: str, max_chars: int = 3500) -> list[Unit]:
    """Escenas explícitas; secciones de biblia; un bloque por issue.

    No adivina cambios de escena no marcados. Los fragmentos largos se dividen
    por párrafos; una línea larga permanece intacta.
    """
    _, body_start = frontmatter(text)
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    ranges: list[tuple[int, int, str, bool | None]] = []
    start = body_start
    heading = ""
    resolved = None
    for i in range(body_start, len(lines)):
        line = lines[i].strip()
        title = re.match(r"^#{1,6}\s+(.+)", line)
        checkbox = re.match(r"^-\s+\[([ xX])\]\s+", line)
        separator = bool(re.fullmatch(r"(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,}", line))
        boundary = (kind == "manuscrito" and separator) or (
            kind != "manuscrito" and (title or (kind == "revision" and checkbox))
        )
        if boundary:
            if any(s.strip() for s in lines[start:i]):
                ranges.append((start, i, heading, resolved))
            start = i + 1 if kind == "manuscrito" else i
            resolved = bool(checkbox and checkbox[1].lower() == "x") if checkbox else None
        if title:
            heading = title[1]
    if any(s.strip() for s in lines[start:]):
        ranges.append((start, len(lines), heading, resolved))

    units = []
    for scene, (start, end, heading, resolved) in enumerate(ranges, 1):
        while start < end and not lines[start].strip():
            start += 1
        while end > start and not lines[end - 1].strip():
            end -= 1
        part_start, size, part = start, 0, 1
        for i in range(start, end):
            if size >= max_chars and not lines[i].strip():
                units.append(
                    Unit(
                        scene,
                        part,
                        heading,
                        part_start + 1,
                        i,
                        offsets[part_start],
                        offsets[i],
                        text[offsets[part_start] : offsets[i]],
                        resolved,
                    )
                )
                part_start, size, part = i, 0, part + 1
            size += len(lines[i])
        if part_start < end:
            units.append(
                Unit(
                    scene,
                    part,
                    heading,
                    part_start + 1,
                    end,
                    offsets[part_start],
                    offsets[end],
                    text[offsets[part_start] : offsets[end]],
                    resolved,
                )
            )
    return units
