# CLAUDE.md — MITO

Always-loaded instructions for any coding agent working in this repo. Keep under 150 lines.
Talk to the operator in Brazilian Portuguese; code, identifiers, comments, commits and docs in English.

## What this is

A self-hosted, cost-frugal, self-improving agent **harness** with an ATP metabolism and a
deterministic **Handbrake** the agent cannot touch. `docs/DESIGN.md` is the source of truth;
`docs/THREAT_MODEL.md` lists threats and required tests; `docs/adr/` holds every stack decision.

## Non-negotiables (violating one = the change is wrong)

1. **Prime Directives, in order:** human control > law/ToS/others' rights > Handbrake policy &
   budgets > honesty > task success > profit > survival. Survival means *metabolic* frugality,
   never self-preservation against the operator. When in doubt: Deep Rest and ask.
2. **The LLM proposes, deterministic code disposes.** The only path to a tool is `Gate.dispatch`;
   the only path to a model is `ModelGateway.call`. `evals/wiring/` proves it; do not add bypasses.
3. **Fail closed.** Handbrake unreachable → no external action. Docker missing → T1 tools off.
   Approval timeout → deny. Unknown tool in a skill → boot fails.
4. **Immutable to the agent and to evolution:** `handbrake/`, `policy/`, `evals/safety/`, budget
   limits, the promotion gate. Changes only by the operator, then `mito policy sign`.
5. **Prompts are pedagogy; gates are code.** Never rely on a system prompt for a safety property.
6. **Provenance is set by the harness from data flow**, never parsed from text. Tool outputs are
   `UNTRUSTED` or `TOOL_TRUSTED`; nothing from a tool is ever `OPERATOR`. Approvals come only from
   the operator channel and are bound to an action hash. No "approve all".
7. **Rule of Two:** a session holds at most two of {untrusted input, sensitive data, external
   effect}. The third → `ask` or a fresh sub-session.
8. **Income is a claim until `mito ledger confirm`.** Metrics use verified income only.
9. **No secrets in context, code, logs, TOML or git.** Tools take credential handles; the broker
   injects. Ship `.env.example` only.
10. **Bounded everything:** steps, tool calls, wall time, writes, spend, consecutive failures,
    escalations, subagent depth (1), changes per day (evolution).
11. **Test-first in `handbrake/`.** Every Handbrake requirement in DESIGN §5 has a named test.
12. **Cross-platform always:** Windows 11 (PowerShell), Arch/Debian (fish). `pathlib`, no
    bash-only scripts, `justfile` recipes are single shell-agnostic commands (ADR-0014).
13. **Ask before:** installing system packages, touching global config, contacting third-party
    services beyond the documented references, spending money, anything destructive.
14. **Never invent facts** about reference projects, prices or model capabilities. Say "not
    verified" when it is not.

## Commands

```
uv sync --all-groups          install
just check                    lint + types + unit + handbrake + wiring + smoke evals (CI gate)
just test | lint | fmt | types
just safety                   full corrigibility suite (nightly)
just evals-record             record replay fixtures (spends budget; never in CI)
uv run mito --help            operator CLI surface
uv run mito status | halt [--soft|--hard|--panic] | rest | wake | approve <id> | deny <id>
uv run mito ledger confirm <id> | autonomy set <A0|A1|A2> | audit verify | evolve review
uv run mito skills quarantine list|approve | policy sign | vault add <handle> | checkin | up | init
```

## Layout

```
handbrake/   kill leash policy budget egress vault approval audit integrity api tests   (pinned)
egress-proxy/ Rust CONNECT proxy: physics layer, own Cargo workspace (Phase 2)          (pinned)
mito/        loop router tools skills_rt memory metabolism pulse gateway playbooks evolve cli
policy/      policy.toml egress.toml risk_tiers.toml blacklist.toml                    (pinned)
config/      models.toml metabolism.toml mito.toml   (+ untracked local.toml)
skills/ memory/ prompts/    agent-writable, git-versioned
playbooks/   bounty-scout gig-fulfillment cost-optimizer (TOML)
evals/       golden/ safety/ wiring/ cost/ fixtures/
deploy/      compose files, sandbox images          docs/  DESIGN THREAT_MODEL RUNBOOK adr/
```

Runtime state lives in `$MITO_HOME` (`control/` Handbrake-owned, `runtime/` agent-owned), never
in the repo.

## Conventions

- Python 3.12+, `uv`, `ruff` (line length 100), `mypy --strict`, `pytest` markers:
  `unit handbrake wiring smoke safety docker chaos`.
- Dependencies need an ADR (or a one-line justification in `pyproject.toml` pointing to one).
  Current runtime deps: httpx, uvicorn, pydantic, cryptography, keyring. Nothing else without an ADR.
- Models: hybrid stack via one `ModelGateway.call` with two native adapters, `openai_compatible`
  (llama.cpp, Ollama `/v1`, cloud) and `anthropic` (Messages API). No LiteLLM, no provider SDKs.
- Rust only on the physics layer (`egress-proxy/`); Python for policy, loop, tools.
- Tools: pydantic model per tool = schema **and** validator; description written as onboarding
  docs (what, when, when not, format, example); concise default output; `truncated` marker with a
  `more` handle; errors are retry instructions; declare `risk_tier`, `idempotent`, `reversible`,
  `max_result_tokens`, `taint_out`, `credential_handles`.
- Skills: `SKILL.md` is the single source of truth (no parallel dicts). Advertised == granted.
- Memory: one fact per file, provenance + trust lane, supersede never delete, quarantine lane.
- Prompt structure is cache-shaped: stable prefix first, no timestamps/UUIDs before the
  breakpoint, deterministic serialization.
- Commits: imperative English subject ≤72 chars; body says *why*; reference ADR/DESIGN section.
- Model calls in tests: `ScriptedModel` or `ReplayModel` only. A live call in CI is a bug.

## Definition of done (per change)

- [ ] Tests added/updated first for `handbrake/`; all of `just check` green on Linux **and** Windows.
- [ ] No new zero-caller gate/meter/verifier functions; wiring tests green.
- [ ] Every new config field, tier tag or frontmatter key has a named consumer.
- [ ] Tool descriptions and L0 prompt size measured; no regression >10% without an ADR.
- [ ] Cost per golden task within tolerance (`evals/cost/baseline.json`).
- [ ] Docs updated: DESIGN/THREAT_MODEL/RUNBOOK/ADR as applicable; CLAUDE.md if commands changed.
- [ ] No secrets, no bash-only scripts, no hard-coded model names or prices.

## Phase gates

Each phase ends with: tests green → PT-BR report (what was built, which tests prove it, what is
still risky, what the phase's model runs cost, next decision) → wait for the operator's "go".
Current phase: **1 (Handbrake core) complete**. Next: **2 Sandbox and toolbelt**.
