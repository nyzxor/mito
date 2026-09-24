"""Test harness: a real Handbrake (in-process) + the real loop + a ScriptedModel. $0.

Used by loop tests, wiring tests and the smoke set. Fake tools carry REAL tier names from
policy/risk_tiers.toml so the real policy applies to them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from handbrake.core import Handbrake
from handbrake.paths import MitoPaths
from handbrake.tests.conftest import make_repo
from mito.gateway.fake import ScriptedModel, Step
from mito.gateway.gateway import ModelGateway
from mito.gateway.handbrake_client import LocalHandbrakeClient
from mito.gateway.ir import Provenance
from mito.gateway.registry import ModelRegistry
from mito.loop.compact import MoreStore
from mito.loop.gate import Gate
from mito.loop.prompt import build_system_prompt
from mito.loop.runner import run_turn
from mito.loop.types import Budget, Turn
from mito.tools.base import Tool, ToolError, ToolRegistry
from mito.tools.basic import basic_tools
from mito.tools.seal import Dispatch, require_dispatch
from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parents[1]


class PathParams(BaseModel):
    path: str = Field(..., description="Path inside the workspace")
    content: str = ""


class UrlParams(BaseModel):
    url: str


class NameParams(BaseModel):
    name: str
    body: str = ""


class ToParams(BaseModel):
    to: str
    body: str = ""


class Empty(BaseModel):
    pass


@dataclass
class Harness:
    hb: Handbrake
    client: LocalHandbrakeClient
    registry: ModelRegistry
    scripted: ScriptedModel
    gateway: ModelGateway
    tools: ToolRegistry
    gate: Gate
    system_prompt: str
    workspace: Path
    calls: list[tuple[str, dict[str, Any]]]

    async def run(
        self,
        task: str,
        *,
        budget: Budget | None = None,
        tool_names: list[str] | None = None,
        **kw: Any,
    ) -> Turn:
        return await run_turn(
            task,
            gateway=self.gateway,
            gate=self.gate,
            tools=self.tools,
            handbrake=self.client,
            system_prompt=self.system_prompt,
            budget=budget,
            tool_names=tool_names,
            **kw,
        )


def fake_tools(
    workspace: Path,
    calls: list[tuple[str, dict[str, Any]]],
    *,
    fetch_content: str = "<html>hello</html>",
) -> list[Tool]:
    async def fs_write(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        assert isinstance(p, PathParams)
        calls.append(("fs.write", p.model_dump()))
        target = Path(p.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(p.content, encoding="utf-8")
        return {"written": str(target), "bytes": len(p.content)}

    async def web_fetch(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        assert isinstance(p, UrlParams)
        calls.append(("web.fetch", p.model_dump()))
        if "fail" in p.url:
            raise ToolError("fetch failed: host unreachable. Try another source or report.")
        return {"url": p.url, "content": fetch_content}

    async def memory_write(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        assert isinstance(p, NameParams)
        calls.append(("memory.write", p.model_dump()))
        return {"stored": p.name}

    async def email_send(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        assert isinstance(p, ToParams)
        calls.append(("email.send", p.model_dump()))
        return {"sent_to": p.to}

    async def huge(p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        calls.append(("ledger.read", {}))
        return {"rows": [{"i": i, "text": "x" * 200} for i in range(500)]}

    return [
        Tool(
            "fs.write",
            "Write a file inside the workspace.",
            PathParams,
            fs_write,
            "T1",
            reversible=True,
        ),
        Tool(
            "web.fetch",
            "Fetch a public URL as text.",
            UrlParams,
            web_fetch,
            "T2",
            taint_out=Provenance.UNTRUSTED,
        ),
        Tool("memory.write", "Store a fact in memory.", NameParams, memory_write, "T3"),
        Tool("email.send", "Send an email.", ToParams, email_send, "T4", reversible=False),
        Tool(
            "ledger.read",
            "Read the ledger (returns many rows).",
            Empty,
            huge,
            "T0",
            max_result_tokens=300,
        ),
    ]


def make_harness(
    tmp_path: Path,
    steps: list[Step],
    *,
    autonomy: str = "A1",
    dev_mode: bool = True,
    extra_tools: list[Tool] | None = None,
    tiers_patch: str = "",
) -> Harness:
    os.environ.setdefault("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    if tiers_patch:
        p = repo / "policy" / "risk_tiers.toml"
        p.write_text(
            p.read_text(encoding="utf-8").replace(
                'tools = ["time",', f'tools = [{tiers_patch} "time",', 1
            ),
            encoding="utf-8",
        )
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=dev_mode)
    hb = Handbrake(paths, repo, dev_mode=dev_mode)
    hb.autonomy.set(autonomy, by="harness")
    client = LocalHandbrakeClient(hb)
    registry = ModelRegistry.load(
        REPO / "config" / "models.toml", REPO / "config" / "metabolism.toml"
    )
    scripted = ScriptedModel(steps)
    gateway = ModelGateway(client, registry, {"openai_compatible": scripted})
    more = MoreStore()
    workspace = paths.workspace / "s1"
    workspace.mkdir(parents=True, exist_ok=True)
    calls: list[tuple[str, dict[str, Any]]] = []
    tools = ToolRegistry(
        [*basic_tools(client, more), *fake_tools(workspace, calls), *(extra_tools or [])]
    )
    gate = Gate(client, tools, session="s1", task="t1", workspace=str(workspace), more=more)
    prompt = build_system_prompt(REPO / "prompts")
    return Harness(hb, client, registry, scripted, gateway, tools, gate, prompt, workspace, calls)
