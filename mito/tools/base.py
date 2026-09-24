"""Tool model (DESIGN §8, guide Ch 03). One pydantic model per tool = JSON schema for the model
AND validator for what comes back. Errors are written for the model (retry instructions)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from mito.gateway.ir import Provenance, ToolSpec
from mito.tools.seal import Dispatch

Handler = Callable[[BaseModel, Dispatch], Awaitable[Any]]


class ToolError(Exception):
    """Raise from handlers with a message written FOR THE MODEL."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    handler: Handler
    risk_tier: str
    idempotent: bool = True
    reversible: bool = True
    max_result_tokens: int = 2000
    taint_out: Provenance = Provenance.TOOL_TRUSTED
    credential_handles: tuple[str, ...] = ()
    core: bool = True  # in the always-loaded set (≤10); others come via tool_search
    tags: tuple[str, ...] = field(default_factory=tuple)

    def spec(self) -> ToolSpec:
        schema = self.params.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(self.name, self.description, schema)

    def validate(self, args: dict[str, Any]) -> BaseModel:
        try:
            return self.params.model_validate(args)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}"
                for e in exc.errors()[:5]
            )
            raise ToolError(
                f"invalid arguments for {self.name}: {problems}. Fix the arguments and call again."
            ) from exc


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def specs(self, names: list[str] | None = None) -> tuple[ToolSpec, ...]:
        chosen = self.names() if names is None else [n for n in names if n in self._tools]
        return tuple(self._tools[n].spec() for n in chosen)

    def core_names(self) -> list[str]:
        return sorted(n for n, t in self._tools.items() if t.core)

    def description_tokens(self) -> int:
        import json

        return sum(
            len(json.dumps(s.parameters)) // 4 + len(s.description) // 4 for s in self.specs()
        )
