from __future__ import annotations

import uvicorn

from src.bot_storage import PostgresDatabase
from src.bot_storage.schema import HEAD_REVISION

from .api import create_app
from .config import ClusterControlSettings
from .diagnostics import DiagnosticStore, IncidentDiagnosticService
from .adapters.maxops import MaxOpsClient
from .service import FleetControlService
from .storage import FleetProjectionStore


def main() -> None:
    settings = ClusterControlSettings.from_env()
    settings.validate()
    database = PostgresDatabase(
        settings.postgres_dsn,
        schema=settings.postgres_schema,
        min_size=settings.postgres_pool_min_size,
        max_size=settings.postgres_pool_max_size,
        timeout_seconds=settings.postgres_pool_timeout_seconds,
        application_name="kennethbot-cluster-control",
    )
    database.require_revision(HEAD_REVISION)
    maxops = (
        MaxOpsClient(
            settings.maxops_base_url,
            settings.maxops_token_file,
            timeout_seconds=settings.maxops_timeout_seconds,
        )
        if settings.maxops_enabled
        else None
    )
    service = FleetControlService(
        maxops,
        store=FleetProjectionStore(database),
        inventory=settings.inventory,
        cache_seconds=settings.cache_seconds,
    )
    diagnostics = IncidentDiagnosticService(
        service,
        DiagnosticStore(database),
        settings.diagnostic_targets,
        local_host_id=settings.local_host_id,
    )
    app = create_app(
        service,
        api_token_file=settings.api_token_file,
        diagnostics=diagnostics,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
