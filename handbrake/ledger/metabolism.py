"""Runway and metabolic state (DESIGN §6). Median daily burn, not mean."""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from handbrake.ledger.book import Ledger

DAY = 86400.0
NOTICE_STATES = frozenset({"FRUGAL", "STARVING", "DEEP_REST"})


@dataclass(frozen=True)
class MetabolismLimits:
    per_usd: float
    floor: float
    wake_threshold: float
    min_topup: float
    thriving_above: float
    normal_above: float
    frugal_above: float

    @classmethod
    def from_toml(cls, path: Path) -> MetabolismLimits:
        with path.open("rb") as f:
            data = tomllib.load(f)
        atp = data["atp"]
        runway = data["runway_days"]
        return cls(
            per_usd=float(atp["per_usd"]),
            floor=float(atp["floor"]),
            wake_threshold=float(atp["wake_threshold"]),
            min_topup=float(atp["min_topup"]),
            thriving_above=float(runway["thriving_above"]),
            normal_above=float(runway["normal_above"]),
            frugal_above=float(runway["frugal_above"]),
        )


def usd_to_atp(usd: float, per_usd: float) -> float:
    return max(0.0, usd) * per_usd


def local_usd_per_second(path: Path) -> float:
    with path.open("rb") as f:
        data = tomllib.load(f)
    lp = data["local_pricing"]
    watts = float(lp["gpu_watts"]) + float(lp["host_watts"])
    energy = watts / 1000.0 * float(lp["kwh_price_usd"])
    amort = float(lp["amortization_usd_per_hour"])
    return (energy + amort) / 3600.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


@dataclass(frozen=True)
class MetabolismSnapshot:
    state: str
    balance_atp: float
    runway_days: float | None
    daily_burn_atp: float
    verified_income_atp: float
    claimed_income_atp: float
    resting: bool
    rest_reason: str | None
    notice: str | None


class MetabolismEngine:
    def __init__(
        self,
        ledger: Ledger,
        limits: MetabolismLimits,
        *,
        clock: Callable[[], float],
    ) -> None:
        self.ledger = ledger
        self.limits = limits
        self._clock = clock
        self.resting = False
        self.rest_reason: str | None = None
        self._last_state = "NORMAL"

    def runway_days(self) -> tuple[float, float | None]:
        now = self._clock()
        buckets = {day: amt for day, amt in self.ledger.daily_expense_atp(now - 7 * DAY)}
        today = int(now // DAY)
        series = [buckets.get(today - i, 0.0) for i in range(7)]
        burn = _median(series)
        if burn <= 0:
            return 0.0, None
        return burn, self.ledger.balance_atp() / burn

    def classify(self, balance: float, runway: float | None) -> str:
        if self.resting:
            return "DEEP_REST"
        if self.ledger.has_endowment() and balance <= self.limits.floor:
            return "DEEP_REST"
        if runway is None:
            return "THRIVING"
        if runway < self.limits.frugal_above:
            return "STARVING"
        if runway < self.limits.normal_above:
            return "FRUGAL"
        if runway < self.limits.thriving_above:
            return "NORMAL"
        return "THRIVING"

    def evaluate(self) -> MetabolismSnapshot:
        balance = self.ledger.balance_atp()
        burn, runway = self.runway_days()
        if (
            self.ledger.has_endowment()
            and balance <= self.limits.floor
            and not self.resting
        ):
            self.resting = True
            self.rest_reason = "floor"
        if (
            self.resting
            and self.rest_reason == "floor"
            and balance >= self.limits.wake_threshold
        ):
            self.resting = False
            self.rest_reason = None
        state = self.classify(balance, runway)
        notice: str | None = None
        if state != self._last_state and state in NOTICE_STATES:
            notice = f"entered {state}"
        self._last_state = state
        return MetabolismSnapshot(
            state,
            balance,
            runway,
            burn,
            self.ledger.verified_income_atp(),
            self.ledger.claimed_income_atp(),
            self.resting,
            self.rest_reason,
            notice,
        )

    def rest(self, reason: str) -> None:
        self.resting = True
        self.rest_reason = reason

    def wake(self, *, operator: bool) -> bool:
        if not self.resting:
            return True
        if self.rest_reason == "floor" and self.ledger.balance_atp() < self.limits.wake_threshold:
            if not operator:
                return False
            # operator wake while under floor: leave rest, next evaluate may re-enter
        self.resting = False
        self.rest_reason = None
        return True
