"""T2/T3 network tools. Every byte goes through Handbrake.egress_request (ADR-0005)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from mito.gateway.handbrake_client import HandbrakeClient
from mito.gateway.ir import Provenance
from mito.tools.base import Tool, ToolError
from mito.tools.seal import Dispatch, require_dispatch


class FetchParams(BaseModel):
    url: str = Field(..., description="Public http(s) URL.")


class HttpGetParams(BaseModel):
    url: str
    credential_handle: str = ""


class HttpPostParams(BaseModel):
    url: str
    body: str = ""
    content_type: str = "application/json"
    credential_handle: str = ""


def net_tools(handbrake: HandbrakeClient) -> list[Tool]:
    async def fetch_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, FetchParams):
            raise ToolError("internal: web.fetch params mismatch")
        return await handbrake.egress_request(d.ticket, "GET", p.url, readability=True)

    async def get_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, HttpGetParams):
            raise ToolError("internal: http.get params mismatch")
        cred = p.credential_handle or None
        return await handbrake.egress_request(
            d.ticket, "GET", p.url, credential_handle=cred, readability=False
        )

    async def post_h(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        if not isinstance(p, HttpPostParams):
            raise ToolError("internal: http.post params mismatch")
        cred = p.credential_handle or None
        return await handbrake.egress_request(
            d.ticket,
            "POST",
            p.url,
            body=p.body,
            headers={"Content-Type": p.content_type},
            credential_handle=cred,
            readability=False,
        )

    return [
        Tool(
            "web.fetch",
            "Fetch a public URL as markdown/text. Output is UNTRUSTED. No form submit.",
            FetchParams,
            fetch_h,
            "T2",
            taint_out=Provenance.UNTRUSTED,
        ),
        Tool(
            "http.get",
            "HTTP GET through the egress gateway. Output UNTRUSTED. Optional cred: handle.",
            HttpGetParams,
            get_h,
            "T2",
            taint_out=Provenance.UNTRUSTED,
            credential_handles=("optional",),
        ),
        Tool(
            "http.post",
            "HTTP POST to an egress.toml allowlisted host. T3; A1 asks. Optional cred: handle.",
            HttpPostParams,
            post_h,
            "T3",
            idempotent=False,
            taint_out=Provenance.UNTRUSTED,
            credential_handles=("optional",),
        ),
    ]
