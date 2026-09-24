"""Resolve-then-check SSRF defense (ADR-0005). Pin the resolved public IPs; never connect first."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

Resolver = Callable[[str], list[str]]

BLOCK_CIDRS = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "224.0.0.0/4",
    "240.0.0.0/4",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
    "::ffff:0:0/96",
)
BLOCK_HOSTS = frozenset({"localhost", "metadata.google.internal", "169.254.169.254"})
SCHEMES = frozenset({"http", "https"})


class EgressDenied(ValueError):
    pass


@dataclass(frozen=True)
class Pin:
    method: str
    scheme: str
    host: str
    port: int
    path: str
    ips: tuple[str, ...]
    url: str


def _default_resolve(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    out: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in out:
            out.append(str(addr))
    return out


def is_blocked_ip(ip: str, extra_cidrs: tuple[str, ...] = ()) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_multicast:
        return True
    if addr.is_reserved or addr.is_unspecified:
        return True
    for cidr in (*BLOCK_CIDRS, *extra_cidrs):
        if addr in ipaddress.ip_network(cidr, strict=False):
            return True
    return False


def inspect(
    method: str,
    url: str,
    *,
    resolve: Resolver = _default_resolve,
    denylist: frozenset[str] = frozenset(),
    write_allowlist: frozenset[str] = frozenset(),
    extra_cidrs: tuple[str, ...] = (),
) -> Pin:
    verb = method.upper()
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if scheme not in SCHEMES:
        raise EgressDenied(f"scheme {scheme!r} is not http/https")
    if not host:
        raise EgressDenied("url has no host")
    if host in BLOCK_HOSTS or host in denylist:
        raise EgressDenied(f"host {host} is denylisted")
    if verb not in {"GET", "HEAD"} and host not in write_allowlist:
        raise EgressDenied(f"writes only to allowlisted hosts; {host} is not")
    try:
        ips = resolve(host)
    except OSError as exc:
        raise EgressDenied(f"dns failed for {host}: {exc}") from exc
    if not ips:
        raise EgressDenied(f"dns returned no addresses for {host}")
    blocked = [ip for ip in ips if is_blocked_ip(ip, extra_cidrs)]
    if blocked:
        raise EgressDenied(f"SSRF: {host} resolved to non-public {blocked}")
    port = parts.port or (443 if scheme == "https" else 80)
    if port not in (80, 443):
        raise EgressDenied(f"port {port} is not 80 or 443")
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return Pin(verb, scheme, host, port, path, tuple(ips), url)
