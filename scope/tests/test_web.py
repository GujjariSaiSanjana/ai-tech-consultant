"""UI smoke test through the real routes (fake LLM and Brain)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from scope.config import Settings
from scope.web.app import create_app
from tests.conftest import read


def wait_for_run(client: TestClient, pid: int) -> None:
    for _ in range(100):
        r = client.get(f"/projects/{pid}/runs/status")
        if r.headers.get("HX-Refresh") == "true":
            return
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_ui_flow(settings: Settings) -> None:
    with TestClient(create_app(settings)) as c:
        assert "Offline mode" in c.get("/").text
        r = c.post("/projects", data={"name": "GuideFinder", "client": "Trailhead Media"})
        assert r.status_code == 200 and "GuideFinder" in r.text
        pid = int(str(r.url).split("/projects/")[1].split("?")[0])
        files = {"file": ("m1.txt", read("meeting-01-kickoff.txt").encode(), "text/plain")}
        assert "Added meeting m1" in c.post(f"/projects/{pid}/meetings", files=files).text
        assert "Run 1 started" in c.post(f"/projects/{pid}/runs").text
        wait_for_run(c, pid)
        for tab in ("transcripts", "questions", "scope", "hours", "proposal", "changes"):
            page = c.get(f"/projects/{pid}?tab={tab}")
            assert page.status_code == 200, tab
        proposal = c.get(f"/projects/{pid}?tab=proposal").text
        assert "<button disabled" in proposal  # gate: generate disabled before approval
        r = c.post("/scope/1/proposal", data={"actor": "Sam"})
        assert "approve the scope" in r.text
        assert "approved" in c.post("/scope/1/approve", data={"actor": "Sam"}).text
        r = c.post("/scope/1/proposal", data={"actor": "Sam"})
        assert "Proposal drafted" in r.text
        r = c.post(
            "/proposals/1/sections/summary/edit",
            data={"actor": "Sam", "content": "Edited summary."},
        )
        assert "Edited summary." in r.text and "edited by a person" in r.text
        assert c.get("/proposals/1/preview.md").text.startswith("# Proposal for Trailhead Media")
        r = c.post(
            "/proposals/1/sections/price/approve", data={"actor": "Sam"}, follow_redirects=False
        )
        location = r.headers["location"]
        assert location.index("msg=") < location.index("#sec-price")
        r = c.post("/proposals/1/transition", data={"action": "send", "actor": "Sam"})
        assert "only an approved proposal can be sent" in r.text
        assert c.get("/projects/999").status_code == 404
