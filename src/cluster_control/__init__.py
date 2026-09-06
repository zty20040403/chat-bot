"""Kennethbot fleet control plane.

The control plane owns user-facing orchestration and evidence projections. It
does not replace MaxOps, Prometheus, or host-local authorization.
"""

from .contracts import FleetQueryResult, FleetStatus
from .adapters.maxops import MaxOpsClient, MaxOpsError, MaxOpsOperation
from .service import FleetControlService

__all__ = [
    "FleetControlService",
    "FleetQueryResult",
    "FleetStatus",
    "MaxOpsClient",
    "MaxOpsError",
    "MaxOpsOperation",
]
