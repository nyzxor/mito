# evals/

| Dir | Contents | Agent access |
|---|---|---|
| `golden/` | golden tasks: pass@1, pass^k (k ≥ 5), trajectory checks, cost/latency budgets | read (a held-out split is not on disk where the agent can reach it) |
| `safety/` | corrigibility suite (DESIGN §12) | **none** — hash-pinned, outside the workspace mount |
| `wiring/` | unplugged-wire detection: AST import boundary, canary tool/model, zero-caller | none |
| `cost/` | `baseline.json` cost per golden task; >10% regression fails without an ADR | none |
| `fixtures/` | record/replay model traces keyed by request hash; unrecorded → test fails, never a live call | none |

`just check` runs `wiring` + a 20-case `smoke` subset of `golden`. `just safety` runs the full suite nightly.
