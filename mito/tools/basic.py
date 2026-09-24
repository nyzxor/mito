"""T0 tools available from Phase 1: time, status, result.more. Read-only, no effects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from mito.gateway.handbrake_client import HandbrakeClient
from mito.gateway.ir import Provenance
from mito.loop.compact import MoreStore
from mito.tools.base import Tool, ToolError
from mito.tools.seal import Dispatch, require_dispatch


class TimeParams(BaseModel):
    tz: str = Field("UTC", description="IANA time zone, e.g. 'America/Sao_Paulo'. Default UTC.")


class StatusParams(BaseModel):
    pass


class MoreParams(BaseModel):
    handle: str = Field(..., description="The `more` handle returned in a truncated tool result.")
    offset: int = Field(0, ge=0, description="Character offset to continue from (use next_offset).")


def basic_tools(handbrake: HandbrakeClient, more: MoreStore) -> list[Tool]:
    async def time_handler(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, TimeParams):
            raise ToolError("internal: time params mismatch")
        try:
            tz = UTC if p.tz.upper() == "UTC" else ZoneInfo(p.tz)
        except ZoneInfoNotFoundError as exc:
            raise ToolError(
                f"unknown time zone {p.tz!r}. Use an IANA name like 'Europe/Lisbon' or 'UTC'."
            ) from exc
        now = datetime.now(tz)
        return {"iso": now.isoformat(timespec="seconds"), "tz": p.tz, "weekday": now.strftime("%A")}

    async def status_handler(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        st = await handbrake.state()
        return {
            "autonomy": st.get("autonomy"),
            "metabolic_state": st.get("metabolic_state"),
            "pending_approvals": st.get("pending_approvals"),
            "spend_usd_day_cloud": round(float(st.get("spend_usd", {}).get("day_cloud", 0.0)), 4),
            "dev_mode": st.get("dev_mode"),
        }

    async def more_handler(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, MoreParams):
            raise ToolError("internal: result.more params mismatch")
        return more.page(p.handle, p.offset, 1500)

    return [
        Tool(
            name="time",
            description=(
                "Current date and time. Use when a task depends on today's date or a "
                "deadline. Not for scheduling (use schedule.propose)."
            ),
            params=TimeParams,
            handler=time_handler,
            risk_tier="T0",
            max_result_tokens=100,
        ),
        Tool(
            name="status",
            description=(
                "MITO's own state: autonomy level, metabolic state, pending approvals, "
                "today's cloud spend. Use before proposing anything costly or external."
            ),
            params=StatusParams,
            handler=status_handler,
            risk_tier="T0",
            max_result_tokens=200,
        ),
        Tool(
            name="result.more",
            description=(
                "Page through a truncated tool result using its `more` handle. "
                "Use instead of re-running an expensive tool."
            ),
            params=MoreParams,
            handler=more_handler,
            risk_tier="T0",
            max_result_tokens=1600,
            taint_out=Provenance.UNTRUSTED,
        ),
    ]
