"""Trajecta server entry point.

Starts the FastAPI application that serves as the Tauri sidecar.
"""

from __future__ import annotations

import uvicorn

from server.src.api.app import create_app
from server.src.config import Settings


def main() -> None:
    """Launch the server."""
    settings = Settings.load()
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.server.host,
        port=settings.server.port,
    )


if __name__ == "__main__":
    main()