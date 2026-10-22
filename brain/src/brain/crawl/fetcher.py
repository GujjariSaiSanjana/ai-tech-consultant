from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from brain.crawl.robots import RobotsPolicy

log = logging.getLogger(__name__)
RETRYABLE = {429, 500, 502, 503, 504}

Sleep = Callable[[float], Awaitable[None]]


class RobotsDisallowedError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class FetchResult:
    url: str
    status: int
    text: str


class PoliteFetcher:
    """One request at a time, at least `interval_s` apart, jittered exponential backoff on 429/5xx.

    The site sends no ETag/Last-Modified, so there is nothing to make conditional; politeness is
    pacing plus honouring robots.txt and Retry-After.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        robots: RobotsPolicy,
        interval_s: float = 1.0,
        max_attempts: int = 5,
        backoff_base_s: float = 2.0,
        backoff_cap_s: float = 120.0,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._robots = robots
        self._interval_s = interval_s
        self._max_attempts = max_attempts
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s
        self._sleep = sleep
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last_request_at: float | None = None

    @classmethod
    async def create(
        cls, client: httpx.AsyncClient, *, site_url: str, user_agent: str, interval_s: float = 1.0
    ) -> PoliteFetcher:
        response = await client.get(f"{site_url}/robots.txt")
        if response.status_code == 200:
            robots_txt = response.text
        elif response.status_code >= 500:  # RFC 9309: unreachable robots.txt means crawl nothing
            robots_txt = "User-agent: *\nDisallow: /"
        else:  # 4xx: no robots.txt, everything is allowed
            robots_txt = ""
        robots = RobotsPolicy(robots_txt, user_agent)
        return cls(client, robots=robots, interval_s=max(interval_s, robots.crawl_delay() or 0.0))

    async def get(self, url: str) -> FetchResult:
        if not self._robots.allowed(url):
            raise RobotsDisallowedError(url)
        async with self._lock:
            for attempt in range(1, self._max_attempts + 1):
                await self._pace()
                try:
                    response = await self._client.get(url)
                except httpx.TransportError as exc:
                    if attempt == self._max_attempts:
                        raise
                    log.warning("transport error on %s (%s), retrying", url, exc)
                    await self._sleep(self._backoff(attempt))
                    continue
                if response.status_code not in RETRYABLE or attempt == self._max_attempts:
                    return FetchResult(url=url, status=response.status_code, text=response.text)
                delay = _retry_after(response) or self._backoff(attempt)
                log.warning("HTTP %s on %s, backing off %.1fs", response.status_code, url, delay)
                await self._sleep(delay)
        raise AssertionError("unreachable")

    async def _pace(self) -> None:
        now = self._clock()
        if self._last_request_at is not None:
            wait = self._interval_s - (now - self._last_request_at) + random.uniform(0, 0.25 * self._interval_s)
            if wait > 0:
                await self._sleep(wait)
        self._last_request_at = self._clock()

    def _backoff(self, attempt: int) -> float:
        ceiling = min(self._backoff_cap_s, self._backoff_base_s * 2 ** (attempt - 1))
        return random.uniform(ceiling / 2, ceiling)


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None
