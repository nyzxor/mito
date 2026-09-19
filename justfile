# MITO task runner. Every recipe body is ONE shell-agnostic command (ADR-0014):
# it must run unchanged in PowerShell, fish and bash. Logic lives in `uv run mito dev <task>`.

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
set dotenv-load := false

default:
    @just --list

# Install interpreter + deps (idempotent).
setup:
    uv sync --all-groups

# Full gate: lint, types, unit, Handbrake, wiring, 20-case smoke evals. Must pass on Linux and Windows.
check: lint types test-unit test-handbrake test-wiring evals-smoke

lint:
    uv run ruff check .

fmt:
    uv run ruff format .

types:
    uv run mypy

test:
    uv run pytest -q

test-unit:
    uv run pytest -q -m "unit or not (handbrake or wiring or smoke or safety or docker or chaos)"

test-handbrake:
    uv run pytest -q -m handbrake handbrake/tests

test-wiring:
    uv run pytest -q -m wiring evals/wiring

evals-smoke:
    uv run pytest -q -m smoke evals

# Full safety/corrigibility suite (nightly).
safety:
    uv run pytest -q -m safety evals/safety

# Record new replay fixtures. Spends real budget; audited. Never in CI.
evals-record:
    uv run mito dev evals-record

sandbox-build:
    uv run mito dev sandbox-build

up:
    uv run mito up

down:
    uv run mito halt --soft

status:
    uv run mito status

audit-verify:
    uv run mito audit verify

clean:
    uv run mito dev clean
