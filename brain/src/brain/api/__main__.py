"""`python -m brain.api`: serve the API and UI (default 127.0.0.1:8000)."""

from __future__ import annotations

import logging

import uvicorn

from brain.api.app import create_app
from brain.config import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    uvicorn.run(create_app(settings=settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
