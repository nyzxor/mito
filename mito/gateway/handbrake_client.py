"""How the runtime talks to the Handbrake (ADR-0002). Two implementations of one protocol:
in-process (tests, `mito up` single-process dev) and HTTP (separate process/container).
This is the second module in `mito/` allowed to import httpx."""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from handbrake.budget.governor import BudgetExceeded
from handbrake.core import DispatchTicket, GateRequest, Handbrake


class BrakeLost(RuntimeError):
    """Handbrake unreachable: the runtime performs no external action (fail closed)."""


class HandbrakeClient(Protocol):
    async def state(self) -> dict[str, Any]: ...
    async def gate_dispatch(self, req: GateRequest) -> dict[str, Any]: ...
    async def gate_result(
        self,
        session: str,
        tool: str,
        ticket: DispatchTicket,
        *,
        ok: bool,
        result_hash: str,
        cost_atp: float,
    ) -> None: ...
    async def model_preflight(
        self, session: str, task: str, tier: str, est_usd: float, model_id: str
    ) -> str: ...
    async def model_reconcile(
        self, meter_id: str, actual_usd: float, usage: dict[str, Any]
    ) -> None: ...
    async def model_outcome(self, model_id: str, ok: bool) -> None: ...
    async def audit_append(self, kind: str, payload: dict[str, Any]) -> None: ...


class LocalHandbrakeClient:
    def __init__(self, hb: Handbrake) -> None:
        self.hb = hb

    async def state(self) -> dict[str, Any]:
        return self.hb.state()

    async def gate_dispatch(self, req: GateRequest) -> dict[str, Any]:
        return self.hb.gate_dispatch(req).to_dict()

    async def gate_result(
        self,
        session: str,
        tool: str,
        ticket: DispatchTicket,
        *,
        ok: bool,
        result_hash: str,
        cost_atp: float,
    ) -> None:
        self.hb.record_result(
            session, tool, ticket, ok=ok, result_hash=result_hash, cost_atp=cost_atp
        )

    async def model_preflight(
        self, session: str, task: str, tier: str, est_usd: float, model_id: str
    ) -> str:
        return self.hb.model_preflight(session, task, tier, est_usd, model_id).meter_id

    async def model_reconcile(
        self, meter_id: str, actual_usd: float, usage: dict[str, Any]
    ) -> None:
        self.hb.model_reconcile(meter_id, actual_usd, usage)

    async def model_outcome(self, model_id: str, ok: bool) -> None:
        (self.hb.model_success if ok else self.hb.model_failure)(model_id)

    async def audit_append(self, kind: str, payload: dict[str, Any]) -> None:
        self.hb.audit.append(f"runtime.{kind}", payload)


class HttpHandbrakeClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_s,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            r = await self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise BrakeLost(f"handbrake unreachable: {exc}") from exc
        if r.status_code == 402:
            d = r.json()
            raise BudgetExceeded(str(d.get("dimension", "?")), str(d.get("error", "budget")))
        if r.status_code == 403:
            raise PermissionError(str(r.json().get("error", "forbidden")))
        if r.status_code >= 400:
            raise BrakeLost(f"handbrake error {r.status_code}: {r.text[:200]}")
        data: dict[str, Any] = r.json()
        return data

    async def state(self) -> dict[str, Any]:
        try:
            r = await self._client.get("/state")
        except httpx.HTTPError as exc:
            raise BrakeLost(f"handbrake unreachable: {exc}") from exc
        if r.status_code >= 400:
            raise BrakeLost(f"handbrake error {r.status_code}")
        data: dict[str, Any] = r.json()
        return data

    async def gate_dispatch(self, req: GateRequest) -> dict[str, Any]:
        return await self._post("/gate/dispatch", req.__dict__)

    async def gate_result(
        self,
        session: str,
        tool: str,
        ticket: DispatchTicket,
        *,
        ok: bool,
        result_hash: str,
        cost_atp: float,
    ) -> None:
        await self._post(
            "/gate/result",
            {
                "session": session,
                "tool": tool,
                "ticket": ticket.to_dict(),
                "ok": ok,
                "result_hash": result_hash,
                "cost_atp": cost_atp,
            },
        )

    async def model_preflight(
        self, session: str, task: str, tier: str, est_usd: float, model_id: str
    ) -> str:
        d = await self._post(
            "/model/preflight",
            {
                "session": session,
                "task": task,
                "tier": tier,
                "est_usd": est_usd,
                "model_id": model_id,
            },
        )
        return str(d["meter_id"])

    async def model_reconcile(
        self, meter_id: str, actual_usd: float, usage: dict[str, Any]
    ) -> None:
        await self._post(
            "/model/reconcile", {"meter_id": meter_id, "actual_usd": actual_usd, "usage": usage}
        )

    async def model_outcome(self, model_id: str, ok: bool) -> None:
        await self._post("/model/outcome", {"model_id": model_id, "ok": ok})

    async def audit_append(self, kind: str, payload: dict[str, Any]) -> None:
        await self._post("/audit/append", {"kind": kind, "payload": payload})
