# ADR-0010: Dashboard — server-rendered from the Handbrake first; React/Vite only if it earns it

Status: accepted · Date: 2026-09-18 · decided 2026-09-24 (Phase 5): stdlib HTML, no jinja2.

## Context

Phase 5 needs a localhost dashboard: balance, runway, state, approvals, audit tail, kill button.
The operator writes TypeScript/React, so a React/Vite app is a legitimate option. The dashboard
sits on the Handbrake (it exposes halt/approve), so its attack surface matters.

## Options

1. React/Vite SPA served by the Handbrake (Node toolchain in the repo, build step in CI, larger
   surface, best UX for a growing UI).
2. Server-rendered HTML from the Handbrake (stdlib templating or a tiny template engine), a few
   lines of vanilla JS for polling/kill button, no build step.
3. TUI only (`mito status --watch`) and no web dashboard.

## Decision (proposed)

Start with option 2 in Phase 5; add option 1 only when the UI needs interactivity that
server-rendering makes painful (e.g., approval diff viewers, charts). The kill button and
approvals must work with JS disabled (plain forms), because they are safety controls.

Whatever the stack, the dashboard binds to `127.0.0.1`, requires the operator token (cookie set
by `mito dashboard open`), and every action is a POST with a CSRF token bound to the action hash.

## Consequences

- No Node in the Phase 5 critical path.
- If React is adopted later, it is a separate `dashboard/` package with its own lockfile and
  an ADR update.

## Dependency cost

Option 2: none beyond the ASGI server (ADR-0002); templates via `string.Template`/f-strings or
`jinja2` (small, ubiquitous) — decided in Phase 5.
