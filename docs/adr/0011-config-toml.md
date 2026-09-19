# ADR-0011: Configuration in TOML, env overrides, no programmatic TOML writes

Status: accepted · Date: 2026-09-18

## Context

Config files: `policy/policy.toml`, `policy/egress.toml`, `policy/risk_tiers.toml`,
`policy/blacklist.toml`, `config/models.toml`, `config/metabolism.toml`, `config/mito.toml`,
`playbooks/*.toml`. Policy files are hash-pinned and operator-signed.

## Options

1. YAML (needs PyYAML; ambiguous typing; easy to mis-indent).
2. TOML (stdlib `tomllib` reader since 3.11; typed; comments; no stdlib writer).
3. JSON (no comments).

## Decision

TOML, read with `tomllib`. Secrets never appear in TOML — only credential handles. Runtime
overrides come from environment variables prefixed `MITO_` (e.g., `MITO_LOCAL_BASE_URL`) and an
optional untracked `config/local.toml` for non-policy settings only.

We never write TOML programmatically: policy changes are edits by the operator followed by
`mito policy sign`; agent-proposed config changes (routing tweaks from evolution) are proposed as
diffs for `prompts/` or `skills/`, never to `policy/` or budgets.

Validation: every TOML is parsed into a pydantic model (ADR-0012) at boot; unknown keys are
errors (fail the boot, not the user).

## Consequences

- No config drift from "helpful" auto-writes.
- `.env.example` documents env overrides; real values live in the OS keyring/vault or the
  compose environment.

## Dependency cost

None.
