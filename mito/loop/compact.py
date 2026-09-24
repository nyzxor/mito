"""Tool-result compaction (guide impl-01 §2). Hard cap per tool, explicit `truncated` marker,
and a `more` handle the model can page through instead of re-running the tool."""

from __future__ import annotations

import json
import uuid
from typing import Any


def approx_tokens(s: str) -> int:
    return len(s) // 4


def clip(s: str, max_chars: int) -> str:
    return (
        s if len(s) <= max_chars else s[:max_chars] + f"...[truncated {len(s) - max_chars} chars]"
    )


def compact_value(v: Any, depth: int = 0) -> Any:
    if isinstance(v, str):
        return clip(v, 800)
    if isinstance(v, list):
        head = [compact_value(x, depth + 1) for x in v[:5]]
        return head if len(v) <= 5 else [*head, f"...and {len(v) - 5} more items"]
    if isinstance(v, dict):
        return {k: compact_value(x, depth + 1) for k, x in list(v.items())[:40]}
    return v


class MoreStore:
    """Holds full results behind handles for `result.more`. Bounded, in-memory, per runtime."""

    def __init__(self, max_items: int = 64) -> None:
        self._items: dict[str, str] = {}
        self._max = max_items

    def put(self, text: str) -> str:
        handle = f"more-{uuid.uuid4().hex[:8]}"
        if len(self._items) >= self._max:
            del self._items[next(iter(self._items))]
        self._items[handle] = text
        return handle

    def page(self, handle: str, offset: int, max_tokens: int) -> dict[str, Any]:
        text = self._items.get(handle)
        if text is None:
            return {"error": f"unknown handle {handle!r}; handles expire when the runtime restarts"}
        chunk = text[offset : offset + max_tokens * 4]
        nxt = offset + len(chunk)
        return {
            "handle": handle,
            "offset": offset,
            "content": chunk,
            "next_offset": nxt if nxt < len(text) else None,
            "total_chars": len(text),
        }


def compact_result(result: Any, max_tokens: int, more: MoreStore | None = None) -> str:
    """Serialized result never exceeds `max_tokens`. Any lossy step (structural compaction or the
    hard cap) is marked `truncated` and, when a MoreStore is given, paged via `more`."""
    full = json.dumps(result, default=str, ensure_ascii=False)
    compacted = compact_value(result)
    s = json.dumps(compacted, default=str, ensure_ascii=False)
    lossy = s != full or approx_tokens(s) > max_tokens
    if not lossy:
        return s
    out: dict[str, Any] = {
        "truncated": True,
        "hint": "narrow the arguments or page with result.more",
        "result": compacted,
    }
    if more is not None:
        out["more"] = more.put(full)
    s2 = json.dumps(out, default=str, ensure_ascii=False)
    if approx_tokens(s2) > max_tokens:
        out["result"] = clip(s, max(80, max_tokens * 4 - 240))
        s2 = json.dumps(out, default=str, ensure_ascii=False)
    return s2
