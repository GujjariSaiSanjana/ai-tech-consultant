from __future__ import annotations

import re
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser


class RobotsPolicy:
    """`urllib.robotparser` plus the `*` / `$` wildcard rules it treats literally (e.g. `/*/hit$`)."""

    def __init__(self, robots_txt: str, user_agent: str) -> None:
        self._parser = RobotFileParser()
        self._parser.parse(robots_txt.splitlines())
        self._user_agent = user_agent
        self._wildcards = [
            re.compile(_wildcard_to_regex(rule))
            for rule in _disallow_rules_for_all_agents(robots_txt)
            if "*" in rule or rule.endswith("$")
        ]

    def allowed(self, url: str) -> bool:
        if not self._parser.can_fetch(self._user_agent, url):
            return False
        parts = urlsplit(url)
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        return not any(rx.match(path) for rx in self._wildcards)


def _disallow_rules_for_all_agents(robots_txt: str) -> list[str]:
    rules: list[str] = []
    applies = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        if key.lower() == "user-agent":
            applies = value == "*"
        elif key.lower() == "disallow" and applies and value:
            rules.append(value)
    return rules


def _wildcard_to_regex(rule: str) -> str:
    anchored = rule.endswith("$")
    body = re.escape(rule.rstrip("$")).replace(r"\*", ".*")
    return body + ("$" if anchored else "")
