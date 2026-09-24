"""Model registry from config/models.toml (DESIGN §7). No model names anywhere else."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TIERS = ("L0", "L1", "L2", "L3")


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class LocalPricing:
    usd_per_second: float
    assumed_tokens_per_s: float

    @classmethod
    def load(cls, metabolism_path: Path) -> LocalPricing:
        with metabolism_path.open("rb") as f:
            lp = tomllib.load(f)["local_pricing"]
        watts = float(lp["gpu_watts"]) + float(lp["host_watts"])
        per_s = (watts / 1000.0 * float(lp["kwh_price_usd"])) / 3600.0 + float(
            lp["amortization_usd_per_hour"]
        ) / 3600.0
        return cls(per_s, float(lp.get("assumed_tokens_per_s", 30)))


@dataclass(frozen=True)
class ModelSpec:
    id: str
    tier: str
    provider: str  # openai_compatible | anthropic | scripted | replay
    model: str
    base_url: str
    context_window: int
    price_in: float  # USD per 1M tokens
    price_out: float
    price_cached_in: float
    capabilities: frozenset[str]
    params: dict[str, Any] = field(default_factory=dict)
    credential: str | None = None

    @property
    def is_local(self) -> bool:
        return self.tier in ("L0", "L1")


@dataclass(frozen=True)
class RouterConfig:
    escalation_max: int
    preferences: dict[str, list[str]]  # task_class -> tier order


class ModelRegistry:
    def __init__(
        self, models: list[ModelSpec], router: RouterConfig, local_pricing: LocalPricing
    ) -> None:
        ids = [m.id for m in models]
        if len(ids) != len(set(ids)):
            raise RegistryError("duplicate model ids")
        self.models = models
        self.router = router
        self.local_pricing = local_pricing

    @classmethod
    def load(cls, models_path: Path, metabolism_path: Path) -> ModelRegistry:
        with models_path.open("rb") as f:
            data = tomllib.load(f)
        specs: list[ModelSpec] = []
        for m in data.get("model", []):
            if m["tier"] not in TIERS:
                raise RegistryError(f"model {m['id']}: bad tier {m['tier']}")
            base_url = str(m.get("base_url", ""))
            if "base_url_env" in m:
                base_url = os.environ.get(
                    str(m["base_url_env"]), base_url or "http://127.0.0.1:8080/v1"
                )
            specs.append(
                ModelSpec(
                    id=str(m["id"]),
                    tier=str(m["tier"]),
                    provider=str(m["provider"]),
                    model=str(m["model"]),
                    base_url=base_url.rstrip("/"),
                    context_window=int(m.get("context_window", 8192)),
                    price_in=float(m.get("price_in", 0.0)),
                    price_out=float(m.get("price_out", 0.0)),
                    price_cached_in=float(m.get("price_cached_in", m.get("price_in", 0.0))),
                    capabilities=frozenset(str(c) for c in m.get("capabilities", [])),
                    params=dict(m.get("params", {})),
                    credential=m.get("credential"),
                )
            )
        r = data.get("router", {})
        prefs = {k: [str(t) for t in v] for k, v in r.items() if isinstance(v, list)}
        router = RouterConfig(int(r.get("escalation_max", 2)), prefs)
        return cls(specs, router, LocalPricing.load(metabolism_path))

    def get(self, model_id: str) -> ModelSpec:
        for m in self.models:
            if m.id == model_id:
                return m
        raise RegistryError(f"unknown model id {model_id!r}")

    def select(
        self,
        task_class: str,
        *,
        tier_request: str | None = None,
        allowed_tiers: frozenset[str] | None = None,
    ) -> ModelSpec:
        """Phase 1 router: first configured model in the preferred tier order. Phase 3 adds
        metabolic state, capabilities and verifier-driven escalation."""
        order = (
            [tier_request]
            if tier_request
            else self.router.preferences.get(task_class, ["L1", "L2", "L3"])
        )
        for tier in order:
            if allowed_tiers is not None and tier not in allowed_tiers:
                continue
            for m in self.models:
                if m.tier == tier:
                    return m
        raise RegistryError(f"no model configured for task class {task_class!r} in tiers {order}")

    # ---- pricing --------------------------------------------------------------------------------
    def estimate_usd(self, spec: ModelSpec, prompt_tokens: int, max_tokens: int) -> float:
        if spec.is_local:
            seconds = (prompt_tokens + max_tokens) / max(
                1.0, self.local_pricing.assumed_tokens_per_s
            )
            return seconds * self.local_pricing.usd_per_second
        return (prompt_tokens * spec.price_in + max_tokens * spec.price_out) / 1_000_000

    def actual_usd(
        self,
        spec: ModelSpec,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int,
        elapsed_s: float,
    ) -> float:
        if spec.is_local:
            return elapsed_s * self.local_pricing.usd_per_second
        uncached = max(0, prompt_tokens - cached_tokens)
        return (
            uncached * spec.price_in
            + cached_tokens * spec.price_cached_in
            + completion_tokens * spec.price_out
        ) / 1_000_000
