# ADR-0004: Thin OpenAI-compatible client first; LiteLLM deferred

Status: accepted · Date: 2026-09-18

## Context

Tiers L1 (llama.cpp/Ollama), L2 (cheap cloud) and L3 (frontier) must be reachable with metering,
prompt-cache awareness and a record/replay mode for tests. The guide (Ch 06) recommends a
gateway for the commodity 80% but warns that "OpenAI-compatible" ends at tool calls and
streaming, and that gateways can strip provider-native state (thinking blocks, cache controls).

## Options

1. **LiteLLM**: broad provider coverage, cost tables, but a heavy dependency (large transitive
   tree, frequent releases) and a second place where model names/prices live.
2. **Provider SDKs** (openai, anthropic, google): three wire formats, three SDKs.
3. **Thin `httpx` client** for the OpenAI-compatible wire format (chat completions with tools,
   streaming with `include_usage`), our own message IR, per-model param table from
   `config/models.toml`, our own price table (single source of truth), record/replay built in.

## Decision

Option 3 for Phase 1–3. Rationale: L1 is OpenAI-compatible by definition; most L2 providers
(OpenRouter, DeepSeek, Groq, Mistral, OpenAI) speak it; and `models.toml` must hold prices
anyway for the ATP ledger, so LiteLLM's cost tables would be a second source of truth.

Adopt an Anthropic-native adapter (or LiteLLM) only when an L3 model that needs native features
(thinking blocks, explicit cache breakpoints) is actually configured — that is a routing
decision the operator makes in `models.toml`, and the adapter is added behind the same
`ModelGateway.call`.

## Consequences

- We own the tool-call fragment accumulator for streaming (guide impl-01 §5) and test chunk
  boundaries.
- Per-model param table (`temperature` allowed?, `max_tokens` name, `parallel_tool_calls`)
  lives in `models.toml`.
- Record/replay: requests are hashed (model + canonical messages + tools); fixtures in
  `evals/fixtures/`; unrecorded requests fail in CI.

## Dependency cost

`httpx` (already required by ADR-0002). No SDKs.
