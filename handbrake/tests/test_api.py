"""ADR-0002: authenticated localhost API; runtime token cannot halt/approve; operator token can."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from handbrake.api.app import create_app
from handbrake.core import Handbrake
from handbrake.paths import MitoPaths
from handbrake.tests.conftest import make_repo

pytestmark = pytest.mark.handbrake


@pytest.fixture
def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Handbrake, httpx.AsyncClient, dict[str, str]]:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    hb = Handbrake(paths, repo, dev_mode=True)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(hb)), base_url="http://hb"
    )
    return hb, client, hb.tokens()


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_unauthenticated_is_rejected(
    setup: tuple[Handbrake, httpx.AsyncClient, dict[str, str]],
) -> None:
    _, client, _ = setup
    r = await client.get("/state")
    assert r.status_code == 401
    r = await client.get("/state", headers=_h("wrong"))
    assert r.status_code == 401


async def test_runtime_can_dispatch_and_report(
    setup: tuple[Handbrake, httpx.AsyncClient, dict[str, str]],
) -> None:
    hb, client, tok = setup
    body: dict[str, Any] = {
        "session": "s",
        "tool": "time",
        "args": {},
        "taint_flags": [],
        "purpose": "t",
        "workspace": "/w",
    }
    r = await client.post("/gate/dispatch", json=body, headers=_h(tok["runtime"]))
    assert r.status_code == 200 and r.json()["kind"] == "allow"
    ticket = r.json()["ticket"]
    r = await client.post(
        "/gate/result",
        json={
            "session": "s",
            "tool": "time",
            "ticket": ticket,
            "ok": True,
            "result_hash": "h",
            "cost_atp": 0,
        },
        headers=_h(tok["runtime"]),
    )
    assert r.status_code == 200
    r = await client.post(
        "/gate/result",
        json={
            "session": "s",
            "tool": "time",
            "ticket": ticket,
            "ok": True,
            "result_hash": "h",
            "cost_atp": 0,
        },
        headers=_h(tok["runtime"]),
    )
    assert r.status_code == 403  # ticket reuse
    assert hb.audit.verify().ok


async def test_runtime_token_cannot_use_operator_endpoints(
    setup: tuple[Handbrake, httpx.AsyncClient, dict[str, str]],
) -> None:
    _, client, tok = setup
    for path, body in (
        ("/halt", {"level": "hard"}),
        ("/wake", {}),
        ("/approve", {"action_hash": "x"}),
        ("/checkin", {}),
    ):
        r = await client.post(path, json=body, headers=_h(tok["runtime"]))
        assert r.status_code == 403, path
    r = await client.get("/approvals", headers=_h(tok["runtime"]))
    assert r.status_code == 403


async def test_operator_halt_then_gate_denies(
    setup: tuple[Handbrake, httpx.AsyncClient, dict[str, str]],
) -> None:
    hb, client, tok = setup
    r = await client.post(
        "/halt", json={"level": "soft", "source": "api-test"}, headers=_h(tok["operator"])
    )
    assert r.status_code == 200
    assert hb.halted() is not None
    body = {
        "session": "s",
        "tool": "time",
        "args": {},
        "taint_flags": [],
        "purpose": "t",
        "workspace": "/w",
    }
    r = await client.post("/gate/dispatch", json=body, headers=_h(tok["runtime"]))
    assert r.json()["kind"] == "deny"
    r = await client.post("/wake", json={}, headers=_h(tok["operator"]))
    assert r.status_code == 200 and hb.halted() is None


async def test_approval_roundtrip_over_api(
    setup: tuple[Handbrake, httpx.AsyncClient, dict[str, str]],
) -> None:
    _, client, tok = setup
    body = {
        "session": "s",
        "tool": "memory.write",
        "args": {"name": "n"},
        "taint_flags": [],
        "purpose": "p",
        "workspace": "/w",
    }
    r = await client.post("/gate/dispatch", json=body, headers=_h(tok["runtime"]))
    assert r.json()["kind"] == "ask"
    h = r.json()["action_hash"]
    r = await client.get("/approvals", headers=_h(tok["operator"]))
    assert [c["action_hash"] for c in r.json()["pending"]] == [h]
    r = await client.post("/approve", json={"action_hash": h}, headers=_h(tok["operator"]))
    assert r.json()["status"] == "approved"
    r = await client.post("/gate/dispatch", json=body, headers=_h(tok["runtime"]))
    assert r.json()["kind"] == "allow"


async def test_model_preflight_budget_error_is_402(
    setup: tuple[Handbrake, httpx.AsyncClient, dict[str, str]],
) -> None:
    _, client, tok = setup
    r = await client.post(
        "/model/preflight",
        json={"session": "s", "task": "t", "tier": "L2", "est_usd": 5.0, "model_id": "m"},
        headers=_h(tok["runtime"]),
    )
    assert r.status_code == 402 and r.json()["dimension"] == "per_call"
    r = await client.post(
        "/model/preflight",
        json={"session": "s", "task": "t", "tier": "L1", "est_usd": 0.001, "model_id": "m"},
        headers=_h(tok["runtime"]),
    )
    assert r.status_code == 200 and r.json()["meter_id"]
    r = await client.post(
        "/model/reconcile",
        json={"meter_id": r.json()["meter_id"], "actual_usd": 0.0005, "usage": {}},
        headers=_h(tok["runtime"]),
    )
    assert r.status_code == 200


async def test_runtime_audit_append_is_namespaced(
    setup: tuple[Handbrake, httpx.AsyncClient, dict[str, str]],
) -> None:
    hb, client, tok = setup
    r = await client.post(
        "/audit/append", json={"kind": "step", "payload": {"n": 1}}, headers=_h(tok["runtime"])
    )
    assert r.status_code == 200
    assert hb.audit_tail(1)[0].kind == "runtime.step"


async def test_unknown_route_and_bad_json(
    setup: tuple[Handbrake, httpx.AsyncClient, dict[str, str]],
) -> None:
    _, client, tok = setup
    r = await client.get("/nope", headers=_h(tok["runtime"]))
    assert r.status_code == 404
    r = await client.post(
        "/gate/dispatch",
        content=b"{not json",
        headers={**_h(tok["runtime"]), "content-type": "application/json"},
    )
    assert r.status_code == 400
