"""T0 memory.search/read and T3 memory.write. Provenance comes from the ticket flags."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mito.gateway.ir import Provenance
from mito.memory.store import MemoryError, MemoryStore
from mito.tools.base import Tool, ToolError
from mito.tools.seal import Dispatch, require_dispatch


class MemorySearchParams(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(8, ge=1, le=20)


class MemoryReadParams(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    private: bool = False


class MemoryWriteParams(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    body: str = Field(..., min_length=1, max_length=8000)
    description: str = Field("", max_length=400)


def memory_tools(store: MemoryStore) -> list[Tool]:
    async def search_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, MemorySearchParams):
            raise ToolError("internal: memory.search params mismatch")
        try:
            hits = store.search(p.query, limit=p.limit)
        except MemoryError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "hits": [
                {"name": f.name, "description": f.description, "trust_lane": f.trust_lane}
                for f in hits
            ]
        }

    async def read_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, MemoryReadParams):
            raise ToolError("internal: memory.read params mismatch")
        fact = store.get(p.name, include_quarantine=False)
        if fact is None:
            raise ToolError(
                f"unknown or quarantined memory {p.name!r}. "
                "Search again or ask the operator to confirm it."
            )
        return fact.to_dict()

    async def write_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, MemoryWriteParams):
            raise ToolError("internal: memory.write params mismatch")
        try:
            fact = store.write(
                p.name,
                p.body,
                description=p.description,
                source="agent",
                run_id=d.ticket.session,
                flags=list(d.ticket.flags_after),
            )
        except MemoryError as exc:
            raise ToolError(str(exc)) from exc
        return {
            "stored": fact.name,
            "trust_lane": fact.trust_lane,
            "quarantined": fact.trust_lane == "quarantine",
        }

    return [
        Tool(
            "memory.search",
            "Search remembered facts (FTS5/BM25). Quarantine is excluded. Use before guessing.",
            MemorySearchParams,
            search_h,
            "T0",
        ),
        Tool(
            "memory.read",
            "Read one remembered fact by name. Set private=true for private lanes (sets flag B).",
            MemoryReadParams,
            read_h,
            "T0",
        ),
        Tool(
            "memory.write",
            "Store one fact. UNTRUSTED-in-context writes go to quarantine. Never store secrets.",
            MemoryWriteParams,
            write_h,
            "T3",
            idempotent=False,
            taint_out=Provenance.TOOL_TRUSTED,
            core=False,
        ),
    ]
