#!/usr/bin/env python3
"""Start the VizaFlow API on 127.0.0.1:8000 (override with HOST/PORT)."""

from __future__ import annotations

import uvicorn

from app.config import get_settings, load_env_file


def main() -> None:
    load_env_file()
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
