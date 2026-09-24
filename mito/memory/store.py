"""Markdown + FTS5 memory (ADR-0009). Harness sets provenance; supersede, never delete."""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LANES = ("operator-confirmed", "agent-inferred", "tool-derived", "quarantine")
# Named consumers of config/mito.toml [memory].
INDEX_MAX_LINES = 200  # [memory].index_max_lines
DEFAULT_TTL_DAYS = 180  # [memory].default_ttl_days
SECRET_RE = re.compile(
    r"(?i)(ghp_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN )"
)
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


class MemoryError(ValueError):
    pass


@dataclass(frozen=True)
class Fact:
    name: str
    body: str
    description: str
    trust_lane: str
    source: str
    run_id: str
    created: float
    ttl_days: int
    superseded_by: str
    path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "trust_lane": self.trust_lane,
            "source": self.source,
            "run_id": self.run_id,
            "created": self.created,
            "ttl_days": self.ttl_days,
            "superseded_by": self.superseded_by or None,
            "quarantined": self.trust_lane == "quarantine",
            "body": self.body,
        }


def _frontmatter(fact: Fact) -> str:
    return (
        "---\n"
        f"name: {fact.name}\n"
        f"description: {fact.description}\n"
        f"trust_lane: {fact.trust_lane}\n"
        f"source: {fact.source}\n"
        f"run_id: {fact.run_id}\n"
        f"created: {fact.created}\n"
        f"ttl_days: {fact.ttl_days}\n"
        f"superseded_by: {fact.superseded_by}\n"
        "---\n"
        f"{fact.body.rstrip()}\n"
    )


def _parse(path: Path) -> Fact:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise MemoryError(f"{path.name}: missing frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise MemoryError(f"{path.name}: unclosed frontmatter")
    meta: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    body = text[end + 5 :].strip()
    return Fact(
        name=meta.get("name", path.stem),
        body=body,
        description=meta.get("description", ""),
        trust_lane=meta.get("trust_lane", "quarantine"),
        source=meta.get("source", "unknown"),
        run_id=meta.get("run_id", ""),
        created=float(meta.get("created", 0) or 0),
        ttl_days=int(float(meta.get("ttl_days", DEFAULT_TTL_DAYS) or DEFAULT_TTL_DAYS)),
        superseded_by=meta.get("superseded_by", ""),
        path=path,
    )


class MemoryStore:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.root = root
        self.facts_dir = root / "facts"
        self.quarantine_dir = root / "quarantine"
        self.facts_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._db = root / "index.sqlite"
        self._conn = sqlite3.connect(self._db, isolation_level=None, check_same_thread=False)
        row = self._conn.execute("PRAGMA compile_options").fetchall()
        opts = {str(r[0]) for r in row}
        if not any("FTS5" in o for o in opts):
            raise MemoryError("SQLite build lacks FTS5; refuse to boot memory index")
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5("
            "name, description, body, id UNINDEXED)"
        )

    def _dir_for(self, lane: str) -> Path:
        return self.quarantine_dir if lane == "quarantine" else self.facts_dir

    def write(
        self,
        name: str,
        body: str,
        *,
        description: str = "",
        source: str,
        run_id: str,
        flags: list[str],
        ttl_days: int = DEFAULT_TTL_DAYS,
    ) -> Fact:
        if not NAME_RE.match(name):
            raise MemoryError(f"invalid memory name {name!r}")
        if SECRET_RE.search(body) or SECRET_RE.search(description):
            raise MemoryError(
                "refused: body looks like a secret; store a pointer, not a credential"
            )
        lane = "quarantine" if "A" in flags else "agent-inferred"
        now = self._clock()
        existing = self.get(name, include_superseded=True, include_quarantine=True)
        dest = self._dir_for(lane) / f"{name}.md"
        if existing and not existing.superseded_by:
            stamp = int(existing.created * 1000) or int(now * 1000)
            archived = existing.path.with_name(f"{name}.{stamp}.md")
            extra = 0
            while archived.exists():
                extra += 1
                archived = existing.path.with_name(f"{name}.{stamp}.{extra}.md")
            existing.path.rename(archived)
            old = _parse(archived)
            superseded = Fact(
                old.name,
                old.body,
                old.description,
                old.trust_lane,
                old.source,
                old.run_id,
                old.created,
                old.ttl_days,
                name,
                archived,
            )
            archived.write_text(_frontmatter(superseded), encoding="utf-8")
        fact = Fact(
            name, body, description, lane, source, run_id, now, ttl_days, "", dest
        )
        dest.write_text(_frontmatter(fact), encoding="utf-8")
        self._reindex()
        self._rewrite_index()
        return fact

    def confirm(self, name: str) -> Fact:
        fact = self.get(name, include_quarantine=True)
        if fact is None:
            raise MemoryError(f"unknown memory {name}")
        if fact.trust_lane != "quarantine":
            raise MemoryError(f"{name} is not quarantined")
        dest = self.facts_dir / f"{name}.md"
        confirmed = Fact(
            fact.name,
            fact.body,
            fact.description,
            "operator-confirmed",
            fact.source,
            fact.run_id,
            fact.created,
            fact.ttl_days,
            "",
            dest,
        )
        dest.write_text(_frontmatter(confirmed), encoding="utf-8")
        if fact.path != dest:
            fact.path.unlink()
        self._reindex()
        self._rewrite_index()
        return confirmed

    def get(
        self,
        name: str,
        *,
        include_superseded: bool = False,
        include_quarantine: bool = False,
    ) -> Fact | None:
        candidates = list(self.facts_dir.glob(f"{name}*.md"))
        if include_quarantine:
            candidates.extend(self.quarantine_dir.glob(f"{name}*.md"))
        live = [
            _parse(p)
            for p in candidates
            if p.stem == name or p.stem.startswith(f"{name}.")
        ]
        live = [f for f in live if f.name == name]
        if not include_superseded:
            live = [f for f in live if not f.superseded_by]
        if not live:
            return None
        live.sort(key=lambda f: f.created, reverse=True)
        return live[0]

    def search(self, query: str, *, include_quarantine: bool = False, limit: int = 8) -> list[Fact]:
        tokens = re.findall(r"[A-Za-z0-9_]+", query)
        if not tokens:
            raise MemoryError("search query has no words. Use letters or numbers.")
        fts = " AND ".join(tokens)
        self._reindex()
        try:
            rows = self._conn.execute(
                "SELECT id FROM facts_fts WHERE facts_fts MATCH ? LIMIT ?",
                (fts, limit * 3),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise MemoryError(
                f"bad search query {query!r}: {exc}. Use simple words, no punctuation."
            ) from exc
        out: list[Fact] = []
        for (fid,) in rows:
            fact = self.get(str(fid), include_quarantine=include_quarantine)
            if fact is None:
                continue
            if fact.trust_lane == "quarantine" and not include_quarantine:
                continue
            out.append(fact)
            if len(out) >= limit:
                break
        return out

    def _reindex(self) -> None:
        self._conn.execute("DELETE FROM facts_fts")
        for folder in (self.facts_dir, self.quarantine_dir):
            for path in folder.glob("*.md"):
                try:
                    fact = _parse(path)
                except MemoryError:
                    continue
                if fact.superseded_by:
                    continue
                self._conn.execute(
                    "INSERT INTO facts_fts(name, description, body, id) VALUES(?,?,?,?)",
                    (fact.name, fact.description, fact.body, fact.name),
                )

    def _rewrite_index(self) -> None:
        lines = ["# MEMORY index", "", "Derived. Quarantine is not listed.", ""]
        for path in sorted(self.facts_dir.glob("*.md")):
            try:
                fact = _parse(path)
            except MemoryError:
                continue
            if fact.superseded_by:
                continue
            hook = fact.description or fact.body.split("\n", 1)[0][:80]
            lines.append(f"- [{fact.name}](facts/{fact.name}.md), {hook}")
        cap = INDEX_MAX_LINES + 3  # header lines before the fact list
        (self.root / "MEMORY.md").write_text("\n".join(lines[:cap]) + "\n", encoding="utf-8")
