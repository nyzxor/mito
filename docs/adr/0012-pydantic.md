# ADR-0012: Schemas and validation with pydantic v2

Status: accepted · Date: 2026-09-18

## Context

We need JSON Schema for tool parameters (sent to models), strict validation of tool arguments
returned by models, config validation (ADR-0011), the Handbrake API request/response models, and
structured outputs for extraction/classification.

## Options

1. `dataclasses` + hand-written JSON Schema (drift between the two; no strict parsing).
2. `pydantic` v2 (Rust core, JSON Schema generation, strict mode, ~2 wheels).
3. `msgspec` (fast, smaller, but JSON Schema generation is less complete for our needs).

## Decision

pydantic v2. One model per tool's parameters generates the schema the model sees **and**
validates what the model returns — a single source of truth (guide Ch 03/05). Validation errors
are rendered as error-as-instruction text for the retry.

## Consequences

- Schema subsets differ per provider (guide Ch 06); the model gateway strips unsupported
  constraints per `models.toml` capability flags.
- Tool descriptions live in the model's docstring/`Field(description=…)`; their token size is
  measured in CI.

## Dependency cost

`pydantic` (+ `pydantic-core`, `typing-extensions`, `annotated-types`). Accepted as one of the
few runtime dependencies.
