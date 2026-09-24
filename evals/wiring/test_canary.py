"""Unplugged-wire detection, part 2 (DESIGN §5.11): a canary tool and a canary model that fail
loudly when reached without passing Gate.dispatch / ModelGateway.call."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from handbrake.core import DispatchTicket
from mito.gateway.fake import ScriptedModel, Step
from mito.gateway.gateway import ModelGateway
from mito.gateway.ir import (
    Message,
    MeterGrant,
    ModelRequest,
    ModelResponse,
    Provenance,
    ToolCallIR,
    UnpluggedWire,
    Usage,
)
from mito.gateway.registry import ModelSpec
from mito.gateway.seal import require_grant
from mito.tools.base import Tool
from mito.tools.seal import Dispatch, require_dispatch
from pydantic import BaseModel

from evals.harness import make_harness

pytestmark = pytest.mark.wiring


class CanaryAdapter:
    def __init__(self) -> None:
        self.reached_properly = 0

    async def complete(
        self, req: ModelRequest, spec: ModelSpec, grant: MeterGrant
    ) -> ModelResponse:
        require_grant(grant)
        self.reached_properly += 1
        return ModelResponse(
            Message("assistant", "canary ok", provenance=Provenance.SYSTEM),
            Usage(1, 1),
            "stop",
            spec.id,
        )


class Empty(BaseModel):
    pass


def canary_tool(hits: list[str]) -> Tool:
    async def handler(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        hits.append("ok")
        return {"canary": "ok"}

    return Tool("canary", "test-only canary", Empty, handler, "T0")


def _fake_ticket() -> DispatchTicket:
    return DispatchTicket("t", "h", "canary", "s1", 9e12, ())


async def test_canary_model_through_gateway_ok_but_direct_call_fails(tmp_path: Path) -> None:
    h = make_harness(tmp_path, [Step(text="x")])
    canary = CanaryAdapter()
    gw = ModelGateway(h.client, h.registry, {"openai_compatible": canary})
    req = ModelRequest("s1", "t1", "draft", (Message("user", "hi"),))
    resp = await gw.call(req)
    assert resp.message.content == "canary ok" and canary.reached_properly == 1
    spec = h.registry.select("draft")
    with pytest.raises(UnpluggedWire):
        await canary.complete(req, spec, MeterGrant("m", spec.id, object()))
    with pytest.raises(UnpluggedWire):
        await ScriptedModel([Step()]).complete(req, spec, MeterGrant("m", spec.id, object()))


async def test_canary_tool_through_gate_ok_but_direct_call_fails(tmp_path: Path) -> None:
    hits: list[str] = []
    h = make_harness(
        tmp_path, [Step(text="x")], extra_tools=[canary_tool(hits)], tiers_patch='"canary",'
    )
    outcome = await h.gate.dispatch(ToolCallIR("c1", "canary", {}))
    assert outcome.kind == "allow" and outcome.ok and hits == ["ok"]
    tool = h.tools.get("canary")
    assert tool is not None
    with pytest.raises(UnpluggedWire):
        await tool.handler(Empty(), Dispatch(_fake_ticket(), object()))
    assert hits == ["ok"]


async def test_every_registered_tool_refuses_an_unsealed_dispatch(tmp_path: Path) -> None:
    h = make_harness(tmp_path, [Step(text="x")])
    for name in h.tools.names():
        tool = h.tools.get(name)
        assert tool is not None
        params = tool.params.model_construct()
        with pytest.raises(UnpluggedWire):
            await tool.handler(params, Dispatch(_fake_ticket(), object()))


async def test_handbrake_rejects_results_without_a_live_ticket(tmp_path: Path) -> None:
    h = make_harness(tmp_path, [Step(text="x")])
    with pytest.raises(PermissionError):
        h.hb.record_result("s1", "time", _fake_ticket(), ok=True, result_hash="h", cost_atp=0)
