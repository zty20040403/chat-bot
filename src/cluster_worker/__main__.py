from __future__ import annotations

import uvicorn

from .api import create_app
from .config import WorkerSettings
from .service import ClusterWorker


def main() -> None:
    settings = WorkerSettings.from_env()
    worker = ClusterWorker(settings)
    uvicorn.run(
        create_app(worker), host=settings.listen_host, port=settings.listen_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
