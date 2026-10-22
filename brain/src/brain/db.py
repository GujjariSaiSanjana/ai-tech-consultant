from __future__ import annotations

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


async def open_pool(database_url: str, *, max_size: int = 10) -> Pool:
    pool: Pool = AsyncConnectionPool(
        database_url,
        min_size=1,
        max_size=max_size,
        open=False,
        kwargs={"row_factory": dict_row, "autocommit": True},
        connection_class=AsyncConnection[DictRow],
    )
    await pool.open(wait=True)
    return pool


def to_vector_literal(values: list[float]) -> str:
    """pgvector's text input format; avoids a binary adapter dependency."""
    return "[" + ",".join(f"{v:.7g}" for v in values) + "]"
