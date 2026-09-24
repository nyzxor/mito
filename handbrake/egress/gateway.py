"""Python egress gateway (ADR-0005 policy layer). Tools never open sockets themselves."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx

from handbrake.egress.ssrf import EgressDenied, Pin, inspect
from handbrake.vault.store import VaultStore

USER_AGENT_TEMPLATE = "MITO/{version} (+{contact})"


class EgressClosed(RuntimeError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip = False
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 — broken HTML still becomes text
        return html
    return " ".join(parser.parts)


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucket:
    def __init__(self, *, interval_s: float, burst: int, clock: Callable[[], float]) -> None:
        self.interval_s = interval_s
        self.burst = float(burst)
        self._clock = clock
        self._buckets: dict[str, _Bucket] = defaultdict(lambda: _Bucket(float(burst), clock()))

    def take(self, host: str) -> bool:
        now = self._clock()
        b = self._buckets[host]
        elapsed = max(0.0, now - b.updated)
        b.tokens = min(self.burst, b.tokens + elapsed / self.interval_s)
        b.updated = now
        if b.tokens < 1.0:
            return False
        b.tokens -= 1.0
        return True


@dataclass
class EgressGateway:
    vault: VaultStore
    denylist: frozenset[str]
    write_allowlist: frozenset[str]
    user_agent: str
    max_response_bytes: int = 5_000_000
    clock: Callable[[], float] = time.time
    resolve: Callable[[str], list[str]] | None = None
    transport: httpx.BaseTransport | None = None
    _closed: bool = False
    _buckets: TokenBucket | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._buckets = TokenBucket(interval_s=2.0, burst=3, clock=self.clock)

    def close(self) -> None:
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def inspect(self, method: str, url: str) -> Pin:
        leak = self.vault.url_leaks_secret(url)
        if leak:
            raise EgressDenied(f"url contains live secret {leak}")
        kwargs: dict[str, Any] = {
            "denylist": self.denylist,
            "write_allowlist": self.write_allowlist,
        }
        if self.resolve is not None:
            kwargs["resolve"] = self.resolve
        return inspect(method, url, **kwargs)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: str | None = None,
        purpose: str = "",
        credential_handle: str | None = None,
        readability: bool = False,
    ) -> dict[str, Any]:
        if self._closed:
            raise EgressClosed("egress closed (panic); no outbound requests")
        pin = self.inspect(method, url)
        buckets = self._buckets
        if buckets is None:
            raise EgressClosed("egress not initialized")
        if not buckets.take(pin.host):
            raise EgressDenied(f"rate limited for {pin.host}; wait and retry")
        hdrs = {"User-Agent": self.user_agent, "Accept": "text/*,application/json,application/xml"}
        if headers:
            hdrs.update({k: v for k, v in headers.items() if k.lower() != "authorization"})
        if credential_handle:
            secret = self.vault.resolve(credential_handle)
            hdrs["Authorization"] = f"Bearer {secret}"
        try:
            with httpx.Client(timeout=15.0, transport=self.transport, follow_redirects=False) as c:
                resp = c.request(method, pin.url, headers=hdrs, content=body)
        except httpx.HTTPError as exc:
            raise EgressDenied(f"egress request failed: {exc}") from exc
        raw = resp.content[: self.max_response_bytes]
        text = raw.decode(resp.encoding or "utf-8", errors="replace")
        text = self.vault.scrub(text)
        ctype = resp.headers.get("content-type", "")
        if readability and "html" in ctype:
            text = html_to_text(text)
        return {
            "url": f"{pin.scheme}://{pin.host}{pin.path}",
            "status": resp.status_code,
            "content_type": ctype,
            "body": text,
            "purpose": purpose,
            "taint": "UNTRUSTED",
        }


def load_egress_policy(data: dict[str, Any], *, version: str, contact: str) -> dict[str, Any]:
    reads = data.get("reads", {})
    ua = str(reads.get("user_agent", USER_AGENT_TEMPLATE))
    ua = ua.replace("{version}", version).replace("{operator_contact_url}", contact or "local")
    deny = frozenset(str(h).lower() for h in data.get("denylist", {}).get("hosts", []))
    allow = frozenset(str(e.get("host", "")).lower() for e in data.get("allow_write", []) if e)
    return {
        "denylist": deny,
        "write_allowlist": allow,
        "user_agent": ua,
        "max_response_bytes": int(reads.get("max_response_bytes", 5_000_000)),
    }


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()
