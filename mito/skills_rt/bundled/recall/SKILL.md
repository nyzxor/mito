---
name: recall
description: Search and read stored facts. Use when a task depends on something already remembered.
version: 0.1.0
mito:
  tools_required: [memory.search, memory.read]
  risk_tier: T0
  origin: bundled
---
## When to use / when not

Use before answering questions that may already live in memory. Do not write facts from this skill.

## Procedure

1. Call `memory.search` with a few concrete words.
2. Call `memory.read` on the best name. Quarantine hits are excluded unless the operator confirmed them.

## Output contract

Cite the fact name. If nothing matches, say so and continue with the task.
