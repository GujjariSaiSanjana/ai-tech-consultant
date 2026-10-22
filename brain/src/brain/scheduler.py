"""`python -m brain.scheduler`: run the incremental sync every day at BRAIN_SYNC_HOUR (default 03:00 local)."""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from brain.config import Settings
from brain.container import Container, build_container
from brain.migrate import migrate
from brain.sync import run_sync

log = logging.getLogger(__name__)


async def daily_sync(c: Container) -> None:
    try:
        # Resume first: a sync killed mid-run (deploy, crash) finishes before a new one is planned.
        run_id, counts = await run_sync(c, resume=True)
        log.info("daily sync %s finished: %s", run_id, counts)
    except Exception:
        log.exception("daily sync failed")


async def _main() -> None:
    settings = Settings()
    await migrate(settings.database_url)
    async with build_container(settings) as c:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            daily_sync,
            CronTrigger(hour=settings.sync_hour, minute=0, jitter=300),
            args=[c],
            id="daily-sync",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        scheduler.start()
        log.info("scheduler started; next run at %s", scheduler.get_job("daily-sync").next_run_time)
        try:
            await asyncio.Event().wait()
        finally:
            scheduler.shutdown(wait=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_main())


if __name__ == "__main__":
    main()
