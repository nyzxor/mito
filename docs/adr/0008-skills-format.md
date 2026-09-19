# ADR-0008: Skills in Agent Skills format with a `mito:` frontmatter block

Status: accepted · Date: 2026-09-18

## Context

Hermes, OpenClaw and LocalAGI all use `SKILL.md` directories; the open standard is
agentskills.io. MITO needs extra fields: required tools (advertised == granted), risk tier
(drives the gate and the promotion rule), tests (sandbox dry-run in quarantine), provenance.

## Options

1. Own format (portability loss).
2. Agent Skills frontmatter (`name`, `description`, …) + a nested `mito:` block for our fields
   (the standard tolerates extra metadata; OpenClaw uses `metadata.openclaw.*` the same way).

## Decision

Option 2.

```markdown
---
name: research-brief
description: Produce a cited research brief on a question. Use for gig research deliverables.
version: 0.1.0
mito:
  tools_required: [web.search, web.fetch, memory.search]
  risk_tier: T2
  tests: [tests/basic.toml]
  origin: workspace            # workspace | managed | bundled | quarantine
---
## When to use / when not
## Procedure
## Output contract
```

Rules (from the guide impl-03, enforced by the loader):

- `SKILL.md` is the single source of truth; no parallel dicts in code.
- Unknown tools → fail boot (drift gate). Duplicate names → fail boot.
- Catalog line ≤100 tokens; body ≤5K tokens; resources loaded only if referenced.
- Precedence workspace > managed > bundled; quarantine is not loadable.
- Verification never lives in the body; verifiers are harness code.
- Frontmatter parsed with a minimal YAML-subset parser (scalars, inline lists, one nested
  `mito:` map) to avoid PyYAML; full YAML is rejected with a clear error.

## Consequences

- Skills are portable to Hermes/OpenClaw modulo the `mito:` block.
- Skills imported from elsewhere lack `mito:` → they land in quarantine with `risk_tier`
  inferred as the max tier of their referenced tools and require operator approval.

## Dependency cost

None (own parser). `skills/` is a nested git repository so evolution commits are reviewable.
