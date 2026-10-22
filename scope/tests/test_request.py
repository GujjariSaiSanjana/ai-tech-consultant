"""The built request: cache breakpoint placement, identical tools, no forbidden parameters."""

from __future__ import annotations

from scope.agents.base import request
from scope.brain.tools import BrainToolBox
from scope.config import AGENTS, Settings
from scope.fakes.brain import FakeBrain
from scope.llm.request import build_request


def test_transcript_is_first_and_carries_the_only_breakpoint(settings: Settings) -> None:
    body = build_request(
        request("extractor", settings, "TRANSCRIPT-TEXT", "{}"), BrainToolBox(FakeBrain())
    )
    first, second = body["system"]
    assert "TRANSCRIPT-TEXT" in first["text"]
    assert first["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in second
    assert "Requirements Extractor" in second["text"]  # role instructions after the breakpoint
    assert body["messages"] == [{"role": "user", "content": "{}"}]


def test_prefix_is_identical_across_agents(settings: Settings) -> None:
    tools = BrainToolBox(FakeBrain())
    bodies = [build_request(request(a, settings, "SAME", "{}"), tools) for a in AGENTS]
    prefixes = {(str(b["tools"]), b["system"][0]["text"]) for b in bodies}
    assert len(prefixes) == 1
    assert [t["name"] for t in bodies[0]["tools"]] == ["brain_feasibility", "brain_search"]
    assert len({b["system"][1]["text"] for b in bodies}) == len(AGENTS)


def test_no_sampling_params_thinking_budget_or_forced_tool_choice(settings: Settings) -> None:
    for a in AGENTS:
        body = build_request(request(a, settings, "x", "{}"), BrainToolBox(FakeBrain()))
        for banned in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
            assert banned not in body
        assert body["output_config"]["effort"] in ("low", "medium", "high", "xhigh", "max")
        assert body["model"] in ("claude-sonnet-5-5", "claude-opus-5-5")


def test_routing_table_matches_design(settings: Settings) -> None:
    r = settings.routes
    assert (
        r["extractor"].model
        == r["feasibility"].model
        == r["proposal_writer"].model
        == "claude-sonnet-5-5"
    )
    assert {r[a].model for a in ("questioner", "scoper", "reviewer", "reconciler")} == {
        "claude-opus-5-5"
    }
    haiku = settings.with_model("claude-haiku-5-5")
    assert {v.model for v in haiku.routes.values()} == {"claude-haiku-5-5"}
