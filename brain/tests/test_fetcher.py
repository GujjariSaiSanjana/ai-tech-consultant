from __future__ import annotations

import httpx
import pytest

from brain.crawl.fetcher import PoliteFetcher, RobotsDisallowedError
from brain.crawl.robots import RobotsPolicy

ROBOTS = """User-agent: *
Disallow: /support/search
Disallow: /support/tickets/
Disallow: /*/hit$
"""


def test_robots_policy_including_wildcards() -> None:
    policy = RobotsPolicy(ROBOTS, "TestBot")
    assert policy.allowed("https://h/support/solutions/articles/1-x")
    assert not policy.allowed("https://h/support/search?term=x")
    assert not policy.allowed("https://h/support/solutions/articles/1/hit")
    assert policy.allowed("https://h/support/solutions/articles/1/hits")


async def test_robots_server_error_means_crawl_nothing_and_crawl_delay_is_honoured() -> None:
    down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)), base_url="https://h")
    fetcher = await PoliteFetcher.create(down, site_url="https://h", user_agent="TestBot")
    with pytest.raises(RobotsDisallowedError):
        await fetcher.get("https://h/support/solutions/articles/1")
    slow = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="User-agent: *\nCrawl-delay: 4\n"))
    )
    assert (await PoliteFetcher.create(slow, site_url="https://h", user_agent="TestBot"))._interval_s == 4.0


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _fetcher(handler: httpx.MockTransport, clock: Clock, **kw: float) -> PoliteFetcher:
    return PoliteFetcher(
        httpx.AsyncClient(transport=handler),
        robots=RobotsPolicy(ROBOTS, "TestBot"),
        sleep=clock.sleep,
        clock=clock.time,
        **kw,  # type: ignore[arg-type]
    )


async def test_backs_off_on_429_honouring_retry_after() -> None:
    calls: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, text="ok")

    clock = Clock()
    result = await _fetcher(httpx.MockTransport(handle), clock).get("https://h/support/solutions/articles/1")
    assert result.status == 200 and result.text == "ok"
    assert 7.0 in clock.sleeps


async def test_gives_up_after_max_attempts_on_5xx() -> None:
    clock = Clock()
    fetcher = _fetcher(httpx.MockTransport(lambda r: httpx.Response(503)), clock, max_attempts=3)
    result = await fetcher.get("https://h/support/solutions/articles/1")
    assert result.status == 503
    backoffs = [s for s in clock.sleeps if s >= 1.0]
    assert len(backoffs) >= 2  # two backoffs between three attempts (plus pacing)


async def test_paces_requests_at_least_interval_apart() -> None:
    clock = Clock()
    fetcher = _fetcher(httpx.MockTransport(lambda r: httpx.Response(200)), clock, interval_s=1.0)
    for _ in range(3):
        await fetcher.get("https://h/support/solutions/articles/1")
    assert len(clock.sleeps) == 2
    assert all(1.0 <= s <= 1.25 for s in clock.sleeps)


async def test_refuses_disallowed_urls_without_requesting() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not be requested")

    with pytest.raises(RobotsDisallowedError):
        await _fetcher(httpx.MockTransport(explode), Clock()).get("https://h/support/search?term=x")
