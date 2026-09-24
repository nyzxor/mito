"""OpenAI-compatible adapter (ADR-0004): llama.cpp, Ollama /v1, OpenRouter, ... over httpx.

This is one of the two modules in `mito/` allowed to import httpx (evals/wiring). Phase 1 is
non-streaming and local-only: models with a `credential` handle are refused here because the
runtime never holds secrets — cloud calls are routed through the Handbrake egress gateway with
broker-injected credentials in Phase 3.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from mito.gateway.adapters import (
    estimate_prompt_tokens,
    parse_openai_tool_calls,
    render_openai_messages,
    render_openai_tools,
)
from mito.gateway.ir import Message, MeterGrant, ModelRequest, ModelResponse, Provenance, Usage
from mito.gateway.registry import ModelSpec
from mito.gateway.seal import require_grant


class OpenAICompatAdapter:
    def __init__(
        self, *, timeout_s: float = 120.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(timeout=timeout_s, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    def build_body(self, req: ModelRequest, spec: ModelSpec) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": spec.model,
            "messages": render_openai_messages(req.messages),
            spec.params.get("max_tokens_name", "max_tokens"): req.max_tokens,
            "stream": False,
        }
        if req.tools:
            body["tools"] = render_openai_tools(req.tools)
            body["tool_choice"] = req.tool_choice
            if "parallel_tool_calls" in spec.params:
                body["parallel_tool_calls"] = bool(spec.params["parallel_tool_calls"])
        temp = req.temperature if req.temperature is not None else spec.params.get("temperature")
        if temp is not None and spec.params.get("temperature_allowed", True):
            body["temperature"] = float(temp)
        return body

    async def complete(
        self, req: ModelRequest, spec: ModelSpec, grant: MeterGrant
    ) -> ModelResponse:
        require_grant(grant)
        if spec.credential:
            raise NotImplementedError(
                "cloud credentials are injected by the Handbrake egress gateway (Phase 3)"
            )
        t0 = time.monotonic()
        resp = await self._client.post(
            f"{spec.base_url}/chat/completions", json=self.build_body(req, spec)
        )
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.monotonic() - t0
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        calls = parse_openai_tool_calls(msg.get("tool_calls"))
        u = data.get("usage") or {}
        usage = Usage(
            int(u.get("prompt_tokens") or estimate_prompt_tokens(req)),
            int(u.get("completion_tokens") or 0),
            int((u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0),
        )
        finish = str(choice.get("finish_reason") or ("tool_calls" if calls else "stop"))
        stop = "tool_calls" if calls else ("length" if finish == "length" else "stop")
        message = Message(
            "assistant", str(msg.get("content") or ""), calls, provenance=Provenance.SYSTEM
        )
        return ModelResponse(message, usage, stop, spec.id, elapsed_s=elapsed)
