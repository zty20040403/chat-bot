"""gaoji fleet control plane.

The control plane owns user-facing orchestration and evidence projections. It
does not replace Ops, Prometheus, or host-local authorization.
"""

from importlib import import_module


_EXPORTS = {
    "FleetControlService": ".service",
    "FleetQueryResult": ".contracts",
    "FleetStatus": ".contracts",
    "IncidentDiagnosticService": ".diagnostics",
    "OpsClient": ".adapters.ops",
    "OpsError": ".adapters.ops",
    "OpsOperation": ".adapters.ops",
}


def __getattr__(name: str):
    # Workers share the job catalog without importing the database or Bot runtime.
    if name not in _EXPORTS:
        raise AttributeError(name)
    value = getattr(import_module(_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value

__all__ = [
    "FleetControlService",
    "FleetQueryResult",
    "FleetStatus",
    "IncidentDiagnosticService",
    "OpsClient",
    "OpsError",
    "OpsOperation",
]
