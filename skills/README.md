# skills/

Agent-writable, git-versioned. Format: Agent Skills `SKILL.md` + `mito:` block (ADR-0008).
Precedence: `skills/` (workspace) > `skills/.managed/` > bundled (`mito/skills_rt/bundled/`).
Imports land in `skills/.quarantine/<name>/` and are enabled only after lint + sandbox dry-run
(+ operator approval for T3+). `skills/.lock.json` records source, hash and scanner version.
