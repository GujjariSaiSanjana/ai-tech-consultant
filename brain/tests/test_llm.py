"""Claude-facing code against a mocked HTTP transport: request shape, citations, refusals, errors."""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest

from brain.domain import SearchHit
from brain.llm.answerer import ClaudeAnswerer
from brain.llm.classifier import Classification, ClaudeClassifier
from brain.llm.client import CallRecord, LLMClient, LLMError, LLMRefusalError, cost_usd
from tests.helpers import make_settings

FORBIDDEN = {"temperature", "top_p", "top_k", "tool_choice"}


class Recorder:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []

    async def record_llm_call(self, record: CallRecord) -> None:
        self.calls.append(record)


def _message(
    content: list[dict[str, Any]], *, stop_reason: str = "end_turn", model: str = "claude-sonnet-5-5", **extra: Any
) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 80,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        **extra,
    }


def _client(responder: Any, requests: list[dict[str, Any]]) -> tuple[LLMClient, Recorder]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        status, payload = responder(body)
        return httpx2.Response(status, json=payload, headers={"request-id": "req_test"})

    sdk = anthropic.AsyncAnthropic(
        api_key="sk-test",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    recorder = Recorder()
    return LLMClient(sdk, make_settings(), recorder), recorder


def _hits() -> list[SearchHit]:
    return [
        SearchHit(
            "c1",
            "5000558389",
            "Settings » General Settings » Social Media Tab",
            "https://h/a/5000558389",
            "Settings",
            "General Settings",
            "text",
            "Add a link to the website's TikTok account using the following format: https://www.tiktok.com/@name",
            "Settings > General Settings > Social Media Tab",
            0.03,
            0.8,
            None,
        ),
        SearchHit(
            "c2",
            "5000558389",
            "Settings » General Settings » Social Media Tab",
            "https://h/a/5000558389",
            "Settings",
            "General Settings",
            "image",
            "Screenshot: footer icons",
            "Settings > General Settings > Social Media Tab",
            0.02,
            0.7,
            "https://s3/img.png?1",
        ),
    ]


async def test_answer_request_shape_and_citation_mapping() -> None:
    requests: list[dict[str, Any]] = []
    cited = "Add a link to the website's TikTok account using the following format: https://www.tiktok.com/@name"
    llm, recorder = _client(
        lambda body: (
            200,
            _message(
                [
                    {
                        "type": "text",
                        "text": "Use the format https://www.tiktok.com/@name.",
                        "citations": [
                            {
                                "type": "char_location",
                                "cited_text": cited,
                                "document_index": 0,
                                "document_title": "t",
                                "start_char_index": 0,
                                "end_char_index": len(cited),
                            },
                            {
                                "type": "char_location",
                                "cited_text": "Screenshot: footer icons",
                                "document_index": 1,
                                "document_title": "t",
                                "start_char_index": 0,
                                "end_char_index": 24,
                            },
                        ],
                    },
                ]
            ),
        ),
        requests,
    )
    draft = await ClaudeAnswerer(llm).answer("TikTok link format?", _hits())

    body = requests[0]
    assert not FORBIDDEN & body.keys()
    assert "budget_tokens" not in json.dumps(body)
    assert body["model"] == "claude-sonnet-5-5"
    assert body["output_config"] == {"effort": "medium"}
    docs = [b for b in body["messages"][0]["content"] if b["type"] == "document"]
    assert len(docs) == 2 and all(d["citations"] == {"enabled": True} for d in docs)
    assert docs[0]["source"]["type"] == "text"

    assert draft.status == "answered"
    assert [(c.hit_index, c.quote) for c in draft.citations] == [(0, cited), (1, "Screenshot: footer icons")]
    assert draft.usage.cost_usd == pytest.approx((1200 * 2 + 80 * 10) / 1e6)
    assert recorder.calls[0].purpose == "answer" and recorder.calls[0].request_id == "req_test"


async def test_no_answer_sentinel() -> None:
    llm, _ = _client(lambda body: (200, _message([{"type": "text", "text": "NO_ANSWER"}])), [])
    draft = await ClaudeAnswerer(llm).answer("Does it integrate with SAP?", _hits())
    assert draft.status == "no_answer" and draft.citations == []


async def test_refusal_is_raised_and_logged() -> None:
    llm, recorder = _client(
        lambda body: (
            200,
            _message(
                [], stop_reason="refusal", stop_details={"type": "refusal", "category": "cyber", "explanation": None}
            ),
        ),
        [],
    )
    with pytest.raises(LLMRefusalError):
        await ClaudeAnswerer(llm).answer("q", _hits())
    assert recorder.calls[0].error == "refusal:cyber"


async def test_rate_limit_becomes_retryable_error() -> None:
    error = {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    llm, recorder = _client(lambda body: (429, error), [])
    with pytest.raises(LLMError) as info:
        await ClaudeAnswerer(llm).answer("q", _hits())
    assert info.value.retryable
    assert recorder.calls[0].error and "RateLimitError" in recorder.calls[0].error


async def test_bad_request_is_not_retryable() -> None:
    error = {"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}}
    llm, _ = _client(lambda body: (400, error), [])
    with pytest.raises(LLMError) as info:
        await ClaudeAnswerer(llm).answer("q", _hits())
    assert not info.value.retryable


async def test_classifier_uses_structured_output_on_haiku() -> None:
    requests: list[dict[str, Any]] = []
    payload: dict[str, Any] = {
        "label": "setting",
        "setting_path": None,
        "warnings": [],
        "rationale": "built in",
        "confidence": 1.4,
    }
    llm, _ = _client(
        lambda body: (200, _message([{"type": "text", "text": json.dumps(payload)}], model="claude-haiku-5-5")),
        requests,
    )
    result, usage = await ClaudeClassifier(llm).classify("add tiktok link", "Use the Social Media tab", _hits())
    body = requests[0]
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["effort"] == "low"
    assert not FORBIDDEN & body.keys()
    assert isinstance(result, Classification)
    assert result.setting_path == "Settings > General Settings > Social Media Tab"  # filled from the title
    assert result.confidence == 1.0  # clamped
    assert usage.cost_usd == pytest.approx((1200 * 0.10 + 80 * 0.50) / 1e6)


def test_cost_includes_cache_tokens() -> None:
    message = anthropic.types.Message.model_validate(
        _message([], model="claude-haiku-5-5")
        | {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 1000,
                "cache_read_input_tokens": 10000,
                "cache_creation_input_tokens": 1000,
            }
        }
    )
    assert cost_usd("claude-haiku-5-5", message) == pytest.approx(
        (1000 * 0.10 + 1000 * 0.50 + 10000 * 0.01 + 1000 * 0.125) / 1e6
    )
    assert cost_usd("unknown-model", message) == 0.0
