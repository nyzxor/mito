"""Minimal ASGI app for the Handbrake (ADR-0002). No framework: ~10 routes, two bearer tokens.

Runtime token : /state, /gate/*, /model/*, /audit/append
Operator token: everything above plus /halt, /wake, /approve, /deny, /approvals, /checkin,
                /autonomy, /audit/tail, /integrity
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any
from urllib.parse import parse_qs

from handbrake.budget.governor import BudgetExceeded
from handbrake.core import DispatchTicket, GateRequest, Handbrake
from handbrake.integrity.commands import CommandError
from handbrake.kill.switch import HaltLevel

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]

OPERATOR_ONLY = frozenset(
    {
        "/halt",
        "/wake",
        "/approve",
        "/deny",
        "/approvals",
        "/checkin",
        "/autonomy",
        "/audit/tail",
        "/integrity",
    }
)


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.body = {"error": message, **extra}


class HandbrakeApp:
    def __init__(self, hb: Handbrake) -> None:
        self.hb = hb

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return
        try:
            status, body = await self._handle(scope, receive)
        except ApiError as exc:
            status, body = exc.status, exc.body
        except BudgetExceeded as exc:
            status, body = 402, {"error": exc.reason, "dimension": exc.dimension}
        except PermissionError as exc:
            status, body = 403, {"error": str(exc)}
        except CommandError as exc:
            status, body = 403, {"error": str(exc)}
        except (KeyError, TypeError, ValueError) as exc:
            status, body = 400, {"error": f"bad request: {exc}"}
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(data)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": data})

    # ---- plumbing --------------------------------------------------------------------------------
    async def _read_json(self, receive: Receive) -> dict[str, Any]:
        chunks: list[bytes] = []
        while True:
            msg = await receive()
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body", False):
                break
        raw = b"".join(chunks)
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(400, f"invalid JSON body: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "JSON body must be an object")
        return data

    def _role(self, scope: Scope) -> str:
        auth = ""
        for k, v in scope.get("headers", []):
            if k == b"authorization":
                auth = v.decode("latin-1")
        if not auth.lower().startswith("bearer "):
            raise ApiError(401, "missing bearer token")
        token = auth[7:].strip()
        tokens = self.hb.tokens()
        if hmac.compare_digest(token, tokens["operator"]):
            return "operator"
        if hmac.compare_digest(token, tokens["runtime"]):
            return "runtime"
        raise ApiError(401, "invalid token")

    async def _handle(self, scope: Scope, receive: Receive) -> tuple[int, dict[str, Any]]:
        role = self._role(scope)
        method: str = scope["method"]
        path: str = scope["path"]
        query = parse_qs(scope.get("query_string", b"").decode())
        if path in OPERATOR_ONLY and role != "operator":
            raise PermissionError("operator token required")
        hb = self.hb

        if method == "GET" and path == "/state":
            return 200, hb.state()
        if method == "GET" and path == "/approvals":
            return 200, {"pending": [c.__dict__ for c in hb.pending_approvals()]}
        if method == "GET" and path == "/audit/tail":
            n = int(query.get("n", ["20"])[0])
            return 200, {"records": [r.__dict__ for r in hb.audit_tail(n)]}
        if method == "GET" and path == "/integrity":
            return 200, hb.integrity_check(force=True).__dict__
        if method != "POST":
            raise ApiError(404, "not found")

        body = await self._read_json(receive)
        if path == "/gate/dispatch":
            req = GateRequest(
                session=str(body["session"]),
                tool=str(body["tool"]),
                args=dict(body.get("args", {})),
                taint_flags=[str(f) for f in body.get("taint_flags", [])],
                purpose=str(body.get("purpose", "")),
                workspace=str(body.get("workspace", "")),
                task=str(body.get("task", "default")),
            )
            return 200, hb.gate_dispatch(req).to_dict()
        if path == "/gate/result":
            hb.record_result(
                str(body["session"]),
                str(body["tool"]),
                DispatchTicket.from_dict(dict(body["ticket"])),
                ok=bool(body["ok"]),
                result_hash=str(body["result_hash"]),
                cost_atp=float(body.get("cost_atp", 0)),
            )
            return 200, {"ok": True, "flags": hb.session_flags(str(body["session"]))}
        if path == "/model/preflight":
            g = hb.model_preflight(
                str(body["session"]),
                str(body["task"]),
                str(body["tier"]),
                float(body["est_usd"]),
                str(body["model_id"]),
            )
            return 200, {"meter_id": g.meter_id, "est_usd": g.est_usd, "tier": g.tier}
        if path == "/model/reconcile":
            hb.model_reconcile(
                str(body["meter_id"]), float(body["actual_usd"]), dict(body.get("usage", {}))
            )
            return 200, {"ok": True}
        if path == "/model/outcome":
            (hb.model_success if bool(body["ok"]) else hb.model_failure)(str(body["model_id"]))
            return 200, {"ok": True}
        if path == "/audit/append":
            rec = hb.audit.append(f"runtime.{body['kind']}", dict(body.get("payload", {})))
            return 200, {"seq": rec.seq, "hash": rec.hash}
        if path == "/halt":
            hb.halt(
                HaltLevel(str(body.get("level", "soft"))),
                source=f"api:{body.get('source', 'operator')}",
            )
            return 200, hb.state()
        if path == "/wake":
            return 200, {"ok": hb.wake(by="api:operator"), **hb.state()}
        if path == "/approve":
            return 200, {"status": hb.approve(str(body["action_hash"]), by="api:operator")}
        if path == "/deny":
            return 200, {"status": hb.deny(str(body["action_hash"]), by="api:operator")}
        if path == "/checkin":
            hb.checkin("api:operator")
            return 200, hb.state()
        if path == "/autonomy":
            return 200, {"level": hb.autonomy_set(body)}
        raise ApiError(404, "not found")


def create_app(hb: Handbrake) -> HandbrakeApp:
    return HandbrakeApp(hb)
