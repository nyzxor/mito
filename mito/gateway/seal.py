"""The meter seal. Only `mito.gateway.gateway` may import `_SEAL` (enforced by evals/wiring).
Adapters call `require_grant` so a direct adapter call without the gateway fails loudly."""

from __future__ import annotations

from mito.gateway.ir import MeterGrant, UnpluggedWire

_SEAL: object = object()


def require_grant(grant: MeterGrant | None) -> MeterGrant:
    if grant is None or grant.seal is not _SEAL:
        raise UnpluggedWire("model adapter reached without a ModelGateway meter grant")
    return grant
