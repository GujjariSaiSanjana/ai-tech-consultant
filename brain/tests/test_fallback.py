"""Gemini/NVIDIA fallback behind Claude, against mocked HTTP transports for both sides."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anthropic
import httpx
import httpx2
import pytest

from brain.domain import ImageRef
from brain.ingest.contextualizer import Contextualizer
from brain.ingest.images import ClaudeImageDescriber
from brain.llm.answerer import ClaudeAnswerer
from brain.llm.classifier import Classification, ClaudeClassifier
from brain.llm.client import LLMClient, LLMError, LLMRefusalError
from brain.llm.fallback import FallbackChain, build_fallback, provider_configs
from tests.helpers import make_settings
from tests.test_llm import FORBIDDEN, Recorder, _hits, _message

Responder = Callable[[dict[str, Any]], tuple[int, dict[str, Any]]]
HOSTS = {"generativelanguage.googleapis.com": "gemini", "integrate.api.nvidia.com": "nvidia"}
CLAUDE_DOWN = {"type": "error", "error": {"type": "api_error", "message": "overloaded"}}
LABEL: dict[str, Any] = {
    "label": "setting",
    "setting_path": None,
    "warnings": [],
    "rationale": "built in",
    "confidence": 0.9,
}


def completion(content: str | None, *, finish: str = "stop", prompt: int = 1000, out: int = 200) -> dict[str, Any]:
    return {
        "id": "cmpl-1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": out, "total_tokens": prompt + out},
    }


class Providers:
    """Mock Chat Completions servers keyed by provider name; `seen` keeps (name, body, request) in order."""

    def __init__(self, **responders: Responder) -> None:
        self.responders = responders
        self.seen: list[tuple[str, dict[str, Any], httpx.Request]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        name = HOSTS[request.url.host]
        body = json.loads(request.content)
        self.seen.append((name, body, request))
        status, payload = self.responders[name](body)
        return httpx.Response(status, json=payload)

    @property
    def names(self) -> list[str]:
        return [name for name, _, _ in self.seen]

    def body(self, i: int = 0) -> dict[str, Any]:
        return self.seen[i][1]


def _llm(
    providers: Providers,
    *,
    claude: Responder | None = None,
    order: str = "gemini,nvidia",
    keys: tuple[str, ...] = ("gemini", "nvidia"),
) -> tuple[LLMClient, Recorder, list[dict[str, Any]]]:
    recorder = Recorder()
    claude_requests: list[dict[str, Any]] = []
    settings = make_settings(
        LLM_FALLBACK_ORDER=order,
        GEMINI_API_KEY="g-key" if "gemini" in keys else None,
        NVIDIA_API_KEY="n-key" if "nvidia" in keys else None,
    )
    chain = build_fallback(settings, httpx.AsyncClient(transport=httpx.MockTransport(providers.handler)), recorder)
    sdk = None
    if claude is not None:
        responder = claude

        def handler(request: httpx2.Request) -> httpx2.Response:
            body = json.loads(request.content)
            claude_requests.append(body)
            status, payload = responder(body)
            return httpx2.Response(status, json=payload, headers={"request-id": "req_test"})

        sdk = anthropic.AsyncAnthropic(
            api_key="sk-test",
            max_retries=0,
            http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
        )
    return LLMClient(sdk, settings, recorder, chain), recorder, claude_requests


def _down(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return 529, CLAUDE_DOWN


def _ok(content: str, **kw: Any) -> Responder:
    return lambda body: (200, completion(content, **kw))


def _fail(status: int) -> Responder:
    return lambda body: (status, {"error": {"code": status, "message": "nope"}})


async def test_fallback_fires_when_claude_is_unavailable() -> None:
    providers = Providers(gemini=_ok(json.dumps(LABEL)), nvidia=_fail(500))
    llm, recorder, claude_requests = _llm(providers, claude=_down)
    result, usage = await ClaudeClassifier(llm).classify("add tiktok link", "Use the Social Media tab", _hits())

    assert len(claude_requests) == 1
    assert isinstance(result, Classification) and result.label == "setting"
    assert providers.names == ["gemini"]
    _, body, request = providers.seen[0]
    assert str(request.url) == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert request.headers["authorization"] == "Bearer g-key"
    assert body["model"] == "gemini-2.5-flash"
    assert not FORBIDDEN & body.keys()
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["name"] == "Classification"
    assert fmt["json_schema"]["schema"] == Classification.model_json_schema()
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert [c.model for c in recorder.calls] == ["claude-haiku-5-5", "gemini-2.5-flash"]
    assert recorder.calls[0].error and "OverloadedError" in recorder.calls[0].error
    assert recorder.calls[1].error is None
    assert usage.input_tokens == 1000


async def test_order_is_followed_and_keyless_providers_are_skipped() -> None:
    both = make_settings(GEMINI_API_KEY="g", NVIDIA_API_KEY="n", LLM_FALLBACK_ORDER=" NVIDIA , gemini, bogus, nvidia")
    assert [c.name for c in provider_configs(both)] == ["nvidia", "gemini"]
    assert [c.name for c in provider_configs(make_settings(GEMINI_API_KEY="g"))] == ["gemini"]
    assert provider_configs(make_settings(GEMINI_API_KEY="g", NVIDIA_API_KEY="n", LLM_FALLBACK_ORDER="")) == []

    providers = Providers(gemini=_ok("from gemini"), nvidia=_fail(503))
    llm, _, _ = _llm(providers, claude=_down, order="nvidia,gemini")
    message, _ = await llm.create("context", messages=[{"role": "user", "content": "hi"}])
    assert providers.names == ["nvidia", "gemini"]
    assert message.model == "gemini-2.5-flash"

    keyless = Providers(gemini=_ok("from gemini"), nvidia=_ok("from nvidia"))
    llm, _, _ = _llm(keyless, claude=_down, order="nvidia,gemini", keys=("gemini",))
    await llm.create("context", messages=[{"role": "user", "content": "hi"}])
    assert keyless.names == ["gemini"]


async def test_refusal_and_bad_request_do_not_fall_back() -> None:
    providers = Providers(gemini=_ok("should not be called"), nvidia=_ok("nor this"))
    refusal = _message(
        [], stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": None}
    )
    llm, _, _ = _llm(providers, claude=lambda body: (200, refusal))
    with pytest.raises(LLMRefusalError):
        await ClaudeAnswerer(llm).answer("q", _hits())

    bad = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    llm, _, _ = _llm(providers, claude=lambda body: (400, bad))
    with pytest.raises(LLMError) as info:
        await ClaudeClassifier(llm).classify("q", None, _hits())
    assert not info.value.retryable
    assert providers.seen == []


async def test_fallback_content_filter_is_a_refusal_and_stops_the_chain() -> None:
    providers = Providers(gemini=_ok("", finish="content_filter"), nvidia=_ok("fine"))
    llm, recorder, _ = _llm(providers)
    with pytest.raises(LLMRefusalError):
        await llm.create("context", messages=[{"role": "user", "content": "hi"}])
    assert providers.names == ["gemini"]
    assert recorder.calls[-1].error == "refusal:gemini:content_filter"


async def test_json_schema_rejected_retries_once_with_json_object() -> None:
    calls = {"n": 0}

    def gemini(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        calls["n"] += 1
        if body["response_format"]["type"] == "json_schema":
            return 400, {"error": {"code": 400, "message": "json_schema not supported"}}
        return 200, completion("```json\n" + json.dumps(LABEL) + "\n```")

    providers = Providers(gemini=gemini, nvidia=_ok("unused"))
    llm, recorder, _ = _llm(providers)
    result, _ = await ClaudeClassifier(llm).classify("q", "a", _hits())

    assert result.label == "setting"
    assert providers.names == ["gemini", "gemini"]
    retry = providers.body(1)
    assert retry["response_format"] == {"type": "json_object"}
    assert retry["messages"][0]["role"] == "system"
    assert json.dumps(Classification.model_json_schema()) in retry["messages"][0]["content"]
    assert [c.error is None for c in recorder.calls] == [False, True]


async def test_validation_failure_is_not_retryable_and_raises_original_claude_error() -> None:
    bad = json.dumps(LABEL | {"label": "magic"})
    providers = Providers(gemini=_ok(bad), nvidia=_ok("not json at all"))
    llm, recorder, _ = _llm(providers)
    with pytest.raises(LLMError) as info:
        await llm.parse("classify", Classification, messages=[{"role": "user", "content": "q"}])
    assert not info.value.retryable
    assert "validation" in str(info.value)
    assert providers.names == ["gemini", "nvidia"]  # a provider's bad output moves on to the next one
    assert all(c.error and "validation" in c.error for c in recorder.calls)

    llm, _, _ = _llm(Providers(gemini=_ok(bad), nvidia=_ok(bad)), claude=_down)
    with pytest.raises(LLMError) as info:
        await llm.parse("classify", Classification, messages=[{"role": "user", "content": "q"}])
    assert info.value.retryable and "Claude API unavailable" in str(info.value)


async def test_answer_fallback_keeps_only_real_substring_citations() -> None:
    hits = _hits()
    real = "using the following format: https://www.tiktok.com/@name"
    reply = {
        "answer_text": "Use https://www.tiktok.com/@name in the Social Media tab.",
        "citations": [
            {"document_index": 0, "cited_text": f"  {real} "},
            {"document_index": 0, "cited_text": "Paste your TikTok handle in the footer."},  # invented
            {"document_index": 1, "cited_text": real},  # real text, wrong document
            {"document_index": 7, "cited_text": real},  # no such document
            {"document_index": 1, "cited_text": "footer icons"},
        ],
    }
    providers = Providers(gemini=_ok(json.dumps(reply)), nvidia=_ok("unused"))
    llm, _, _ = _llm(providers, claude=_down)
    draft = await ClaudeAnswerer(llm).answer("TikTok link format?", hits)

    assert draft.status == "answered" and draft.model == "gemini-2.5-flash"
    assert [(c.hit_index, c.quote) for c in draft.citations] == [(0, real), (1, "footer icons")]
    body = providers.body()
    assert body["response_format"]["json_schema"]["name"] == "CitedAnswer"
    system, user = body["messages"]
    assert "NO_ANSWER" in system["content"] and "[doc N]" in system["content"]
    assert user["content"].index("[doc 0]") < user["content"].index("[doc 1]") < user["content"].index("Question:")
    assert hits[0].text in user["content"]


async def test_answer_fallback_no_answer_drops_citations() -> None:
    reply = {"answer_text": "NO_ANSWER", "citations": [{"document_index": 0, "cited_text": "Add a link"}]}
    llm, _, _ = _llm(Providers(gemini=_ok(json.dumps(reply)), nvidia=_ok("unused")), claude=_down)
    draft = await ClaudeAnswerer(llm).answer("Does it integrate with SAP?", _hits())
    assert draft.status == "no_answer" and draft.citations == []


async def test_cost_and_recording_per_provider() -> None:
    providers = Providers(gemini=_fail(429), nvidia=_ok("sentence", prompt=5000, out=40))
    llm, recorder, _ = _llm(providers)
    _, usage = await llm.create("context", messages=[{"role": "user", "content": "hi"}])
    assert usage.cost_usd == 0.0  # NVIDIA free tier
    gemini, nvidia = recorder.calls
    assert gemini.model == "gemini-2.5-flash" and gemini.error and "429" in gemini.error
    assert nvidia.model == "meta/llama-3.3-70b-instruct" and nvidia.error is None
    assert (nvidia.input_tokens, nvidia.output_tokens, nvidia.stop_reason) == (5000, 40, "stop")
    assert nvidia.request_id == "cmpl-1" and nvidia.latency_ms >= 0

    llm, recorder, _ = _llm(Providers(gemini=_ok("sentence"), nvidia=_ok("unused")))
    _, usage = await llm.create("context", messages=[{"role": "user", "content": "hi"}])
    assert usage.cost_usd == pytest.approx((1000 * 0.30 + 200 * 2.50) / 1e6)
    assert recorder.calls[0].cost_usd == usage.cost_usd

    priced = make_settings(GEMINI_API_KEY="g", GEMINI_INPUT_PRICE_PER_MTOK=1.0, GEMINI_OUTPUT_PRICE_PER_MTOK=2.0)
    assert provider_configs(priced)[0].price.input == 1.0


async def test_no_anthropic_key_routes_straight_to_fallback() -> None:
    assert build_fallback(make_settings(), httpx.AsyncClient(), Recorder()) is None
    assert isinstance(build_fallback(make_settings(NVIDIA_API_KEY="n"), httpx.AsyncClient(), Recorder()), FallbackChain)

    providers = Providers(gemini=_ok("Situates the chunk."), nvidia=_ok("unused"))
    llm, recorder, _ = _llm(providers)
    assert llm.model_for("answer") == "gemini-2.5-flash"
    sentence = await Contextualizer(llm).situate("Social Media Tab", "Whole article text.", "Chunk text.")
    assert sentence == "Situates the chunk."
    [message] = providers.body()["messages"]
    assert message["role"] == "user" and isinstance(message["content"], str)  # text blocks flattened
    assert "Whole article text." in message["content"] and "cache_control" not in json.dumps(providers.body())
    assert [c.model for c in recorder.calls] == ["gemini-2.5-flash"]  # no Claude attempt

    nothing = LLMClient(None, make_settings(), Recorder())
    with pytest.raises(LLMError, match="no fallback provider"):
        await nothing.create("context", messages=[{"role": "user", "content": "hi"}])


async def test_vision_falls_back_to_gemini_only() -> None:
    image = ImageRef("1", "https://s3/x.png?1", "1", 0, "Footer menu")
    providers = Providers(gemini=_ok("Tabs: General, Social Media."), nvidia=_ok("unused"))
    llm, _, _ = _llm(providers, claude=_down, order="nvidia,gemini")
    described = await ClaudeImageDescriber(llm).describe(image, "Social Media Tab")
    assert described.described_by == "claude" and described.text == "Tabs: General, Social Media."
    assert providers.names == ["gemini"]
    parts = providers.body()["messages"][0]["content"]
    assert parts[0] == {"type": "image_url", "image_url": {"url": "https://s3/x.png?1"}}

    nvidia_only, _, _ = _llm(Providers(nvidia=_ok("unused")), keys=("nvidia",))
    assert not nvidia_only.can_serve("vision") and nvidia_only.can_serve("answer")


async def test_unreachable_fallbacks_raise_the_original_error() -> None:
    def boom(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        raise httpx.ConnectError("refused")

    providers = Providers(gemini=boom, nvidia=_fail(502))
    llm, recorder, _ = _llm(providers, claude=_down)
    with pytest.raises(LLMError) as info:
        await llm.create("context", messages=[{"role": "user", "content": "hi"}])
    assert info.value.retryable and "Claude" in str(info.value)
    assert [c.model for c in recorder.calls] == ["claude-haiku-5-5", "gemini-2.5-flash", "meta/llama-3.3-70b-instruct"]
    assert all(c.error for c in recorder.calls)


async def test_batches_stay_claude_only() -> None:
    llm, _, _ = _llm(Providers())
    with pytest.raises(LLMError, match="Message Batches need ANTHROPIC_API_KEY"):
        await llm.submit_batch("vision", [("x", [{"role": "user", "content": "hi"}])])
