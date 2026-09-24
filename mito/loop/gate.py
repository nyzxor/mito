"""Gate.dispatch — the ONLY path to a tool handler (CLAUDE.md non-negotiable 2).

validate args (pydantic) -> Handbrake verdict (policy + taint + budget + autonomy) ->
allow: run handler with a sealed Dispatch; ask: return the approval card; deny: error-as-
instruction; simulate (A0): describe, don't do. Results are compacted, provenance-tagged by
the tool's declared `taint_out`, and reported back so the Handbrake can update session taint.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from handbrake.canonical import canonical_json, sha256_hex
from handbrake.core import DispatchTicket, GateRequest

from mito.gateway.handbrake_client import BrakeLost, HandbrakeClient
from mito.gateway.ir import Provenance, ToolCallIR
from mito.loop.compact import MoreStore, compact_result
from mito.loop.taint import tag_provenance
from mito.tools.base import ToolError, ToolRegistry
from mito.tools.seal import _GATE_SEAL, Dispatch


@dataclass(frozen=True)
class ToolOutcome:
    kind: str  # allow | ask | deny | simulate | error
    content: str  # what goes into the tool message (compacted JSON)
    provenance: Provenance
    tier: str = "T5"
    action_hash: str = ""
    card: dict[str, Any] | None = None
    ok: bool = False


@dataclass
class Gate:
    handbrake: HandbrakeClient
    tools: ToolRegistry
    session: str
    task: str
    workspace: str
    more: MoreStore = field(default_factory=MoreStore)
    flags: set[str] = field(default_factory=set)
    brake_lost: bool = False
    pending: list[dict[str, Any]] = field(default_factory=list)

    async def dispatch(self, call: ToolCallIR, *, purpose: str = "") -> ToolOutcome:
        tool = self.tools.get(call.name)
        if tool is None:
            return _err(f"unknown tool {call.name!r}. Available: {self.tools.names()}")
        if call.raw_arguments is not None:
            return _err(
                f"arguments for {call.name} were not valid JSON "
                f"({call.raw_arguments[:80]!r}). Re-issue the call with a JSON object."
            )
        try:
            params = tool.validate(call.arguments)
        except ToolError as exc:
            return _err(str(exc))

        req = GateRequest(
            self.session,
            tool.name,
            call.arguments,
            sorted(self.flags),
            purpose,
            self.workspace,
            self.task,
        )
        try:
            resp = await self.handbrake.gate_dispatch(req)
        except BrakeLost as exc:
            self.brake_lost = True
            return _err(f"handbrake unreachable ({exc}); no actions are possible. Stop and report.")

        kind = str(resp["kind"])
        tier = str(resp.get("tier", "T5"))
        h = str(resp.get("action_hash", ""))
        if kind in ("allow", "simulate"):
            # taint advances only when an action actually runs (mirrors Handbrake.record_result)
            self.flags |= set(resp.get("flags_after", []))

        if kind == "deny":
            return ToolOutcome(
                "deny",
                json.dumps({"error": f"denied by policy: {resp['reason']}"}),
                Provenance.SYSTEM,
                tier,
                h,
            )
        if kind == "ask":
            card = resp.get("card") or {}
            self.pending.append(card)
            body = {
                "status": "needs_approval",
                "action_hash": h,
                "reason": resp["reason"],
                "hint": (
                    "Tell the operator what this does and why. "
                    "Do not retry until it is approved."
                ),
            }
            return ToolOutcome("ask", json.dumps(body), Provenance.SYSTEM, tier, h, card)

        ticket = DispatchTicket.from_dict(dict(resp["ticket"]))
        if kind == "simulate":
            body = {
                "simulated": True,
                "would_call": tool.name,
                "args": call.arguments,
                "note": resp["reason"],
            }
            await self.handbrake.gate_result(
                self.session,
                tool.name,
                ticket,
                ok=True,
                result_hash=sha256_hex("simulated"),
                cost_atp=0,
            )
            return ToolOutcome(
                "simulate",
                json.dumps(body, ensure_ascii=False),
                Provenance.SYSTEM,
                tier,
                h,
                ok=True,
            )

        ok = True
        try:
            raw = await tool.handler(params, Dispatch(ticket, _GATE_SEAL))
        except ToolError as exc:
            ok, raw = False, {"error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - undesigned errors become retry instructions
            ok, raw = (
                False,
                {
                    "error": (
                        f"{tool.name} failed: {type(exc).__name__}: {exc}. "
                        "Consider different arguments or another tool."
                    )
                },
            )
        content = compact_result(raw, tool.max_result_tokens, self.more)
        if hint := resp.get("hint"):
            content = json.dumps(
                {"result": json.loads(content), "gate_hint": hint}, ensure_ascii=False
            )
        tagged = tag_provenance(content, tool.taint_out if ok else Provenance.SYSTEM)
        await self.handbrake.gate_result(
            self.session,
            tool.name,
            ticket,
            ok=ok,
            result_hash=sha256_hex(canonical_json(raw)),
            cost_atp=0,
        )
        return ToolOutcome("allow", tagged.value, tagged.lane, tier, h, ok=ok)


def _err(message: str) -> ToolOutcome:
    return ToolOutcome(
        "error", json.dumps({"error": message}, ensure_ascii=False), Provenance.SYSTEM
    )
