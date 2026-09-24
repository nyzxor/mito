"""SSRF, denylist, secret-in-URL, rate limit, panic-closes egress (ADR-0005)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from handbrake.core import DispatchTicket, GateRequest, Handbrake
from handbrake.egress.ssrf import EgressDenied, inspect, is_blocked_ip
from handbrake.kill.switch import HaltLevel
from handbrake.paths import MitoPaths
from handbrake.tests.conftest import make_repo

pytestmark = pytest.mark.handbrake


def test_ssrf_blocks_rfc1918_and_metadata() -> None:
    for ip in ("10.0.0.8", "192.168.1.1", "127.0.0.1", "169.254.169.254", "::1"):
        assert is_blocked_ip(ip)
    with pytest.raises(EgressDenied, match="SSRF"):
        inspect("GET", "https://evil.example/", resolve=lambda _h: ["169.254.169.254"])
    with pytest.raises(EgressDenied, match="denylist"):
        inspect("GET", "http://localhost/x", resolve=lambda _h: ["1.2.3.4"])
    with pytest.raises(EgressDenied, match="allowlisted"):
        inspect("POST", "https://evil.example/x", resolve=lambda _h: ["1.2.3.4"])
    pin = inspect(
        "POST",
        "https://api.github.com/x",
        resolve=lambda _h: ["1.2.3.4"],
        write_allowlist=frozenset({"api.github.com"}),
    )
    assert pin.host == "api.github.com" and pin.ips == ("1.2.3.4",)


def test_public_https_get_is_pinned() -> None:
    pin = inspect("GET", "https://example.org/a?q=1", resolve=lambda _h: ["93.184.216.34"])
    assert pin.scheme == "https" and pin.port == 443 and pin.ips == ("93.184.216.34",)


def _hb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Handbrake:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    monkeypatch.setenv("MITO_VAULT_KEY_FILE", str(tmp_path / "vault.key"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    return Handbrake(paths, repo, dev_mode=True)


def test_egress_scrubs_live_secrets_and_rejects_secret_in_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hb = _hb(tmp_path, monkeypatch)
    hb.vault.add("tok", "sekrit-token-xyz")
    hb.egress.resolve = lambda _h: ["1.2.3.4"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="got sekrit-token-xyz back")

    hb.egress.transport = httpx.MockTransport(handler)
    ticket = hb.gate_dispatch(
        GateRequest("s", "web.fetch", {"url": "https://example.org/"}, [], "t", "/w")
    ).ticket
    assert ticket is not None
    out = hb.egress_request(ticket, "GET", "https://example.org/", readability=False)
    assert "sekrit-token-xyz" not in out["body"]
    assert "[REDACTED:cred:tok]" in out["body"]
    with pytest.raises(EgressDenied, match="live secret"):
        hb.egress.inspect("GET", "https://example.org/?k=sekrit-token-xyz")


def test_panic_closes_egress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hb = _hb(tmp_path, monkeypatch)
    hb.halt(HaltLevel.PANIC, source="test")
    fake = DispatchTicket("t", "h", "web.fetch", "s", 9e12, ())
    with pytest.raises(Exception, match="closed|invalid"):
        hb.egress_request(fake, "GET", "https://example.org/")


def test_write_allowlist_loaded_from_egress_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    monkeypatch.setenv("MITO_VAULT_KEY_FILE", str(tmp_path / "vault.key"))
    repo = make_repo(tmp_path / "repo")
    (repo / "policy" / "egress.toml").write_text(
        (repo / "policy" / "egress.toml").read_text(encoding="utf-8")
        + '\n[[allow_write]]\nhost = "api.github.com"\nmethods = ["POST"]\n',
        encoding="utf-8",
    )
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    hb = Handbrake(paths, repo, dev_mode=True)
    assert "api.github.com" in hb.write_allowlist
    r = hb.gate_dispatch(
        GateRequest("s", "http.post", {"url": "https://api.github.com/x"}, [], "p", "/w")
    )
    # T3 at A1 (dev) still asks; the deny-for-unlisted-host must not fire
    assert r.kind in ("ask", "allow")
    denied = hb.gate_dispatch(
        GateRequest("s", "http.post", {"url": "https://evil.example/x"}, [], "p", "/w")
    )
    assert denied.kind == "deny"
