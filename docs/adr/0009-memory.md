# ADR-0009: Memory — markdown files + SQLite FTS5, embeddings off by default

Status: accepted · Date: 2026-09-18

## Context

Guide Ch 08 / impl-06: files beat databases until scale forces otherwise; provenance and trust
tiers are mandatory; memory is an attack surface. MITO is single-operator with a small memory.

## Options

1. Vector DB + embeddings (extra model calls, another store, benchmark claims that don't
   replicate).
2. Markdown entries with frontmatter + `MEMORY.md` index + SQLite FTS5 (BM25) for agentic search.
3. mem0/Zep-style service (dependency and network surface).

## Decision

Option 2. Layout:

```
memory/
  MEMORY.md            always-loaded index (≤200 lines), derived from files
  facts/*.md           semantic entries: name, description, type, source, trust_lane, run_id,
                       created, ttl, superseded_by, body (Why / How to apply / [[links]])
  quarantine/*.md      entries written while UNTRUSTED content was in context; excluded from
                       recall for T3+ decisions until operator confirms
  index.sqlite         FTS5 over name/description/body; rebuilt in consolidation
```

Write policy (harness code, not prompt): `source` and `trust_lane` are set from data flow;
recomputable facts are refused (store pointers); secret-shaped strings are refused;
update-before-create; supersede, never delete. Recall is injected with the background framing.
Consolidation runs on the pulse at L1 with only memory + read tools and produces a diff for the
operator.

Embeddings: optional extra (`mito[embeddings]`), off by default; enabling requires a measured
recall eval win on our own cases.

## Consequences

- Human-readable, diffable, versionable; snapshots are file copies.
- BM25 misses paraphrases; mitigated by descriptions written as recall hints and by agentic
  search (multiple queries).

## Dependency cost

None (stdlib sqlite3 with FTS5).
