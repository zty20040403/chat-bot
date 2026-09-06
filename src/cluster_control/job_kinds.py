from __future__ import annotations


WORKER_JOB_KINDS = frozenset(
    {"probe.http", "artifact.inspect", "document.verify", "media.inspect", "preview.static"}
)
