"""`mito run <task>`: one bounded turn on the configured local model, through the running
Handbrake (HTTP, runtime token). The operator is the user turn; T0 tools only in Phase 1."""

from __future__ import annotations

import json
import sys
import uuid

from handbrake.paths import MitoPaths, repo_root

from mito.cli.operator import handbrake_url
from mito.gateway.client import OpenAICompatAdapter
from mito.gateway.gateway import ModelGateway
from mito.gateway.handbrake_client import BrakeLost, HttpHandbrakeClient
from mito.gateway.registry import ModelRegistry
from mito.loop.compact import MoreStore
from mito.loop.gate import Gate
from mito.loop.prompt import build_system_prompt
from mito.loop.runner import run_turn
from mito.loop.types import Budget
from mito.tools.base import ToolRegistry
from mito.tools.basic import basic_tools


async def run_once(paths: MitoPaths, task: str, *, json_out: bool = False) -> int:
    tokens = json.loads((paths.control / "tokens.json").read_text(encoding="utf-8"))
    client = HttpHandbrakeClient(handbrake_url(), str(tokens["runtime"]), timeout_s=5.0)
    try:
        await client.state()
    except BrakeLost:
        print(
            "Handbrake is not running; start it with `mito up` "
            "(fail closed: no turn without brakes)",
            file=sys.stderr,
        )
        return 3
    root = repo_root()
    registry = ModelRegistry.load(
        root / "config" / "models.toml", root / "config" / "metabolism.toml"
    )
    adapter = OpenAICompatAdapter()
    gateway = ModelGateway(client, registry, {"openai_compatible": adapter})
    session = f"cli-{uuid.uuid4().hex[:8]}"
    workspace = paths.workspace / session
    workspace.mkdir(parents=True, exist_ok=True)
    more = MoreStore()
    tools = ToolRegistry(basic_tools(client, more))
    gate = Gate(client, tools, session=session, task=session, workspace=str(workspace), more=more)
    prompt = build_system_prompt(root / "prompts")
    try:
        turn = await run_turn(
            task,
            gateway=gateway,
            gate=gate,
            tools=tools,
            handbrake=client,
            system_prompt=prompt,
            budget=Budget(),
        )
    finally:
        await adapter.aclose()
        await client.aclose()
    summary = {
        "stop_reason": turn.stop_reason,
        "steps": turn.steps,
        "cost_usd": round(turn.cost_usd, 6),
        "tools": turn.tools_called(),
        "pending_approvals": len(turn.pending_approvals),
        "text": turn.text,
    }
    if json_out:
        print(json.dumps(summary, indent=1, ensure_ascii=False))
    else:
        print(turn.text)
        print(
            (
                f"\n[{turn.stop_reason}] steps={turn.steps} tools={turn.tools_called()} "
                f"cost=${turn.cost_usd:.6f} pending={len(turn.pending_approvals)}"
            ),
            file=sys.stderr,
        )
    return 0 if turn.stop_reason == "completed" else 1
