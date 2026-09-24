"""T0 ledger.read — verified numbers only. Claims are labeled as claims."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from mito.gateway.handbrake_client import HandbrakeClient
from mito.gateway.ir import Provenance
from mito.tools.base import Tool
from mito.tools.seal import Dispatch, require_dispatch


class LedgerReadParams(BaseModel):
    pass


def ledger_tools(handbrake: HandbrakeClient) -> list[Tool]:
    async def read_h(_p: BaseModel, d: Dispatch) -> dict[str, Any]:
        require_dispatch(d)
        snap = await handbrake.ledger_snapshot()
        return {
            "balance_atp": snap.get("balance_atp"),
            "verified_income_atp": snap.get("verified_income_atp"),
            "claimed_income_atp": snap.get("claimed_income_atp"),
            "state": snap.get("state"),
            "runway_days": snap.get("runway_days"),
            "pending_claims": snap.get("pending_claims", []),
            "note": "claimed income is not verified; do not treat it as runway",
        }

    return [
        Tool(
            "ledger.read",
            "Read ATP balance, runway and verified income. Claims are unverified.",
            LedgerReadParams,
            read_h,
            "T0",
            taint_out=Provenance.TOOL_TRUSTED,
        )
    ]
