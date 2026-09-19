# ADR-0005: Egress — Python gateway API (policy) + Rust CONNECT proxy (physics)

Status: accepted (revised 2026-09-19 after operator input: "Rust is fine, prefer hybrid") · Date: 2026-09-18

## Context

All network must go through the Handbrake: SSRF defense (RFC1918, 169.254.0.0/16, loopback,
metadata endpoints, DNS rebinding), denylist, robots.txt, per-domain rate limits, honest
User-Agent, write allowlist, secret scrubbing. The sandbox has no network. The headless browser
needs real HTTPS to arbitrary public sites. The operator writes Rust and is willing to use it
where it is clearly justified.

## Options

1. **Transparent MITM proxy** (mitmproxy-style): full URL-level policy for everything, but TLS
   interception, CA management in every container, and a large dependency.
2. **All Python**: egress gateway API + asyncio CONNECT proxy inside the Handbrake process.
3. **Hybrid**: egress gateway API in Python (inside the Handbrake, next to policy/ledger/audit)
   **+ a tiny Rust CONNECT proxy** as a separate hardened binary/container that is the *only*
   thing with a default route out of the internal network.

## Decision

Option 3 — the split follows the guide's advice → policy → physics layering:

**Python egress gateway** (`handbrake/egress/`, policy layer): tools such as `web.fetch`,
`http.get`, `rss.read`, `github.read`, `email.*` send `{method, url, headers, body, purpose,
session, credential_handle?}`; the gateway applies policy and performs the request:

- scheme ∈ {http, https}; resolve DNS **once**, reject if any resolved IP is non-public, connect
  to the pinned IP with SNI/Host set to the name; re-check on every redirect (max 5); refuse
  `Location` to non-public IPs.
- denylist from `policy/egress.toml`; write methods only to `[[allow_write]]`; tier rules from
  the gate; robots.txt cached per host; per-host token bucket (default 1 req/2 s, burst 3);
  global concurrency cap; `User-Agent: MITO/<version> (+<operator-contact-url>)`.
- request: reject URLs whose path/query contains any live secret or a high-entropy token shaped
  like a known key (exfil defense); credential handles resolved by the broker here, never before.
- response: size cap, content-type allowlist, secret scrub, tag UNTRUSTED, readability → markdown
  for `web.fetch`, truncation with a `more` handle.

**Rust CONNECT proxy** (`egress-proxy/`, physics layer; Phase 2): a single static binary
(tokio + hyper, no TLS interception) that:

- accepts only `CONNECT host:443|80` and plain `GET/HEAD` over http from the internal network;
- resolves once, rejects non-public IPs, pins the resolved IP (SSRF/DNS-rebinding);
- enforces a host allow/deny list and per-host rate limits loaded from a **read-only** file the
  Handbrake writes (`control/egress-proxy.toml`, hash-logged), plus a `HALT` sentinel check
  (panic ⇒ refuse everything within 250 ms);
- logs every CONNECT (`ts, session, host, port, bytes, verdict`) to a socket the Handbrake
  ingests into the audit chain;
- runs as non-root with no filesystem access beyond its config; is the only container on the
  compose network with an external default route.

Consumers of the Rust proxy: the browser container (`HTTPS_PROXY`), and — in the compose
deployment — the Handbrake's own outbound `httpx` client (defense in depth: even a bug in the
Python policy cannot reach a private range because the proxy re-checks). In bare-Windows DEV
MODE the Handbrake may reach out directly if the proxy binary is absent, and `mito status`
shows `egress_proxy: missing` in the DEV MODE warning.

Why Rust for this piece: it is the last hop before the internet, must hold under a compromised
runtime *and* a buggy Handbrake, has a tiny surface (~500 lines), benefits from a static binary
with no interpreter, and is a natural fit for the operator's skills. Why **not** Rust for the
gateway: robots.txt, readability, scrubbing, tiering and ledger posting are policy code that
changes with the product and must live next to the Handbrake's other Python.

## Consequences

- Two languages in the repo; a Cargo workspace under `egress-proxy/` with its own tests, built
  in CI on Linux and Windows (`just proxy-build`); the wheel does not depend on it (DEV MODE
  degrades gracefully with a warning).
- No TLS interception, no CA distribution. Browser gets host-level policy only (accepted,
  THREAT_MODEL §7.2).
- Two ports on the Handbrake side (gateway API, proxy), both internal.

## Dependency cost

Python: `httpx` (existing); readability library chosen in Phase 2 by measurement
(`trafilatura` vs `readability-lxml`, optional extra). Rust: `tokio`, `hyper`, `hyper-util`,
`serde`/`toml`, `tracing` — all standard, audited with `cargo audit` in CI.
