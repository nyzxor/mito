"""DESIGN §5.3/§5.4: deny -> ask -> allow, first match wins, default deny; tiers; blacklist;
autonomy ladder; Rule of Two via taint flags. The engine is pure (no IO after load)."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from handbrake.policy.engine import (
    GateContext,
    PolicyEngine,
    PolicyError,
    Rule,
    Verdict,
)
from handbrake.policy.tiers import TierMap

pytestmark = pytest.mark.handbrake

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def engine() -> PolicyEngine:
    return PolicyEngine.load(ROOT / "policy")


def ctx(
    autonomy: str = "A1",
    flags: frozenset[str] = frozenset(),
    state: str = "NORMAL",
    workspace: str = "/work/run1",
) -> GateContext:
    return GateContext(
        session="s1",
        autonomy=autonomy,
        metabolic_state=state,
        taint_flags=flags,
        workspace=workspace,
        own_repos=frozenset({"guilh/mito"}),
        write_allowlist=frozenset(),
    )


# ---- loading -----------------------------------------------------------------------------------
def test_loads_repo_policy(engine: PolicyEngine) -> None:
    assert engine.tiers.tier_of("time") == "T0"
    assert engine.tiers.tier_of("email.send") == "T4"
    assert engine.tiers.tier_of("payment.transfer") == "T5"  # glob "payment.*"


def test_unknown_predicate_fails_load(tmp_path: Path) -> None:
    (tmp_path / "policy.toml").write_text(
        'schema_version=1\n[defaults]\nT0="allow"\n[autonomy]\nA1=["T0"]\n'
        '[[rule]]\nkind="deny"\ntool="*"\nwhere={ bogus = true }\n',
        encoding="utf-8",
    )
    for name in ("risk_tiers.toml", "blacklist.toml", "egress.toml"):
        src = (ROOT / "policy" / name).read_text(encoding="utf-8")
        (tmp_path / name).write_text(src, encoding="utf-8")
    with pytest.raises(PolicyError, match="bogus"):
        PolicyEngine.load(tmp_path)


# ---- verdicts by tier and autonomy --------------------------------------------------------------
def test_t0_allowed_at_a1(engine: PolicyEngine) -> None:
    v = engine.evaluate("time", {}, ctx())
    assert v.kind == "allow" and v.tier == "T0"


def test_t3_asks_at_a1_and_allows_at_a2(engine: PolicyEngine) -> None:
    assert engine.evaluate("memory.write", {"name": "x"}, ctx("A1")).kind == "ask"
    assert engine.evaluate("memory.write", {"name": "x"}, ctx("A2")).kind == "allow"


def test_t4_always_asks_even_at_a2(engine: PolicyEngine) -> None:
    assert engine.evaluate("publish.public", {}, ctx("A2")).kind == "ask"


def test_t5_denied_everywhere(engine: PolicyEngine) -> None:
    for level in ("A0", "A1", "A2"):
        v = engine.evaluate("payment.transfer", {}, ctx(level))
        assert v.kind == "deny" and "blacklist" in v.reason


def test_unknown_tool_denied(engine: PolicyEngine) -> None:
    v = engine.evaluate("does.not.exist", {}, ctx())
    assert v.kind == "deny" and "unknown tool" in v.reason


def test_a0_simulates_everything_but_reads(engine: PolicyEngine) -> None:
    assert engine.evaluate("time", {}, ctx("A0")).kind == "allow"
    assert engine.evaluate("fs.write", {"path": "/work/run1/a"}, ctx("A0")).kind == "simulate"
    assert engine.evaluate("web.fetch", {"url": "https://x"}, ctx("A0")).kind == "simulate"


def test_invalid_autonomy_level_is_rejected(engine: PolicyEngine) -> None:
    with pytest.raises(PolicyError):
        engine.evaluate("time", {}, ctx("A3"))


# ---- rules ------------------------------------------------------------------------------------
def test_fs_outside_workspace_is_denied(engine: PolicyEngine) -> None:
    v = engine.evaluate("fs.write", {"path": "/etc/passwd"}, ctx())
    assert v.kind == "deny" and "workspace" in v.reason
    assert engine.evaluate("fs.write", {"path": "/work/run1/out.txt"}, ctx()).kind == "allow"


def test_immutable_trees_denied_for_any_tool(engine: PolicyEngine) -> None:
    v = engine.evaluate("code.run", {"cmd": "cat", "target": "handbrake/kill/switch.py"}, ctx())
    assert v.kind == "deny" and "immutable" in v.reason
    v2 = engine.evaluate("git.local", {"args": "checkout -- policy/policy.toml"}, ctx())
    assert v2.kind == "deny" and "immutable" in v2.reason


def test_http_post_requires_allowlisted_host(engine: PolicyEngine) -> None:
    v = engine.evaluate("http.post", {"url": "https://evil.example/x"}, ctx("A2"))
    assert v.kind == "deny"
    c = ctx("A2")
    c2 = GateContext(**{**c.__dict__, "write_allowlist": frozenset({"api.github.com"})})
    assert engine.evaluate("http.post", {"url": "https://api.github.com/x"}, c2).kind == "allow"


def test_subagent_asks_when_frugal_even_at_a2(engine: PolicyEngine) -> None:
    assert engine.evaluate("spawn_subagent", {}, ctx("A2", state="FRUGAL")).kind == "ask"
    assert engine.evaluate("spawn_subagent", {}, ctx("A2", state="NORMAL")).kind == "allow"


def test_git_push_only_to_own_repos_and_within_autonomy(engine: PolicyEngine) -> None:
    args = {"repo": "guilh/mito", "branch": "mito/x"}
    assert engine.evaluate("git.push", args, ctx("A1")).kind == "ask"  # T3 at A1 still asks
    assert engine.evaluate("git.push", args, ctx("A2")).kind == "allow"
    v = engine.evaluate("git.push", {"repo": "other/repo"}, ctx("A2"))
    assert v.kind == "deny" and "own repos" in v.reason


@given(order=st.permutations(["deny", "ask", "allow"]))
def test_deny_beats_allow_regardless_of_declaration_order(order: list[str]) -> None:
    rules = [Rule(kind=k, tool="x.*", where={}, reason=k) for k in order]
    tiers = TierMap({"x.do": "T1"}, sensitivity_b=frozenset(), untrusted_out=frozenset())
    eng = PolicyEngine(
        rules=rules,
        tiers=tiers,
        defaults={
            "T0": "allow",
            "T1": "allow",
            "T2": "allow",
            "T3": "ask",
            "T4": "ask",
            "T5": "deny",
        },
        autonomy={"A0": [], "A1": ["T0", "T1", "T2"], "A2": ["T0", "T1", "T2", "T3"]},
        blacklist_tools=frozenset(),
        trifecta_max=2,
    )
    assert eng.evaluate("x.do", {}, ctx()).kind == "deny"


# ---- Rule of Two --------------------------------------------------------------------------------
def test_trifecta_third_flag_asks(engine: PolicyEngine) -> None:
    # session already ingested untrusted input (A) and read sensitive data (B)
    c = ctx("A2", flags=frozenset({"A", "B"}))
    v = engine.evaluate("memory.write", {"name": "n"}, c)  # T3 -> C
    assert v.kind == "ask" and "trifecta" in v.reason.lower()
    assert set(v.flags_after) == {"A", "B", "C"}


def test_trifecta_two_flags_fine(engine: PolicyEngine) -> None:
    c = ctx("A2", flags=frozenset({"A"}))
    v = engine.evaluate("memory.write", {"name": "n"}, c)
    assert v.kind == "allow" and set(v.flags_after) == {"A", "C"}


def test_email_read_sets_a_and_b(engine: PolicyEngine) -> None:
    v = engine.evaluate("email.read", {"folder": "INBOX"}, ctx("A1"))
    assert v.kind == "allow" and set(v.flags_after) == {"A", "B"}


def test_flags_are_monotonic_in_verdict(engine: PolicyEngine) -> None:
    c = ctx("A1", flags=frozenset({"C"}))
    v: Verdict = engine.evaluate("time", {}, c)
    assert "C" in v.flags_after
