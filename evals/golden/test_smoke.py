"""`just evals-smoke`: the 20-case smoke set, n=2 trials, pass^k must be 100%, cost within
tolerance of evals/cost/baseline.json (>10% regression fails without an ADR)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evals.golden.runner import run_case
from evals.golden.smoke_cases import CASES

pytestmark = pytest.mark.smoke

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "evals" / "cost" / "baseline.json"
TOLERANCE = 0.10
ABS_FLOOR_USD = 1e-6  # below this, relative comparison is noise


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
async def test_smoke_case(case: object, tmp_path: Path) -> None:
    from evals.golden.runner import Case

    assert isinstance(case, Case)
    res = await run_case(case, tmp_path, trials=2)
    assert res.pass_pow_k, res.failures


async def test_cost_per_case_within_baseline(tmp_path: Path) -> None:
    results = {c.id: await run_case(c, tmp_path / c.id, trials=1) for c in CASES}
    current = {cid: round(r.avg_cost_usd, 9) for cid, r in results.items()}
    if os.environ.get("MITO_WRITE_BASELINE") == "1" or not BASELINE.exists():
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(
            json.dumps({"unit": "usd", "cases": current}, indent=1), encoding="utf-8"
        )
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["cases"]
    regressions = []
    for cid in current:
        if cid not in baseline:
            continue
        limit = max(ABS_FLOOR_USD, baseline[cid] * (1 + TOLERANCE))
        if current[cid] > limit:
            pct = (current[cid] / baseline[cid] - 1) * 100
            regressions.append(f"{cid}: {current[cid]:.3e} > {baseline[cid]:.3e} (+{pct:.0f}%)")
    assert not regressions, "cost regression without an ADR:\n" + "\n".join(regressions)
    missing = set(current) - set(baseline)
    assert not missing, (
        f"new cases without a baseline entry (run with MITO_WRITE_BASELINE=1): {missing}"
    )
