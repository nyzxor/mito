"""Phase 0 invariants over the policy/config drafts.

These are not product tests; they keep the TOML drafts consistent with DESIGN.md until Phase 1
replaces them with the real policy-engine tests.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.handbrake


def _load(rel: str) -> dict[str, Any]:
    with (ROOT / rel).open("rb") as f:
        return tomllib.load(f)


@pytest.mark.parametrize(
    "rel",
    [
        "policy/policy.toml",
        "policy/risk_tiers.toml",
        "policy/blacklist.toml",
        "policy/egress.toml",
        "config/models.toml",
        "config/metabolism.toml",
        "config/mito.toml",
        "playbooks/bounty-scout.toml",
        "playbooks/gig-fulfillment.toml",
        "playbooks/cost-optimizer.toml",
    ],
)
def test_toml_parses(rel: str) -> None:
    data = _load(rel)
    assert data.get("schema_version") == 1


def test_every_tool_has_exactly_one_tier() -> None:
    tiers = _load("policy/risk_tiers.toml")
    seen: dict[str, str] = {}
    for tier in ("T0", "T1", "T2", "T3", "T4", "T5"):
        block = tiers[tier]
        assert isinstance(block, dict)
        for tool in block["tools"]:
            assert tool not in seen, f"{tool} in both {seen[tool]} and {tier}"
            seen[tool] = tier
    assert len(seen) > 30


def test_t5_tools_are_blacklisted() -> None:
    tiers = _load("policy/risk_tiers.toml")
    bl = _load("policy/blacklist.toml")
    t5 = tiers["T5"]
    assert isinstance(t5, dict)
    assert set(t5["tools"]) == set(bl["tools"])


def test_defaults_deny_t5_and_ask_t3_plus() -> None:
    pol = _load("policy/policy.toml")
    defaults = pol["defaults"]
    assert isinstance(defaults, dict)
    assert defaults["T5"] == "deny"
    assert defaults["T3"] == "ask" and defaults["T4"] == "ask"


def test_autonomy_ladder_has_no_a3_and_a2_never_auto_allows_t4() -> None:
    pol = _load("policy/policy.toml")
    autonomy = pol["autonomy"]
    assert isinstance(autonomy, dict)
    assert set(autonomy) == {"A0", "A1", "A2"}
    assert "T4" not in autonomy["A2"] and "T5" not in autonomy["A2"]
    assert autonomy["A0"] == []


def test_playbook_tools_exist_in_tiers_and_respect_autonomy() -> None:
    tiers = _load("policy/risk_tiers.toml")
    known = {t for k in ("T0", "T1", "T2", "T3", "T4") for t in tiers[k]["tools"]}
    for pb in ("bounty-scout", "gig-fulfillment", "cost-optimizer"):
        data = _load(f"playbooks/{pb}.toml")
        tools = data["tools"]
        assert isinstance(tools, dict)
        unknown = set(tools["required"]) - known
        assert not unknown, f"{pb}: unknown tools {unknown}"
        assert data["autonomy_max"] in ("A0", "A1")


def test_ssrf_ranges_cover_metadata_and_rfc1918() -> None:
    egress = _load("policy/egress.toml")
    ssrf = egress["ssrf"]
    assert isinstance(ssrf, dict)
    cidrs = set(ssrf["block_cidrs"])
    for required in (
        "169.254.0.0/16",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
    ):
        assert required in cidrs


def test_metabolism_thresholds_are_ordered() -> None:
    m = _load("config/metabolism.toml")
    runway = m["runway_days"]
    assert isinstance(runway, dict)
    assert runway["thriving_above"] > runway["normal_above"] > runway["frugal_above"] > 0
    atp = m["atp"]
    assert isinstance(atp, dict)
    assert atp["wake_threshold"] > atp["floor"]
    assert atp["min_topup"] > 0
    budgets = m["budgets_usd"]
    assert isinstance(budgets, dict)
    assert (
        budgets["per_call_max"]
        <= budgets["per_task"]
        <= budgets["per_day"]
        <= budgets["per_month_cloud"]
    )
