"""Taint algebra: provenance is monotonic; UNTRUSTED is sticky (ADR-0016)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mito.gateway.ir import Provenance
from mito.loop.taint import TaintError, flags_from, rank, wrap

pytestmark = pytest.mark.unit

_LANES = list(Provenance)


def test_wrap_attaches_lane() -> None:
    t = wrap("hello", Provenance.UNTRUSTED)
    assert t.value == "hello" and t.lane is Provenance.UNTRUSTED


def test_map_preserves_lane() -> None:
    t = wrap("ab", Provenance.UNTRUSTED).map(str.upper)
    assert t.value == "AB" and t.lane is Provenance.UNTRUSTED


def test_cannot_downgrade_untrusted_to_operator() -> None:
    t = wrap("x", Provenance.UNTRUSTED)
    with pytest.raises(TaintError, match="downgrade"):
        t.retag(Provenance.OPERATOR)


def test_merge_takes_worse_lane() -> None:
    a = wrap("a", Provenance.TOOL_TRUSTED)
    b = wrap("b", Provenance.UNTRUSTED)
    m = a.merge(b, lambda x, y: x + y)
    assert m.value == "ab" and m.lane is Provenance.UNTRUSTED


def test_untrusted_sets_flag_a() -> None:
    assert flags_from([Provenance.UNTRUSTED]) == frozenset({"A"})
    assert flags_from([Provenance.TOOL_TRUSTED], sensitive=True) == frozenset({"B"})
    assert flags_from([], external=True) == frozenset({"C"})


@given(src=st.sampled_from(_LANES), dst=st.sampled_from(_LANES))
def test_retag_never_downgrades(src: Provenance, dst: Provenance) -> None:
    t = wrap("x", src)
    if rank(dst) < rank(src):
        with pytest.raises(TaintError):
            t.retag(dst)
    else:
        assert t.retag(dst).lane is dst
