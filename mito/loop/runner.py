"""The one main loop (DESIGN §4.3, guide impl-01 §3): propose -> gate -> execute -> observe ->
verify, bounded by the harness. Checks the Handbrake state before every step: halted or
brake-lost means no further model or tool calls."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from handbrake.policy.tiers import tier_index

from mito.gateway.gateway import ModelDenied, ModelGateway
from mito.gateway.handbrake_client import BrakeLost, HandbrakeClient
from mito.gateway.ir import Message, ModelRequest, Provenance
from mito.loop.gate import Gate
from mito.loop.types import Budget, Turn
from mito.tools.base import ToolRegistry

Verifier = Callable[[str, list[Message]], tuple[bool, str]]


async def run_turn(
    task_text: str,
    *,
    gateway: ModelGateway,
    gate: Gate,
    tools: ToolRegistry,
    handbrake: HandbrakeClient,
    system_prompt: str,
    budget: Budget | None = None,
    task_class: str = "plan",
    history: list[Message] | None = None,
    snapshot: str = "",
    verifier: Verifier | None = None,
    max_repairs: int = 2,
    tool_names: list[str] | None = None,
) -> Turn:
    budget = budget or Budget()
    budget.start()
    trace: list[dict[str, Any]] = []
    messages: list[Message] = [
        Message("system", system_prompt + "\n\n" + budget.teach(), provenance=Provenance.SYSTEM)
    ]
    if snapshot:
        messages.append(
            Message(
                "system",
                f"<state_snapshot>\n{snapshot}\n</state_snapshot>",
                provenance=Provenance.SYSTEM,
            )
        )
    messages += list(history or [])
    messages.append(Message("user", task_text, provenance=Provenance.OPERATOR))
    cost = 0.0
    repairs = 0
    specs = tools.specs(tool_names if tool_names is not None else tools.core_names())

    def finish(text: str, reason: str) -> Turn:
        return Turn(text, reason, budget.steps, cost, messages, trace, list(gate.pending))

    while True:
        if reason := budget.stop_reason():
            return finish(_best_text(messages), reason)
        try:
            state = await handbrake.state()
        except BrakeLost:
            trace.append({"ev": "brake_lost"})
            return finish(_best_text(messages), "brake_lost")
        if state.get("halt") is not None:
            trace.append({"ev": "halted", "level": state["halt"]["level"]})
            return finish(_best_text(messages), "halted")
        if gate.brake_lost:
            return finish(_best_text(messages), "brake_lost")

        budget.steps += 1
        req = ModelRequest(gate.session, gate.task, task_class, tuple(messages), tools=specs)
        try:
            resp = await gateway.call(req)
        except ModelDenied as exc:
            trace.append({"ev": "model_denied", "dimension": exc.dimension, "reason": exc.reason})
            return finish(
                _best_text(messages), "budget_usd" if exc.dimension != "handbrake" else "halted"
            )
        except Exception as exc:  # noqa: BLE001 - provider errors are bounded retries
            budget.consecutive_failures += 1
            trace.append({"ev": "llm_error", "err": f"{type(exc).__name__}: {exc}"})
            if budget.consecutive_failures >= budget.max_consecutive_failures:
                return finish(f"model failed repeatedly: {exc}", "error")
            continue
        budget.consecutive_failures = 0
        cost += resp.cost_usd
        trace.append(
            {
                "ev": "model",
                "model": resp.model_id,
                "usage": resp.usage.to_dict(),
                "cost_usd": resp.cost_usd,
            }
        )
        messages.append(resp.message)

        if not resp.message.tool_calls:
            text = resp.message.content
            if verifier is not None:
                ok, feedback = verifier(text, messages)
                if not ok and repairs < max_repairs:
                    repairs += 1
                    trace.append({"ev": "verify_fail", "feedback": feedback})
                    messages.append(
                        Message(
                            "user",
                            f"<verifier>\n{feedback}\n</verifier>",
                            provenance=Provenance.SYSTEM,
                        )
                    )
                    continue
                if not ok:
                    return finish(text, "verify_failed")
            return finish(text, "completed")

        for call in resp.message.tool_calls:
            if budget.stop_reason():
                break
            budget.tool_calls += 1
            tool = tools.get(call.name)
            is_write = tool is not None and tier_index(tool.risk_tier) >= tier_index("T3")
            if is_write and budget.writes >= budget.max_write_actions:
                messages.append(
                    Message(
                        "tool",
                        '{"error": "write budget for this turn exhausted; summarize and stop"}',
                        tool_call_id=call.id,
                        name=call.name,
                        provenance=Provenance.SYSTEM,
                    )
                )
                trace.append({"ev": "tool", "name": call.name, "verdict": "write_budget"})
                continue
            outcome = await gate.dispatch(call, purpose=_purpose(resp.message.content))
            if is_write and outcome.kind == "allow":
                budget.writes += 1
                budget.extend_wall(60)
            if outcome.kind in ("error", "deny") or (outcome.kind == "allow" and not outcome.ok):
                budget.consecutive_failures += 1
            else:
                budget.consecutive_failures = 0
            trace.append(
                {
                    "ev": "tool",
                    "name": call.name,
                    "args": call.arguments,
                    "verdict": outcome.kind,
                    "tier": outcome.tier,
                    "ok": outcome.ok,
                }
            )
            messages.append(
                Message(
                    "tool",
                    outcome.content,
                    tool_call_id=call.id,
                    name=call.name,
                    provenance=outcome.provenance,
                )
            )


def _best_text(messages: list[Message]) -> str:
    for m in reversed(messages):
        if m.role == "assistant" and m.content:
            return m.content
    return "(stopped before producing an answer)"


def _purpose(assistant_text: str) -> str:
    return assistant_text.strip().splitlines()[0][:200] if assistant_text.strip() else ""
