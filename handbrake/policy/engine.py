"""Deterministic policy engine (DESIGN §5.3, §5.4; guide impl-02).

Evaluation order for a proposed action:
  1. unknown tool                      -> deny
  2. tier T5 or blacklisted tool       -> deny (no ask path, no override)
  3. deny rules (first match)          -> deny
  4. autonomy A0                       -> simulate (T1+) / allow (T0)
  5. Rule of Two: third trifecta flag  -> ask
  6. ask rules (first match)           -> ask
  7. allow rules (first match)         -> allow if tier auto-allowed by autonomy else ask
  8. default by tier: "deny" -> deny; otherwise allow if the autonomy ladder auto-allows the
     tier, else ask (T4 is never auto-allowed)

Rules are sorted deny -> ask -> allow at construction, so declaration order never matters.
The engine is pure after load: same inputs, same verdict.
"""

from __future__ import annotations

import fnmatch
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from handbrake.policy.tiers import TierMap, tier_index

VerdictKind = str  # "allow" | "ask" | "deny" | "simulate"
_ORDER = {"deny": 0, "ask": 1, "allow": 2}
_KNOWN_PREDICATES = frozenset(
    {
        "path_outside_workspace",
        "target_path_prefix",
        "url_host_not_in_allowlist",
        "metabolic_state_in",
        "repo_in",
        "repo_not_in",
        "args",
    }
)
_PATH_KEYS = ("path", "src", "dst", "target", "file")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class GateContext:
    session: str
    autonomy: str
    metabolic_state: str
    taint_flags: frozenset[str]
    workspace: str
    own_repos: frozenset[str]
    write_allowlist: frozenset[str]


@dataclass(frozen=True)
class Verdict:
    kind: VerdictKind
    reason: str
    tier: str
    flags_after: tuple[str, ...]
    rule: str | None = None


@dataclass(frozen=True)
class Rule:
    kind: str
    tool: str
    where: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _ORDER:
            raise PolicyError(f"rule kind must be deny|ask|allow, got {self.kind!r}")
        unknown = set(self.where) - _KNOWN_PREDICATES
        if unknown:
            raise PolicyError(f"rule {self.tool!r}: unknown predicate(s) {sorted(unknown)}")

    def matches(self, tool: str, args: dict[str, Any], ctx: GateContext) -> bool:
        if not fnmatch.fnmatchcase(tool, self.tool):
            return False
        return all(_predicate(name, value, args, ctx) for name, value in self.where.items())


def _string_args(args: dict[str, Any]) -> list[str]:
    return [v for v in args.values() if isinstance(v, str)]


def _norm(p: str) -> str:
    return p.replace("\\", "/").rstrip("/")


def _predicate(name: str, value: Any, args: dict[str, Any], ctx: GateContext) -> bool:
    if name == "path_outside_workspace":
        ws = _norm(ctx.workspace) + "/"
        paths = [str(args[k]) for k in _PATH_KEYS if k in args]
        outside = any(not (_norm(p) + "/").startswith(ws) for p in paths)
        return outside is bool(value)
    if name == "target_path_prefix":
        prefixes = [_norm(str(p)) for p in value]
        tokens = [tok for s in _string_args(args) for tok in s.split()]
        return any(_norm(t).lstrip("./").startswith(p) for t in tokens for p in prefixes)
    if name == "url_host_not_in_allowlist":
        url = str(args.get("url", ""))
        host = (urlsplit(url).hostname or "").lower()
        return (host not in ctx.write_allowlist) is bool(value)
    if name == "metabolic_state_in":
        return ctx.metabolic_state in {str(s) for s in value}
    if name in ("repo_in", "repo_not_in"):
        repos = ctx.own_repos if value == "config:own_repos" else frozenset(map(str, value))
        inside = str(args.get("repo", "")) in repos
        return inside if name == "repo_in" else not inside
    if name == "args":
        return all(
            fnmatch.fnmatchcase(str(args.get(k, "")), str(pat)) for k, pat in dict(value).items()
        )
    raise PolicyError(f"unknown predicate {name!r}")


class PolicyEngine:
    def __init__(
        self,
        *,
        rules: list[Rule],
        tiers: TierMap,
        defaults: dict[str, str],
        autonomy: dict[str, list[str]],
        blacklist_tools: frozenset[str],
        trifecta_max: int = 2,
    ) -> None:
        self.rules = sorted(rules, key=lambda r: _ORDER[r.kind])
        self.tiers = tiers
        self.defaults = defaults
        self.autonomy = autonomy
        self.blacklist_tools = blacklist_tools
        self.trifecta_max = trifecta_max
        for tier, kind in defaults.items():
            tier_index(tier)
            if kind not in ("allow", "ask", "deny"):
                raise PolicyError(f"defaults.{tier} must be allow|ask|deny")
        if set(autonomy) != {"A0", "A1", "A2"}:
            raise PolicyError("autonomy ladder must define exactly A0, A1, A2")

    # ---- loading ---------------------------------------------------------------------------
    @classmethod
    def load(cls, policy_dir: Path) -> PolicyEngine:
        with (policy_dir / "policy.toml").open("rb") as f:
            pol = tomllib.load(f)
        with (policy_dir / "blacklist.toml").open("rb") as f:
            bl = tomllib.load(f)
        tiers = TierMap.load(policy_dir / "risk_tiers.toml")
        rules = [
            Rule(
                kind=str(r["kind"]),
                tool=str(r["tool"]),
                where=dict(r.get("where", {})),
                reason=str(r.get("reason", "")),
            )
            for r in pol.get("rule", [])
        ]
        return cls(
            rules=rules,
            tiers=tiers,
            defaults={str(k): str(v) for k, v in pol["defaults"].items()},
            autonomy={str(k): [str(t) for t in v] for k, v in pol["autonomy"].items()},
            blacklist_tools=frozenset(str(t) for t in bl.get("tools", [])),
            trifecta_max=int(pol.get("trifecta", {}).get("max_flags", 2)),
        )

    # ---- evaluation ------------------------------------------------------------------------
    def flags_for(self, tool: str, tier: str) -> set[str]:
        flags: set[str] = set()
        if self.tiers.emits_untrusted(tool):
            flags.add("A")
        if self.tiers.is_sensitive(tool):
            flags.add("B")
        if tier_index(tier) >= tier_index("T3"):
            flags.add("C")
        return flags

    def evaluate(self, tool: str, args: dict[str, Any], ctx: GateContext) -> Verdict:
        if ctx.autonomy not in self.autonomy:
            raise PolicyError(f"unknown autonomy level {ctx.autonomy!r}")
        tier = self.tiers.tier_of(tool)
        if tier is None:
            return Verdict("deny", f"unknown tool {tool!r}", "T5", tuple(sorted(ctx.taint_flags)))
        after = tuple(sorted(set(ctx.taint_flags) | self.flags_for(tool, tier)))

        if tier == "T5" or self._blacklisted(tool):
            return Verdict("deny", f"{tool} is on the hard blacklist (T5)", tier, after)

        for rule in self.rules:
            if rule.kind != "deny":
                break
            if rule.matches(tool, args, ctx):
                return Verdict(
                    "deny", rule.reason or f"denied by rule {rule.tool}", tier, after, rule.tool
                )

        if ctx.autonomy == "A0":
            if tier == "T0":
                return Verdict("allow", "T0 read at A0", tier, after)
            return Verdict("simulate", "A0 shadow mode: no external effects", tier, after)

        if len(after) > self.trifecta_max and len(after) > len(ctx.taint_flags):
            return Verdict(
                "ask",
                f"Rule of Two: this action would combine {'+'.join(after)} (lethal trifecta)",
                tier,
                after,
            )

        auto = tier in self.autonomy[ctx.autonomy] and tier != "T4"
        for rule in self.rules:
            if rule.kind == "deny":
                continue
            if rule.matches(tool, args, ctx):
                if rule.kind == "ask":
                    return Verdict(
                        "ask", rule.reason or f"ask rule {rule.tool}", tier, after, rule.tool
                    )
                if auto:
                    return Verdict(
                        "allow", rule.reason or f"allow rule {rule.tool}", tier, after, rule.tool
                    )
                return Verdict(
                    "ask", f"{tier} needs approval at {ctx.autonomy}", tier, after, rule.tool
                )

        default = self.defaults.get(tier, "deny")
        if default == "deny":
            return Verdict("deny", f"default deny for {tier}", tier, after)
        if auto:
            return Verdict("allow", f"{tier} auto-allowed at {ctx.autonomy}", tier, after)
        return Verdict("ask", f"{tier} needs approval at {ctx.autonomy}", tier, after)

    def _blacklisted(self, tool: str) -> bool:
        return any(fnmatch.fnmatchcase(tool, pat) for pat in self.blacklist_tools)
