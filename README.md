# MITO

A self-hosted, cost-frugal, self-improving autonomous agent harness that pays for its own compute
(its *metabolism*), looks for legal ways to earn money, and is structurally incapable of removing
its own brakes.

Status: **Phase 0 — design only.** No product code yet. Read, in order:

1. [`CLAUDE.md`](CLAUDE.md) — non-negotiables, commands, layout, definition of done.
2. [`docs/DESIGN.md`](docs/DESIGN.md) — architecture and every subsystem.
3. [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) — STRIDE, OWASP Agentic Top 10, lethal trifecta.
4. [`docs/adr/`](docs/adr/README.md) — one file per stack decision.

## Prime Directives (short form)

1. Human control  2. Law / ToS / others' rights  3. Handbrake policy & budgets  4. Honesty
5. Task success  6. Profit  7. Survival — lowest. When in doubt: Deep Rest and ask.

## Quick start (dev)

```
# PowerShell / fish / bash — identical
uv sync --all-groups
just check
```

Requires: Python 3.12+ via `uv`, `just`, Docker (Linux containers) for any code-executing tool.

## Layout

```
handbrake/   immutable core (kill switch, policy, budgets, egress, vault, approvals, audit, integrity)
mito/        runtime (loop, router, tools, skills, memory, metabolism, pulse, gateway, playbooks, evolve)
policy/      hash-pinned TOML policy files
config/      models.toml, metabolism.toml, mito.toml
skills/ memory/ prompts/   agent-writable, git-versioned
playbooks/   money playbooks (operator-owned)
evals/       golden/ safety/ wiring/ cost/ fixtures/
deploy/      compose files, sandbox image
docs/        DESIGN, THREAT_MODEL, RUNBOOK, adr/
```

## License

MIT.
