from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

import httpx

from tools.live_file_outbox_acceptance import (
    AcceptanceError, NapCatTransport, main, read_artifact, read_token, run_live, validate_url,
)


class LiveFileAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.name = "gaoji-acceptance-" + "a" * 32 + "-uploaded.txt"
        self.content = b"isolated test content"
        self.requests = []

    def tearDown(self):
        self.temp.cleanup()

    def transport(self, response=None):
        def handle(request):
            self.requests.append(request)
            return httpx.Response(200, json={"code": 0, "data": response})
        return NapCatTransport("http://127.0.0.1:6100", "private-credential", 123, 456,
            self.root, self.name, self.content, transport=httpx.MockTransport(handle))

    def params(self):
        return {"group_id": 456, "name": self.name,
                "file": "base64://" + base64.b64encode(self.content).decode()}

    def test_requires_loopback_and_no_credential_in_url(self):
        for value in ("http://example.com:6100", "http://127.0.0.1:6100@evil.test", "http://localhost:6100",
                      "http://127.0.0.1:6100/?token=secret", "http://user:pass@127.0.0.1:6100",
                      "http://127.0.0.1:6100/api", "file:///tmp/socket", "http://127.0.0.1:99999"):
            with self.subTest(value=value), self.assertRaises(AcceptanceError):
                validate_url(value)
        self.assertEqual(validate_url("http://[::1]:6100/"), "http://[::1]:6100")

    async def test_blocks_other_apis_groups_names_and_content_before_network(self):
        bot = self.transport()
        for action, params in (("delete_group_file", {}), ("set_group_ban", {}),
                               ("get_group_root_files", {"group_id": 999}),
                               ("upload_group_file", {**self.params(), "name": "private.pdf"}),
                               ("upload_group_file", {**self.params(), "file": "file:///etc/passwd"}),
                               ("upload_group_file", {**self.params(), "group_id": 999})):
            with self.subTest(action=action, params=params), self.assertRaises(AcceptanceError):
                await bot.call_api(action, **params)
        self.assertFalse(self.requests)
        self.assertFalse(list(self.root.iterdir()))

    async def test_upload_budget_survives_transport_recreation(self):
        bot = self.transport({"status": "ok", "retcode": 0, "data": {"file_id": "receipt"}})
        self.assertEqual(await bot.call_api("upload_group_file", **self.params()), {"file_id": "receipt"})
        with self.assertRaises(FileExistsError):
            await self.transport().call_api("upload_group_file", **self.params())
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].url.path, "/api/Debug/call")
        self.assertEqual(json.loads(self.requests[0].content)["action"], "upload_group_file")

    async def test_transport_failure_never_reuses_upload_reservation(self):
        bot = self.transport()
        def fail(request):
            self.requests.append(request)
            raise httpx.ReadTimeout("sensitive server diagnostic")
        bot.transport = httpx.MockTransport(fail)
        with self.assertRaises(httpx.ReadTimeout):
            await bot.call_api("upload_group_file", **self.params())
        with self.assertRaises(FileExistsError):
            await self.transport().call_api("upload_group_file", **self.params())
        self.assertEqual(len(self.requests), 1)

    async def test_debug_root_files_explicitly_supplies_schema_default(self):
        await self.transport({"files": []}).call_api("get_group_root_files", group_id=456)
        self.assertEqual(json.loads(self.requests[0].content), {
            "action": "get_group_root_files", "params": {"group_id": 456, "file_count": 50}})

    async def test_login_stops_at_second_factor_or_wrong_account(self):
        for response in ({"require2FA": True}, {"Credential": "credential"}):
            bot = self.transport(response)
            with self.assertRaises(AcceptanceError):
                await bot.login("not-a-real-token")
        self.assertTrue(all(json.loads(r.content).get("action") != "upload_group_file" for r in self.requests))
        self.assertNotIn("Authorization", self.requests[0].headers)
        self.assertEqual(json.loads(self.requests[0].content),
                         {"hash": hashlib.sha256(b"not-a-real-token.napcat").hexdigest()})

    async def test_valid_login_checks_expected_account_without_upload(self):
        def handler(request):
            self.requests.append(request)
            payload = {"Credential": "test-session"} if request.url.path == "/api/auth/login" else {
                "status": "ok", "retcode": 0, "data": {"user_id": 123}}
            return httpx.Response(200, json={"code": 0, "data": payload})
        bot = self.transport()
        bot.transport = httpx.MockTransport(handler)
        await bot.login("not-a-real-token")
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[1].headers["Authorization"], "Bearer test-session")

    async def test_server_errors_and_redirects_do_not_leak_response_or_forward_credentials(self):
        for response in (httpx.Response(302, headers={"Location": "https://evil.test"}),
                         httpx.Response(200, json={"code": -1, "message": "private secret"}),
                         httpx.Response(200, json={"code": 0, "data": {"status": "failed", "retcode": 1200, "wording": "secret"}})):
            bot = self.transport()
            bot.transport = httpx.MockTransport(lambda request: response)
            with self.assertRaises(AcceptanceError) as caught:
                await bot.call_api("get_group_root_files", group_id=456)
            self.assertNotIn("secret", str(caught.exception))

    def test_artifact_bytes_are_checked_after_restart(self):
        (self.root / self.name).write_bytes(self.content)
        digest = hashlib.sha256(self.content).hexdigest()
        self.assertEqual(read_artifact(self.root, self.name, digest), self.content)
        (self.root / self.name).write_bytes(b"changed")
        with self.assertRaises(AcceptanceError):
            read_artifact(self.root, self.name, digest)

    def test_token_can_be_read_from_existing_config_without_copying_it(self):
        path = self.root / "webui.json"
        path.write_text(json.dumps({"token": "test-only-token", "unrelated": "ignored"}))
        self.assertEqual(read_token(path), "test-only-token")
        path.write_text(json.dumps({"token": None}))
        with self.assertRaises(AcceptanceError):
            read_token(path)

    def test_default_command_only_prints_plan(self):
        with patch("tools.live_file_outbox_acceptance.run_live") as run, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--webui-url", "http://127.0.0.1:6100", "--bot-id", "123",
                                   "--group-id", "456", "--requester-id", "789"]), 0)
        run.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["max_uploads"], 4)

    async def test_rejects_bot_database_before_files_or_network(self):
        with patch.dict("os.environ", TEST_POSTGRES_DSN="dbname=qq_bot", AI_POSTGRES_DSN="", clear=False):
            with self.assertRaisesRegex(AcceptanceError, "dedicated database"):
                await run_live(None)

    async def test_failed_preflight_records_phase_without_database_or_upload(self):
        token = self.root / "token"
        token.write_text("test-secret")
        args = SimpleNamespace(webui_url="http://127.0.0.1:6100", bot_id=123, group_id=456,
            requester_id=789, output_dir=self.root / "output", token_file=token)
        with patch.dict("os.environ", TEST_POSTGRES_DSN="dbname=gaoji_acceptance", AI_POSTGRES_DSN=""), \
                patch.object(NapCatTransport, "login", new_callable=AsyncMock), \
                patch.object(NapCatTransport, "call_api", side_effect=[{"online": True}, AcceptanceError("QQ unavailable")]) as api, \
                patch("psycopg.connect") as connect:
            with self.assertRaises(AcceptanceError):
                await run_live(args)
        connect.assert_not_called()
        self.assertEqual(api.await_args_list, [call("get_status"), call("get_group_root_files", group_id=456)])
        raw = (args.output_dir / "result.json").read_text()
        report = json.loads(raw)
        self.assertEqual(report["phase"], "preflight_group_files")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["schema_cleanup"], "not_created")
        self.assertFalse(report["cases"])
        self.assertNotIn("test-secret", raw)

    async def test_offline_account_stops_before_group_query_database_and_upload(self):
        token = self.root / "token"
        token.write_text("test-secret")
        args = SimpleNamespace(webui_url="http://127.0.0.1:6100", bot_id=123, group_id=456,
            requester_id=789, output_dir=self.root / "output", token_file=token)
        with patch.dict("os.environ", TEST_POSTGRES_DSN="dbname=gaoji_acceptance", AI_POSTGRES_DSN=""), \
                patch.object(NapCatTransport, "login", new_callable=AsyncMock), \
                patch.object(NapCatTransport, "call_api", return_value={"online": False, "good": True}) as api, \
                patch("psycopg.connect") as connect:
            with self.assertRaisesRegex(AcceptanceError, "offline"):
                await run_live(args)
        connect.assert_not_called()
        api.assert_awaited_once_with("get_status")
        report = json.loads((args.output_dir / "result.json").read_text())
        self.assertEqual(report["phase"], "preflight_online")
        self.assertEqual(report["schema_cleanup"], "not_created")


if __name__ == "__main__":
    unittest.main()
