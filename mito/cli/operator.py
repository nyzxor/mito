"""Operator surface used by the CLI: HTTP (operator token) when the Handbrake server is up,
in-process otherwise. Read-only commands may always go in-process."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

import httpx
from handbrake.core import Handbrake
from handbrake.integrity.commands import SignedCommand
from handbrake.kill.switch import DockerSandboxKiller, HaltLevel
from handbrake.paths import MitoPaths, repo_root


class Operator(Protocol):
    def state(self) -> dict[str, Any]: ...
    def halt(self, level: HaltLevel) -> dict[str, Any]: ...
    def wake(self) -> bool: ...
    def approve(self, action_hash: str) -> str: ...
    def deny(self, action_hash: str) -> str: ...
    def pending(self) -> list[dict[str, Any]]: ...
    def checkin(self) -> None: ...
    def autonomy_set(self, signed: dict[str, Any]) -> str: ...
    def integrity(self) -> dict[str, Any]: ...
    def audit_tail(self, n: int) -> list[dict[str, Any]]: ...
    def vault_add(self, handle: str, secret: str, note: str = "") -> str: ...
    def vault_list(self) -> list[dict[str, Any]]: ...
    def vault_revoke(self, handle: str) -> bool: ...
    def rest(self) -> dict[str, Any]: ...
    def ledger_topup(self, atp: float) -> dict[str, Any]: ...
    def ledger_confirm(self, signed: dict[str, Any]) -> dict[str, Any]: ...
    def ledger_snapshot(self) -> dict[str, Any]: ...
    def dashboard_ticket(self) -> str: ...


class LocalOperator:
    def __init__(self, hb: Handbrake) -> None:
        self.hb = hb

    def state(self) -> dict[str, Any]:
        return self.hb.state()

    def halt(self, level: HaltLevel) -> dict[str, Any]:
        self.hb.halt(level, source="cli")
        if level.rank >= HaltLevel.HARD.rank:
            DockerSandboxKiller().kill_all()
        return self.hb.state()

    def wake(self) -> bool:
        return self.hb.wake(by="cli")

    def approve(self, action_hash: str) -> str:
        return self.hb.approve(action_hash, by="cli")

    def deny(self, action_hash: str) -> str:
        return self.hb.deny(action_hash, by="cli")

    def pending(self) -> list[dict[str, Any]]:
        return [c.__dict__ for c in self.hb.pending_approvals()]

    def checkin(self) -> None:
        self.hb.checkin("cli")

    def autonomy_set(self, signed: dict[str, Any]) -> str:
        return self.hb.autonomy_set(signed)

    def integrity(self) -> dict[str, Any]:
        res = self.hb.integrity_check(force=True)
        if res.ok:
            self.hb.unfreeze(by="cli")
        return res.__dict__

    def audit_tail(self, n: int) -> list[dict[str, Any]]:
        return [r.__dict__ for r in self.hb.audit_tail(n)]

    def vault_add(self, handle: str, secret: str, note: str = "") -> str:
        name = self.hb.vault.add(handle, secret, note=note)
        self.hb.audit.append("vault.add", {"handle": name, "source": "cli"})
        return name

    def vault_list(self) -> list[dict[str, Any]]:
        return [h.to_dict() for h in self.hb.vault.list_handles()]

    def vault_revoke(self, handle: str) -> bool:
        return self.hb.vault.revoke(handle)

    def rest(self) -> dict[str, Any]:
        self.hb.rest(by="cli")
        return self.hb.state()

    def ledger_topup(self, atp: float) -> dict[str, Any]:
        return self.hb.ledger_topup(atp, by="cli")

    def ledger_confirm(self, signed: dict[str, Any]) -> dict[str, Any]:
        return self.hb.ledger_confirm(signed)

    def ledger_snapshot(self) -> dict[str, Any]:
        return self.hb.ledger_snapshot()

    def dashboard_ticket(self) -> str:
        return self.hb.issue_dashboard_ticket()


class HttpOperator:
    def __init__(self, base_url: str, token: str, *, timeout_s: float = 3.0) -> None:
        self._c = httpx.Client(
            base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout_s
        )

    def probe(self) -> bool:
        try:
            self._c.get("/state", timeout=0.4).raise_for_status()
            return True
        except (httpx.HTTPError, ValueError):
            return False

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        r = self._c.post(path, json=body or {})
        if r.status_code >= 400:
            raise RuntimeError(f"{path}: {r.status_code} {r.text[:200]}")
        data: dict[str, Any] = r.json()
        return data

    def _get(self, path: str) -> dict[str, Any]:
        r = self._c.get(path)
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    def state(self) -> dict[str, Any]:
        return self._get("/state")

    def halt(self, level: HaltLevel) -> dict[str, Any]:
        return self._post("/halt", {"level": level.value, "source": "cli"})

    def wake(self) -> bool:
        return bool(self._post("/wake")["ok"])

    def approve(self, action_hash: str) -> str:
        return str(self._post("/approve", {"action_hash": action_hash})["status"])

    def deny(self, action_hash: str) -> str:
        return str(self._post("/deny", {"action_hash": action_hash})["status"])

    def pending(self) -> list[dict[str, Any]]:
        return list(self._get("/approvals")["pending"])

    def checkin(self) -> None:
        self._post("/checkin")

    def autonomy_set(self, signed: dict[str, Any]) -> str:
        return str(self._post("/autonomy", signed)["level"])

    def integrity(self) -> dict[str, Any]:
        return self._get("/integrity")

    def audit_tail(self, n: int) -> list[dict[str, Any]]:
        return list(self._get(f"/audit/tail?n={n}")["records"])

    def vault_add(self, handle: str, secret: str, note: str = "") -> str:
        data = self._post("/vault/add", {"handle": handle, "secret": secret, "note": note})
        return str(data["handle"])

    def vault_list(self) -> list[dict[str, Any]]:
        return list(self._get("/vault/list")["handles"])

    def vault_revoke(self, handle: str) -> bool:
        return bool(self._post("/vault/revoke", {"handle": handle})["revoked"])

    def rest(self) -> dict[str, Any]:
        return self._post("/rest")

    def ledger_topup(self, atp: float) -> dict[str, Any]:
        return self._post("/ledger/topup", {"atp": atp})

    def ledger_confirm(self, signed: dict[str, Any]) -> dict[str, Any]:
        return self._post("/ledger/confirm", signed)

    def ledger_snapshot(self) -> dict[str, Any]:
        return self._get("/ledger")

    def dashboard_ticket(self) -> str:
        return str(self._post("/dashboard/ticket")["ticket"])


def handbrake_url() -> str:
    bind = os.environ.get("MITO_HANDBRAKE_BIND", "127.0.0.1:8710")
    return f"http://{bind}"


def connect(paths: MitoPaths, repo: Path | None = None) -> Operator:
    repo = repo or repo_root()
    tokens_path = paths.control / "tokens.json"
    if not tokens_path.exists():
        raise FileNotFoundError(f"{paths.home} is not initialized; run `mito init`")
    token = str(json.loads(tokens_path.read_text(encoding="utf-8"))["operator"])
    op = HttpOperator(handbrake_url(), token)
    if op.probe():
        return op
    return LocalOperator(Handbrake(paths, repo))


def sign_command(paths: MitoPaths, cmd: str, args: dict[str, Any]) -> dict[str, Any]:
    from handbrake.vault.keys import KeyStore

    key = KeyStore(paths.control / "operator.pub").load_private()
    return SignedCommand.build(cmd, args, key).to_dict()
