"""`python -m brain.crawl [--limit N] [--resume] [--save-html DIR]`: the initial polite crawl."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from brain.config import Settings
from brain.container import build_container
from brain.migrate import migrate


async def run(limit: int | None, resume: bool, save_html: Path | None) -> None:
    settings = Settings()
    await migrate(settings.database_url)
    async with build_container(settings) as c:
        engine = await c.crawl_engine(save_html_dir=save_html)
        resumed = await c.repo.latest_resumable("crawl") if resume else None
        if resume and resumed is None:
            print("no unfinished crawl to resume; starting a new one")
        run_id = resumed or await engine.start("crawl", limit=limit)
        print(f"crawl run {run_id}")
        counts = await engine.execute(run_id, owner=resumed is None)
        reembedded = await c.service.reembed_expert_answers()
        if reembedded:
            print(f"re-embedded {reembedded} expert answer(s) with {c.embedder.name}")
        print(
            f"done: added={counts.added} updated={counts.updated} unchanged={counts.unchanged} "
            f"removed={counts.removed} errors={counts.errors}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m brain.crawl", description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="crawl N articles spread across the sitemap")
    parser.add_argument("--resume", action="store_true", help="continue the latest unfinished crawl run")
    parser.add_argument("--save-html", type=Path, default=None, help="also write raw pages to this directory")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(args.limit, args.resume, args.save_html))


if __name__ == "__main__":
    main()
