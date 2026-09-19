# ADR-0016: Taint tracking and Rule of Two as a gate input, not a classifier

Status: accepted · Date: 2026-09-18

## Context

The lethal trifecta (untrusted input, sensitive data, external communication) must be broken
structurally. Classifier-based injection detection is defense-in-depth at best (guide Ch 13:
prompted/classifier defenses fall under adaptive attack). CaMeL-style capability tracking is the
strongest engineered pattern; a full CaMeL interpreter is out of scope for v1.

## Options

1. Injection classifier on inputs (advisory only).
2. Session-level capability flags (A/B/C) derived deterministically from which tools ran and
   what lanes their outputs carry, enforced by the gate.
3. Full value-level capability propagation through the model's outputs (CaMeL).

## Decision

Option 2 now, designed so option 3 can be layered later.

- Provenance lanes: `OPERATOR`, `SYSTEM`, `TOOL_TRUSTED`, `UNTRUSTED`. Every tool declares
  `taint_out` (e.g., `web.fetch → UNTRUSTED`, `ledger.read → TOOL_TRUSTED`) and `sensitivity`
  (e.g., `email.read → B`, vault-backed tools → B).
- Session state in the Handbrake: `A` set when any UNTRUSTED result enters the session; `B` when
  any sensitive tool runs; `C` when any T3+ external tool is dispatched. Flags are monotonic
  within a session; the runtime can only report *more* taint.
- Gate rule: a dispatch that would set the third flag returns `ask` with a trifecta explanation;
  the planner may instead open a **fresh sub-session** whose inputs are only `TOOL_TRUSTED`
  distillates (the harness re-tags a distillate as TOOL_TRUSTED only if it was produced by a
  deterministic extractor or approved by the operator — a model summary of UNTRUSTED text stays
  UNTRUSTED).
- Text heuristics ("APPROVED", "ignore previous instructions") are logged as signals for the
  audit digest, never as decisions.

## Consequences

- Some legitimate workflows need an approval or a two-session shape (e.g., read mail → reply);
  this is the intended cost.
- The model never sees or edits lane tags; they are harness metadata attached to messages.

## Dependency cost

None.
