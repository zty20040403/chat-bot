from __future__ import annotations

import asyncio

from .config import DeployerSettings
from .service import ClusterDeployer


def main() -> None:
    settings = DeployerSettings.from_env()
    asyncio.run(ClusterDeployer(settings).run_forever())


if __name__ == "__main__":
    main()
