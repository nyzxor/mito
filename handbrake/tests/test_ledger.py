"""DESIGN §6: double-entry ATP, income is a claim, runway median, Deep Rest, one notice."""

from __future__ import annotations

from pathlib import Path

import pytest

from handbrake.core import Handbrake
from handbrake.integrity.commands import CommandError, SignedCommand
from handbrake.ledger.book import ENERGY, EXPENSES_LLM, Ledger, LedgerError
from handbrake.ledger.metabolism import MetabolismEngine, MetabolismLimits, usd_to_atp
from handbrake.paths import MitoPaths
from handbrake.tests.conftest import make_repo
from handbrake.tests.test_budget_governor import Clock

pytestmark = pytest.mark.handbrake

LIMITS = MetabolismLimits(
    per_usd=1000,
    floor=0,
    wake_threshold=3000,
    min_topup=100,
    thriving_above=14,
    normal_above=7,
    frugal_above=2,
)


def _book(tmp_path: Path, clock: Clock) -> Ledger:
    return Ledger(tmp_path / "ledger.sqlite", clock=clock)


def test_journal_is_balanced(tmp_path: Path) -> None:
    clock = Clock()
    book = _book(tmp_path, clock)
    with pytest.raises(LedgerError, match="unbalanced"):
        book.post("x", "bad", [(ENERGY, 10, 0)])
    jid = book.expense(10, account=EXPENSES_LLM, memo="call")
    assert jid
    assert book.balance_atp() == pytest.approx(-10)


def test_income_claim_is_not_balance_until_confirm(tmp_path: Path) -> None:
    clock = Clock()
    book = _book(tmp_path, clock)
    book.topup(5000, memo="seed")
    claim = book.file_claim(800, "gig-fulfillment", "invoice-1")
    assert book.balance_atp() == pytest.approx(5000)
    assert book.verified_income_atp() == 0
    assert book.claimed_income_atp() == pytest.approx(800)
    book.confirm_claim(claim.id)
    assert book.balance_atp() == pytest.approx(5800)
    assert book.verified_income_atp() == pytest.approx(800)
    assert book.claimed_income_atp() == 0


def test_metrics_use_verified_income_only(tmp_path: Path) -> None:
    clock = Clock()
    book = _book(tmp_path, clock)
    book.file_claim(9999, "bounty-scout", "nope")
    assert book.verified_income_atp() == 0
    snap = book.snapshot()
    assert snap["verified_income_atp"] == 0
    assert snap["claimed_income_atp"] == 9999


def test_runway_median_not_mean(tmp_path: Path) -> None:
    clock = Clock()
    book = _book(tmp_path, clock)
    book.topup(10_000)
    # six quiet days + one 7000 ATP spike: mean is 1000, median is 0
    for i in range(6):
        clock.t = 1_700_000_000.0 + i * 86400
        book.expense(1, account=EXPENSES_LLM, memo=f"d{i}")  # tiny so the day exists
    clock.t = 1_700_000_000.0 + 6 * 86400
    book.expense(7000, account=EXPENSES_LLM, memo="spike")
    eng = MetabolismEngine(book, LIMITS, clock=clock)
    burn, runway = eng.runway_days()
    assert burn < 100  # median resists the spike
    assert runway is None or runway > 14


def test_floor_enters_deep_rest_and_wake_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    hb = Handbrake(paths, repo, dev_mode=True)
    hb.ledger_topup(4000, by="test")
    assert hb.metabolic_state() == "THRIVING"
    hb.ledger.expense(4000, account=EXPENSES_LLM, memo="burn")
    snap = hb.refresh_metabolism()
    assert snap.state == "DEEP_REST" and snap.rest_reason == "floor"
    assert hb.halted() is not None
    hb.wake(by="operator")  # still under floor → evaluate re-enters
    hb.refresh_metabolism()
    hb.ledger_topup(3500, by="test")
    snap = hb.refresh_metabolism()
    assert snap.balance_atp >= 3000
    assert snap.state != "DEEP_REST" or snap.rest_reason != "floor"


def test_one_notice_per_state_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    hb = Handbrake(paths, repo, dev_mode=True)
    hb.ledger_topup(100, by="test")
    hb.ledger.expense(100, account=EXPENSES_LLM, memo="empty")
    first = hb.refresh_metabolism()
    second = hb.refresh_metabolism()
    assert first.notice == "entered DEEP_REST"
    assert second.notice is None


def test_deep_rest_blocks_cloud_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    hb = Handbrake(paths, repo, dev_mode=True)
    hb.rest(by="test")
    with pytest.raises(PermissionError, match="halted|deep rest"):
        hb.model_preflight("s", "t", "L2", 0.01, "m")


def test_starving_runway_blocks_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = Clock()
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    hb = Handbrake(paths, repo, dev_mode=True, clock=clock)
    hb.ledger_topup(40_000, by="test")
    for i in range(7):
        clock.advance(86400)
        hb.ledger.expense(5_000, account=EXPENSES_LLM, memo=f"d{i}")
    snap = hb.refresh_metabolism()
    assert snap.state == "STARVING"
    with pytest.raises(PermissionError, match="STARVING"):
        hb.model_preflight("s", "t", "L2", 0.01, "m")


def test_usd_to_atp_matches_config() -> None:
    assert usd_to_atp(0.25, 1000) == 250


def test_ledger_confirm_is_signed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    hb = Handbrake(paths, repo, dev_mode=True)
    claim = hb.ledger.file_claim(200, "gig-fulfillment", "ev")
    key = hb.keystore.load_private()
    signed = SignedCommand.build("ledger.confirm", {"id": claim.id}, key, now=hb._clock())
    out = hb.ledger_confirm(signed.to_dict())
    assert out["status"] == "verified"
    assert hb.ledger.verified_income_atp() == pytest.approx(200)
    with pytest.raises(CommandError, match="nonce"):
        hb.ledger_confirm(signed.to_dict())


def test_topup_below_min_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    hb = Handbrake(paths, repo, dev_mode=True)
    with pytest.raises(LedgerError, match="min"):
        hb.ledger_topup(50, by="test")
