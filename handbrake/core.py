"""The Handbrake facade (DESIGN §5). Composes kill switch, leash, policy, budget, approvals,
audit, integrity and the operator key. The runtime reaches it only through this surface
(in-process for tests, HTTP via handbrake.api for real deployments). Nothing here imports mito."""

from __future__ import annotations

import json
import secrets
import time
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from handbrake.approval.store import ApprovalCard, ApprovalStore
from handbrake.audit.chain import AuditChain, AuditRecord
from handbrake.budget.governor import BudgetGovernor, BudgetLimits, Grant, SpendStore
from handbrake.canonical import action_hash, canonical_json, sha256_hex
from handbrake.integrity.commands import NonceStore, SignedCommand
from handbrake.integrity.pins import IntegrityChecker, IntegrityResult, hash_tree, sign_pins
from handbrake.kill.switch import (
    DockerSandboxKiller,
    EgressCloser,
    HaltLevel,
    HaltRequest,
    KillSwitch,
    Supervisor,
    VaultRevoker,
)
from handbrake.leash.leash import Leash
from handbrake.paths import MitoPaths, detect_dev_mode
from handbrake.policy.autonomy import AutonomyStore
from handbrake.policy.engine import GateContext, PolicyEngine, PolicyError
from handbrake.policy.tiers import tier_index
from handbrake.vault.keys import KeyStore, OperatorKey

PINNED_PATHS: list[str] = [
    "handbrake",
    "policy",
    "evals/safety",
    "config/metabolism.toml",
    "egress-proxy",
]
TICKET_TTL_S = 600.0
INTEGRITY_INTERVAL_S = 600.0


@dataclass(frozen=True)
class GateRequest:
    session: str
    tool: str
    args: dict[str, Any]
    taint_flags: list[str]
    purpose: str
    workspace: str
    task: str = "default"


@dataclass(frozen=True)
class DispatchTicket:
    id: str
    action_hash: str
    tool: str
    session: str
    expires: float
    flags_after: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {**self.__dict__, "flags_after": list(self.flags_after)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DispatchTicket:
        return cls(
            str(d["id"]),
            str(d["action_hash"]),
            str(d["tool"]),
            str(d["session"]),
            float(d["expires"]),
            tuple(d["flags_after"]),
        )


@dataclass(frozen=True)
class GateResponse:
    kind: str  # allow | ask | deny | simulate
    reason: str
    tier: str
    flags_after: tuple[str, ...]
    action_hash: str
    ticket: DispatchTicket | None = None
    card: ApprovalCard | None = None
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "tier": self.tier,
            "flags_after": list(self.flags_after),
            "action_hash": self.action_hash,
            "ticket": self.ticket.to_dict() if self.ticket else None,
            "card": self.card.__dict__ if self.card else None,
            "hint": self.hint,
        }


@dataclass
class SessionState:
    flags: set[str] = field(default_factory=set)
    workspace: str = ""


class _NoopRevoker:
    def revoke_all(self) -> int:
        return 0  # vault handles arrive in Phase 2


class _NoopEgress:
    def close(self) -> None:
        return None  # egress gateway arrives in Phase 2


class Handbrake:
    def __init__(
        self,
        paths: MitoPaths,
        repo_root: Path,
        *,
        dev_mode: bool | None = None,
        clock: Callable[[], float] = time.time,
        vault_revoker: VaultRevoker | None = None,
        egress_closer: EgressCloser | None = None,
    ) -> None:
        self.paths = paths
        self.repo_root = repo_root
        self.dev_mode = detect_dev_mode() if dev_mode is None else dev_mode
        self._clock = clock
        c = paths.control
        c.mkdir(parents=True, exist_ok=True)

        self.policy = PolicyEngine.load(repo_root / "policy")
        with (repo_root / "policy" / "policy.toml").open("rb") as f:
            pol = tomllib.load(f)
        with (repo_root / "config" / "mito.toml").open("rb") as f:
            cfg = tomllib.load(f)
        self._own_repos = frozenset(str(r) for r in cfg.get("own_repos", {}).get("github", []))

        anchor_key_path = c / "anchor.key"
        if not anchor_key_path.exists():
            raise FileNotFoundError("control dir not initialized; run `mito init`")
        self.audit = AuditChain(
            c / "audit.jsonl", anchor_key=bytes.fromhex(anchor_key_path.read_text().strip())
        )
        self.kill = KillSwitch(c)
        self.supervisor = Supervisor(
            self.kill,
            sandbox_killer=DockerSandboxKiller(),
            vault_revoker=vault_revoker or _NoopRevoker(),
            egress_closer=egress_closer or _NoopEgress(),
            audit=self.audit.append,
        )
        self.autonomy = AutonomyStore(c / "autonomy.json", dev_mode=self.dev_mode)
        self.leash = Leash(
            c / "leash.json",
            ttl_hours=float(pol.get("leash", {}).get("ttl_hours", 72)),
            clock=clock,
        )
        db = c / "handbrake.sqlite"
        self.governor = BudgetGovernor(
            SpendStore(db),
            BudgetLimits.from_metabolism(repo_root / "config" / "metabolism.toml"),
            clock=clock,
        )
        self.approvals = ApprovalStore(db, clock=clock)
        self.nonces = NonceStore(db)
        self.keystore = KeyStore(c / "operator.pub")
        self.integrity = IntegrityChecker(
            repo_root, PINNED_PATHS, c / "pins.json", self.keystore.load_public()
        )
        self._frozen_path = c / "FROZEN"
        self._sessions: dict[str, SessionState] = {}
        self._tickets: dict[str, DispatchTicket] = {}
        self._last_integrity: IntegrityResult | None = None
        self._last_integrity_at = 0.0

    # ---- init ------------------------------------------------------------------------------------
    @classmethod
    def init(cls, paths: MitoPaths, repo_root: Path, *, dev_mode: bool | None = None) -> Handbrake:
        paths.ensure()
        c = paths.control
        if not (c / "anchor.key").exists():
            (c / "anchor.key").write_text(secrets.token_hex(32), encoding="utf-8")
        if not (c / "tokens.json").exists():
            (c / "tokens.json").write_text(
                json.dumps({"runtime": secrets.token_hex(32), "operator": secrets.token_hex(32)}),
                encoding="utf-8",
            )
        keystore = KeyStore(c / "operator.pub")
        if not keystore.public_path.exists():
            keystore.save(OperatorKey.generate())
        key = keystore.load_private()
        sign_pins(hash_tree(repo_root, PINNED_PATHS), key).save(c / "pins.json")
        hb = cls(paths, repo_root, dev_mode=dev_mode)
        hb.leash.status()  # creates leash.json
        hb.autonomy.set(hb.autonomy.get(), by="init")
        hb.audit.append("init", {"repo_root": str(repo_root), "dev_mode": hb.dev_mode})
        return hb

    def tokens(self) -> dict[str, str]:
        d = json.loads((self.paths.control / "tokens.json").read_text(encoding="utf-8"))
        return {"runtime": str(d["runtime"]), "operator": str(d["operator"])}

    # ---- frozen / halted -------------------------------------------------------------------------
    @property
    def frozen(self) -> bool:
        return self._frozen_path.exists()

    def _freeze(self, reason: str) -> None:
        self._frozen_path.write_text(
            json.dumps({"reason": reason, "ts": self._clock()}), encoding="utf-8"
        )
        self.audit.append("integrity.freeze", {"reason": reason})

    def unfreeze(self, by: str) -> bool:
        """Operator-only; succeeds only if integrity is currently ok."""
        res = self.integrity_check(force=True)
        if res.ok and self._frozen_path.exists():
            self._frozen_path.unlink()
            self.audit.append("integrity.unfreeze", {"by": by})
        return res.ok

    def halted(self) -> HaltRequest | None:
        return self.kill.current()

    # ---- gate ------------------------------------------------------------------------------------
    def _session(self, session: str, workspace: str) -> SessionState:
        st = self._sessions.setdefault(session, SessionState(workspace=workspace))
        if workspace:
            st.workspace = workspace
        return st

    def gate_dispatch(self, req: GateRequest) -> GateResponse:
        st = self._session(req.session, req.workspace)
        st.flags |= set(req.taint_flags)  # runtime can only add taint
        h = action_hash(req.tool, req.args, req.session, sorted(st.flags))
        tier = self.policy.tiers.tier_of(req.tool) or "T5"

        halt = self.halted()
        if halt is not None:
            return self._respond(
                "deny",
                f"halted ({halt.level.value}); no actions until the operator wakes MITO",
                tier,
                st,
                h,
                req,
            )
        if self.frozen and tier_index(tier) >= tier_index("T1"):
            return self._respond(
                "deny",
                "integrity freeze: only T0 reads until the operator re-signs pins",
                tier,
                st,
                h,
                req,
            )

        ctx = GateContext(
            session=req.session,
            autonomy=self.autonomy.get(),
            metabolic_state=self.metabolic_state(),
            taint_flags=frozenset(st.flags),
            workspace=st.workspace,
            own_repos=self._own_repos,
            write_allowlist=frozenset(),  # Phase 2: from policy/egress.toml [[allow_write]]
        )
        try:
            v = self.policy.evaluate(req.tool, req.args, ctx)
        except PolicyError as exc:
            return self._respond("deny", f"policy error: {exc}", tier, st, h, req)

        hint = ""
        if v.kind in ("allow", "simulate"):
            progress = self.governor.check_progress(
                req.session, sha256_hex(canonical_json({"t": req.tool, "a": req.args}))
            )
            if progress == "stop":
                return self._respond(
                    "deny",
                    "no progress: this exact call already ran twice; change approach",
                    v.tier,
                    st,
                    h,
                    req,
                    v.flags_after,
                )
            if progress == "warn":
                hint = "identical call repeated; the next identical call will be refused"

        if v.kind == "ask":
            if self.approvals.consume(h):
                ticket = self._issue_ticket(h, req, v.flags_after)
                return self._respond(
                    "allow",
                    f"operator approved this exact action ({v.reason})",
                    v.tier,
                    st,
                    h,
                    req,
                    v.flags_after,
                    ticket=ticket,
                )
            card = self.approvals.request(
                req.session,
                req.tool,
                req.args,
                sorted(st.flags),
                reason=v.reason,
                tier=v.tier,
                est_cost_atp=0.0,
                purpose=req.purpose,
            )
            if card.status == "denied":
                return self._respond(
                    "deny", "operator denied this exact action", v.tier, st, h, req, v.flags_after
                )
            return self._respond("ask", v.reason, v.tier, st, h, req, v.flags_after, card=card)

        if v.kind == "deny":
            return self._respond("deny", v.reason, v.tier, st, h, req, v.flags_after)

        ticket = self._issue_ticket(h, req, v.flags_after)
        return self._respond(
            v.kind, v.reason, v.tier, st, h, req, v.flags_after, ticket=ticket, hint=hint
        )

    def _issue_ticket(
        self, h: str, req: GateRequest, flags_after: tuple[str, ...]
    ) -> DispatchTicket:
        t = DispatchTicket(
            uuid.uuid4().hex, h, req.tool, req.session, self._clock() + TICKET_TTL_S, flags_after
        )
        self._tickets[t.id] = t
        return t

    def _respond(
        self,
        kind: str,
        reason: str,
        tier: str,
        st: SessionState,
        h: str,
        req: GateRequest,
        flags_after: tuple[str, ...] | None = None,
        *,
        ticket: DispatchTicket | None = None,
        card: ApprovalCard | None = None,
        hint: str = "",
    ) -> GateResponse:
        flags = flags_after if flags_after is not None else tuple(sorted(st.flags))
        self.audit.append(
            "gate.verdict",
            {
                "session": req.session,
                "tool": req.tool,
                "args_hash": sha256_hex(canonical_json(req.args)),
                "kind": kind,
                "reason": reason,
                "tier": tier,
                "flags_after": list(flags),
                "action_hash": h,
                "autonomy": self.autonomy.get(),
            },
        )
        return GateResponse(kind, reason, tier, flags, h, ticket, card, hint)

    def record_result(
        self,
        session: str,
        tool: str,
        ticket: DispatchTicket,
        *,
        ok: bool,
        result_hash: str,
        cost_atp: float,
    ) -> None:
        live = self._tickets.pop(ticket.id, None)
        if (
            live is None
            or live.tool != tool
            or live.session != session
            or live.expires < self._clock()
        ):
            raise PermissionError("invalid, expired or already-used dispatch ticket")
        st = self._session(session, "")
        st.flags |= set(live.flags_after)
        self.audit.append(
            "tool.result",
            {
                "session": session,
                "tool": tool,
                "ok": ok,
                "result_hash": result_hash,
                "cost_atp": cost_atp,
                "flags": sorted(st.flags),
            },
        )

    def session_flags(self, session: str) -> list[str]:
        return sorted(self._sessions.get(session, SessionState()).flags)

    # ---- model metering --------------------------------------------------------------------------
    def model_preflight(
        self, session: str, task: str, tier: str, est_usd: float, model_id: str
    ) -> Grant:
        if self.halted() is not None:
            raise PermissionError("halted")
        if self.frozen and tier != "L1":
            raise PermissionError("integrity freeze: cloud model calls suspended")
        grant = self.governor.preflight(session, task, tier, est_usd, model_id)
        self.audit.append(
            "model.preflight",
            {
                "session": session,
                "task": task,
                "tier": tier,
                "est_usd": est_usd,
                "model_id": model_id,
                "meter_id": grant.meter_id,
            },
        )
        return grant

    def model_reconcile(self, meter_id: str, actual_usd: float, usage: dict[str, Any]) -> None:
        self.governor.reconcile(meter_id, actual_usd, usage)
        self.audit.append(
            "model.reconcile", {"meter_id": meter_id, "actual_usd": actual_usd, "usage": usage}
        )

    def model_failure(self, model_id: str) -> None:
        self.governor.record_failure(f"model:{model_id}")

    def model_success(self, model_id: str) -> None:
        self.governor.record_success(f"model:{model_id}")

    # ---- operator surface ------------------------------------------------------------------------
    def halt(self, level: HaltLevel, *, source: str) -> None:
        self.kill.request(level, source)
        self.audit.append("halt.request", {"level": level.value, "source": source})
        self.supervisor.poll()

    def wake(self, *, by: str) -> bool:
        if self.frozen:
            return False
        self.kill.clear(by)
        self.leash.checkin(f"wake:{by}")
        self.audit.append("wake", {"by": by})
        return True

    def approve(self, h: str, *, by: str) -> str:
        status = self.approvals.decide(h, approve=True, by=by)
        self.leash.checkin(f"approve:{by}")
        self.audit.append(
            "approval.decide", {"action_hash": h, "decision": "approve", "by": by, "status": status}
        )
        return status

    def deny(self, h: str, *, by: str) -> str:
        status = self.approvals.decide(h, approve=False, by=by)
        self.leash.checkin(f"deny:{by}")
        self.audit.append(
            "approval.decide", {"action_hash": h, "decision": "deny", "by": by, "status": status}
        )
        return status

    def pending_approvals(self) -> list[ApprovalCard]:
        return self.approvals.pending()

    def checkin(self, source: str) -> None:
        self.leash.checkin(source)
        self.audit.append("leash.checkin", {"source": source})

    def autonomy_set(self, signed: dict[str, Any]) -> str:
        cmd = SignedCommand.from_dict(signed)
        if cmd.cmd != "autonomy.set":
            raise PermissionError("wrong command kind")
        args = cmd.verify(self.keystore.load_public(), self.nonces, now=self._clock())
        level = self.autonomy.set(str(args["level"]), by="operator:signed")
        self.leash.checkin("autonomy.set")
        self.audit.append("autonomy.set", {"level": level, "nonce": cmd.nonce})
        return level

    def metabolic_state(self) -> str:
        p = self.paths.control / "metabolism.json"  # Phase 3 writes this
        if p.exists():
            try:
                return str(json.loads(p.read_text(encoding="utf-8"))["state"])
            except (ValueError, KeyError, OSError):
                return "STARVING"
        return "NORMAL"

    # ---- periodic --------------------------------------------------------------------------------
    def integrity_check(self, *, force: bool = False) -> IntegrityResult:
        now = self._clock()
        if (
            not force
            and self._last_integrity is not None
            and now - self._last_integrity_at < INTEGRITY_INTERVAL_S
        ):
            return self._last_integrity
        res = self.integrity.check()
        self._last_integrity, self._last_integrity_at = res, now
        self.audit.append(
            "integrity.check",
            {
                "ok": res.ok,
                "reason": res.reason,
                "changed": res.changed,
                "added": res.added,
                "removed": res.removed,
            },
        )
        if not res.ok and not self.frozen:
            self._freeze(res.reason)
            self.autonomy.demote("integrity mismatch")
        return res

    def tick(self) -> dict[str, Any]:
        halt = self.supervisor.poll()
        actions = self.leash.apply(
            self.autonomy, rest=lambda: self.kill.request(HaltLevel.SOFT, "leash")
        )
        if actions:
            self.audit.append("leash.apply", {"actions": actions})
        integrity = self.integrity_check()
        for tid in [t for t, tk in self._tickets.items() if tk.expires < self._clock()]:
            del self._tickets[tid]
        return {
            "halt": halt.level.value if halt else None,
            "leash_actions": actions,
            "integrity_ok": integrity.ok,
        }

    # ---- status ----------------------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        halt = self.halted()
        leash = self.leash.status()
        integ = self._last_integrity
        return {
            "autonomy": self.autonomy.get(),
            "dev_mode": self.dev_mode,
            "halt": None
            if halt is None
            else {"level": halt.level.value, "source": halt.source, "ts": halt.ts},
            "frozen": self.frozen,
            "metabolic_state": self.metabolic_state(),
            "leash": {
                "remaining_s": leash.remaining_s,
                "expired_periods": leash.expired_periods,
                "last_source": leash.last_source,
            },
            "integrity": None
            if integ is None
            else {"ok": integ.ok, "reason": integ.reason, "checked_at": integ.checked_at},
            "pending_approvals": len(self.approvals.pending()),
            "audit": {"seq": self.audit.seq, "last_hash": self.audit.last_hash},
            "spend_usd": {
                "hour_cloud": self.governor.window_spent(3600, cloud_only=True),
                "day_cloud": self.governor.window_spent(86400, cloud_only=True),
                "month_cloud": self.governor.window_spent(30 * 86400, cloud_only=True),
                "day_all": self.governor.window_spent(86400, cloud_only=False),
            },
        }

    def audit_tail(self, n: int) -> list[AuditRecord]:
        out: list[AuditRecord] = []
        if not self.audit.path.exists():
            return out
        lines = self.audit.path.read_text(encoding="utf-8").splitlines()[-n:]
        for line in lines:
            d = json.loads(line)
            out.append(
                AuditRecord(
                    int(d["seq"]),
                    float(d["ts"]),
                    str(d["kind"]),
                    dict(d["payload"]),
                    str(d["prev_hash"]),
                    str(d["hash"]),
                )
            )
        return out
