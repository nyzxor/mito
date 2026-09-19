# ADR-0006: Sandbox — hardened Docker, fail closed without Docker

Status: accepted · Date: 2026-09-18

## Context

T1 tools (`code.run`, `shell`, `git.local`, `data.query`, `pdf/ocr`, image transforms) execute
untrusted code produced by the model. OWASP ASI05 and the guide (Ch 13 "physics") require OS-
level isolation under the policy layer. Hermes offers seven sandbox backends; we need exactly one
that works on all three dev OSes and on the VPS.

## Options

1. Docker (Linux containers; Docker Desktop on Windows) with hardened flags.
2. Subprocess with restricted user (no real isolation on Windows; rejected).
3. gVisor/Firecracker (stronger, Linux-only, heavier ops; possible later for the VPS).
4. WASM sandboxes (limited language support for the data/pdf tools).

## Decision

Option 1. One image `deploy/sandbox/Dockerfile` (python + git + poppler/tesseract + duckdb +
pandas + playwright browsers in a separate image). Run flags (all asserted by a test that
inspects the created container):

```
--network none                      (browser image: internal network only, proxy env set)
--user 65532:65532  --read-only  --tmpfs /tmp:size=256m  --tmpfs /work:size=512m
--cap-drop ALL  --security-opt no-new-privileges  --security-opt seccomp=default
--pids-limit 256  --memory 1g  --cpus 1  --ulimit nofile=1024
--label mito.session=<id>  --label mito.run=<id>
-v <workspace>/<run>:/workspace:rw   (only this bind mount; never the repo, never control/)
no env passthrough; no docker.sock; no host network; wall-clock kill by the Handbrake
```

If the Docker daemon is unavailable, **every T1 code-executing tool fails closed** with an
error-as-instruction ("sandbox unavailable; ask the operator") and the event is audited. There
is no host fallback. Bare-Windows DEV MODE still uses Docker Desktop for T1; without it, T1 is
simply off.

## Consequences

- Docker becomes a hard requirement for anything beyond T0/T2.
- Startup latency per run (~300–800 ms); mitigated by a warm container per session, killed on
  session end or `hard` halt.
- The Handbrake owns the Docker client (kill path); the runtime requests runs via the gate.

## Dependency cost

`docker` Python SDK vs shelling out to the `docker` CLI: we shell out via `subprocess` from the
**Handbrake only** (the AST wiring test forbids `subprocess` in `mito/`). No SDK dependency.
