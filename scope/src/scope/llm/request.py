"""Build the exact Messages API request for an agent call.

Cache layout (render order is tools -> system -> messages):
  tools:   identical list for every agent (sorted by name)
  system:  [0] shared preamble + transcripts  <- cache_control breakpoint
           [1] role instructions               (differs per agent, after the breakpoint)
  messages: [user payload]
So every agent on the same model reads the cached transcript prefix. Nothing volatile (dates,
IDs, unsorted JSON) is allowed before the breakpoint.

API rules for 5.5 models respected here: no temperature/top_p/top_k, no thinking budget, no
forced tool_choice; depth is set with ``output_config.effort``.
"""

from __future__ import annotations

from typing import Any

from scope.llm.types import LLMRequest, ToolBox
from scope.prompts import load_prompt

SHARED_PREAMBLE_PROMPT = "shared.v1"


def system_blocks(req: LLMRequest) -> list[dict[str, Any]]:
    preamble = load_prompt(SHARED_PREAMBLE_PROMPT)
    return [
        {
            "type": "text",
            "text": f"{preamble}\n\n<transcripts>\n{req.context}\n</transcripts>",
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": req.instructions},
    ]


def build_request(req: LLMRequest, tools: ToolBox | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": req.route.model,
        "max_tokens": req.route.max_tokens,
        "system": system_blocks(req),
        "messages": [{"role": "user", "content": req.payload}],
        "output_config": {"effort": req.route.effort},
    }
    if tools is not None:
        body["tools"] = sorted(tools.definitions(), key=lambda t: str(t["name"]))
    return body
