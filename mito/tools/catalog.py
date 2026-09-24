"""The advertised toolbelt. Harness tests keep their own fakes for email/memory."""

from __future__ import annotations

from pathlib import Path

from handbrake.paths import MitoPaths

from mito.gateway.handbrake_client import HandbrakeClient
from mito.loop.compact import MoreStore
from mito.mcp.registry import McpRegistry
from mito.memory.store import MemoryStore
from mito.skills_rt.loader import discover, skill_roots_from_config
from mito.tools.base import Tool
from mito.tools.basic import basic_tools
from mito.tools.email import email_tools
from mito.tools.fs import fs_tools
from mito.tools.ledger import ledger_tools
from mito.tools.mcp import mcp_tools
from mito.tools.memory import memory_tools
from mito.tools.net import net_tools
from mito.tools.notify import notify_tools
from mito.tools.sandbox import sandbox_tools
from mito.tools.schedule import schedule_tools
from mito.tools.search import make_tool_search
from mito.tools.skills import skills_tools

# Names default_tools() advertises. Skill lint / boot fail on anything outside this set.
ADVERTISED = frozenset(
    {
        "time",
        "status",
        "result.more",
        "ledger.read",
        "fs.read",
        "fs.write",
        "fs.list",
        "code.run",
        "shell",
        "git.local",
        "web.fetch",
        "http.get",
        "http.post",
        "notify.human",
        "memory.search",
        "memory.read",
        "memory.write",
        "skills.list",
        "skills.read",
        "skill.propose",
        "mcp.read",
        "tool_search",
        "email.read",
        "email.draft",
        "email.send",
        "schedule.propose",
    }
)


def default_tools(
    handbrake: HandbrakeClient,
    more: MoreStore,
    workspace: str,
    *,
    paths: MitoPaths,
    repo: Path,
) -> list[Tool]:
    memory = MemoryStore(paths.runtime / "memory")
    roots = skill_roots_from_config(repo)
    workspace_skills = repo / "skills"
    skill_index = discover(roots, available_tools=ADVERTISED)
    tools: list[Tool] = [
        *basic_tools(handbrake, more),
        *ledger_tools(handbrake),
        *fs_tools(workspace),
        *sandbox_tools(handbrake),
        *net_tools(handbrake),
        *notify_tools(handbrake),
        *memory_tools(memory),
        *skills_tools(roots, workspace_skills, ADVERTISED),
        *mcp_tools(McpRegistry.load(repo / "config" / "mcp.toml")),
        *email_tools(handbrake),
        *schedule_tools(handbrake),
    ]
    tools.append(make_tool_search(tools, skill_index.index_lines()))
    names = {t.name for t in tools}
    if names != ADVERTISED:
        missing = ADVERTISED - names
        extra = names - ADVERTISED
        raise RuntimeError(f"catalog drift vs ADVERTISED missing={missing} extra={extra}")
    return tools
