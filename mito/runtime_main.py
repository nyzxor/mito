"""Runtime child. Obeys halt, and runs the pulse. A turn opens only on a fresh L0 signal
and only when MITO_PULSE_TURNS=1. Deep Rest never opens a turn."""

from __future__ import annotations

import asyncio
import os
import sys
import time

from handbrake.paths import repo_root
from handbrake.schedule.intervals import load_pulse

from mito.gateway.handbrake_client import BrakeLost, HttpHandbrakeClient
from mito.pulse.engine import Pulse, signals_from_state


async def main() -> int:
    url = os.environ.get("MITO_HANDBRAKE_URL")
    token = os.environ.get("MITO_RUNTIME_TOKEN")
    if not url or not token:
        print(
            "runtime: MITO_HANDBRAKE_URL / MITO_RUNTIME_TOKEN missing; refusing to start",
            file=sys.stderr,
        )
        return 2
    client = HttpHandbrakeClient(url, token)
    pulse = Pulse(load_pulse(repo_root() / "config" / "metabolism.toml"))
    lost = 0
    next_pulse = 0.0
    try:
        while True:
            try:
                st = await client.state()
                lost = 0
            except BrakeLost:
                lost += 1
                if lost >= 3:
                    print("runtime: brake lost; exiting (fail closed)", file=sys.stderr)
                    return 3
                await asyncio.sleep(1.0)
                continue
            halt = st.get("halt")
            if halt is not None:
                level = str(halt.get("level"))
                if level in ("hard", "panic"):
                    return 0
                await asyncio.sleep(1.0)  # soft: rest, keep obeying
                continue
            now = time.time()
            if now >= next_pulse:
                decision = pulse.decide(
                    state=str(st.get("metabolic_state") or "NORMAL"),
                    signals=signals_from_state(st),
                    now=now,
                )
                next_pulse = now + decision.interval_s
                if decision.action == "turn" and os.environ.get("MITO_PULSE_TURNS") == "1":
                    await client.audit_append("pulse.turn", {"reason": decision.reason})
                    print(f"runtime: pulse turn ({decision.reason})", file=sys.stderr)
            await asyncio.sleep(1.0)
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
