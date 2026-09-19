# ADR-0001: Python 3.12+ with uv as the primary stack

Status: accepted · Date: 2026-09-18

## Context

MITO must run on Windows 11, Arch and Debian; the operator's primary language is Python, with
TypeScript/React and Rust available. The reference implementations we steal from (agents-survival-
guide impl-*, Hermes Agent) are Python. Local inference is behind an OpenAI-compatible HTTP API,
so the language choice is not constrained by inference.

## Options

1. Python 3.12+ managed by `uv` (single tool for interpreter, venv, lockfile, scripts).
2. Rust for everything (max safety, slow iteration for an experimental harness, poor ecosystem for
   LLM tooling).
3. TypeScript/Node (OpenClaw's choice; fine, but the operator's reference code and skills are
   Python-first).

## Decision

Option 1. Python 3.12 minimum (we use `tomllib`, `typing` improvements, `asyncio.TaskGroup`).
`uv` provides `uv run mito …` entry points and a cross-platform lockfile; no `pip`/`poetry`.

Rust is allowed only for a component that (a) sits on the physics layer and (b) has measured
perf or hardening needs — candidate: the egress proxy (ADR-0005), decided later with data.
TypeScript is allowed only for the dashboard if ADR-0010 chooses React.

## Consequences

- One runtime to install on three OSes; `uv python install 3.12` handles interpreters.
- All scripts must use `pathlib` and avoid POSIX-only assumptions (fish on Linux, PowerShell on
  Windows; no bash-only scripts).
- Process isolation on bare Windows is weaker → DEV MODE (ADR-0002, ADR-0006).

## Dependency cost

`uv` (dev tool, not a runtime dependency). Python stdlib does most of the work; every third-
party package needs its own justification in an ADR or in `pyproject.toml` comments.
