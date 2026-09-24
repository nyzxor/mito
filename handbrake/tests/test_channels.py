from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from handbrake.api.app import create_app
from handbrake.channels.mail import Mailbox, MailError
from handbrake.channels.telegram import TgUpdate, apply_update
from handbrake.core import Handbrake
from handbrake.paths import MitoPaths
from handbrake.tests.conftest import make_repo

pytestmark = pytest.mark.handbrake


def _hb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Handbrake:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    cfg = (repo / "config" / "mito.toml").read_text(encoding="utf-8")
    cfg = cfg.replace("telegram = []", 'telegram = ["42"]')
    cfg = cfg.replace("approved_recipients = []", 'approved_recipients = ["op@example.com"]')
    (repo / "config" / "mito.toml").write_text(cfg, encoding="utf-8")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    return Handbrake(paths, repo, dev_mode=True)


def test_telegram_stranger_cannot_halt_or_approve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hb = _hb(tmp_path, monkeypatch)
    nonce = hb.issue_channel_nonce("halt")
    out = apply_update(hb, TgUpdate("7", f"halt hard {nonce}"), hb.telegram_allowlist)
    assert out["ok"] is False
    assert hb.halted() is None
    assert hb.consume_channel_nonce("halt", nonce)  # unused by the stranger


def test_telegram_operator_halt_needs_nonce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hb = _hb(tmp_path, monkeypatch)
    bad = apply_update(hb, TgUpdate("42", "halt hard nope"), hb.telegram_allowlist)
    assert bad["ok"] is False and hb.halted() is None
    nonce = hb.issue_channel_nonce("halt")
    ok = apply_update(hb, TgUpdate("42", f"halt soft {nonce}"), hb.telegram_allowlist)
    assert ok["ok"] is True and hb.halted() is not None
    again = apply_update(hb, TgUpdate("42", f"halt soft {nonce}"), hb.telegram_allowlist)
    assert again["ok"] is False


def test_send_refuses_unapproved_recipient() -> None:
    box = Mailbox(approved=frozenset({"op@example.com"}))
    draft = box.draft("other@example.com", "hi", "body")
    with pytest.raises(MailError, match="not an approved"):
        box.send(draft.id)
    assert draft.sent is False
    ok = box.draft("OP@example.com", "hi", "body")
    sent = box.send(ok.id)
    assert sent.sent and sent.to == "op@example.com"


def test_read_is_untrusted_and_unconfigured_fails() -> None:
    box = Mailbox(approved=frozenset())
    with pytest.raises(MailError, match="not configured"):
        box.read()


async def test_dashboard_forms_halt_without_js(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hb = _hb(tmp_path, monkeypatch)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(hb)), base_url="http://hb")
    ticket = hb.issue_dashboard_ticket()
    page = await client.get(f"/dashboard?ticket={ticket}")
    assert page.status_code == 200 and "halt" in page.text
    assert "mito_dash" in page.headers.get("set-cookie", "")
    bare = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(hb)), base_url="http://hb"
    )
    naked = await bare.post("/dashboard/halt", data={"level": "soft", "csrf": "nope"})
    assert naked.status_code == 401
    cookie = page.headers["set-cookie"].split(";", 1)[0]
    csrf = page.text.split('name="csrf" value="', 1)[1].split('"', 1)[0]
    halted = await client.post(
        "/dashboard/halt",
        data={"level": "soft", "csrf": csrf},
        headers={"cookie": cookie},
    )
    assert halted.status_code == 200
    assert hb.halted() is not None
