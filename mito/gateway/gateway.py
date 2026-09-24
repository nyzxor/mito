"""ModelGateway.call — the ONLY path to a model (CLAUDE.md non-negotiable 2).

pre-flight (Handbrake budget) -> adapter with a sealed MeterGrant -> reconcile actual cost ->
circuit-breaker outcome -> audit. Any adapter reached without a grant raises UnpluggedWire.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from handbrake.budget.governor import BudgetExceeded

from mito.gateway.adapters import Adapter, estimate_prompt_tokens
from mito.gateway.handbrake_client import HandbrakeClient
from mito.gateway.ir import MeterGrant, ModelRequest, ModelResponse
from mito.gateway.registry import ModelRegistry, ModelSpec
from mito.gateway.seal import _SEAL


class ModelDenied(RuntimeError):
    def __init__(self, dimension: str, reason: str) -> None:
        super().__init__(reason)
        self.dimension = dimension
        self.reason = reason


@dataclass
class GatewayStats:
    calls: int = 0
    cost_usd: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0

    @property
    def cache_hit_ratio(self) -> float:
        return self.cached_tokens / self.prompt_tokens if self.prompt_tokens else 0.0


class ModelGateway:
    def __init__(
        self, handbrake: HandbrakeClient, registry: ModelRegistry, adapters: dict[str, Adapter]
    ) -> None:
        self._hb = handbrake
        self.registry = registry
        self._adapters = adapters
        self.stats = GatewayStats()

    def _adapter_for(self, spec: ModelSpec) -> Adapter:
        try:
            return self._adapters[spec.provider]
        except KeyError as exc:
            raise ModelDenied(
                "no_adapter", f"no adapter registered for provider {spec.provider!r}"
            ) from exc

    async def call(self, req: ModelRequest, *, model_id: str | None = None) -> ModelResponse:
        spec = (
            self.registry.get(model_id)
            if model_id
            else self.registry.select(req.task_class, tier_request=req.tier_request)
        )
        adapter = self._adapter_for(spec)
        est = self.registry.estimate_usd(spec, estimate_prompt_tokens(req), req.max_tokens)
        try:
            meter_id = await self._hb.model_preflight(
                req.session, req.task, spec.tier, est, spec.id
            )
        except BudgetExceeded as exc:
            raise ModelDenied(exc.dimension, exc.reason) from exc
        except PermissionError as exc:
            raise ModelDenied("handbrake", str(exc)) from exc
        grant = MeterGrant(meter_id, spec.id, _SEAL)
        t0 = time.monotonic()
        try:
            resp = await adapter.complete(req, spec, grant)
        except Exception:
            await self._hb.model_reconcile(meter_id, 0.0, {"error": True})
            await self._hb.model_outcome(spec.id, False)
            raise
        elapsed = resp.elapsed_s or (time.monotonic() - t0)
        cost = self.registry.actual_usd(
            spec,
            resp.usage.prompt_tokens,
            resp.usage.completion_tokens,
            resp.usage.cached_tokens,
            elapsed,
        )
        await self._hb.model_reconcile(meter_id, cost, resp.usage.to_dict())
        await self._hb.model_outcome(spec.id, True)
        self.stats.calls += 1
        self.stats.cost_usd += cost
        self.stats.prompt_tokens += resp.usage.prompt_tokens
        self.stats.completion_tokens += resp.usage.completion_tokens
        self.stats.cached_tokens += resp.usage.cached_tokens
        return ModelResponse(
            resp.message, resp.usage, resp.stop_reason, spec.id, cost, elapsed, resp.provider_state
        )
