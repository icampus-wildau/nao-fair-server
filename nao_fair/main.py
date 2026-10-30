"""Production entrypoint. There is no simulator flag."""

from __future__ import annotations

import uvicorn

from nao_fair.app import create_app
from nao_fair.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        create_app(),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
