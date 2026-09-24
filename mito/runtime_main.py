"""Runtime child process entry point. Phase 1: idle heartbeat that obeys the Handbrake — polls
/state, exits on hard/panic halt, rests on soft halt. The pulse and playbooks arrive in Phase 5;
`mito run` drives a single turn on demand meanwhile."""

from __future__ import annotations

import asyncio
import os
import sys

from mito.gateway.handbrake_client import BrakeLost, HttpHandbrakeClient


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
    lost = 0
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
            await asyncio.sleep(1.0)
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
