# Architecture Decision Records

One file per decision. Format: Context → Options → Decision → Consequences → Dependency cost.
Status is one of `proposed` (needs operator go), `accepted`, `superseded by ADR-xxxx`.

| # | Title | Status |
|---|---|---|
| 0001 | Python 3.12+ with uv as the primary stack | accepted |
| 0002 | Handbrake as a separate process with a one-directional localhost API | accepted |
| 0003 | SQLite (WAL) for ledger, audit index and memory search | accepted |
| 0004 | Thin OpenAI-compatible client first; LiteLLM deferred | accepted |
| 0005 | Egress: gateway API + CONNECT proxy, Python first, Rust deferred | proposed |
| 0006 | Sandbox: hardened Docker, fail closed without Docker | accepted |
| 0007 | Audit chain, hash pins and signed operator commands (Ed25519 + HMAC anchors) | accepted |
| 0008 | Skills in Agent Skills format with a `mito:` frontmatter block | accepted |
| 0009 | Memory: markdown files + SQLite FTS5, embeddings off | accepted |
| 0010 | Dashboard: server-rendered from the Handbrake first; React/Vite only if it earns it | proposed |
| 0011 | Configuration in TOML (stdlib `tomllib`), env overrides, no programmatic TOML writes | accepted |
| 0012 | Schemas and validation with pydantic v2 | accepted |
| 0013 | CLI with stdlib `argparse`; `rich` deferred | accepted |
| 0014 | Cross-platform tasks: `justfile` with shell-agnostic recipes + `uv run mito dev` | accepted |
| 0015 | Testing and CI: pytest, ruff, mypy, record/replay fake model, Linux+Windows matrix | accepted |
| 0016 | Taint tracking and Rule of Two as a gate input, not a classifier | accepted |
| 0017 | Task/pulse scheduling in-process (APScheduler-free), persisted in SQLite | accepted |
