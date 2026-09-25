"""L0 playbook runner. No model import. Disabled playbooks refuse."""

from __future__ import annotations

from typing import Any

from mito.playbooks.load import Playbook, PlaybookError

_NO_EVOLUTION = frozenset({"FRUGAL", "STARVING", "DEEP_REST"})


def run_l0(book: Playbook, *, state: str, daily_burn_atp: float) -> dict[str, Any]:
    if not book.enabled:
        raise PlaybookError(f"{book.name} is disabled; the operator enables it in playbooks/")
    if book.autonomy_max not in ("A0", "A1"):
        raise PlaybookError(f"{book.name} autonomy_max {book.autonomy_max} is above v1 (A1)")
    if state in _NO_EVOLUTION and book.name != "cost-optimizer":
        return {"action": "skip", "reason": f"{state} blocks income playbooks", "llm": False}
    if book.name == "cost-optimizer":
        if daily_burn_atp <= 0:
            return {"action": "skip", "reason": "no measured burn", "llm": False}
        return {
            "action": "notice",
            "reason": (
                f"median burn {daily_burn_atp:.0f} ATP/day; savings are a claim until confirmed"
            ),
            "llm": False,
        }
    return {
        "action": "skip",
        "reason": f"{book.name} has no L0 body yet",
        "llm": False,
    }
