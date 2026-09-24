# mito-egress-proxy

Physics-layer CONNECT proxy (ADR-0005). The only binary that should have a default
route on the compose network. No TLS interception.

```
just proxy-build
just proxy-test
```

Env: `MITO_EGRESS_BIND` (default `127.0.0.1:18790`), `MITO_EGRESS_POLICY` (Handbrake-written
`control/egress-proxy.toml`), `MITO_HALT` (`control/HALT`). Panic / any HALT file ⇒ refuse
every CONNECT.

DEV MODE: if this binary is missing the Python gateway may dial the public internet
directly after its own SSRF check. `mito status` then shows `egress_proxy: missing`.
