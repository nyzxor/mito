"""Pulse interval from config/metabolism.toml [pulse]. Named consumer of those fields."""

from __future__ import annotations

import tomllib
from pathlib import Path


def load_pulse(path: Path) -> dict[str, float]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    pulse = data["pulse"]
    return {
        "base_interval_s": float(pulse["base_interval_s"]),
        "frugal_multiplier": float(pulse["frugal_multiplier"]),
        "starving_multiplier": float(pulse["starving_multiplier"]),
    }


def interval_s(state: str, pulse: dict[str, float]) -> float:
    base = pulse["base_interval_s"]
    if state == "FRUGAL":
        return base * pulse["frugal_multiplier"]
    if state in ("STARVING", "DEEP_REST"):
        return base * pulse["starving_multiplier"]
    return base
