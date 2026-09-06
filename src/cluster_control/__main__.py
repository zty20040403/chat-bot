from __future__ import annotations

import uvicorn
from pathlib import Path

from src.bot_storage import PostgresDatabase
from src.bot_storage.schema import HEAD_REVISION

from .api import create_app
from .auth import CredentialFileAuthenticator
from .config import ClusterControlSettings
from .deployment_service import DeploymentService
from .deployment_storage import DeploymentStore
from .diagnostics import DiagnosticStore, IncidentDiagnosticService
from .adapters.maxops import MaxOpsClient
from .service import FleetControlService
from .storage import FleetProjectionStore
from .execution_service import ClusterExecutionService, WorkerAuthenticator
from .execution_storage import ClusterExecutionStore
from .reliability import GuardianService, ReliabilityStore
from .scheduling import ResourcePolicyStore


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
    execution_store = ClusterExecutionStore(database, Path(settings.artifact_dir))
    resource_policies = ResourcePolicyStore(database)
    reliability = ReliabilityStore(database)
    worker_hosts = {
        item["worker_id"]: item["host_id"] for item in settings.worker_identities
    }
    worker_owners = {
        item["worker_id"]: item["owner_actor_id"] for item in settings.worker_identities
    }
    execution = ClusterExecutionService(
        execution_store,
        inventory=settings.inventory,
        diagnostic_targets=settings.diagnostic_targets,
        worker_hosts=worker_hosts,
        worker_owners=worker_owners,
        resource_policies=resource_policies,
    )
    guardian = GuardianService(
        reliability,
        settings.diagnostic_targets,
        operation_factory=lambda raw, actor, origin: execution.prepare_operation(
            raw, actor_id=actor, origin_scope=origin
        ),
    )
    worker_authenticator = WorkerAuthenticator(
        {item["worker_id"]: item["token_file"] for item in settings.worker_identities}
    )
    deployments = DeploymentService(
        DeploymentStore(database),
        repositories=settings.deployment_repositories,
        deployer_repositories={
            str(item["deployer_id"]): tuple(item["repository_ids"])
            for item in settings.deployer_identities
        },
    )
    deployer_authenticator = CredentialFileAuthenticator(
        {
            str(item["deployer_id"]): str(item["token_file"])
            for item in settings.deployer_identities
        }
    )
    app = create_app(
        service,
        api_token_file=settings.api_token_file,
        diagnostics=diagnostics,
        execution=execution,
        worker_authenticator=worker_authenticator,
        resource_policies=resource_policies,
        reliability=reliability,
        guardian=guardian,
        deployments=deployments,
        deployer_authenticator=deployer_authenticator,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
