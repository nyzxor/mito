"""Adapter protocol + shared rendering helpers for the OpenAI-compatible wire format."""

from __future__ import annotations

import json
from typing import Any, Protocol

from mito.gateway.ir import Message, MeterGrant, ModelRequest, ModelResponse, ToolCallIR, ToolSpec
from mito.gateway.registry import ModelSpec


class Adapter(Protocol):
    async def complete(
        self, req: ModelRequest, spec: ModelSpec, grant: MeterGrant
    ) -> ModelResponse: ...


def render_openai_messages(messages: tuple[Message, ...]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        d: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.raw_arguments
                        if tc.raw_arguments is not None
                        else json.dumps(tc.arguments, sort_keys=True),
                    },
                }
                for tc in m.tool_calls
            ]
        if m.role == "tool":
            d["tool_call_id"] = m.tool_call_id
            if m.name:
                d["name"] = m.name
        out.append(d)
    return out


def render_openai_tools(tools: tuple[ToolSpec, ...]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools
    ]


def parse_openai_tool_calls(raw: list[dict[str, Any]] | None) -> tuple[ToolCallIR, ...]:
    calls: list[ToolCallIR] = []
    for i, tc in enumerate(raw or []):
        fn = tc.get("function", {})
        args_raw = fn.get("arguments", "") or "{}"
        try:
            parsed = json.loads(args_raw)
            if not isinstance(parsed, dict):
                raise ValueError("arguments must be an object")
            calls.append(
                ToolCallIR(str(tc.get("id") or f"call_{i}"), str(fn.get("name", "")), parsed)
            )
        except (ValueError, TypeError):
            calls.append(
                ToolCallIR(
                    str(tc.get("id") or f"call_{i}"),
                    str(fn.get("name", "")),
                    {},
                    raw_arguments=str(args_raw),
                )
            )
    return tuple(calls)


def approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def estimate_prompt_tokens(req: ModelRequest) -> int:
    n = sum(approx_tokens(m.content) + 4 for m in req.messages)
    n += sum(
        approx_tokens(json.dumps(t.parameters)) + approx_tokens(t.description) + 8
        for t in req.tools
    )
    return n
