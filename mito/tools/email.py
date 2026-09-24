"""email.read (T2, UNTRUSTED), email.draft (T3), email.send (T4, allowlist)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mito.gateway.handbrake_client import HandbrakeClient
from mito.gateway.ir import Provenance
from mito.tools.base import Tool, ToolError
from mito.tools.seal import Dispatch, require_dispatch


class EmailReadParams(BaseModel):
    pass


class EmailDraftParams(BaseModel):
    to: str = Field(..., min_length=3, max_length=200)
    subject: str = Field("", max_length=200)
    body: str = Field(..., min_length=1, max_length=8000)


class EmailSendParams(BaseModel):
    draft_id: str = Field(..., min_length=4, max_length=64)


def email_tools(handbrake: HandbrakeClient) -> list[Tool]:
    async def read_h(_p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        return await handbrake.mail_read(d.ticket)

    async def draft_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, EmailDraftParams):
            raise ToolError("internal: email.draft params mismatch")
        return await handbrake.mail_draft(d.ticket, p.to, p.subject, p.body)

    async def send_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, EmailSendParams):
            raise ToolError("internal: email.send params mismatch")
        try:
            return await handbrake.mail_send(d.ticket, p.draft_id)
        except Exception as exc:
            raise ToolError(
                f"send refused: {exc}. Draft stays local. Approved recipients only."
            ) from exc

    return [
        Tool(
            "email.read",
            "Read unseen mail from the dedicated mailbox. Content is UNTRUSTED.",
            EmailReadParams,
            read_h,
            "T2",
            taint_out=Provenance.UNTRUSTED,
            core=False,
        ),
        Tool(
            "email.draft",
            "Save a draft. Does not send. Use email.send only after the operator approves.",
            EmailDraftParams,
            draft_h,
            "T3",
            idempotent=False,
            core=False,
        ),
        Tool(
            "email.send",
            "Send one draft to an operator-approved recipient. T4: approval every time.",
            EmailSendParams,
            send_h,
            "T4",
            idempotent=False,
            reversible=False,
            core=False,
        ),
    ]
