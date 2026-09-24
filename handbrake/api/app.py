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
from handbrake.egress.gateway import EgressClosed
from handbrake.egress.ssrf import EgressDenied
from handbrake.integrity.commands import CommandError
from handbrake.kill.switch import HaltLevel
from handbrake.ledger.book import LedgerError
from handbrake.sandbox.runner import SandboxUnavailable
from handbrake.vault.store import VaultError

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
        "/vault/list",
        "/vault/add",
        "/vault/revoke",
        "/ledger/confirm",
        "/ledger/topup",
        "/rest",
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
        except (EgressDenied, EgressClosed, SandboxUnavailable, VaultError, LedgerError) as exc:
            status, body = 403, {"error": str(exc)}
        except CommandError as exc:
            status, body = 403, {"error": str(exc)}
        except (KeyError, TypeError, ValueError) as exc:
            status, body = 400, {"error": f"bad request: {exc}"}
        if isinstance(body, tuple) and len(body) == 2 and isinstance(body[0], str):
            html_body, extra = body
            data = html_body.encode("utf-8")
            headers = [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", str(len(data)).encode()),
                *extra,
            ]
        else:
            data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            headers = [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(data)).encode()),
            ]
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": headers,
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

    def _cookie(self, scope: Scope, name: str) -> str:
        for k, v in scope.get("headers", []):
            if k == b"cookie":
                for part in v.decode("latin-1").split(";"):
                    k2, _, val = part.strip().partition("=")
                    if k2 == name:
                        return str(val)
        return ""

    async def _form(self, receive: Receive, scope: Scope) -> dict[str, str]:
        ctype = ""
        for k, v in scope.get("headers", []):
            if k == b"content-type":
                ctype = v.decode("latin-1")
        raw = b""
        while True:
            msg = await receive()
            raw += msg.get("body", b"")
            if not msg.get("more_body", False):
                break
        if "application/json" in ctype:
            data = json.loads(raw or b"{}")
            return {str(k): str(v) for k, v in data.items()}
        parsed = parse_qs(raw.decode("utf-8"))
        return {k: v[0] for k, v in parsed.items() if v}

    def _dash(self, scope: Scope) -> str:
        sid = self._cookie(scope, "mito_dash")
        csrf = self.hb.dashboard_csrf(sid)
        if not csrf:
            raise ApiError(401, "dashboard session missing; run `mito dashboard`")
        return csrf

    async def _dashboard(
        self, scope: Scope, receive: Receive
    ) -> tuple[int, Any]:
        from handbrake.channels.dashboard import render
        from handbrake.kill.switch import HaltLevel

        method: str = scope["method"]
        path: str = scope["path"]
        query = parse_qs(scope.get("query_string", b"").decode())
        if method == "GET" and path == "/dashboard":
            ticket = (query.get("ticket") or [""])[0]
            opened = self.hb.open_dashboard(ticket) if ticket else None
            if opened:
                sid, csrf = opened.split(":", 1)
                cookie = f"mito_dash={sid}; HttpOnly; SameSite=Strict; Path=/".encode()
                return 200, (render(self.hb, csrf), [(b"set-cookie", cookie)])
            csrf = self._dash(scope)
            return 200, (render(self.hb, csrf), [])
        if method != "POST" or not path.startswith("/dashboard/"):
            raise ApiError(404, "not found")
        csrf = self._dash(scope)
        form = await self._form(receive, scope)
        if not hmac.compare_digest(form.get("csrf", ""), csrf):
            raise ApiError(403, "csrf mismatch")
        if path == "/dashboard/halt":
            self.hb.halt(HaltLevel(form.get("level", "soft")), source="dashboard")
            return 200, (render(self.hb, csrf), [])
        if path in ("/dashboard/approve", "/dashboard/deny"):
            h = form.get("action_hash", "")
            if path.endswith("approve"):
                self.hb.approve(h, by="dashboard")
            else:
                self.hb.deny(h, by="dashboard")
            return 200, (render(self.hb, csrf), [])
        raise ApiError(404, "not found")

    async def _handle(self, scope: Scope, receive: Receive) -> tuple[int, Any]:
        path: str = scope["path"]
        if path == "/dashboard" or (
            path.startswith("/dashboard/") and path != "/dashboard/ticket"
        ):
            return await self._dashboard(scope, receive)
        role = self._role(scope)
        method: str = scope["method"]
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
        if method == "GET" and path == "/vault/list":
            return 200, {"handles": [h.to_dict() for h in hb.vault.list_handles()]}
        if method == "GET" and path == "/ledger":
            return 200, hb.ledger_snapshot()
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
        if path == "/vault/add":
            name = hb.vault.add(
                str(body["handle"]), str(body["secret"]), note=str(body.get("note", ""))
            )
            hb.audit.append("vault.add", {"handle": name})
            return 200, {"handle": name}
        if path == "/vault/revoke":
            return 200, {"revoked": hb.vault.revoke(str(body["handle"]))}
        if path == "/egress/request":
            return 200, hb.egress_request(
                DispatchTicket.from_dict(dict(body["ticket"])),
                str(body["method"]),
                str(body["url"]),
                headers=dict(body.get("headers") or {}),
                body=None if body.get("body") is None else str(body.get("body")),
                purpose=str(body.get("purpose", "")),
                credential_handle=body.get("credential_handle") or None,
                readability=bool(body.get("readability", False)),
            )
        if path == "/sandbox/run":
            return 200, hb.sandbox_run(
                DispatchTicket.from_dict(dict(body["ticket"])),
                [str(a) for a in body.get("argv", [])],
                timeout_s=float(body.get("timeout_s", 15)),
            )
        if path == "/rest":
            hb.rest(by="api:operator")
            return 200, hb.state()
        if path == "/ledger/topup":
            return 200, hb.ledger_topup(float(body["atp"]), by="api:operator")
        if path == "/ledger/confirm":
            return 200, hb.ledger_confirm(body)
        if path == "/ledger/claim":
            return 200, hb.ledger_claim(
                float(body["amount_atp"]), str(body["playbook"]), str(body.get("evidence", ""))
            )
        if path == "/notify":
            return 200, hb.notify_human(
                DispatchTicket.from_dict(dict(body["ticket"])), str(body["message"])
            )
        if path == "/mail/read":
            return 200, hb.mail_read(DispatchTicket.from_dict(dict(body["ticket"])))
        if path == "/mail/draft":
            return 200, hb.mail_draft(
                DispatchTicket.from_dict(dict(body["ticket"])),
                str(body["to"]),
                str(body.get("subject", "")),
                str(body.get("body", "")),
            )
        if path == "/mail/send":
            return 200, hb.mail_send(
                DispatchTicket.from_dict(dict(body["ticket"])), str(body["draft_id"])
            )
        if path == "/schedule/propose":
            return 200, hb.propose_schedule(
                DispatchTicket.from_dict(dict(body["ticket"])),
                str(body["name"]),
                str(body["cron"]),
            )
        if path == "/channel/telegram":
            if role != "operator":
                raise PermissionError("operator token required")
            from handbrake.channels.telegram import TgUpdate, apply_update

            update = TgUpdate(str(body["user_id"]), str(body["text"]))
            return 200, apply_update(hb, update, hb.telegram_allowlist)
        if path == "/dashboard/ticket":
            if role != "operator":
                raise PermissionError("operator token required")
            return 200, {"ticket": hb.issue_dashboard_ticket()}
        if path == "/channel/nonce":
            if role != "operator":
                raise PermissionError("operator token required")
            return 200, {"nonce": hb.issue_channel_nonce(str(body.get("purpose", "halt")))}
        raise ApiError(404, "not found")


def create_app(hb: Handbrake) -> HandbrakeApp:
    return HandbrakeApp(hb)
