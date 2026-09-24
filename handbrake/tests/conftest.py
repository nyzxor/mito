"""Shared fixtures for Handbrake tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _vault_key_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MITO_VAULT_KEY_FILE", str(tmp_path / "vault.key"))


def make_repo(dst: Path) -> Path:
    """Copy the real policy/ and config/ trees into a temp repo root so tests can tamper freely."""
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("policy", "config"):
        shutil.copytree(REPO / name, dst / name, dirs_exist_ok=True)
    (dst / "handbrake").mkdir(exist_ok=True)
    (dst / "handbrake" / "marker.py").write_text("# pinned marker\n", encoding="utf-8")
    (dst / "evals" / "safety").mkdir(parents=True, exist_ok=True)
    (dst / "evals" / "safety" / "README.md").write_text("safety\n", encoding="utf-8")
    return dst
