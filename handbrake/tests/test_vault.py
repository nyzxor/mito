"""Vault/broker: secrets stay inside the Handbrake; panic revokes; scrub is exact-match."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from handbrake.api.app import create_app
from handbrake.core import Handbrake
from handbrake.kill.switch import HaltLevel
from handbrake.paths import MitoPaths
from handbrake.tests.conftest import make_repo
from handbrake.vault.store import VaultError, normalize_handle

pytestmark = pytest.mark.handbrake


def _hb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Handbrake:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    monkeypatch.setenv("MITO_VAULT_KEY_FILE", str(tmp_path / "vault.key"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    return Handbrake(paths, repo, dev_mode=True)


def test_normalize_handle() -> None:
    assert normalize_handle("github-readonly") == "cred:github-readonly"
    assert normalize_handle("cred:x") == "cred:x"
    with pytest.raises(VaultError):
        normalize_handle("cred:../etc")


def test_add_resolve_scrub_and_never_list_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hb = _hb(tmp_path, monkeypatch)
    name = hb.vault.add("github-readonly", "ghp_supersecret", note="ro")
    assert name == "cred:github-readonly"
    assert hb.vault.resolve(name) == "ghp_supersecret"
    listed = [h.to_dict() for h in hb.vault.list_handles()]
    assert listed == [{"handle": name, "note": "ro", "revoked": False}]
    assert "ghp_supersecret" not in str(listed)
    assert (
        hb.vault.scrub("token=ghp_supersecret&x=1") == "token=[REDACTED:cred:github-readonly]&x=1"
    )
    assert hb.vault.url_leaks_secret("https://ex/?k=ghp_supersecret") == name


def test_revoked_handle_cannot_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hb = _hb(tmp_path, monkeypatch)
    hb.vault.add("x", "s3cret")
    assert hb.vault.revoke("cred:x")
    with pytest.raises(VaultError, match="revoked"):
        hb.vault.resolve("x")


def test_panic_revokes_vault_handles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hb = _hb(tmp_path, monkeypatch)
    hb.vault.add("a", "alpha")
    hb.vault.add("b", "beta")
    hb.halt(HaltLevel.PANIC, source="test")
    with pytest.raises(VaultError):
        hb.vault.resolve("a")
    assert all(h.revoked for h in hb.vault.list_handles())


async def test_runtime_token_cannot_read_or_add_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hb = _hb(tmp_path, monkeypatch)
    hb.vault.add("keep", "dont-leak-me")
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(hb)), base_url="http://hb"
    )
    rt = {"Authorization": f"Bearer {hb.tokens()['runtime']}"}
    op = {"Authorization": f"Bearer {hb.tokens()['operator']}"}
    r = await client.get("/vault/list", headers=rt)
    assert r.status_code == 403
    r = await client.post("/vault/add", headers=rt, json={"handle": "x", "secret": "y"})
    assert r.status_code == 403
    r = await client.get("/vault/list", headers=op)
    assert r.status_code == 200
    body = r.json()
    assert "dont-leak-me" not in r.text
    assert body["handles"][0]["handle"] == "cred:keep"
    await client.aclose()
