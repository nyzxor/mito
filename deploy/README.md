# deploy/

Phase 7. Host publish is `127.0.0.1` only. `internal` has no external route.
`egress-proxy` is the only service on the `egress` network. No `docker.sock`.
User `65532`. The runtime token is not stored in this file.

```
docker compose -f deploy/docker-compose.yaml build
docker compose -f deploy/docker-compose.yaml up
```

Dev: `deploy/docker-compose.dev.yaml`. Windows/Arch/Debian bare metal stay DEV MODE
(`uv run mito up`). Compose is the production posture.

- `docker-compose.yaml` — Handbrake and runtime in separate containers and OS users on an
  internal network; no public ports; control surfaces bound to `127.0.0.1` (Cloudflare Tunnel or
  SSH for remote access).
- `docker-compose.dev.yaml` — same shape for local Linux dev.
- `sandbox/Dockerfile` — hardened T1 image (python, git, poppler, tesseract, duckdb, pandas).
- `sandbox/Dockerfile.browser` — Playwright image; egress only via the Handbrake CONNECT proxy.

Run flags and hardening are specified in ADR-0006 and asserted by tests.
