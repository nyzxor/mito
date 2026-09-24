"""Dedicated mailbox (DESIGN §8). Drafts stay local. Send only to approved recipients."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


class MailError(ValueError):
    pass


@dataclass
class MailMessage:
    id: str
    sender: str
    subject: str
    body: str
    unseen: bool = True

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "id": self.id,
            "from": self.sender,
            "subject": self.subject,
            "body": self.body,
            "unseen": self.unseen,
            "taint": "UNTRUSTED",
        }


@dataclass
class Draft:
    id: str
    to: str
    subject: str
    body: str
    sent: bool = False


@dataclass
class Mailbox:
    """In-process mailbox. Live IMAP waits for cred:mailbox; absence fails closed."""

    approved: frozenset[str]
    inbox: list[MailMessage] = field(default_factory=list)
    drafts: dict[str, Draft] = field(default_factory=dict)
    sent: list[str] = field(default_factory=list)
    configured: bool = False

    def unseen_count(self) -> int:
        return sum(1 for m in self.inbox if m.unseen)

    def read(self, *, limit: int = 5) -> list[dict[str, str | bool]]:
        if not self.configured and not self.inbox:
            raise MailError("mailbox not configured; ask the operator to vault-add cred:mailbox")
        out: list[dict[str, str | bool]] = []
        for msg in self.inbox:
            if not msg.unseen:
                continue
            msg.unseen = False
            out.append(msg.to_dict())
            if len(out) >= limit:
                break
        return out

    def draft(self, to: str, subject: str, body: str) -> Draft:
        item = Draft(id=uuid.uuid4().hex[:12], to=to.strip().lower(), subject=subject, body=body)
        self.drafts[item.id] = item
        return item

    def send(self, draft_id: str) -> Draft:
        item = self.drafts.get(draft_id)
        if item is None or item.sent:
            raise MailError(f"unknown draft {draft_id}")
        if item.to not in self.approved:
            raise MailError(
                f"{item.to} is not an approved recipient; operator must edit channels.email"
            )
        item.sent = True
        self.sent.append(item.id)
        return item
