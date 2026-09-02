"""Restricted WSL service controller. No bot imports or request-supplied commands."""

from __future__ import annotations

import argparse
import csv
import hmac
import ipaddress
import json
import sqlite3
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


UNIT = "podman-qwen38.service"


class Controller:
    def __init__(self, database: str, systemctl: str, nvidia_smi: str) -> None:
        self.systemctl = systemctl
        self.nvidia_smi = nvidia_smi
        self.lock = threading.RLock()
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, action TEXT NOT NULL, outcome TEXT NOT NULL, created_at REAL NOT NULL)")
        self.db.commit()
        self.cached_at = 0.0
        self.cached: dict = {}

    def control(self, action: str, request_id: str) -> tuple[int, dict]:
        if action not in {"start", "stop"}:
            return 400, {"error": "invalid action"}
        try:
            request_id = str(uuid.UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            return 400, {"error": "request_id must be a UUID"}
        with self.lock:
            previous = self.db.execute("SELECT action, outcome FROM requests WHERE id = ?", (request_id,)).fetchone()
            if previous:
                if previous[0] != action:
                    return 409, {"error": "request_id already used for another action"}
                accepted = previous[1] == "accepted"
                return (202 if accepted else 409), {"request_id": request_id, "accepted": accepted, "outcome": previous[1]}
            # Commit before the side effect: a crash must not repeat a command.
            self.db.execute("INSERT INTO requests VALUES (?, ?, 'unknown', ?)", (request_id, action, time.time()))
            self.db.commit()
            try:
                result = subprocess.run(
                    [self.systemctl, "--no-block", action, UNIT],
                    capture_output=True, text=True, timeout=3, check=False,
                )
                outcome = "accepted" if result.returncode == 0 else "rejected"
            except (OSError, subprocess.TimeoutExpired):
                outcome = "unknown"
            self.db.execute("UPDATE requests SET outcome = ? WHERE id = ?", (outcome, request_id))
            self.db.commit()
            self.cached_at = 0
            accepted = outcome == "accepted"
            return (202 if accepted else 502), {"request_id": request_id, "accepted": accepted, "outcome": outcome}

    def status(self) -> dict:
        with self.lock:
            if time.monotonic() - self.cached_at < 3:
                return self.cached
            try:
                result = subprocess.run(
                    [self.systemctl, "show", UNIT, "--property=ActiveState", "--value"],
                    capture_output=True, text=True, timeout=1, check=False,
                )
                state = {"inactive": "stopped", "activating": "starting", "active": "running", "deactivating": "stopping", "failed": "failed"}.get(result.stdout.strip(), "unknown") if result.returncode == 0 else "unknown"
            except (OSError, subprocess.TimeoutExpired):
                state = "unknown"
            latest = self.db.execute("SELECT action, created_at FROM requests WHERE outcome = 'accepted' ORDER BY created_at DESC LIMIT 1").fetchone()
            if latest and time.time() - latest[1] < 10:
                if latest[0] == "start" and state == "stopped":
                    state = "starting"
                elif latest[0] == "stop" and state in {"starting", "running"}:
                    state = "stopping"
            gpu = None
            try:
                result = subprocess.run(
                    [self.nvidia_smi, "--query-gpu=index,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=1, check=False,
                )
                if result.returncode == 0:
                    gpu = [{"index": int(row[0]), "used_mib": float(row[1]), "total_mib": float(row[2])} for row in csv.reader(result.stdout.splitlines()) if len(row) == 4]
            except (OSError, subprocess.TimeoutExpired, ValueError):
                pass
            self.cached = {"state": state, "gpu": gpu, "checked_at": int(time.time())}
            self.cached_at = time.monotonic()
            return self.cached


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], controller: Controller, token: str, peers: set[str]) -> None:
        if len(token) < 32 or not peers:
            raise ValueError("A token of at least 32 characters and an explicit peer allowlist are required")
        self.controller = controller
        self.token = token
        self.peers = {ipaddress.ip_address(peer) for peer in peers}
        super().__init__(address, Handler)

    def verify_request(self, request, client_address) -> bool:
        return ipaddress.ip_address(client_address[0]) in self.peers


class Handler(BaseHTTPRequestHandler):
    server: ControlServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format: str, *args) -> None:
        # Paths, tokens and request bodies never enter the journal.
        pass

    def reply(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def authorized(self) -> bool:
        if not hmac.compare_digest(self.headers.get("Authorization", "").encode(), ("Bearer " + self.server.token).encode()):
            self.reply(401, {"error": "unauthorized"})
            return False
        return True

    def do_GET(self) -> None:
        if not self.authorized():
            return
        if self.path != "/status":
            self.reply(404, {"error": "not found"})
            return
        self.reply(200, self.server.controller.status())

    def do_POST(self) -> None:
        if not self.authorized():
            return
        if self.path not in {"/start", "/stop"}:
            self.reply(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 1024 or self.headers.get("Transfer-Encoding"):
                raise ValueError("invalid body size")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict) or set(payload) != {"request_id"}:
                raise ValueError("only request_id is accepted")
        except (ValueError, UnicodeError):
            self.reply(400, {"error": "expected JSON with only request_id (UUID)"})
            return
        status, body = self.server.controller.control(self.path[1:], payload["request_id"])
        self.reply(status, body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--allow-peer", action="append", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--systemctl", required=True)
    parser.add_argument("--nvidia-smi", required=True)
    args = parser.parse_args()
    token = Path(args.token_file).read_text().strip()
    controller = Controller(args.database, args.systemctl, args.nvidia_smi)
    server = ControlServer((args.host, args.port), controller, token, set(args.allow_peer))
    try:
        server.serve_forever()
    finally:
        server.server_close()
        controller.db.close()


if __name__ == "__main__":
    main()
