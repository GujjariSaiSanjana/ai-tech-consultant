"""Apply plain-SQL migrations from `brain/migrations/` in filename order, once each."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from psycopg import AsyncConnection

from brain.config import PROJECT_DIR, Settings

MIGRATIONS_DIR = PROJECT_DIR / "migrations"
log = logging.getLogger(__name__)


async def migrate(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    applied: list[str] = []
    async with await AsyncConnection.connect(database_url, autocommit=True) as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        # Serialise concurrent runners (API and scheduler may start together).
        await conn.execute("SELECT pg_advisory_lock(727001)")
        try:
            cur = await conn.execute("SELECT name FROM schema_migrations")
            done = {row[0] for row in await cur.fetchall()}
            for path, script in _migration_files(migrations_dir):
                if path.name in done:
                    continue
                async with conn.transaction():
                    await conn.execute(script)
                    await conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                applied.append(path.name)
                log.info("applied migration %s", path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(727001)")
    return applied


def _migration_files(directory: Path) -> list[tuple[Path, bytes]]:
    return [(path, path.read_bytes()) for path in sorted(directory.glob("*.sql"))]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    applied = asyncio.run(migrate(Settings().database_url))
    print(f"applied {len(applied)} migration(s): {', '.join(applied) or 'none pending'}")


if __name__ == "__main__":
    main()
