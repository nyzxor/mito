"""Smoke-set placeholder so `just evals-smoke` has a target in Phase 0.

Phase 1 replaces this with the 20-case golden smoke set on the replay model (ADR-0015).
"""

import pytest

pytestmark = pytest.mark.smoke


def test_smoke_set_not_yet_recorded() -> None:
    # Deliberately trivial: the real cases arrive with the loop in Phase 1.
    assert True
