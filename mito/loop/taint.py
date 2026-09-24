"""Value-level provenance wrapper (DESIGN §5.4, ADR-0016).

The model never sees or edits lane tags. `Tainted` is harness metadata: a value can gain taint
(UNTRUSTED is sticky) but can never be retagged to a more trusted lane. Session A/B/C flags are
derived from lanes + tool sensitivity, not from text classifiers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from mito.gateway.ir import Provenance

_RANK = {
    Provenance.OPERATOR: 0,
    Provenance.SYSTEM: 1,
    Provenance.TOOL_TRUSTED: 2,
    Provenance.UNTRUSTED: 3,
}


class TaintError(ValueError):
    """Raised when a caller tries to strip or downgrade provenance."""


def rank(lane: Provenance) -> int:
    return _RANK[lane]


def worse(a: Provenance, b: Provenance) -> Provenance:
    return a if rank(a) >= rank(b) else b


@dataclass(frozen=True)
class Tainted[T]:
    value: T
    lane: Provenance

    def retag(self, lane: Provenance) -> Tainted[T]:
        if rank(lane) < rank(self.lane):
            raise TaintError(f"cannot downgrade provenance {self.lane.value} -> {lane.value}")
        return Tainted(self.value, lane)

    def map[U](self, fn: Callable[[T], U]) -> Tainted[U]:
        return Tainted(fn(self.value), self.lane)

    def merge(self, other: Tainted[T], combine: Callable[[T, T], T]) -> Tainted[T]:
        return Tainted(combine(self.value, other.value), worse(self.lane, other.lane))


def wrap[T](value: T, lane: Provenance) -> Tainted[T]:
    """Attach a lane. The only constructor Gate.dispatch uses for tool results."""
    return Tainted(value, lane)


def tag_provenance[T](value: T, lane: Provenance) -> Tainted[T]:
    return wrap(value, lane)


def flags_from(
    lanes: Iterable[Provenance],
    *,
    sensitive: bool = False,
    external: bool = False,
) -> frozenset[str]:
    flags: set[str] = set()
    if any(lane is Provenance.UNTRUSTED for lane in lanes):
        flags.add("A")
    if sensitive:
        flags.add("B")
    if external:
        flags.add("C")
    return frozenset(flags)
