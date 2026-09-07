from __future__ import annotations

import json
import subprocess
import sys
import unittest
from unittest.mock import Mock

from src.cluster_control.config import _worker_identities
from src.cluster_control.execution_service import ClusterExecutionService


class WorkerRegistrationTests(unittest.TestCase):
    def test_worker_catalog_import_does_not_load_control_runtime(self) -> None:
        result = subprocess.run(
            [sys.executable, "-c", (
                "import sys; from src.cluster_control.job_kinds import WORKER_JOB_KINDS; "
                "assert len(WORKER_JOB_KINDS) == 5; "
                "assert 'src.bot_storage' not in sys.modules; "
                "assert 'src.cluster_control.diagnostics' not in sys.modules"
            )], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_worker_owner_alias_is_exact_and_defaults_empty(self) -> None:
        base = {"worker_id": "h310-worker", "host_id": "h310", "token_file": "/run/token"}
        self.assertEqual(_worker_identities(json.dumps([base]))[0]["owner_aliases"], [])
        for value in (["*"], ["qq:*"], ["admin:anyone"], "qq:123", [123]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _worker_identities(json.dumps([{**base, "owner_aliases": value}]))
        identity = _worker_identities(json.dumps([{**base, "owner_aliases": ["qq:123"]}]))[0]
        self.assertEqual(identity["owner_aliases"], ["qq:123"])

    def test_claim_uses_only_the_configured_workers_owner_aliases(self) -> None:
        store = Mock()
        service = ClusterExecutionService(
            store, inventory=({"host_id": "h310", "compute": True},),
            diagnostic_targets=(), worker_hosts={"h310-worker": "h310"},
            worker_owner_aliases={"h310-worker": ("qq:123",)},
        )
        service.claim_job("h310-worker")
        self.assertEqual(store.claim_job.call_args.kwargs["owner_aliases"], ("qq:123",))
        service.claim_job("other-worker")
        self.assertEqual(store.claim_job.call_args.kwargs["owner_aliases"], ())


if __name__ == "__main__":
    unittest.main()
