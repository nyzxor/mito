"""Telegram operator channel (DESIGN §5.1, §5.8). Allowlisted IDs only. No model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from handbrake.core import Handbrake
from handbrake.kill.switch import HaltLevel


class ChannelError(ValueError):
    pass


@dataclass(frozen=True)
class TgUpdate:
    user_id: str
    text: str


def parse_update(raw: dict[str, Any]) -> TgUpdate | None:
    msg = raw.get("message") or raw.get("edited_message") or {}
    user = (msg.get("from") or {}).get("id")
    text = msg.get("text")
    if user is None or not isinstance(text, str):
        return None
    return TgUpdate(str(user), text.strip())


def apply_update(hb: Handbrake, update: TgUpdate, allowlist: frozenset[str]) -> dict[str, Any]:
    """Halt/approve never consult the model. Unknown users are ignored and audited."""
    if update.user_id not in allowlist:
        hb.audit.append(
            "channel.rejected",
            {"channel": "telegram", "user": update.user_id, "chars": len(update.text)},
        )
        return {"ok": False, "reason": "not an allowlisted operator"}
    parts = update.text.split()
    if not parts:
        return {"ok": False, "reason": "empty"}
    cmd = parts[0].lower().removeprefix("/")
    if cmd == "halt":
        if len(parts) != 3 or parts[1] not in ("soft", "hard", "panic"):
            return {"ok": False, "reason": "usage: halt <soft|hard|panic> <nonce>"}
        if not hb.consume_channel_nonce("halt", parts[2]):
            hb.audit.append("channel.halt_rejected", {"user": update.user_id, "reason": "nonce"})
            return {"ok": False, "reason": "halt nonce missing or already used"}
        hb.halt(HaltLevel(parts[1]), source=f"telegram:{update.user_id}")
        return {"ok": True, "action": "halt", "level": parts[1]}
    if cmd in ("approve", "deny"):
        if len(parts) != 2:
            return {"ok": False, "reason": f"usage: {cmd} <action-hash-prefix>"}
        matches = [
            c.action_hash
            for c in hb.pending_approvals()
            if c.action_hash.startswith(parts[1])
        ]
        if len(matches) != 1:
            return {"ok": False, "reason": f"{len(matches)} approvals match {parts[1]!r}"}
        who = f"telegram:{update.user_id}"
        status = hb.approve(matches[0], by=who) if cmd == "approve" else hb.deny(matches[0], by=who)
        hb.checkin(who)
        return {"ok": status in ("approved", "denied"), "action": cmd, "status": status}
    if cmd == "checkin":
        hb.checkin(f"telegram:{update.user_id}")
        return {"ok": True, "action": "checkin"}
    if cmd == "status":
        st = hb.state()
        return {
            "ok": True,
            "action": "status",
            "text": (
                f"state={st['metabolic_state']} autonomy={st['autonomy']} "
                f"halt={st['halt']['level'] if st['halt'] else '-'} "
                f"atp={st['atp']['balance']:.0f}"
            ),
        }
    return {"ok": False, "reason": f"unknown command {cmd!r}"}
