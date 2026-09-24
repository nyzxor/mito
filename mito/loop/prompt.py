"""Cache-shaped system prompt (DESIGN §7, guide Ch 11): stable prefix in fixed order from
prompts/. No timestamps, no UUIDs, deterministic. L0 must stay ≤ 3,000 tokens (tested)."""

from __future__ import annotations

from pathlib import Path

ORDER = ("constitution.md", "hard_rules.md", "tool_policy.md", "response_contract.md")


def build_system_prompt(
    prompts_dir: Path, *, capability_catalog: str = "", memory_index: str = ""
) -> str:
    parts: list[str] = []
    for name in ORDER:
        p = prompts_dir / name
        if p.exists():
            tag = name.removesuffix(".md")
            parts.append(f"<{tag}>\n{p.read_text(encoding='utf-8').strip()}\n</{tag}>")
    if capability_catalog:
        parts.append(f"<capabilities>\n{capability_catalog.strip()}\n</capabilities>")
    if memory_index:
        parts.append(f"<memory_index>\n{memory_index.strip()}\n</memory_index>")
    return "\n\n".join(parts)


def approx_tokens(text: str) -> int:
    return len(text) // 4
