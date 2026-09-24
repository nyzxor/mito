"""T3 schedule.propose — structured cron, validated by the Handbrake. No free-text cron daemon."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mito.gateway.handbrake_client import HandbrakeClient
from mito.tools.base import Tool, ToolError
from mito.tools.seal import Dispatch, require_dispatch


class ScheduleProposeParams(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    cron: str = Field(..., description="5-field cron or @hourly/@daily/@weekly")


def schedule_tools(handbrake: HandbrakeClient) -> list[Tool]:
    async def propose_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, ScheduleProposeParams):
            raise ToolError("internal: schedule.propose params mismatch")
        try:
            return await handbrake.propose_schedule(d.ticket, p.name, p.cron)
        except Exception as exc:
            raise ToolError(f"schedule rejected: {exc}. Use 5 fields or @daily.") from exc

    return [
        Tool(
            "schedule.propose",
            "Propose a cron schedule. Handbrake validates it. Playbooks run in Phase 6.",
            ScheduleProposeParams,
            propose_h,
            "T3",
            idempotent=False,
            core=False,
        )
    ]
