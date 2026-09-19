# deploy/

Phase 7 deliverables (stubs now):

- `docker-compose.yaml` — Handbrake and runtime in separate containers and OS users on an
  internal network; no public ports; control surfaces bound to `127.0.0.1` (Cloudflare Tunnel or
  SSH for remote access).
- `docker-compose.dev.yaml` — same shape for local Linux dev.
- `sandbox/Dockerfile` — hardened T1 image (python, git, poppler, tesseract, duckdb, pandas).
- `sandbox/Dockerfile.browser` — Playwright image; egress only via the Handbrake CONNECT proxy.

Run flags and hardening are specified in ADR-0006 and asserted by tests.
