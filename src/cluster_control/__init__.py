"""gaoji fleet control plane.

The control plane owns user-facing orchestration and evidence projections. It
does not replace Ops, Prometheus, or host-local authorization.
"""

from .contracts import FleetQueryResult, FleetStatus
from .diagnostics import IncidentDiagnosticService
from .adapters.ops import OpsClient, OpsError, OpsOperation
from .service import FleetControlService

__all__ = [
    "FleetControlService",
    "FleetQueryResult",
    "FleetStatus",
    "IncidentDiagnosticService",
    "OpsClient",
    "OpsError",
    "OpsOperation",
]
