"""Minimal YAML-subset frontmatter (ADR-0008). Full YAML is rejected."""

from __future__ import annotations

import re
from typing import Any

from mito.memory.store import SECRET_RE

BIDI_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")


class SkillParseError(ValueError):
    pass


def _scalar(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
    if text in {"true", "True"}:
        return True
    if text in {"false", "False"}:
        return False
    return text.strip("'\"")


def parse_skill_md(text: str) -> tuple[dict[str, Any], str]:
    if BIDI_RE.search(text):
        raise SkillParseError("unicode smuggling (bidi overrides) is refused")
    if "!!" in text or "\n&" in text or text.startswith("&"):
        raise SkillParseError("full YAML (tags/anchors) is refused; use the mito subset")
    if not text.startswith("---"):
        raise SkillParseError("SKILL.md must start with --- frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise SkillParseError("unclosed frontmatter")
    block = text[3:end].strip("\n")
    body = text[end + 4 :].lstrip("\n")
    data: dict[str, Any] = {}
    mito: dict[str, Any] = {}
    in_mito = False
    for line in block.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith("mito:"):
            in_mito = True
            rest = line[5:].strip()
            if rest:
                raise SkillParseError("mito: must be a nested map, not a scalar")
            continue
        if in_mito and line.startswith("  "):
            if ":" not in line:
                raise SkillParseError(f"bad mito line: {line!r}")
            key, val = line.strip().split(":", 1)
            mito[key.strip()] = _scalar(val)
            continue
        if in_mito and not line.startswith(" "):
            in_mito = False
        if ":" not in line or line.startswith(" "):
            raise SkillParseError(f"unsupported frontmatter line: {line!r}")
        key, val = line.split(":", 1)
        data[key.strip()] = _scalar(val)
    if mito:
        data["mito"] = mito
    if SECRET_RE.search(text):
        raise SkillParseError("secret-shaped string in SKILL.md")
    return data, body
