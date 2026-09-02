from __future__ import annotations

import http.client
import json
import subprocess
import threading
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from tools.qwen_control import Controller, ControlServer, UNIT


class QwenControlTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(":memory:", "/test/systemctl", "/test/nvidia-smi")
        self.addCleanup(self.controller.db.close)

    def test_fixed_command_deduplicates_and_rejects_other_actions(self):
        request_id = str(uuid.uuid4())
        with patch("tools.qwen_control.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
            self.assertEqual(self.controller.control("start", request_id)[0], 202)
            self.assertEqual(self.controller.control("start", request_id)[0], 202)
            self.assertEqual(self.controller.control("stop", request_id)[0], 409)
            self.assertEqual(self.controller.control("restart", str(uuid.uuid4()))[0], 400)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], ["/test/systemctl", "--no-block", "start", UNIT])
            self.assertNotIn("shell", run.call_args.kwargs)

    def test_timeout_is_unknown_and_never_automatically_retried(self):
        request_id = str(uuid.uuid4())
        with patch("tools.qwen_control.subprocess.run", side_effect=subprocess.TimeoutExpired("systemctl", 3)) as run:
            self.assertEqual(self.controller.control("stop", request_id)[1]["outcome"], "unknown")
            self.assertEqual(self.controller.control("stop", request_id)[0], 409)
            run.assert_called_once()

    def test_status_reads_gpu_without_starting_service_and_is_cached(self):
        responses = [SimpleNamespace(returncode=0, stdout="inactive\n"), SimpleNamespace(returncode=0, stdout="0, 2048, 8192, 10\n")]
        with patch("tools.qwen_control.subprocess.run", side_effect=responses) as run:
            status = self.controller.status()
            self.assertEqual(status["state"], "stopped")
            self.assertEqual(status["gpu"][0]["used_mib"], 2048)
            self.assertEqual(status, self.controller.status())
            self.assertEqual(run.call_count, 2)
            self.assertNotIn("start", run.call_args_list[0].args[0])

    def test_http_token_allowlist_and_no_arbitrary_parameters(self):
        server = ControlServer(("127.0.0.1", 0), self.controller, "x" * 32, {"127.0.0.1"})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.assertFalse(server.verify_request(None, ("192.0.2.2", 123)))

        def post(path, payload, token="x" * 32):
            conn = http.client.HTTPConnection(*server.server_address, timeout=2)
            try:
                conn.request("POST", path, json.dumps(payload), {"Authorization": "Bearer " + token})
                response = conn.getresponse()
                response.read()
                return response.status
            finally:
                conn.close()

        with patch.object(self.controller, "control") as control:
            body = {"request_id": str(uuid.uuid4())}
            self.assertEqual(post("/start", body, "wrong"), 401)
            self.assertEqual(post("/exec", body), 404)
            self.assertEqual(post("/start", {**body, "unit": "other.service"}), 400)
            control.assert_not_called()
