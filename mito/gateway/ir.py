"""Internal message IR (ADR-0004). Provider wire formats are rendered at the adapter edge."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Provenance(StrEnum):
    OPERATOR = "OPERATOR"
    SYSTEM = "SYSTEM"
    TOOL_TRUSTED = "TOOL_TRUSTED"
    UNTRUSTED = "UNTRUSTED"


class UnpluggedWire(RuntimeError):
    """Raised when a tool handler or model adapter is reached without passing the gate/meter."""


@dataclass(frozen=True)
class ToolCallIR:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str | None = None  # kept when JSON parsing failed


@dataclass(frozen=True)
class Message:
    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: tuple[ToolCallIR, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None
    provenance: Provenance = Provenance.SYSTEM


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
        }


@dataclass(frozen=True)
class ModelRequest:
    session: str
    task: str
    task_class: str  # classify | extract | draft | plan | review | code
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...] = ()
    max_tokens: int = 1024
    temperature: float | None = None
    tier_request: str | None = None  # force a tier (router otherwise decides)
    tool_choice: str = "auto"


@dataclass(frozen=True)
class ModelResponse:
    message: Message
    usage: Usage
    stop_reason: str  # stop | tool_calls | length | error
    model_id: str
    cost_usd: float = 0.0
    elapsed_s: float = 0.0
    provider_state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MeterGrant:
    """Issued by ModelGateway after Handbrake pre-flight. Adapters refuse to run without one."""

    meter_id: str
    model_id: str
    seal: object
