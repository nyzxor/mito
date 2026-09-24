"""SKILL.md loader (ADR-0008). Quarantine is not loadable. Advertised == granted."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mito.skills_rt.frontmatter import SkillParseError, parse_skill_md

KNOWN_ORIGINS = frozenset({"workspace", "managed", "bundled", "quarantine"})
URL_RE = re.compile(r"https?://[^\s)>\"]+", re.I)
DEFAULT_ROOTS = ("skills", "skills/.managed", "mito/skills_rt/bundled")
# Named consumers of config/mito.toml [skills] (tokens ≈ chars/4).
CATALOG_MAX_CHARS = 400  # catalog_max_tokens_per_skill = 100
BODY_MAX_CHARS = 20_000  # body_max_tokens = 5000


class SkillError(ValueError):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    version: str
    tools_required: tuple[str, ...]
    risk_tier: str
    origin: str
    body: str
    path: Path
    content_hash: str

    def catalog_line(self) -> str:
        line = f"{self.name}: {self.description}"
        if len(line) <= CATALOG_MAX_CHARS:
            return line
        return line[: CATALOG_MAX_CHARS - 3] + "..."

    def granted_tools(self) -> tuple[str, ...]:
        """Advertised == granted. The only function that computes a skill's tool grant."""
        return self.tools_required

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "tools_required": list(self.tools_required),
            "risk_tier": self.risk_tier,
            "origin": self.origin,
            "path": str(self.path),
            "hash": self.content_hash,
        }


def lint_skill(meta: dict[str, Any], body: str, *, available_tools: frozenset[str]) -> list[str]:
    errors: list[str] = []
    name = str(meta.get("name", ""))
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name):
        errors.append(f"bad name {name!r}")
    mito = meta.get("mito")
    if not isinstance(mito, dict):
        errors.append("missing mito: block")
        return errors
    tools = mito.get("tools_required", [])
    if not isinstance(tools, list) or not tools:
        errors.append("mito.tools_required must be a non-empty list")
        tools = []
    unknown = [t for t in tools if t not in available_tools]
    if unknown:
        errors.append(f"unknown tools: {unknown}")
    hosts: set[str] = set()
    for match in URL_RE.findall(body):
        parts = match.split("/")
        if len(parts) >= 3 and parts[2]:
            hosts.add(parts[2])
    raw_hosts = mito.get("hosts", [])
    declared = {str(h) for h in raw_hosts} if isinstance(raw_hosts, list) else set()
    extra_hosts = hosts - declared
    if extra_hosts:
        errors.append(f"undeclared hosts in body: {sorted(extra_hosts)}")
    if len(body) > BODY_MAX_CHARS:
        errors.append(f"body exceeds {BODY_MAX_CHARS} chars")
    return errors


def load_skill(path: Path, *, available_tools: frozenset[str]) -> Skill:
    text = path.read_text(encoding="utf-8")
    meta, body = parse_skill_md(text)
    errors = lint_skill(meta, body, available_tools=available_tools)
    if errors:
        raise SkillError(f"{path}: {'; '.join(errors)}")
    mito = dict(meta["mito"])
    tools = tuple(str(t) for t in mito["tools_required"])
    origin = str(mito.get("origin", "workspace"))
    if origin not in KNOWN_ORIGINS:
        raise SkillError(f"{path}: bad origin {origin}")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Skill(
        name=str(meta["name"]),
        description=str(meta.get("description", "")),
        version=str(meta.get("version", "0.0.0")),
        tools_required=tools,
        risk_tier=str(mito.get("risk_tier", "T5")),
        origin=origin,
        body=body,
        path=path,
        content_hash=digest,
    )


class SkillRegistry:
    def __init__(self, skills: list[Skill]) -> None:
        self._by_name: dict[str, Skill] = {}
        for skill in skills:
            if skill.name in self._by_name:
                raise SkillError(f"duplicate skill name {skill.name}")
            self._by_name[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._by_name.get(name)

    def all(self) -> list[Skill]:
        return [self._by_name[k] for k in sorted(self._by_name)]

    def index_lines(self) -> list[str]:
        return [s.catalog_line() for s in self.all()]


def discover(
    roots: list[Path],
    *,
    available_tools: frozenset[str],
    include_quarantine: bool = False,
) -> SkillRegistry:
    found: list[Skill] = []
    seen_names: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        if root.name == ".quarantine" and not include_quarantine:
            continue
        names_here: set[str] = set()
        for skill_md in sorted(root.rglob("SKILL.md")):
            if ".quarantine" in skill_md.parts and not include_quarantine:
                continue
            skill = load_skill(skill_md, available_tools=available_tools)
            if skill.name in names_here:
                raise SkillError(f"duplicate skill name {skill.name} under {root}")
            names_here.add(skill.name)
            if skill.name in seen_names:
                continue  # higher-precedence root already won
            seen_names.add(skill.name)
            found.append(skill)
    return SkillRegistry(found)


def skill_roots_from_config(repo: Path) -> list[Path]:
    cfg = repo / "config" / "mito.toml"
    rels: list[str] = list(DEFAULT_ROOTS)
    if cfg.is_file():
        with cfg.open("rb") as fh:
            data = tomllib.load(fh)
        raw = data.get("skills", {}).get("roots", rels)
        if isinstance(raw, list) and raw:
            rels = [str(r) for r in raw]
    return [repo / r for r in rels]


def quarantine_dir(workspace_skills: Path) -> Path:
    q = workspace_skills / ".quarantine"
    q.mkdir(parents=True, exist_ok=True)
    return q


def propose(
    workspace_skills: Path, name: str, body: str, *, available_tools: frozenset[str]
) -> Path:
    dest = quarantine_dir(workspace_skills) / name / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(body, encoding="utf-8")
    load_skill(dest, available_tools=available_tools)  # lint now
    return dest


def list_quarantine(
    workspace_skills: Path, *, available_tools: frozenset[str]
) -> list[dict[str, Any]]:
    root = quarantine_dir(workspace_skills)
    out: list[dict[str, Any]] = []
    for skill_md in sorted(root.rglob("SKILL.md")):
        try:
            skill = load_skill(skill_md, available_tools=available_tools)
            out.append({**skill.to_dict(), "ok": True})
        except (SkillError, SkillParseError) as exc:
            out.append({"path": str(skill_md), "ok": False, "error": str(exc)})
    return out


def approve(workspace_skills: Path, name: str, *, available_tools: frozenset[str]) -> Path:
    src = quarantine_dir(workspace_skills) / name / "SKILL.md"
    if not src.is_file():
        raise SkillError(f"no quarantined skill {name}")
    skill = load_skill(src, available_tools=available_tools)
    dest = workspace_skills / name / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = src.read_text(encoding="utf-8")
    dest.write_text(text, encoding="utf-8")
    lock_path = workspace_skills / ".lock.json"
    lock: dict[str, Any] = {}
    if lock_path.exists():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock[name] = {
        "hash": skill.content_hash,
        "source": "quarantine",
        "scanner": "mito-skill-lint/1",
        "risk_tier": skill.risk_tier,
    }
    lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest
