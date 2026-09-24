from __future__ import annotations

import pytest
from mito.skills_rt.frontmatter import SkillParseError, parse_skill_md

pytestmark = pytest.mark.unit

VALID = """---
name: recall
description: Search memory.
version: 0.1.0
mito:
  tools_required: [memory.search, memory.read]
  risk_tier: T0
  origin: bundled
---
## Procedure
Call memory.search.
"""


def test_parses_mito_block_and_inline_list() -> None:
    meta, body = parse_skill_md(VALID)
    assert meta["name"] == "recall"
    assert meta["mito"]["tools_required"] == ["memory.search", "memory.read"]
    assert "Procedure" in body


def test_refuses_bidi_overrides() -> None:
    with pytest.raises(SkillParseError, match="bidi"):
        parse_skill_md("---\nname: x\n---\n\u202e hidden\n")


def test_refuses_yaml_tags() -> None:
    with pytest.raises(SkillParseError, match="YAML"):
        parse_skill_md("---\nname: !!python/object\n---\n")


def test_refuses_secret_shaped_text() -> None:
    with pytest.raises(SkillParseError, match="secret"):
        parse_skill_md("---\nname: x\n---\nsk-" + ("a" * 24) + "\n")
