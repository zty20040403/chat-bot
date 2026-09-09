import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.bot_security.cli import main


class DeploymentBootstrapTests(unittest.TestCase):
    def test_generated_password_is_private_and_existing_accounts_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "key"
            key.write_bytes(b"x" * 32)
            key.chmod(0o600)
            output = root / "initial-password"
            store = MagicMock()
            store.accounts.return_value = []
            with patch.dict(os.environ, AI_POSTGRES_DSN="isolated-test"), patch("src.bot_security.cli.PostgresDatabase"), patch("src.bot_security.cli.SecurityStore", return_value=store):
                args = ["bootstrap", "--secret-file", str(key), "--username", "kenneth", "--qq-id", "3526452465", "--generate-password-file", str(output)]
                self.assertEqual(main(args), 0)
                password = output.read_text().strip()
                self.assertGreaterEqual(len(password), 24)
                self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
                store.bootstrap.assert_called_once_with("kenneth", password, "3526452465")
                store.accounts.return_value = [{"username": "kenneth"}]
                self.assertEqual(main(args), 1)
                self.assertEqual(output.read_text().strip(), password)

    def test_napcat_only_updates_matching_reverse_client_and_rejects_ambiguous_targets(self):
        source = Path(__file__).resolve().parents[1] / "nix" / "napcat-auth.py"
        spec = importlib.util.spec_from_file_location("napcat_auth", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token = root / "token"
            token.write_text("fresh-credential-" * 4)
            config = root / "config.json"
            target = {"enable": True, "url": "ws://local/onebot/v11/ws", "token": "old", "name": "bot"}
            other = {"enable": True, "url": "ws://another/", "token": "unrelated"}
            config.write_text(json.dumps({"network": {"websocketClients": [target, other]}, "untouched": True}))
            module.configure(config, token, target["url"])
            updated = json.loads(config.read_text())
            self.assertTrue(updated["untouched"])
            self.assertEqual(updated["network"]["websocketClients"][1], other)
            self.assertEqual(updated["network"]["websocketClients"][0]["token"], token.read_text())
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            before = config.read_bytes()
            with self.assertRaises(ValueError):
                module.configure(config, token, "ws://missing/")
            self.assertEqual(config.read_bytes(), before)
