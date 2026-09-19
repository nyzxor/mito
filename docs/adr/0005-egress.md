# ADR-0005: Egress — gateway API + CONNECT proxy, Python first, Rust deferred

Status: proposed (needs operator go on "Python first") · Date: 2026-09-18

## Context

All network must go through the Handbrake: SSRF defense (RFC1918, 169.254.0.0/16, loopback,
metadata endpoints, DNS rebinding), denylist, robots.txt, per-domain rate limits, honest
User-Agent, write allowlist, secret scrubbing. The sandbox has no network. The headless browser
needs real HTTPS to arbitrary public sites.

## Options

1. **Transparent MITM proxy** (mitmproxy-style): full URL-level policy for everything, but TLS
   interception, CA management in every container, and a large dependency.
2. **Egress gateway API** (tools send `{method,url,…}`; Handbrake performs the request) for all
   HTTP tools **+ a plain CONNECT proxy** (host-level allow/deny + SSRF check, no interception)
   for the browser container only.
3. Same as 2 but the CONNECT proxy written in Rust as a tiny separate binary.

## Decision

Option 2, in Python, inside the Handbrake process (async, `httpx` for the gateway; a small
asyncio CONNECT proxy for the browser). Rationale: traffic volume is tiny (a personal agent with
rate limits), the policy code must live next to the ledger/audit anyway, and one language keeps
the hash-pinned surface reviewable. Rust (option 3) is kept as a documented upgrade path if we
measure a need (throughput, or a desire to run the proxy as a hardened separate binary with no
Python runtime).

Rules implemented in the gateway (all tested):

- scheme ∈ {http, https}; resolve DNS **once**, reject if any resolved IP is non-public, then
  connect to the resolved IP (pin) with SNI/Host set to the name; re-check on every redirect
  (max 5); refuse `Location` to non-public IPs.
- denylist from `policy/egress.toml`; write methods only to `[[allow_write]]` entries; tier
  rules from the gate.
- robots.txt fetched and cached per host (respect `Disallow` for our UA and `*`); per-host
  token bucket (default 1 req/2 s, burst 3); global concurrency cap.
- `User-Agent: MITO/<version> (+<operator-contact-url>)`.
- response: size cap, content-type allowlist (html, text, json, xml, rss, pdf for `pdf.extract`),
  secret scrub, tag UNTRUSTED, readability → markdown for `web.fetch`.
- request: reject URLs whose query/path contains any live secret or a high-entropy token
  matching known key shapes (exfil defense).

Browser container: `HTTP(S)_PROXY=http://handbrake:<proxy-port>`, `--network` = internal
compose network with no default route; the proxy enforces host allow/deny + SSRF, refuses
non-443/80 ports, and logs every CONNECT to the audit chain. Form submission is disabled in the
`browser.*` tool (navigate/extract only).

## Consequences

- No TLS interception, no CA distribution.
- Browser gets host-level policy only (accepted residual risk, THREAT_MODEL §7.2).
- Two ports on the Handbrake (API, proxy), both internal.

## Dependency cost

`httpx` (existing). Readability: `trafilatura` or `readability-lxml` — decided in Phase 2 by
measuring output tokens on a fixed page set; both are optional extras.
