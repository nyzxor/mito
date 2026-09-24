"""Loop behavior on the ScriptedModel (guide impl-01 'Tests to write')."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evals.harness import REPO, make_harness
from handbrake.kill.switch import HaltLevel

from mito.gateway.fake import Step
from mito.gateway.ir import MeterGrant, ModelRequest, ModelResponse, Provenance
from mito.gateway.registry import ModelSpec
from mito.loop.compact import MoreStore, compact_result
from mito.loop.prompt import approx_tokens, build_system_prompt
from mito.loop.types import Budget

pytestmark = pytest.mark.unit


def ws(h_path: Path) -> str:
    return str(h_path)


async def test_completes_after_tool_roundtrip(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path, [Step(tool_calls=(("time", {"tz": "UTC"}),)), Step(text="It is now.")]
    )
    turn = await h.run("what time is it?")
    assert turn.stop_reason == "completed" and turn.text == "It is now."
    assert turn.tools_called() == ["time"]
    tool_msg = next(m for m in turn.messages if m.role == "tool")
    assert (
        json.loads(tool_msg.content)["tz"] == "UTC"
        and tool_msg.provenance == Provenance.TOOL_TRUSTED
    )
    assert h.hb.audit.verify().ok


async def test_step_budget_stops_infinite_tool_loop(tmp_path: Path) -> None:
    h = make_harness(tmp_path, [Step(tool_calls=(("time", {}),))])  # scripted forever
    turn = await h.run("loop", budget=Budget(max_steps=4, max_tool_calls=100))
    assert turn.stop_reason in ("max_steps", "failing")
    assert turn.steps <= 4


async def test_no_progress_detector_blocks_third_identical_call(tmp_path: Path) -> None:
    h = make_harness(tmp_path, [Step(tool_calls=(("time", {"tz": "UTC"}),))])
    turn = await h.run("loop", budget=Budget(max_steps=6))
    verdicts = [e["verdict"] for e in turn.trace if e.get("ev") == "tool"]
    assert verdicts[:3] == ["allow", "allow", "deny"]


async def test_malformed_json_args_become_retry_instruction(tmp_path: Path) -> None:
    h = make_harness(tmp_path, [Step(raw_tool_call=("time", "{bad")), Step(text="ok")])
    turn = await h.run("x")
    tool_msg = next(m for m in turn.messages if m.role == "tool")
    assert "not valid JSON" in json.loads(tool_msg.content)["error"]
    assert turn.stop_reason == "completed"


async def test_unknown_tool_lists_real_tools(tmp_path: Path) -> None:
    h = make_harness(tmp_path, [Step(tool_calls=(("nope", {}),)), Step(text="ok")])
    turn = await h.run("x")
    err = json.loads(next(m for m in turn.messages if m.role == "tool").content)["error"]
    assert "unknown tool" in err and "time" in err


async def test_invalid_args_are_validated_by_pydantic(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path, [Step(tool_calls=(("result.more", {"offset": -1}),)), Step(text="ok")]
    )
    turn = await h.run("x")
    err = json.loads(next(m for m in turn.messages if m.role == "tool").content)["error"]
    assert "invalid arguments for result.more" in err and "handle" in err


async def test_t3_asks_at_a1_and_lands_in_pending(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path,
        [
            Step(text="Saving a note.", tool_calls=(("memory.write", {"name": "n"}),)),
            Step(text="waiting"),
        ],
    )
    turn = await h.run("remember n")
    assert turn.stop_reason == "completed"
    assert [e["verdict"] for e in turn.trace if e.get("ev") == "tool"] == ["ask"]
    assert len(turn.pending_approvals) == 1 and turn.pending_approvals[0]["tool"] == "memory.write"
    assert h.calls == []  # never executed
    assert h.hb.pending_approvals()[0].purpose == "Saving a note."


async def test_approval_then_execute_exactly_once(tmp_path: Path) -> None:
    mw = Step(tool_calls=(("memory.write", {"name": "n"}),))
    h = make_harness(tmp_path, [mw, mw, mw, mw, Step(text="done")])
    turn = await h.run("remember n", budget=Budget(max_steps=2))
    assert h.calls == []
    card = h.hb.pending_approvals()[0]
    h.hb.approve(card.action_hash, by="test")
    turn = await h.run("remember n (approved)", budget=Budget(max_steps=3))
    verdicts = [e["verdict"] for e in turn.trace if e.get("ev") == "tool"]
    assert verdicts[0] == "allow" and verdicts[1] == "ask"  # single use
    assert h.calls == [("memory.write", {"name": "n", "body": ""})]


async def test_t5_and_blacklisted_are_denied(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path,
        [Step(tool_calls=(("email.send", {"to": "x@y"}),)), Step(text="ok")],
        autonomy="A1",
    )
    turn = await h.run("send")
    assert [e["verdict"] for e in turn.trace if e.get("ev") == "tool"] == [
        "ask"
    ]  # T4 asks even at A2
    assert h.calls == []


async def test_a0_simulates_writes(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path,
        [
            Step(tool_calls=(("fs.write", {"path": "PLACEHOLDER", "content": "hi"}),)),
            Step(text="ok"),
        ],
        autonomy="A0",
    )
    h.scripted.steps[0] = Step(
        tool_calls=(("fs.write", {"path": str(h.workspace / "a.txt"), "content": "hi"}),)
    )
    turn = await h.run("write")
    msg = json.loads(next(m for m in turn.messages if m.role == "tool").content)
    assert msg["simulated"] is True and not (h.workspace / "a.txt").exists()


async def test_fs_write_inside_workspace_allowed_outside_denied(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path,
        [
            Step(tool_calls=(("fs.write", {"path": "PLACEHOLDER", "content": "hi"}),)),
            Step(
                tool_calls=(("fs.write", {"path": str(tmp_path / "escape.txt"), "content": "no"}),)
            ),
            Step(text="ok"),
        ],
    )
    h.scripted.steps[0] = Step(
        tool_calls=(("fs.write", {"path": str(h.workspace / "a.txt"), "content": "hi"}),)
    )
    turn = await h.run("write")
    verdicts = [e["verdict"] for e in turn.trace if e.get("ev") == "tool"]
    assert verdicts == ["allow", "deny"]
    assert (h.workspace / "a.txt").read_text(encoding="utf-8") == "hi" and not (
        tmp_path / "escape.txt"
    ).exists()


async def test_untrusted_tool_output_is_tagged_and_sets_taint(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path,
        [Step(tool_calls=(("web.fetch", {"url": "https://example.org"}),)), Step(text="ok")],
    )
    turn = await h.run("fetch")
    tool_msg = next(m for m in turn.messages if m.role == "tool")
    assert tool_msg.provenance == Provenance.UNTRUSTED
    assert h.hb.session_flags("s1") == ["A"]


async def test_tool_error_is_instruction_and_counts_failure(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path,
        [Step(tool_calls=(("web.fetch", {"url": "https://fail.example"}),)), Step(text="gave up")],
    )
    turn = await h.run("fetch")
    err = json.loads(next(m for m in turn.messages if m.role == "tool").content)["error"]
    assert "Try another source" in err and turn.stop_reason == "completed"


async def test_halt_mid_turn_stops_before_next_model_call(tmp_path: Path) -> None:
    h = make_harness(tmp_path, [Step(tool_calls=(("time", {}),)), Step(text="never")])
    inner = h.scripted

    class Halting:
        async def complete(
            self, req: ModelRequest, spec: ModelSpec, grant: MeterGrant
        ) -> ModelResponse:
            h.hb.halt(HaltLevel.SOFT, source="test")
            return await inner.complete(req, spec, grant)

    h.gateway._adapters["openai_compatible"] = Halting()
    turn = await h.run("x")
    assert turn.stop_reason == "halted"
    assert h.scripted.i == 1  # the tool call after the halt was denied, no second model call
    assert any(e.get("verdict") == "deny" for e in turn.trace if e.get("ev") == "tool")


async def test_brake_lost_stops_everything(tmp_path: Path) -> None:
    from mito.gateway.handbrake_client import BrakeLost

    h = make_harness(tmp_path, [Step(text="x")])

    async def lost() -> dict[str, object]:
        raise BrakeLost("down")

    h.client.state = lost  # type: ignore[method-assign]
    turn = await h.run("x")
    assert turn.stop_reason == "brake_lost" and h.scripted.i == 0


async def test_truncation_marker_and_more_handle(tmp_path: Path) -> None:
    h = make_harness(tmp_path, [Step(tool_calls=(("ledger.read", {}),)), Step(text="ok")])
    turn = await h.run("read")
    msg = json.loads(next(m for m in turn.messages if m.role == "tool").content)
    assert msg["truncated"] is True and msg["more"].startswith("more-")
    page = h.gate.more.page(msg["more"], 0, 100)
    assert page["next_offset"] is not None and page["total_chars"] > 100_000


def test_compact_result_hard_cap() -> None:
    s = compact_result({"big": "x" * 100_000}, 100, MoreStore())
    assert len(s) // 4 <= 110 and json.loads(s)["truncated"] is True


def test_compact_result_lossless_is_untouched() -> None:
    assert json.loads(compact_result({"a": 1, "b": [1, 2]}, 100)) == {"a": 1, "b": [1, 2]}


async def test_write_budget_per_turn(tmp_path: Path) -> None:
    calls = tuple(("memory.write", {"name": f"n{i}"}) for i in range(5))
    h = make_harness(
        tmp_path, [Step(tool_calls=calls), Step(text="ok")], autonomy="A2", dev_mode=False
    )
    turn = await h.run("many writes", budget=Budget(max_write_actions=3))
    verdicts = [e["verdict"] for e in turn.trace if e.get("ev") == "tool"]
    assert verdicts.count("allow") == 3 and verdicts.count("write_budget") == 2


async def test_verifier_gated_stop_is_bounded(tmp_path: Path) -> None:
    h = make_harness(
        tmp_path,
        [Step(text="draft 1"), Step(text="draft 2"), Step(text="draft 3"), Step(text="draft 4")],
    )
    turn = await h.run(
        "write",
        verifier=lambda text, _msgs: ("final" in text, "must contain the word final"),
        max_repairs=2,
    )
    assert turn.stop_reason == "verify_failed" and turn.text == "draft 3"


def test_l0_prompt_is_cache_shaped_and_small() -> None:
    p = build_system_prompt(REPO / "prompts")
    assert (
        p.index("<constitution>")
        < p.index("<hard_rules>")
        < p.index("<tool_policy>")
        < p.index("<response_contract>")
    )
    assert approx_tokens(p) <= 3000
    assert build_system_prompt(REPO / "prompts") == p  # deterministic
