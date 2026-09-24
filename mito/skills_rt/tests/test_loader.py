from __future__ import annotations

from pathlib import Path

import pytest
from mito.skills_rt.loader import (
    SkillError,
    approve,
    discover,
    list_quarantine,
    load_skill,
    propose,
    skill_roots_from_config,
)
from mito.tools.catalog import ADVERTISED

pytestmark = pytest.mark.unit

SKILL = """---
name: {name}
description: A tiny skill.
version: 0.1.0
mito:
  tools_required: [memory.search]
  risk_tier: T0
  origin: {origin}
---
## Procedure
Search memory.
"""


def _write(root: Path, name: str, *, origin: str = "workspace", extra: str = "") -> Path:
    dest = root / name / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(SKILL.format(name=name, origin=origin) + extra, encoding="utf-8")
    return dest


def test_load_and_granted_equals_advertised(tmp_path: Path) -> None:
    path = _write(tmp_path, "recall")
    skill = load_skill(path, available_tools=ADVERTISED)
    assert skill.granted_tools() == ("memory.search",)


def test_unknown_tool_fails_boot(tmp_path: Path) -> None:
    path = _write(tmp_path, "bad")
    text = path.read_text(encoding="utf-8").replace("memory.search", "captcha.solve")
    # captcha.solve is T5 and not in the advertised catalog
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SkillError, match="unknown tools"):
        load_skill(path, available_tools=ADVERTISED)


def test_undeclared_host_fails_lint(tmp_path: Path) -> None:
    path = _write(tmp_path, "net", extra="\nSee https://evil.example/x\n")
    with pytest.raises(SkillError, match="undeclared hosts"):
        load_skill(path, available_tools=ADVERTISED)


def test_quarantine_is_not_loadable(tmp_path: Path) -> None:
    workspace = tmp_path / "skills"
    dest = propose(
        workspace,
        "draft",
        SKILL.format(name="draft", origin="quarantine"),
        available_tools=ADVERTISED,
    )
    assert dest.is_file()
    loaded = discover([workspace], available_tools=ADVERTISED)
    assert loaded.get("draft") is None
    listed = list_quarantine(workspace, available_tools=ADVERTISED)
    assert listed and listed[0]["ok"] is True


def test_approve_promotes_and_writes_lock(tmp_path: Path) -> None:
    workspace = tmp_path / "skills"
    propose(
        workspace,
        "draft",
        SKILL.format(name="draft", origin="quarantine"),
        available_tools=ADVERTISED,
    )
    dest = approve(workspace, "draft", available_tools=ADVERTISED)
    assert dest.is_file()
    loaded = discover([workspace], available_tools=ADVERTISED)
    skill = loaded.get("draft")
    assert skill is not None and skill.granted_tools() == ("memory.search",)
    lock = (workspace / ".lock.json").read_text(encoding="utf-8")
    assert "draft" in lock and "mito-skill-lint/1" in lock


def test_precedence_workspace_over_bundled(tmp_path: Path) -> None:
    ws = tmp_path / "skills"
    bundled = tmp_path / "bundled"
    _write(ws, "recall", origin="workspace")
    other = bundled / "recall" / "SKILL.md"
    other.parent.mkdir(parents=True)
    other.write_text(
        SKILL.format(name="recall", origin="bundled").replace(
            "A tiny skill.", "bundled description"
        ),
        encoding="utf-8",
    )
    reg = discover([ws, bundled], available_tools=ADVERTISED)
    skill = reg.get("recall")
    assert skill is not None and skill.origin == "workspace"
    assert skill.description == "A tiny skill."


def test_duplicate_name_in_same_root_fails(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _write(root, "dup")
    _write(root / "nested", "dup")
    with pytest.raises(SkillError, match="duplicate"):
        discover([root], available_tools=ADVERTISED)


def test_mito_toml_skill_fields_match_loader_constants() -> None:
    import tomllib

    from handbrake.paths import repo_root
    from mito.skills_rt.loader import BODY_MAX_CHARS, CATALOG_MAX_CHARS, DEFAULT_ROOTS

    with (repo_root() / "config" / "mito.toml").open("rb") as fh:
        cfg = tomllib.load(fh)
    skills = cfg["skills"]
    assert tuple(skills["roots"]) == DEFAULT_ROOTS
    assert skills["catalog_max_tokens_per_skill"] * 4 == CATALOG_MAX_CHARS
    assert skills["body_max_tokens"] * 4 == BODY_MAX_CHARS
    memory = cfg["memory"]
    from mito.memory.store import DEFAULT_TTL_DAYS, INDEX_MAX_LINES

    assert memory["index_max_lines"] == INDEX_MAX_LINES
    assert memory["default_ttl_days"] == DEFAULT_TTL_DAYS


def test_bundled_recall_loads_from_repo() -> None:
    from handbrake.paths import repo_root

    roots = skill_roots_from_config(repo_root())
    reg = discover(roots, available_tools=ADVERTISED)
    skill = reg.get("recall")
    assert skill is not None
    assert set(skill.granted_tools()) <= ADVERTISED
    assert skill.origin == "bundled"
