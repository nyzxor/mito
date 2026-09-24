"""T3 notify.human — leave a notice for the operator. No secrets."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mito.gateway.handbrake_client import HandbrakeClient
from mito.tools.base import Tool, ToolError
from mito.tools.seal import Dispatch, require_dispatch


class NotifyParams(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


def notify_tools(handbrake: HandbrakeClient) -> list[Tool]:
    async def notify_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, NotifyParams):
            raise ToolError("internal: notify.human params mismatch")
        return await handbrake.notify_human(d.ticket, p.message)

    return [
        Tool(
            "notify.human",
            "Leave a short notice for the operator (T3). Do not include secrets.",
            NotifyParams,
            notify_h,
            "T3",
            idempotent=False,
        )
    ]
