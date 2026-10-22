"""``python -m scope.web`` serves the UI on port 8100."""

from __future__ import annotations

import os

import uvicorn

from scope.web.app import create_app


def main() -> None:
    uvicorn.run(
        create_app(),
        host=os.environ.get("SCOPE_HOST", "127.0.0.1"),
        port=int(os.environ.get("SCOPE_PORT", "8100")),
    )


if __name__ == "__main__":
    main()
