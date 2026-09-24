from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from handbrake.core import Handbrake
from handbrake.paths import MitoPaths
from handbrake.tests.conftest import make_repo

from mito.gateway.client import OpenAICompatAdapter
from mito.gateway.fake import FixtureMissing, RecordingAdapter, ReplayModel, ScriptedModel, Step
from mito.gateway.gateway import ModelDenied, ModelGateway
from mito.gateway.handbrake_client import LocalHandbrakeClient
from mito.gateway.ir import Message, MeterGrant, ModelRequest, ModelResponse, ToolSpec
from mito.gateway.registry import ModelRegistry, ModelSpec, RouterConfig

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def hb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Handbrake:
    monkeypatch.setenv("MITO_OPERATOR_KEY_FILE", str(tmp_path / "opkey"))
    repo = make_repo(tmp_path / "repo")
    paths = MitoPaths(tmp_path / "home")
    Handbrake.init(paths, repo, dev_mode=True)
    return Handbrake(paths, repo, dev_mode=True)


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry.load(ROOT / "config" / "models.toml", ROOT / "config" / "metabolism.toml")


def _req(text: str = "hi", tools: tuple[ToolSpec, ...] = ()) -> ModelRequest:
    return ModelRequest(
        "s",
        "t",
        "draft",
        (Message("system", "sys"), Message("user", text)),
        tools=tools,
        max_tokens=64,
    )


def test_registry_loads_local_default(registry: ModelRegistry) -> None:
    spec = registry.select("draft")
    assert spec.tier == "L1" and spec.provider == "openai_compatible" and spec.is_local
    assert registry.estimate_usd(spec, 1000, 100) > 0  # local is priced, not free


async def test_gateway_meters_scripted_model(hb: Handbrake, registry: ModelRegistry) -> None:
    scripted = ScriptedModel([Step(text="hello")])
    gw = ModelGateway(LocalHandbrakeClient(hb), registry, {"openai_compatible": scripted})
    resp = await gw.call(_req())
    assert resp.message.content == "hello" and resp.stop_reason == "stop"
    assert resp.cost_usd >= 0 and gw.stats.calls == 1
    kinds = [r.kind for r in hb.audit_tail(3)]
    assert "model.preflight" in kinds and "model.reconcile" in kinds


def _cloud_registry(registry: ModelRegistry) -> ModelRegistry:
    cloud = ModelSpec(
        id="cloud-test",
        tier="L2",
        provider="scripted",
        model="x",
        base_url="",
        context_window=128000,
        price_in=1.0,
        price_out=4.0,
        price_cached_in=0.1,
        capabilities=frozenset({"tools"}),
    )
    return ModelRegistry([cloud], RouterConfig(2, {"draft": ["L2"]}), registry.local_pricing)


async def test_gateway_budget_denied_for_cloud_spend(
    hb: Handbrake, registry: ModelRegistry
) -> None:
    gw = ModelGateway(
        LocalHandbrakeClient(hb),
        _cloud_registry(registry),
        {"scripted": ScriptedModel([Step(completion_tokens=64)])},
    )
    tripped: ModelDenied | None = None
    for _ in range(200):
        try:
            await gw.call(
                _req("x" * 40000)
            )  # ~10K prompt tokens at $1/M + 64 completion at $4/M ≈ $0.0103
        except ModelDenied as exc:
            tripped = exc
            break
    assert tripped is not None and tripped.dimension == "per_task"
    assert hb.governor.task_spent("t") <= 0.25


async def test_local_calls_are_priced_but_cheap(hb: Handbrake, registry: ModelRegistry) -> None:
    gw = ModelGateway(
        LocalHandbrakeClient(hb), registry, {"openai_compatible": ScriptedModel([Step()])}
    )
    resp = await gw.call(_req("x" * 4000))
    assert 0 < resp.cost_usd < 0.001


async def test_gateway_failure_records_breaker(hb: Handbrake, registry: ModelRegistry) -> None:
    class Boom:
        async def complete(
            self, req: ModelRequest, spec: ModelSpec, grant: MeterGrant
        ) -> ModelResponse:
            raise RuntimeError("provider down")

    gw = ModelGateway(LocalHandbrakeClient(hb), registry, {"openai_compatible": Boom()})
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await gw.call(_req())
    with pytest.raises(ModelDenied) as ei:
        await gw.call(_req())
    assert ei.value.dimension == "circuit_open"


async def test_openai_compat_adapter_renders_and_parses(
    hb: Handbrake, registry: ModelRegistry
) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "time", "arguments": '{"tz": "UTC"}'},
                                },
                                {
                                    "id": "c2",
                                    "type": "function",
                                    "function": {"name": "time", "arguments": "{bad"},
                                },
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            },
        )

    adapter = OpenAICompatAdapter(transport=httpx.MockTransport(handler))
    gw = ModelGateway(LocalHandbrakeClient(hb), registry, {"openai_compatible": adapter})
    tool = ToolSpec(
        "time", "current time", {"type": "object", "properties": {"tz": {"type": "string"}}}
    )
    resp = await gw.call(_req("what time", tools=(tool,)))
    assert str(seen["url"]).endswith("/chat/completions")
    body = seen["body"]
    assert (
        isinstance(body, dict)
        and body["tools"][0]["function"]["name"] == "time"
        and body["stream"] is False
    )
    assert resp.stop_reason == "tool_calls" and len(resp.message.tool_calls) == 2
    assert resp.message.tool_calls[0].arguments == {"tz": "UTC"}
    assert resp.message.tool_calls[1].raw_arguments == "{bad"
    assert resp.usage.prompt_tokens == 12
    await adapter.aclose()


async def test_replay_and_record(hb: Handbrake, registry: ModelRegistry, tmp_path: Path) -> None:
    fixtures = tmp_path / "fx"
    replay = ModelGateway(
        LocalHandbrakeClient(hb), registry, {"openai_compatible": ReplayModel(fixtures)}
    )
    with pytest.raises(FixtureMissing):
        await replay.call(_req("record me"))
    recorder = ModelGateway(
        LocalHandbrakeClient(hb),
        registry,
        {"openai_compatible": RecordingAdapter(ScriptedModel([Step(text="recorded")]), fixtures)},
    )
    await recorder.call(_req("record me"))
    resp = await replay.call(_req("record me"))
    assert resp.message.content == "recorded"
