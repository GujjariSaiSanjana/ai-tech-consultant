"""`python -m brain.sync [--limit N] [--resume] [--sitemap-file F]`: one incremental sync.

Diffs the live sitemap against the index: new URLs are added, URLs whose lastmod moved are
re-fetched (the body hash decides whether anything changed), and URLs that left the sitemap are
marked removed and lose their chunks.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from brain.config import Settings
from brain.container import Container, build_container
from brain.domain import RunCounts
from brain.migrate import migrate


async def run_sync(
    c: Container, *, limit: int | None = None, resume: bool = False, sitemap_xml: str | None = None
) -> tuple[str, RunCounts]:
    engine = await c.crawl_engine()
    run_id = await c.repo.latest_running("sync") if resume else None
    run_id = run_id or await engine.start("sync", limit=limit)
    return run_id, await engine.execute(run_id, sitemap_xml=sitemap_xml)


async def _main(limit: int | None, resume: bool, sitemap_xml: str | None) -> None:
    settings = Settings()
    await migrate(settings.database_url)
    async with build_container(settings) as c:
        run_id, counts = await run_sync(c, limit=limit, resume=resume, sitemap_xml=sitemap_xml)
    print(
        f"sync {run_id}: added={counts.added} updated={counts.updated} removed={counts.removed} "
        f"unchanged={counts.unchanged} errors={counts.errors}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m brain.sync", description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="fetch at most N new/changed articles")
    parser.add_argument("--resume", action="store_true", help="continue the latest unfinished sync run")
    parser.add_argument("--sitemap-file", type=Path, default=None, help="diff against a local sitemap copy")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sitemap_xml = args.sitemap_file.read_text() if args.sitemap_file else None
    asyncio.run(_main(args.limit, args.resume, sitemap_xml))


if __name__ == "__main__":
    main()
