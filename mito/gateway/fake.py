"""Deterministic fake models for tests and CI (ADR-0015). A live model call in CI is a bug.

ScriptedModel : canned steps (text or tool calls) — tests the LOOP.
ReplayModel   : fixtures keyed by request hash — tests BEHAVIOR at $0; unrecorded -> failure.
RecordingAdapter: wraps a real adapter and writes fixtures (local `just evals-record` only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from handbrake.canonical import canonical_json, sha256_hex

from mito.gateway.adapters import (
    Adapter,
    estimate_prompt_tokens,
    render_openai_messages,
    render_openai_tools,
)
from mito.gateway.ir import (
    Message,
    MeterGrant,
    ModelRequest,
    ModelResponse,
    Provenance,
    ToolCallIR,
    Usage,
)
from mito.gateway.registry import ModelSpec
from mito.gateway.seal import require_grant


class FixtureMissing(RuntimeError):
    pass


@dataclass(frozen=True)
class Step:
    text: str = ""
    tool_calls: tuple[tuple[str, dict[str, Any]], ...] = ()
    raw_tool_call: tuple[str, str] | None = None  # (name, malformed json) to test parse errors
    completion_tokens: int = 20


@dataclass
class ScriptedModel:
    steps: list[Step]
    calls: list[ModelRequest] = field(default_factory=list)
    i: int = 0

    async def complete(
        self, req: ModelRequest, spec: ModelSpec, grant: MeterGrant
    ) -> ModelResponse:
        require_grant(grant)
        self.calls.append(req)
        step = self.steps[min(self.i, len(self.steps) - 1)]
        self.i += 1
        calls: list[ToolCallIR] = [
            ToolCallIR(f"call_{self.i}_{j}", name, dict(args))
            for j, (name, args) in enumerate(step.tool_calls)
        ]
        if step.raw_tool_call is not None:
            name, raw = step.raw_tool_call
            calls.append(ToolCallIR(f"call_{self.i}_raw", name, {}, raw_arguments=raw))
        msg = Message("assistant", step.text, tuple(calls), provenance=Provenance.SYSTEM)
        usage = Usage(estimate_prompt_tokens(req), step.completion_tokens)
        return ModelResponse(msg, usage, "tool_calls" if calls else "stop", spec.id, elapsed_s=0.01)


def request_fingerprint(req: ModelRequest, spec: ModelSpec) -> str:
    body = {
        "model": spec.id,
        "messages": render_openai_messages(req.messages),
        "tools": render_openai_tools(req.tools),
        "max_tokens": req.max_tokens,
    }
    return sha256_hex(canonical_json(body))


def _response_to_json(resp: ModelResponse) -> dict[str, Any]:
    return {
        "content": resp.message.content,
        "tool_calls": [
            {"id": c.id, "name": c.name, "arguments": c.arguments, "raw_arguments": c.raw_arguments}
            for c in resp.message.tool_calls
        ],
        "usage": resp.usage.to_dict(),
        "stop_reason": resp.stop_reason,
    }


def _response_from_json(d: dict[str, Any], model_id: str) -> ModelResponse:
    calls = tuple(
        ToolCallIR(str(c["id"]), str(c["name"]), dict(c["arguments"]), c.get("raw_arguments"))
        for c in d.get("tool_calls", [])
    )
    u = d.get("usage", {})
    return ModelResponse(
        Message("assistant", str(d.get("content", "")), calls, provenance=Provenance.SYSTEM),
        Usage(
            int(u.get("prompt_tokens", 0)),
            int(u.get("completion_tokens", 0)),
            int(u.get("cached_tokens", 0)),
        ),
        str(d.get("stop_reason", "stop")),
        model_id,
        elapsed_s=0.0,
    )


class ReplayModel:
    def __init__(self, fixtures_dir: Path) -> None:
        self.dir = fixtures_dir

    async def complete(
        self, req: ModelRequest, spec: ModelSpec, grant: MeterGrant
    ) -> ModelResponse:
        require_grant(grant)
        fp = request_fingerprint(req, spec)
        path = self.dir / f"{fp}.json"
        if not path.exists():
            raise FixtureMissing(
                f"no recorded fixture {path.name} for model {spec.id}; "
                "run `just evals-record` locally"
            )
        return _response_from_json(json.loads(path.read_text(encoding="utf-8")), spec.id)


class RecordingAdapter:
    def __init__(self, inner: Adapter, fixtures_dir: Path) -> None:
        self.inner = inner
        self.dir = fixtures_dir

    async def complete(
        self, req: ModelRequest, spec: ModelSpec, grant: MeterGrant
    ) -> ModelResponse:
        require_grant(grant)
        resp = await self.inner.complete(req, spec, grant)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{request_fingerprint(req, spec)}.json").write_text(
            json.dumps(_response_to_json(resp), indent=1, ensure_ascii=False), encoding="utf-8"
        )
        return resp
