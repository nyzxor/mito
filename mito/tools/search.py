"""T0 tool_search — find non-core tools and skills by name or description."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mito.tools.base import Tool
from mito.tools.seal import Dispatch, require_dispatch


class ToolSearchParams(BaseModel):
    query: str = Field(..., min_length=1, max_length=80)


def make_tool_search(tools: list[Tool], skill_lines: list[str]) -> Tool:
    catalog = [(t.name, t.risk_tier, t.description) for t in tools]
    extra = [("skill:" + line.split(":", 1)[0], "skill", line) for line in skill_lines]

    async def search_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, ToolSearchParams):
            return {"hits": []}
        q = p.query.lower()
        hits: list[dict[str, str]] = []
        for name, kind, desc in [*catalog, *extra]:
            blob = f"{name} {kind} {desc}".lower()
            if q in blob:
                hits.append({"name": name, "kind": kind, "description": desc[:200]})
            if len(hits) >= 12:
                break
        return {"hits": hits}

    return Tool(
        "tool_search",
        "Find a tool or skill by name or description. Use when a needed tool is not in core.",
        ToolSearchParams,
        search_h,
        "T0",
    )
