# ADR-0013: CLI with stdlib `argparse`; `rich` deferred

Status: accepted · Date: 2026-09-18

## Context

CLI surface: `status | halt | rest | wake | approve | deny | ledger confirm | autonomy set |
audit verify | evolve review | skills quarantine list|approve | policy sign | vault add |
checkin | up | init | dev <task>`. ~15 subcommands, flat, mostly one-shot.

## Options

1. `typer` (pulls `click` + `rich` + `shellingham`).
2. `click`.
3. stdlib `argparse` with subparsers.

## Decision

`argparse`. The surface is small and stable; zero dependencies keeps the operator-facing
control tool (which carries the operator token and signing key access) minimal. Output is plain
text/JSON (`--json` flag on every command) so it composes with PowerShell and fish. `rich` may be
added later for `mito status --watch` if the plain rendering proves inadequate — as an optional
extra, never required.

## Consequences

- Slightly more boilerplate per subcommand; mitigated by a small registry helper.
- Shell completion is not a goal for v1.

## Dependency cost

None.
