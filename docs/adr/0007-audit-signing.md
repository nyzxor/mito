# ADR-0007: Audit chain, hash pins and signed operator commands

Status: accepted · Date: 2026-09-18

## Context

Three needs: (1) an append-only audit log whose tampering is detectable; (2) hash pins for
`handbrake/`, `policy/`, `evals/safety/`, budgets and the promotion gate; (3) operator commands
that change limits (`policy sign`, `autonomy set`, `ledger confirm`, allowlist changes) must be
unforgeable by the runtime.

## Options

1. Plain JSONL + file permissions (undetectable edits by anyone with the file).
2. Hash-chained JSONL (detects edits/truncation except a full rewrite from the tampered point
   with recomputed hashes) + periodic **HMAC anchors** with a key only the Handbrake holds
   (defeats recompute by the runtime) + optional external anchor (daily digest sent to the
   operator channel contains the latest anchor).
3. Merkle tree / transparency log (overkill for one operator).

For signing: (a) Ed25519 keypair, private key in the operator's OS keyring, public key pinned
in `control/operator.pub`; (b) shared passphrase (weaker; replayable).

## Decision

Option 2 + Ed25519 (a).

- Record: `{seq, ts, kind, payload, prev_hash, hash}`, `hash = sha256(prev_hash ‖ canonical_json(payload))`.
- Anchor every 100 records and at rotation: `{anchor_seq, hmac_sha256(key_hb, hash_at_seq)}`.
- `mito audit verify` recomputes the chain and validates anchors; reports first divergence.
- Pins: `control/pins.json` = `{path: sha256}` over the pinned trees + an Ed25519 signature by the
  operator key. Integrity checker verifies signature then hashes.
- Signed commands: `mito <cmd> …` builds a canonical message `{cmd, args, nonce, ts}`, signs with
  the operator key (keyring prompt/agent), sends to the Handbrake with the operator token; the
  Handbrake verifies signature, freshness (±5 min) and nonce uniqueness, then audits it.

## Consequences

- Operator must generate a key once (`mito init` does it; stored via `keyring`).
- Loss of the private key = no more limit changes until re-keyed via a documented recovery
  (RUNBOOK) that requires stopping the system.
- The daily digest doubles as an out-of-band anchor.

## Dependency cost

`cryptography` (Ed25519; well-maintained wheels on all three OSes) and `keyring` (OS keyring
access). Both justified: no stdlib Ed25519; keyring avoids secrets on disk in dev.
