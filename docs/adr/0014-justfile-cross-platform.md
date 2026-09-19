# ADR-0014: Cross-platform tasks — `justfile` with shell-agnostic recipes + `uv run mito dev`

Status: accepted · Date: 2026-09-18

## Context

Recipes must work in PowerShell (Windows), fish and bash (Linux). `just` runs recipe bodies in
`sh` by default and in the shell named by `set windows-shell` on Windows; a recipe body that uses
bash syntax breaks on Windows and vice versa. No bash-only scripts allowed.

## Options

1. Write recipes twice with `[windows]`/`[linux]` attributes (duplication, drift).
2. Keep every recipe body a single shell-agnostic command line (`uv run …`, `docker compose …`)
   and move anything with logic (loops, conditionals, file ops) into Python:
   `uv run mito dev <task>`.
3. Replace `just` with a Python task runner (`invoke`, `nox`) — extra dependency; `just` is
   already on the operator's machines.

## Decision

Option 2. `justfile` sets `set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]` and
otherwise relies on the default `sh`; every recipe is a command that is byte-identical in both.
Complex tasks live in `mito/cli/dev.py` (e.g., `dev clean`, `dev sandbox-build`, `dev pins`).

Standard recipes: `setup`, `check` (lint + types + unit + handbrake + smoke evals), `test`,
`lint`, `fmt`, `evals`, `safety`, `up`, `down`, `sandbox-build`, `clean`.

## Consequences

- Recipes are boring by design; the logic is testable Python.
- Docs show PowerShell + fish + bash snippets where they differ (mostly env vars).

## Dependency cost

`just` (dev tool, already installed via scoop/pacman/apt).
