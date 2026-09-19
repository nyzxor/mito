# ADR-0004: Model gateway with two native adapters (OpenAI-compatible + Anthropic); LiteLLM rejected

Status: accepted (revised 2026-09-19 after operator input) · Date: 2026-09-18

## Context

The operator will run a **hybrid** model stack from day one:

- **Local (L1)**: Ollama or llama.cpp server. Both expose an OpenAI-compatible
  `/v1/chat/completions` (llama.cpp `llama-server`; Ollama at `/v1`). Ollama also has its
  native `/api/chat`; we do not need it. *To verify in Phase 3 against the installed versions:
  tool-calling reliability and `usage` reporting differ per server/model (guide Ch 06 rule 10).*
- **Cloud OpenAI-compatible (L2/L3)**: OpenRouter, DeepSeek, Groq, Mistral, OpenAI, etc.
- **Anthropic (L2/L3)**: native Messages API, with features we must not flatten: `tool_use` /
  `tool_result` content blocks, all parallel results in one user message, explicit prompt-cache
  breakpoints (`cache_control`), thinking blocks that must be echoed back verbatim.

The guide (Ch 06) warns that "OpenAI-compatible" ends at tool calls and streaming, and that
gateways which normalize everything silently strip provider-native state.

## Options

1. **LiteLLM** for everything: broad coverage, but a heavy dependency tree, a second source of
   truth for model names/prices, and documented gaps for Anthropic extended thinking + tools.
2. **Provider SDKs** (`openai`, `anthropic`): two SDKs, two dependency trees, ~fine but each SDK
   brings its own retry/streaming semantics we would have to wrap anyway.
3. **Own message IR + two thin `httpx` adapters** behind one `ModelGateway.call`:
   `OpenAICompatAdapter` (local + cloud OpenAI-shaped) and `AnthropicAdapter` (native Messages
   API). Per-model param table and prices in `config/models.toml`; record/replay built into the
   gateway, above the adapters, so fixtures are adapter-agnostic.

## Decision

Option 3. Rationale: the ledger needs our own price table anyway; two wire formats are a bounded
amount of code (~300 lines each incl. streaming accumulators) that we fully own and test; native
Anthropic caching and thinking round-trips are exactly the things a normalizing gateway loses.
Adding a third adapter (e.g., Gemini) is deliberate work behind the same interface.

Shape:

```
ModelGateway.call(request: ModelRequest) -> ModelResponse     # the ONLY model entry point
  ├─ router picks model id (tier × task class × metabolic state × capabilities)
  ├─ budget pre-flight (Handbrake) → grant + meter id
  ├─ replay/record layer (hash of model id + canonical IR)
  ├─ adapter = adapters[model.provider]   # "openai_compatible" | "anthropic"
  │     render IR → wire; stream; accumulate tool-call fragments; parse usage + cache stats
  └─ reconcile (Handbrake posts cost to ledger) ; audit
```

IR rules: messages are our own dataclasses; provider-specific opaque state (Anthropic thinking
blocks with signatures) is carried as an opaque `provider_state` field and echoed by the same
adapter only; caches are per model — the router never swaps models mid-session (spawn a subagent
instead).

Capability flags in `models.toml` drive rendering: `tools`, `json_schema`, `parallel_tools`,
`cache_explicit` (Anthropic breakpoints), `cache_auto` (OpenAI prefix), `thinking`, `vision`,
and the param table (`temperature_allowed`, `max_tokens_name`).

## Consequences

- Phase 1 ships `OpenAICompatAdapter` + `ScriptedModel`/`ReplayModel`; Phase 3 ships
  `AnthropicAdapter` and the local-tier verification suite (tool-use eval per configured model).
- We own and test streaming chunk-boundary cases for both formats.
- Every configured model must pass the tool-use eval before the router may select it.

## Dependency cost

`httpx` only (already required). No `openai`, `anthropic` or `litellm` packages.
