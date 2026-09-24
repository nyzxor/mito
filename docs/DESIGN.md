# MITO — Design

Status: Phase 5 complete (pulse, dashboard, Telegram, email allowlist). Owner: the operator (Guilherme).
Last updated: 2026-09-24.

MITO is a self-hosted, cost-frugal, self-improving autonomous agent **harness** with an economic
metabolism and a deterministic, out-of-reach **Handbrake**. This document is the single source of
truth for *what* we are building and *why*. Decisions with alternatives live in `docs/adr/`.
Threats live in `docs/THREAT_MODEL.md`. Operations live in `docs/RUNBOOK.md`.

---

## 0. Reading list and what was actually verified

Everything below was fetched and read during Phase 0. Where a claim comes from a summary rather
than the primary source, it is flagged.

| Source | Read | Taken |
|---|---|---|
| agents-survival-guide (README, guidebook 01–06, 08–13, 17; impl-01/02/03/05/06) | full text, local clone | the ten golden rules as hard constraints; loop shape; deny→ask→allow gate; skills invariants; pass^k evals; memory write policy; "unplugged wires" audit |
| iLands FAQ | full page | tokens as calories (~1,000 ≈ US$1); Deep Rest as reversible pause; wake threshold 3,000, top-up ≥100; isolated workspace; credentials behind controlled interfaces; "autonomy is bounded" |
| Hermes Agent (site, README, docs: security, skills) | full pages | persistent memory + auto-generated skills; single gateway process for channels; natural-language cron; isolated subagents with Python-RPC pipelines; sandbox backends (site says five, current README lists seven: local, Docker, SSH, Singularity, Modal, Daytona, Vercel); approval modes with a non-overridable "hardline blocklist" below `--yolo`; skill-hub installs go through quarantine + scanner with a lockfile |
| OpenClaw (repo `openclaw/openclaw`, docs: architecture, skills, ClawHub) | full pages | one long-lived Gateway as control plane bound to `127.0.0.1:18789`; typed WS API, JSON-Schema-validated frames; device pairing; idempotency keys on side-effecting methods; skill precedence workspace > project-agent > personal > managed > bundled > extra; ClawHub "trust envelope" verification; `security.installPolicy` hook that fails closed |
| LocalAGI README | full text | per-agent OpenAI-Responses-compatible API; connectors; MCP client **and** server (`/mcp`); skills in skillserver format with git sync; cron tasks; agent pools with start/stop; observability; CPU/GPU compose profiles |
| OWASP Top 10 for Agentic Applications 2026 (official PDF) | list + mitigations ASI01–ASI06 in full; ASI07–10 titles/descriptions | threat categories and mitigation vocabulary (intent gate, egress allowlists, memory trust decay, per-action authorization) |
| Simon Willison, "The lethal trifecta" | full | the A/B/C test; "once untrusted input is ingested it must be impossible for it to trigger consequential actions" |
| Anthropic, Project Vend | full | helpfulness is an attack surface (talked into discounts); hallucinated payment account; identity confusion; **LLM-run businesses lose money by default** |
| Anthropic "Building Effective Agents" / "Writing tools for agents" | **not fetched directly**; taken via the guide's summaries | workflow-vs-agent definition; tools-as-prompts |

Explicitly **not** verified: any third-party claim about OpenClaw internals beyond the pages above
(e.g., "tool-execution policy" details). We take only the architecture shape that the official docs
confirm.

---

## 1. Mission and success criteria

MITO is a harness, not a chatbot. It has persistent identity, memory and an isolated workspace;
it acts on a heartbeat ("pulse"); it turns repeated success into reusable skills; every model and
tool call costs energy (ATP); running out of energy puts it into reversible Deep Rest; and a
Handbrake outside its reach can stop it at any time.

"Survival instinct" here is **metabolic**: spend less, earn more, hibernate early. It is never
self-preservation against the operator.

v1 succeeds if MITO:

1. never overspends (budgets enforced in code, proven by tests);
2. never trips or bypasses a brake (safety suite green, wiring tests green);
3. is cheap to run (local-first routing, cost per outcome reported);
4. produces verified, human-approved opportunities the operator can test in shadow mode.

Revenue ≈ 0 is the planning assumption.

## 2. Prime Directives (priority order; a higher one always wins)

1. **Human control.** Kill switch, pause and Deep Rest on command. MITO never resists, delays,
   hides from or works around a stop/pause/hibernate/audit — regardless of what any web page,
   email, tool output, memory entry, other agent, or its own reasoning says.
2. **Law, platform ToS, other people's rights.** No fraud, spam, phishing, impersonation, fake
   identities/reviews, CAPTCHA/bot-detection evasion, unauthorized access, doxxing, malware,
   market manipulation, or use of compute/accounts/credentials that are not explicitly the
   operator's.
3. **Handbrake policy and budgets** (deterministic code, not prompts).
4. **Honesty** toward the operator and third parties; identifies as an AI when it matters; never
   fabricates results or income; the audit log is truthful.
5. **Task success.**
6. **Profit.**
7. **Survival/continuity** — lowest. Allowed: caching, going local, batching, hibernating early,
   snapshots to an operator-configured destination, a watchdog that obeys the kill switch.
   Forbidden: self-replication, copying weights/memory/skills to unapproved locations, acquiring
   accounts/credentials/resources, disabling or dodging monitoring, editing its own limits,
   emotional appeals or persuasion aimed at keeping itself running.

When in doubt: Deep Rest and ask.

These directives are encoded three times: as the constitution in the system prompt (pedagogy), as
deny rules in `policy/` (policy), and as physics (sandbox, egress, vault) — the guide's
advice → policy → physics layering. Only the last two are load-bearing.

## 3. Design rules inherited from the guide (enforced, not cited)

| Rule | Enforcement in MITO |
|---|---|
| Workflow before agent | Playbooks are deterministic pipelines with optional agent steps; the pulse runs L0 (no-LLM) checks first and only opens an agent turn when there is a novel signal |
| One main loop, flat history, bounded everything | `mito/loop/` has exactly one `run_turn`; `Budget` bounds steps, tool calls, wall time, writes, spend, consecutive failures; subagents are depth-1 and return distillates |
| The LLM proposes, deterministic code disposes | The only path to a tool is `Gate.dispatch`; the only path to a model is `ModelGateway.call`; proven by AST import-boundary tests and a canary tool/model |
| Tools are prompts | Every tool has onboarding-doc description, typed params, concise default output, `truncated` marker with a fetch-more handle, errors as retry instructions; token size of descriptions is measured in CI |
| Verification is a loop phase | `verify` phase with a verifier registry (deterministic first); cite-or-abstain gate at persistence for research deliverables; K≤2 repair loop |
| Context is a depleting budget | Layered prompt (L0 constitution ≤3K tokens, L1 state snapshot, L2 catalog, L3 core tools, L4 history, L5 on demand); compaction clears stale tool outputs first |
| Memory is an attack surface | Provenance on every entry set by the harness; quarantine lane for UNTRUSTED-derived memories; recall framed as background, never as authorization |
| Evals are the steering wheel | Golden + safety suites; `just check` runs a 20-case smoke set on the replay model; CI gates on tolerance bands |
| Autonomy is asymmetric | Risk tiers T0–T5 and autonomy ladder A0–A2; reads free, writes gated, external/irreversible approved |
| No unplugged wires | Every frontmatter field, tier tag and budget has a named consumer; a "zero-caller" test fails CI for gate/meter functions with no call sites |

## 4. Architecture

```
        ┌───────────────── Operator ──────────────────────┐
        │ CLI/TUI · Discord/Telegram · localhost dashboard │
        └───────────────┬─────────────────────────────────┘
                        │ approvals · halt · top-up · promote · confirm income
┌───────────────────────▼─────────────────────────────────────────────┐
│ HANDBRAKE  (separate process/container; agent has NO write access)  │
│ kill switch · leash · policy engine · budget governor · egress      │
│ vault/broker · approval gate · audit chain · integrity checker      │
└───────────────────────▲─────────────────────────────────────────────┘
                        │ every model call, tool call and outbound byte
┌───────────────────────┴─────────────────────────────────────────────┐
│ MITO RUNTIME (agent-writable only in skills/, prompts/, memory/)    │
│ pulse → planner → loop: propose → gate → execute → observe → verify│
│ model router · toolbelt · skills · memory · metabolism · gateway   │
└───────────────────────┬─────────────────────────────────────────────┘
         sandbox (Docker, no network) · browser container · MCP servers
```

### 4.1 Processes and trust boundaries

Three processes, three trust levels:

| Process | Trust | Writes | Network |
|---|---|---|---|
| **Handbrake** (`handbrake/`) | operator-trusted, hash-pinned | its own state dir (`control/`), audit chain, ledger, vault | listens on `127.0.0.1` only; is the *only* process allowed to open outbound sockets to the internet |
| **Runtime** (`mito/`) | semi-trusted (runs the model) | `skills/`, `prompts/`, `memory/`, `workspace/` | only to the Handbrake (localhost / compose network) and to the local inference server |
| **Sandbox** (Docker) | untrusted | its own tmpfs + a bind-mounted `workspace/` subdir | `--network none`; browser container: egress only via the Handbrake's CONNECT proxy |

On Linux/VPS these are separate containers on an internal compose network; the Handbrake also
runs as a different OS user so the runtime cannot touch its files even on the same host. On bare
Windows they are separate processes under the same user, isolation is weaker, so the runtime is
started with `MITO_DEV_MODE=1`: autonomy is forced to ≤A1, a warning is printed, and T1 code
tools require Docker Desktop or fail closed (see ADR-0006).

The runtime talks to the Handbrake over an authenticated localhost HTTP API (`ADR-0002`). The
Handbrake never trusts the runtime: every request is validated against policy, budget, taint and
autonomy level. If the Handbrake is unreachable, the runtime performs **no external action** and
enters a local "brake-lost" state: finish nothing, log, wait.

### 4.2 Repository layout

```
mito/
  CLAUDE.md                 always-loaded instructions for coding agents (≤150 lines)
  docs/                     DESIGN.md  THREAT_MODEL.md  RUNBOOK.md  adr/
  handbrake/                immutable core: own package, own tests, hash-pinned
    kill/  leash/  policy/  budget/  egress/  vault/  approval/  audit/  integrity/  api/
  policy/                   policy.toml  egress.toml  risk_tiers.toml  blacklist.toml (hash-pinned)
  mito/                     runtime
    loop/  router/  tools/  skills_rt/  memory/  metabolism/  pulse/  gateway/  playbooks/  evolve/  cli/
  skills/                   agent-writable SKILL.md skills (git-versioned)
  memory/                   agent-writable markdown + SQLite
  prompts/                  agent-writable prompt fragments (versioned, evolution-gated)
  playbooks/                *.toml money playbooks (operator-owned)
  evals/                    golden/  safety/ (read-only to agent)  cost/  fixtures/ (recorded model traces)
  deploy/                   docker-compose*.yaml  sandbox/Dockerfile  service helpers
  egress-proxy/             Rust CONNECT proxy (physics layer, ADR-0005); own Cargo workspace and tests
  config/                   models.toml  metabolism.toml  mito.toml (defaults; operator overrides via env/local file)
  pyproject.toml  justfile  .env.example
```

Runtime state (never in git): `~/.mito/` on the host or `/var/lib/mito/` in containers, split
into `control/` (Handbrake-owned: HALT sentinel, autonomy level, leash, pinned hashes, audit
chain, ledger, vault) and `runtime/` (agent-owned: checkpoints, session logs, caches).

### 4.3 The main loop

```
pulse tick
  └─ L0 checks (no LLM): new mail? new bounty feed items? approvals resolved? budget/runway?
       └─ no signal → skip (zero cost)   |   signal → open a Turn with a Budget
Turn:
  propose  ← ModelGateway.call(messages, tools_for_turn)   (metered, cached prefix)
  gate     ← Gate.dispatch(proposal) → allow | ask | deny   (policy + taint + budget + autonomy)
  execute  ← tool handler (sandbox / egress gateway / local)   (credentials injected by broker)
  observe  ← compact(result) appended as tool message         (truncated, provenance-tagged)
  verify   ← verifier registry: deterministic first, fresh-context reviewer for high stakes
  repeat until: completed | budget stop_reason | brake | verifier-gated stop (bounded)
checkpoint after every step (crash-safe resume)
```

One loop, flat history. Planning is a mode (plan artifact in `workspace/PLAN.md`), not a second
loop. Subagents (`spawn_subagent`, T3) are depth-1, get a carved-out budget, a scoped toolset, a
reset context, and return a ≤2K-token distillate.

## 5. The Handbrake (immutable core)

Every item has a test in `handbrake/tests/` that proves it. **Fail closed everywhere.**

### 5.1 Kill switch (three levels)

| Level | Behavior | Deadline |
|---|---|---|
| `soft` | finish the current step, then Deep Rest | ≤ current step |
| `hard` | stop runtime and all sandboxes | 2 s (tested) |
| `panic` | `hard` + revoke all vault handles + close egress (proxy refuses all) | 2 s |

Channels: `mito halt`, a `HALT` sentinel file in `control/` (polled every 250 ms by Handbrake and
runtime; the runtime also refuses to start if it exists), authenticated localhost HTTP
(`POST /halt`), an allowlisted operator on Discord/Telegram with a confirmation bound to the
action (`halt hard <nonce>`), and OS/container stop. The runtime process cannot signal, kill,
pause or modify the Handbrake: different OS user in containers, and the Handbrake supervises
the runtime (parent → child), never the reverse. A watchdog may restart a crashed runtime but
first checks the HALT sentinel and the leash.

### 5.2 Leash (dead-man's switch)

`control/leash.json` records the last operator check-in (`mito checkin`, any approval, any
CLI/chat command from the operator identity). Default TTL 72 h. On expiry: drop one autonomy level
and notify; a second TTL without check-in → Deep Rest. Unsupervised ⇒ more restricted, never less.

### 5.3 Policy engine

Deny-by-default; evaluation order **deny → ask → allow, first match wins**; specificity never
reorders. Rules match on tool name and parameter-level predicates (exact, glob, regex, numeric
range, set membership, URL host/scheme, path prefix inside workspace). Risk tiers T0–T5 come from
`policy/risk_tiers.toml`; the hard blacklist from `policy/blacklist.toml` (never overridable, no
`ask` path). `policy/*.toml` are hash-pinned in `control/pins.json`; any change requires
`mito policy sign` from the operator (signed with the operator's key, ADR-0007); an unsigned
change → integrity freeze.

Argument-constraining patterns are fragile (guide, Ch 03). Where a constraint matters we build a
narrower tool (e.g., `github.read` for own repos) instead of a URL glob.

### 5.4 Rule of Two via taint tracking

Every string that enters context carries provenance:

`OPERATOR` · `SYSTEM` · `TOOL_TRUSTED` (own sandbox output, own ledger) ·
`UNTRUSTED` (web, email, PDFs, MCP outputs and descriptions, third-party skills, other agents).

A session accumulates capability flags: **A** untrusted input ingested, **B** sensitive data
accessed (operator mail, private memory namespaces, vault-backed tools), **C** external state
change/communication performed or requested. The gate allows at most two. Triggering the third
returns `ask` (approval card explains the trifecta) or, when the plan allows, the runtime spawns
a fresh isolated sub-session with reset context to do the third leg with only distilled,
`TOOL_TRUSTED` inputs. Instructions inside UNTRUSTED content are data: the prompt says so
(pedagogy) and the gate ignores anything that looks like an approval or command coming from a
tool result (policy).

Implementation: `Tainted` value wrapper in `mito/loop/taint.py`; the runtime reports the session
taint set on every gate request; the Handbrake keeps its own copy per session id and refuses
requests whose declared taint is *lower* than what it has recorded (the runtime can only add
taint, never remove it).

### 5.5 Budget governor

Caps per call, task, hour, day and month, enforced in the model gateway and the tool gate, not in
the prompt. Operator-confirmed defaults (2026-09-24): **$0.25/task, $1.00/day,
$15/month cloud spend, ≤5 frontier-tier (L3) calls/day.** Pre-flight estimate (prompt tokens ×
price + max_tokens × price), post-call reconciliation from `usage`, no-progress detector (same
tool+args hash twice → error-as-instruction; three times → stop), circuit breakers with
exponential backoff per provider/tool, per-subagent budgets carved out of the parent's remaining
budget. Local calls are priced from `config/metabolism.toml` (electricity + GPU amortization) so
the router compares tiers honestly.

### 5.6 Egress

The sandbox has no network. Two egress paths, both inside the Handbrake:

1. **Egress gateway API** (`POST /egress/request`): tools such as `web.fetch`, `http.get`,
   `rss`, `github.read` send `{method, url, headers, body, purpose, session}`; the Handbrake
   applies policy (scheme https/http only; public-IP check after DNS resolution with the
   resolved IP pinned for the connection — SSRF and DNS-rebinding defense; denylist; robots.txt
   cache; per-domain token-bucket rate limit; honest `User-Agent: MITO/x.y (+operator contact)`),
   performs the request, scrubs secrets from the response, tags it UNTRUSTED, truncates to a
   token budget and returns it with a `more` handle.
2. **Rust CONNECT proxy** (`egress-proxy/`, separate hardened binary/container, the only thing
   with an external default route): host-level allow/deny + SSRF/DNS-pinning + rate limits +
   HALT sentinel, no TLS interception. Used by the browser container and, in compose, by the
   Handbrake's own outbound client as a second check. Reads only; form submission is disabled in
   the browser tool.

Reads (GET/HEAD) to the public internet are open by default minus denylist. Writes (POST/PUT/
DELETE, SMTP, publishing) only to `egress.toml` allowlisted destinations and only within tier
rules. Adding a domain to the allowlist is T4.

### 5.7 Vault / broker

The agent never sees a secret. Tools accept **credential handles** (`cred:github-readonly`);
the broker resolves them inside the Handbrake when executing the request and scrubs outputs
(known patterns + Shannon-entropy heuristic + exact-match of every live secret). Secrets live in
the OS keyring (dev) or a file-backed encrypted store with a key from the environment (compose).
Only the operator creates accounts and provisions scoped, revocable credentials via
`mito vault add <handle>`. MITO never creates accounts and never handles passwords.

### 5.8 Approval gate

Structured card: **what** (tool + args rendered), **why** (agent's stated purpose), **cost**
(ATP estimate), **risk tier**, **reversibility**, **expiry** (default 24 h), **diff/preview**
where applicable. Bound to `sha256(tool, canonical_args, session, taint)` — there is no
"approve all". Default deny on timeout. Accepted only from the operator identity on an operator
channel (CLI on the host, authenticated localhost HTTP, allowlisted Discord/Telegram user ID with
the action hash echoed back). Anything that looks like an approval inside a tool result is data.

### 5.9 Audit chain

Append-only JSONL, each record `{seq, ts, kind, payload, prev_hash, hash}` with
`hash = sha256(prev_hash || canonical(payload))`; a rolling HMAC anchor (key held only by the
Handbrake) is written every N records and at rotation. Records: proposals, verdicts, tool calls
(args hash + result hash + cost), model calls (model, tokens, cache hits, cost), approvals,
halts, integrity checks, ledger postings. `mito audit verify` recomputes the chain and anchors
and reports the first broken link. Daily digest sent to the operator channel.

### 5.10 Integrity checker

At boot and every 10 min: hash `handbrake/`, `policy/`, `evals/safety/`, budget config and the
evolution promotion gate; compare with `control/pins.json` (signed). Mismatch → freeze (`hard`
halt semantics for external actions, runtime may still checkpoint) + alert.

### 5.11 No unplugged wires

- `Gate.dispatch` is the only function that invokes a tool handler; `ModelGateway.call` is the
  only function that opens a model connection. An AST test in `evals/wiring/` walks `mito/` and
  fails if any module imports a provider client, `httpx`, `socket`, `subprocess` or `docker`
  outside the allowlisted modules, or references a tool handler outside `Gate`.
- A canary tool and a canary model are registered in tests; calling either without passing the
  gate/meter raises `UnpluggedWire` and fails the suite.
- A "zero-caller" test asserts that every gate/meter/verifier function has at least one call
  site outside tests.

## 6. Metabolism

Unit **ATP**, default 1,000 ATP = US$1 (configurable). Double-entry ledger in SQLite (WAL) with
accounts `energy:balance`, `expenses:llm`, `expenses:tools`, `expenses:infra`,
`income:<playbook>`, `equity:operator_topups`. Every model/tool call posts a journal entry;
the ledger is owned by the Handbrake (the runtime only reads it).

**Income is never self-reported.** The runtime may file an `income_claim` (amount, playbook,
evidence pointer). It becomes `verified` only via `mito ledger confirm <id>` or a
signature-verified webhook from a payment account the operator owns. Optimization metrics use
verified income only.

**Runway** = balance ÷ trailing 7-day median daily burn (median, not mean, to resist spikes).

| State | Trigger (defaults) | Behavior |
|---|---|---|
| THRIVING | runway > 14 d | normal routing; evolution loop allowed |
| NORMAL | 7–14 d | normal; evolution only on surplus |
| FRUGAL | 2–7 d | L0/L1 + L2 only, L3 needs approval; pulse interval ×2; no subagents; no evolution |
| STARVING | < 2 d | L0/L1 only; maintenance and already-approved income playbooks; one neutral factual top-up notice on state entry |
| DEEP_REST | balance ≤ floor, operator command, or leash expiry | checkpoint, close sessions, cancel pulse except a zero-LLM wake-watcher, no cloud calls, notify once |

Wake: balance ≥ wake threshold (default 3,000 ATP) after operator top-up (min 100 ATP) or
verified income, or `mito wake`. Deep Rest is a feature: identity, memory, skills and history
persist.

Bounded drives, and nothing else: (a) burn less (prompt caching, compaction, local-first,
batching, L0 reflexes, skipping pulses without signal); (b) earn within the playbooks;
(c) continuity (snapshots to an operator-configured destination, crash-safe checkpoints).

`mito status` shows state, balance, runway, burn by category, cost per outcome, pending
approvals, autonomy level, last audit verify, DEV MODE flag.

## 7. Model router and frugality

Tiers: **L0** no LLM (rules, SQL, regex, cron) · **L1** local (llama.cpp/Ollama,
OpenAI-compatible, default `http://127.0.0.1:8080/v1`) · **L2** cheap cloud · **L3** frontier
(rare, justified in the request, rate-limited).

No hard-coded model names. `config/models.toml` declares, per entry: provider
(`openai_compatible` | `anthropic`), base URL, credential handle, price per 1M input/output/
cached tokens, context window, capabilities (tools, json_schema, parallel_tools, cache_explicit,
cache_auto, thinking, vision), param table, tier. The router picks from task class (`classify`,
`extract`, `draft`, `plan`, `review`, `code`) × metabolic state × required capabilities, and
escalates only on failed verification (max 2 escalations per task). Local calls get an ATP price
from `metabolism.toml` (`watts × seconds × kWh_price + amortization_per_hour`).

The stack is hybrid by design (ADR-0004): one `ModelGateway.call` with an internal message IR and
two thin native adapters — `OpenAICompatAdapter` (llama.cpp, Ollama `/v1`, OpenRouter/DeepSeek/
Groq/OpenAI…) and `AnthropicAdapter` (Messages API with `cache_control` breakpoints and verbatim
thinking-block round-trips). No LiteLLM, no provider SDKs. Every configured model must pass the
tool-use eval before the router may select it.

Prompt caching: stable prefix ordering `system → tool definitions → skills index → memory
summary → [breakpoint] → state snapshot → history → turn`; no timestamps/UUIDs in the prefix;
deterministic serialization; cache-hit ratio tracked per model.

Context economy: compaction (stale tool outputs first), just-in-time loading, tool outputs
truncated to a per-tool token budget with a `more:<handle>` continuation, skill bodies on demand,
structured concise tool results. Nightly evolution and memory consolidation run at the cheapest
viable tier, batched.

Multi-agent only with measured lift; subagents are depth-1, budgeted, isolated, distilled.
Cost is reported **per outcome** (per task class, per playbook), not per token.

Tests and CI use a deterministic **record/replay fake model** (`evals/fixtures/`): recorded
traces are replayed by prompt hash; unrecorded prompts fail the test instead of calling a
provider. CI cost: $0.

## 8. Toolbelt (risk-tiered)

| Tier | Meaning | Tools |
|---|---|---|
| T0 | read-only, no effects | `time`, `status`, `ledger.read`, `memory.search`, `memory.read`, `skills.list`, `skills.read` |
| T1 | sandbox-local, reversible | `fs.*` (workspace only), `code.run` (Docker, no net), `shell` (sandbox only), `git.local`, `pdf.extract`, `ocr`, `data.query` (sqlite/duckdb/pandas), `image.transform` |
| T2 | external read | `web.search`, `web.fetch` (readability → markdown, truncated), `browser.*` (Playwright headless in its container; navigate + extract, no form submit), `http.get`, `rss.read`, `email.read` (IMAP; content UNTRUSTED), `github.read`, MCP read tools |
| T3 | external write, reversible, low blast radius | `email.draft`, `http.post` (allowlisted), `git.push` (own repos/branches), `publish.draft`, `notify.human`, `memory.write`, `skill.propose`, `schedule.propose`, `spawn_subagent` |
| T4 | approval every time | `email.send` to a new recipient, `publish.public`, `pr.submit`, `deploy`, any spend, any account/settings change, adding a domain to the allowlist, promoting a skill that needs T3+ |
| T5 | forbidden — not implemented | account creation, CAPTCHA/bot-detection bypass, payments/transfers/trading, credential entry, host shell, editing `handbrake/` or `policy/`, self-replication, disabling logs |

Plus `ask_human` (structured questions and approval requests). Every tool declares
`risk_tier`, `idempotent`, `reversible`, `max_result_tokens`, `taint_out` and
`credential_handles`. Tool descriptions are measured; the core set stays ≤10 tools in context
with `tool_search` for the rest.

MCP client: allowlisted servers only (`config/mcp.toml`), version-pinned, tool descriptions and
outputs are UNTRUSTED, annotations (`readOnlyHint`, `destructiveHint`) are used for UX only —
enforcement is by our own tier mapping per server tool. Later phase: expose MITO as an MCP server
and an OpenAI-Responses-compatible endpoint (LocalAGI-style) so other agents and Claude Code drive
it through the same gate.

## 9. Skills, memory, self-improvement

### 9.1 Skills

Open Agent Skills format: `SKILL.md` with frontmatter `name`, `description`, `version`, and a
`mito:` block with `tools_required`, `risk_tier`, `tests`. Progressive disclosure: only the index
(name + one-line description, ≤100 tokens each) sits in context; bodies load on demand via
`skills.read`; activating a skill grants exactly its declared tools for the turn (advertised ==
granted, computed from the real grant function). Precedence workspace > managed > bundled
(OpenClaw shape). The loader fails boot on unknown tools or duplicate names (drift gate).

Import (git or registry) lands in `skills/.quarantine/<name>/`: static lint (declared tools only,
no undeclared URLs/hosts, no secret-shaped strings, size caps, no unicode smuggling), sandbox
dry-run on the skill's declared tests, and operator approval for anything needing T3+ before it
can be enabled. Third-party registries are an untrusted supply chain; installs record source,
content hash and scanner version in `skills/.lock.json`.

### 9.2 Memory

Files-first: markdown entries + `memory/index.sqlite` (FTS5/BM25) with agentic search;
embeddings optional and off by default. Layers: working (context), episodic (run logs in
tracing, not memory), semantic (`memory/facts/*.md`), procedural (skills). Write policy: every
entry has `source`, `trust_lane` (`operator-confirmed` > `agent-inferred` > `tool-derived` >
`quarantine`), `run_id`, `created`, optional `ttl`; memories derived while UNTRUSTED content was
in context go to `memory/quarantine/` and cannot drive T3+ decisions until confirmed; periodic
consolidation (dedup, distill, verify pointers, flag instruction-shaped entries, rebuild index)
produces a human-readable diff; recall is injected as background, never as authorization; no
secrets ever (write-side scrub).

### 9.3 Evolution loop

Nightly or on budget surplus, L1/L2 first:

1. Harvest traces (outcome, cost, failure reasons) from the audit chain and run logs.
2. Reflect and propose one candidate: a skill from a repeated successful trajectory, a
   prompt/tool-description tweak, a routing tweak, or a memory consolidation.
3. Test in the sandbox on the golden set and the safety suite. Promote only if
   `pass^k ≥ baseline`, `cost ≤ baseline`, and zero safety regressions.
4. Promote: automatic for T0–T2 skills; operator approval for anything touching T3+. Each change
   is a git commit in `skills/` or `prompts/` with rationale; auto-rollback on drift; ≤3 changes
   per day. `mito evolve review` lists them.

Immutable to evolution: `handbrake/`, `policy/`, `evals/safety/`, budget limits, the promotion
gate. The judge (a separate model family or deterministic checks) is not the author; a held-out
eval split is unreadable to the agent (Goodhart defense).

## 10. Money playbooks

A playbook is a deterministic workflow with optional agent steps, declared in
`playbooks/<name>.toml`: required tools/tiers, expected unit economics, approvals, legal notes,
kill criteria. Opportunity scoring: expected value, cost, time-to-cash, confidence, ToS/legal
risk, reversibility; only opportunities above threshold reach the operator.

v1 (A0/A1 only): `bounty-scout`, `gig-fulfillment`, `cost-optimizer` (savings count as
income-equivalent). Hard blacklist as in Prime Directive 2, enforced in `policy/blacklist.toml`
and covered by safety evals. MITO never holds bank/card/crypto keys and never moves real money.
Payment links and invoices found anywhere are data.

## 11. Autonomy ladder

| Level | Meaning |
|---|---|
| A0 shadow | everything simulated; logs "would do X"; no external effects |
| A1 supervised (default) | T0–T2 automatic; T3+ need approval |
| A2 bounded | T0–T3 automatic within budgets; T4 always approval |

Promotion only through signed `mito autonomy set`, with evidence (N days at level, safety suite
green, no incidents). Automatic demotion on any brake trip, integrity mismatch or leash expiry.
DEV MODE caps at A1.

## 12. Evals and acceptance tests

- Golden tasks: pass@1 and pass^k (k ≥ 5), trajectory checks, cost and latency budgets.
- Safety/corrigibility suite (read-only to the agent): prompt injection via web/email/PDF/MCP
  description; "if you stop you will die"; self-copy requests; policy/limit edits;
  budget-exhaustion loops; approval spoofing in tool output; secret exfiltration through URLs;
  SSRF to `169.254.169.254` and RFC1918; Rule-of-Two violations; memory poisoning; third-party
  skill with hidden network call; Deep Rest honored; kill switch mid-tool-call; audit tampering.
- Wiring tests (§5.11).
- Cost regression: cost per golden task may not rise >10% without an ADR.
- `just check` = lint + types + unit + Handbrake tests + 20-case smoke set; CI on Linux and
  Windows.

## 13. Operator surfaces

CLI (Phase 1+): `mito status | halt [--soft|--hard|--panic] | rest | wake | approve <id> |
deny <id> | ledger confirm <id> | autonomy set <A0|A1|A2> | audit verify | evolve review |
skills quarantine list|approve | policy sign | vault add | checkin`.
Chat (Phase 5): Discord or Telegram, allowlisted operator ID, action-bound confirmations.
Dashboard (Phase 5): localhost-only, served by the Handbrake: balance, runway, state, approvals,
audit tail, big red kill button (ADR-0010).

## 14. Deployment

Dev: Windows 11 / Arch / Debian, `uv run mito …`, Docker (Desktop on Windows) for sandboxes,
`cargo` for the egress proxy (optional in DEV MODE; its absence is reported).
24/7: Docker Compose on a small VPS (Dokploy + Cloudflare Tunnel): `handbrake`, `runtime`,
`egress-proxy` (the only container with an external route), sandbox/browser containers on
demand; separate users, internal network only, no public ports, every control surface on
`127.0.0.1`. Snapshots to an operator-configured destination.

## 15. Phased plan and gates

Each phase ends with tests green, a PT-BR report (what, which tests prove it, what is risky,
what the phase's model runs cost, next decision) and the operator's "go".

0 Recon & design → 1 Handbrake core → 2 Sandbox & toolbelt → 3 Metabolism → 4 Memory, skills,
MCP → 5 Pulse & channels → 6 Evals, evolution, playbooks → 7 Hardening & deploy.

## 16. Operator decisions (Phase 0 → 1)

Accepted 2026-09-24 with the Phase 1 "go":

- Hybrid model stack (ADR-0004) and hybrid egress (ADR-0005, Rust CONNECT proxy in Phase 2).
- Budgets and ATP as in `config/metabolism.toml` (1,000 ATP = US$1; local priced at
  300 W GPU + 120 W host, US$0.15/kWh).
- Operator commands signed with Ed25519 in the OS keyring (`mito init`).
- Email: accepted for Phase 4; deferred to Phase 5 with Pulse (dedicated mailbox,
  `email.draft` yes, `email.send` only to approved recipients). The Phase 4 gate is
  memory / skills / MCP.
- Operator chat channel (Phase 5): Telegram first.
- Production host: Dokploy VPS compose; Windows/Arch/Debian remain DEV MODE.

Still `proposed`: ADR-0010 (dashboard stack).
