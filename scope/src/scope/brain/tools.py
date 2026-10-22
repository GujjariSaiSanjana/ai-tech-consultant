"""Brain tools exposed to every agent. The list is constant so the cached prefix is identical."""

from __future__ import annotations

import json
from typing import Any

from scope.brain.client import BrainClient, BrainUnavailableError

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "brain_feasibility",
        "description": (
            "Ask the product Knowledge Brain whether a requirement is solved by an existing "
            "Brilliant Directories setting, an add-on, a widget customization, or needs custom "
            "development. Returns kind, setting_path, rationale, confidence and citations."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"requirement": {"type": "string"}},
            "required": ["requirement"],
            "additionalProperties": False,
        },
    },
    {
        "name": "brain_search",
        "description": "Search the product help-center articles; returns the top passages.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


class BrainToolBox:
    def __init__(self, brain: BrainClient) -> None:
        self._brain = brain

    def definitions(self) -> list[dict[str, Any]]:
        return TOOL_DEFINITIONS

    async def execute(self, name: str, tool_input: dict[str, Any]) -> tuple[str, bool]:
        try:
            if name == "brain_feasibility":
                ans = await self._brain.feasibility(str(tool_input["requirement"]))
                return ans.model_dump_json(), False
            if name == "brain_search":
                hits = await self._brain.search(str(tool_input["query"]), top_k=5)
                return hits.model_dump_json(), False
        except BrainUnavailableError as exc:
            return json.dumps({"error": "brain_unavailable", "message": str(exc)}), True
        except KeyError as exc:
            return json.dumps({"error": "bad_input", "message": f"missing {exc}"}), True
        return json.dumps({"error": "unknown_tool", "message": name}), True
