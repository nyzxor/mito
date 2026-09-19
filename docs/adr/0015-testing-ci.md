# ADR-0015: Testing and CI — pytest, ruff, mypy, record/replay fake model, Linux+Windows matrix

Status: accepted · Date: 2026-09-18

## Context

Test-first for everything in `handbrake/`; CI must cost $0 in model calls; `just check` must pass
on Linux and Windows; evals need pass@1/pass^k with n≥4 trials.

## Options

- Test runner: `pytest` (de facto) vs `unittest` (stdlib, weaker fixtures/parametrization).
- Lint/format: `ruff` (one tool, fast) vs flake8+black+isort.
- Types: `mypy --strict` vs `pyright` (needs Node).
- Fake model: scripted responses (impl-05 `ScriptedLLM`) vs record/replay by prompt hash.

## Decision

- `pytest` + `pytest-asyncio` (+ `hypothesis` for the policy engine and taint algebra, where
  property tests pay: deny-beats-allow regardless of order, taint monotonicity, budget
  arithmetic).
- `ruff` for lint and format; `mypy --strict` for `handbrake/` and `mito/`.
- Fake model: **both**. `ScriptedModel` for loop-logic unit tests (budgets, dispatch, approval
  round-trip); `ReplayModel` for evals: requests hashed over (model id, canonical messages,
  tools); fixtures in `evals/fixtures/<suite>/<hash>.json`; unrecorded → test failure (never a
  live call in CI). Recording is an explicit local command (`just evals-record`) that spends
  budget and is audited like any run.
- Test tiers: `unit` (fast), `handbrake` (includes real-process 2 s hard-stop test), `wiring`
  (AST + canary), `smoke` (20 golden cases on replay), `safety` (full suite nightly),
  `chaos` (Phase 7, manual/nightly).
- CI: GitHub Actions matrix `ubuntu-latest` × `windows-latest`, Python 3.12, `uv sync`,
  `just check`. Docker-dependent tests are skipped with an explicit marker when the daemon is
  absent and run on Linux CI with Docker available.
- Cost regression: `evals/cost/baseline.json` per golden task; >10% increase fails unless an ADR
  is referenced in the PR.

## Consequences

- Handbrake tests are in `handbrake/tests/` and are part of the hash-pinned tree.
- `evals/safety/` is read-only to the agent (the runtime's file tools cannot reach it — it is
  outside the workspace mount) and hash-pinned.

## Dependency cost

Dev-only: `pytest`, `pytest-asyncio`, `hypothesis`, `ruff`, `mypy`. None at runtime.
