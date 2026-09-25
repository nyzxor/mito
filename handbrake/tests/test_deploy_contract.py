"""Compose contract (DESIGN §14). No public ports, no docker.sock, separate user."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.handbrake

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (
    ROOT / "deploy" / "docker-compose.yaml",
    ROOT / "deploy" / "docker-compose.dev.yaml",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", COMPOSE, ids=["prod", "dev"])
def test_compose_publishes_loopback_only(path: Path) -> None:
    text = _text(path)
    assert "127.0.0.1:8710:8710" in text
    assert "0.0.0.0:" not in text.split("ports:")[1].split("command:")[0]
    assert "docker.sock" not in text
    assert 'user: "65532:65532"' in text
    assert "cap_drop: [ALL]" in text
    assert "internal: true" in text


def test_only_egress_proxy_has_an_external_route() -> None:
    text = _text(ROOT / "deploy" / "docker-compose.yaml")
    runtime = text.split("runtime:", 1)[1].split("egress-proxy:", 1)[0]
    egress = text.split("egress-proxy:", 1)[1]
    assert "ports:" not in runtime
    assert "networks: [internal, egress]" in egress
    assert "networks: [internal]" in text.split("runtime:", 1)[0]


def test_runtime_token_is_not_committed() -> None:
    text = _text(ROOT / "deploy" / "docker-compose.yaml")
    runtime = text.split("runtime:", 1)[1]
    assert 'MITO_RUNTIME_TOKEN: ""' in runtime or "MITO_RUNTIME_TOKEN:" not in runtime


def test_image_dockerfiles_are_non_root() -> None:
    for rel in (
        "deploy/Dockerfile",
        "deploy/sandbox/Dockerfile",
        "deploy/sandbox/Dockerfile.browser",
        "egress-proxy/Dockerfile",
    ):
        text = _text(ROOT / rel)
        assert "65532" in text
        assert "USER 65532:65532" in text or "useradd --uid 65532" in text
