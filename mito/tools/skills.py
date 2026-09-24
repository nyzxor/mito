"""T0 skills.list/read and T3 skill.propose. Advertised tools == granted tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mito.skills_rt.frontmatter import SkillParseError
from mito.skills_rt.loader import (
    SkillError,
    SkillRegistry,
    discover,
    propose,
)
from mito.tools.base import Tool, ToolError
from mito.tools.seal import Dispatch, require_dispatch


class SkillsListParams(BaseModel):
    pass


class SkillsReadParams(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class SkillProposeParams(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    body: str = Field(..., min_length=20, max_length=20_000)


def skills_tools(
    roots: list[Path],
    workspace_skills: Path,
    available_tools: frozenset[str],
) -> list[Tool]:
    def _loaded() -> SkillRegistry:
        try:
            return discover(roots, available_tools=available_tools)
        except (SkillError, SkillParseError) as exc:
            raise ToolError(f"skill registry failed to load: {exc}") from exc

    async def list_h(_p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        skills = _loaded().all()
        return {
            "skills": [
                {
                    "name": s.name,
                    "description": s.description,
                    "risk_tier": s.risk_tier,
                    "tools": list(s.granted_tools()),
                }
                for s in skills
            ]
        }

    async def read_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, SkillsReadParams):
            raise ToolError("internal: skills.read params mismatch")
        skill = _loaded().get(p.name)
        if skill is None:
            raise ToolError(
                f"unknown skill {p.name!r}. Call skills.list or tool_search, then retry."
            )
        return {
            "name": skill.name,
            "description": skill.description,
            "granted_tools": list(skill.granted_tools()),
            "risk_tier": skill.risk_tier,
            "body": skill.body,
            "note": "granted_tools are exactly mito.tools_required (advertised == granted)",
        }

    async def propose_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, SkillProposeParams):
            raise ToolError("internal: skill.propose params mismatch")
        try:
            dest = propose(
                workspace_skills, p.name, p.body, available_tools=available_tools
            )
        except (SkillError, SkillParseError) as exc:
            raise ToolError(
                f"skill rejected by lint: {exc}. Fix frontmatter/tools and propose again."
            ) from exc
        return {
            "quarantined": str(dest),
            "name": p.name,
            "note": "not loadable until the operator runs mito skills quarantine approve",
        }

    return [
        Tool(
            "skills.list",
            "List loadable skills (quarantine excluded). Use to pick a procedure.",
            SkillsListParams,
            list_h,
            "T0",
        ),
        Tool(
            "skills.read",
            "Read a skill body and the exact tools it grants. Advertised == granted.",
            SkillsReadParams,
            read_h,
            "T0",
        ),
        Tool(
            "skill.propose",
            "Write a SKILL.md into skills/.quarantine. Operator must approve before it loads.",
            SkillProposeParams,
            propose_h,
            "T3",
            idempotent=False,
            core=False,
        ),
    ]
