"""Dispatch seal. Only `mito.loop.gate` may import `_GATE_SEAL` (enforced by evals/wiring).
Tool handlers call `require_dispatch` so a direct handler call without the Gate fails loudly."""

from __future__ import annotations

from dataclasses import dataclass

from handbrake.core import DispatchTicket

from mito.gateway.ir import UnpluggedWire

_GATE_SEAL: object = object()


@dataclass(frozen=True)
class Dispatch:
    ticket: DispatchTicket
    seal: object


def require_dispatch(d: Dispatch | None) -> Dispatch:
    if d is None or d.seal is not _GATE_SEAL:
        raise UnpluggedWire("tool handler reached without a Gate dispatch")
    return d
