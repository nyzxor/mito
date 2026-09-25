from __future__ import annotations

from pathlib import Path

import pytest
from handbrake.paths import repo_root
from mito.playbooks.load import PlaybookError, blacklist_categories, load_dir, load_playbook
from mito.playbooks.runner import run_l0

pytestmark = pytest.mark.unit


def test_repo_playbooks_load_and_disabled_ones_refuse() -> None:
    root = repo_root()
    books = load_dir(root / "playbooks", categories=blacklist_categories(root / "policy"))
    names = {b.name for b in books}
    assert names == {"bounty-scout", "gig-fulfillment", "cost-optimizer"}
    cost = next(b for b in books if b.name == "cost-optimizer")
    assert cost.enabled is True
    out = run_l0(cost, state="NORMAL", daily_burn_atp=12)
    assert out["llm"] is False and out["action"] == "notice"
    bounty = next(b for b in books if b.name == "bounty-scout")
    with pytest.raises(PlaybookError, match="disabled"):
        run_l0(bounty, state="THRIVING", daily_burn_atp=1)


def test_blacklisted_category_refuses_load(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(
        'schema_version = 1\nname = "bad"\nenabled = true\nautonomy_max = "A1"\n'
        '[tools]\nrequired = ["ledger.read"]\nnotes = "fraud"\n',
        encoding="utf-8",
    )
    cats = blacklist_categories(repo_root() / "policy")
    with pytest.raises(PlaybookError, match="fraud"):
        load_playbook(path, categories=cats)


def test_zero_burn_skips_without_a_model() -> None:
    from mito.playbooks.load import Playbook

    book = Playbook("cost-optimizer", True, "A1", ("ledger.read",), "T0", "@daily", Path("x"))
    out = run_l0(book, state="STARVING", daily_burn_atp=0)
    assert out["action"] == "skip" and out["llm"] is False
