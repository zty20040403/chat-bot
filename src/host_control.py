"""Target-side preflight and replay protection; invoked by the existing Ops profile.

This module is deliberately standard-library-only. The Nix entry point runs it
with Python isolated mode and a baked-in configuration, without setuid or a daemon.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any


PROTOCOL = 1
MAX_REQUEST_BYTES = 256 * 1024
MAX_EXECUTABLE_BYTES = 256 * 1024 * 1024
OPERATION_ID = re.compile(r"op_[a-f0-9]{32}")
REPORT_PREFIX = "GAOJI_HOST_CONTROL_V1 "
VOLATILE_ENV = frozenset({"_", "PWD", "OLDPWD", "SHLVL", "INVOCATION_ID",
    "JOURNAL_STREAM", "SYSTEMD_EXEC_PID", "CREDENTIALS_DIRECTORY"})


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


class CheckError(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.details = code, details


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise CheckError(code, message)


def effective_environment(intent: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    base = dict(os.environ)
    base.setdefault("PATH", config["path"])
    extra = intent.get("env", {})
    require(isinstance(extra, dict) and len(extra) <= 128, "invalid_environment", "Invalid command environment")
    for key, value in extra.items():
        require(isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is not None
            and isinstance(value, str) and "\0" not in value and len(value) <= 16 * 1024,
            "invalid_environment", "Invalid environment entry")
    return {**base, **extra}


def fingerprint(program: str, path: str, *, suggest: bool = True) -> dict[str, Any]:
    require(bool(program) and "\0" not in program, "invalid_program", "Invalid command program")
    require("/" not in program or Path(program).is_absolute(),
        "relative_program", "Use a program name or an explicit absolute path")
    found = shutil.which(program, path=path)
    if found is None:
        candidate = shutil.which(Path(program).name, path=path) if suggest and "/" in program else None
        raise CheckError("program_unavailable", f"Program does not exist or is not executable: {program}",
            requested_program=program, suggested_program=candidate, command_started=False)
    resolved = Path(found).resolve(strict=True)
    fd = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size <= MAX_EXECUTABLE_BYTES,
            "invalid_executable", "Executable is not a bounded regular file")
        sha = hashlib.sha256()
        remaining = MAX_EXECUTABLE_BYTES + 1
        while chunk := stream.read(min(1024 * 1024, remaining)):
            remaining -= len(chunk)
            require(remaining > 0, "invalid_executable", "Executable grew beyond the size limit")
            sha.update(chunk)
        after = os.fstat(stream.fileno())
        require((before.st_size, before.st_mtime_ns, before.st_ino) ==
            (after.st_size, after.st_mtime_ns, after.st_ino), "program_changed", "Executable changed while checking")
    return {"requested": program, "launch_path": str(Path(found).absolute()),
        "resolved": str(resolved), "sha256": sha.hexdigest(),
        "device": before.st_dev, "inode": before.st_ino, "size": before.st_size,
        "mtime_ns": before.st_mtime_ns, "mode": stat.S_IMODE(before.st_mode)}


def boot_id(config: dict[str, Any]) -> str:
    value = Path(config["boot_id_file"]).read_text().strip()
    require(re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", value) is not None,
        "invalid_boot_id", "Target boot identity is unavailable")
    return value


def command_spec(intent: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if intent.get("action") == "reboot":
        require(set(intent) <= {"action", "host", "reason"}, "invalid_reboot", "Reboot accepts no model-generated command or environment")
        return {"argv": [config["systemctl"], "reboot"]}
    require(intent.get("action") == "exec", "invalid_action", "Unsupported host action")
    require(set(intent) <= {"action", "host", "command", "cwd", "env"}, "invalid_intent", "Unknown execution fields")
    command = intent.get("command")
    require(isinstance(command, dict) and set(command) in ({"argv"}, {"script"}),
        "invalid_command", "Provide exactly one argv array or script")
    if "argv" in command:
        argv = command["argv"]
        require(isinstance(argv, list) and 1 <= len(argv) <= 256 and all(
            isinstance(arg, str) and "\0" not in arg and len(arg) <= 16 * 1024 for arg in argv),
            "invalid_command", "Invalid argv array")
    else:
        script = command["script"]
        require(isinstance(script, str) and 0 < len(script.encode()) <= 64 * 1024 and "\0" not in script,
            "invalid_script", "Invalid script")
    return command


def preflight(intent: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    require(isinstance(intent, dict) and intent.get("host") == config["host_id"],
        "wrong_host", "Intent does not belong to this target")
    require(len(encoded(intent)) <= MAX_REQUEST_BYTES, "request_too_large", "Intent is too large")
    command = command_spec(intent, config)
    environment = effective_environment(intent, config)
    # Relative PATH entries could resolve differently once the child changes cwd.
    require(all(item and Path(item).is_absolute() for item in environment["PATH"].split(os.pathsep)),
        "invalid_path", "Execution PATH must contain only absolute, nonempty directories")
    cwd = intent.get("cwd") or config["default_cwd"]
    require(isinstance(cwd, str) and Path(cwd).is_absolute(), "invalid_cwd", "Working directory must be absolute")
    try:
        directory = Path(cwd).resolve(strict=True)
    except OSError as exc:
        raise CheckError("invalid_cwd", "Working directory is unavailable") from exc
    require(directory.is_dir() and os.access(directory, os.X_OK), "invalid_cwd", "Working directory is inaccessible")
    directory_stat = directory.stat()
    programs = []
    if "argv" in command:
        programs.append(fingerprint(command["argv"][0], environment["PATH"]))
        # An existing script can still fail ENOENT because its shebang interpreter is absent.
        with Path(programs[0]["resolved"]).open("rb") as stream:
            header = stream.readline(4096)
        if header.startswith(b"#!"):
            parts = header[2:].strip().split(None, 1)
            require(bool(parts) and b"\n" in header, "invalid_shebang", "Invalid or overlong shebang")
            interpreter = parts[0].decode("utf-8", errors="strict")
            require(Path(interpreter).is_absolute(), "invalid_shebang", "Shebang interpreter must be absolute")
            programs.append(fingerprint(interpreter, environment["PATH"], suggest=False))
            if Path(interpreter).name == "env":
                # Support the common env shebang forms without executing env during checking.
                words = shlex.split(parts[1].decode()) if len(parts) == 2 else []
                if words[:1] == ["-S"]:
                    words = words[1:]
                else:
                    require(len(words) == 1, "unsupported_shebang", "env shebang arguments require -S")
                require(bool(words) and not words[0].startswith("-") and "=" not in words[0],
                    "unsupported_shebang", "Use a direct interpreter or a simple env shebang")
                programs.append(fingerprint(words[0], environment["PATH"]))
    else:
        shell = fingerprint(config["shell"], config["path"], suggest=False)
        programs.append(shell)
        syntax = subprocess.run([shell["launch_path"], "--noprofile", "--norc", "-n"],
            input=command["script"].encode(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            env={"PATH": config["path"], "LC_ALL": "C"}, timeout=10, check=False)
        if syntax.returncode:
            raise CheckError("script_syntax", "Shell syntax check failed", stderr=syntax.stderr.decode(errors="replace")[:2000])
    evidence = {"protocol": PROTOCOL, "host": config["host_id"], "boot_id": boot_id(config),
        "intent_hash": digest(intent), "config_hash": digest(config),
        "uid": os.geteuid(), "gid": os.getegid(), "groups": sorted(os.getgroups()),
        "environment_hash": digest({key: value for key, value in environment.items() if key not in VOLATILE_ENV}),
        "programs": programs,
        "cwd": {"resolved": str(directory), "device": directory_stat.st_dev, "inode": directory_stat.st_ino}}
    return {"ok": True, "command_started": False, "evidence": evidence, "fingerprint": digest(evidence)}


def write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded(receipt))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def receipt_root(config: dict[str, Any]) -> Path:
    # Diagnostic profiles may write only their systemd StateDirectory. It survives
    # a job restart, unlike PrivateTmp, and is already scoped/owned by the executor.
    state = os.environ.get("STATE_DIRECTORY", "")
    if config.get("cgroup_file"):
        # Ops intentionally clears the inherited environment, including systemd's
        # STATE_DIRECTORY. Resolve its existing job directory from kernel identity.
        cgroup = Path(config["cgroup_file"]).read_text()
        match = re.search(r"(?:^|/)maxops-job-([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})\.service(?:/|$)", cgroup, re.MULTILINE)
        state = str(Path(config["job_state_root"]) / match[1]) if match else ""
        if state:
            require(Path(state).is_dir() and not Path(state).is_symlink(), "unsafe_receipt_directory", "Executor job directory is unavailable")
    root = Path(state) / "gaoji-host-control" if state else Path(config["receipt_directory"])
    require(root.is_absolute(), "unsafe_receipt_directory", "Receipt directory must be absolute")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(root.is_dir() and not root.is_symlink() and root.stat().st_uid == os.geteuid()
        and stat.S_IMODE(root.stat().st_mode) & 0o077 == 0,
        "unsafe_receipt_directory", "Receipt directory must be private and owned by the execution identity")
    return root


def validate_execution(intent: dict[str, Any], expected: dict[str, Any], operation_id: str) -> None:
    require(isinstance(operation_id, str) and OPERATION_ID.fullmatch(operation_id) is not None,
        "invalid_operation_id", "Invalid execution identity")
    require(isinstance(expected, dict) and isinstance(expected.get("evidence"), dict)
        and expected.get("fingerprint") == digest(expected["evidence"])
        and expected.get("ok") is True and expected["evidence"].get("protocol") == PROTOCOL
        and expected["evidence"].get("intent_hash") == digest(intent),
        "invalid_evidence", "Preflight evidence is missing or belongs to another intent")


def execute(intent: dict[str, Any], expected: dict[str, Any], operation_id: str, config: dict[str, Any]) -> int:
    validate_execution(intent, expected, operation_id)
    root = receipt_root(config)
    path = root / (operation_id + ".json")
    lock_fd = os.open(root / (operation_id + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        require(stat.S_ISREG(os.fstat(lock.fileno()).st_mode), "invalid_receipt", "Invalid receipt lock")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CheckError("outcome_unknown", "The same execution is still in progress", command_started=True) from exc
        if path.exists() or path.is_symlink():
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "invalid_receipt", "Invalid receipt file")
                try:
                    receipt = json.loads(stream.read(MAX_REQUEST_BYTES))
                except ValueError as exc:
                    raise CheckError("invalid_receipt", "Stored execution receipt is unreadable; inspect the existing job",
                        command_started=None) from exc
            if not isinstance(receipt, dict):
                raise CheckError("invalid_receipt", "Stored execution receipt is not an object", command_started=None)
            require(receipt.get("fingerprint") == expected["fingerprint"], "operation_conflict", "Execution identity was already used for another intent")
            if receipt.get("state") == "finished":
                code = receipt.get("exit_code")
                if type(code) is not int or not 0 <= code <= 255 or receipt.get("preflight") != expected:
                    raise CheckError("invalid_receipt", "Stored completion evidence is incomplete", command_started=None)
                print(REPORT_PREFIX + encoded({"operation_id": operation_id, "preflight": expected}).decode(), flush=True)
                return code
            raise CheckError("outcome_unknown", "A previous attempt may have started; do not execute it again", command_started=True)
        actual = preflight(intent, config)
        require(actual["fingerprint"] == expected["fingerprint"], "preflight_changed",
            "Target, program, working directory or execution environment changed; prepare again")
        receipt = {"fingerprint": expected["fingerprint"], "boot_id": actual["evidence"]["boot_id"],
            "operation_id": operation_id, "preflight": actual, "created_at": int(time.time()),
            "action": intent["action"], "state": "dispatching"}
        write_receipt(path, receipt)
        # Flush evidence before starting a command that might reboot this machine.
        print(REPORT_PREFIX + encoded({"operation_id": operation_id, "preflight": actual}).decode(), flush=True)
        command = command_spec(intent, config)
        # Preserve argv[0]/symlink semantics (multicall tools and Python venvs),
        # while the evidence above binds both the selected path and its target.
        program = actual["evidence"]["programs"][0]["launch_path"]
        argv = [program, *command["argv"][1:]] if "argv" in command else [program, "-c", command["script"]]
        try:
            result = subprocess.run(argv, cwd=actual["evidence"]["cwd"]["resolved"],
                env=effective_environment(intent, config), stdin=subprocess.DEVNULL, check=False)
        except OSError as exc:
            write_receipt(path, {**receipt, "state": "finished", "exit_code": 126, "error": type(exc).__name__})
            raise CheckError("spawn_failed", str(exc), command_started=False) from exc
        exit_code = result.returncode if result.returncode >= 0 else 128 - result.returncode
        write_receipt(path, {**receipt, "state": "finished", "exit_code": exit_code})
        return exit_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--request-json", required=True)
    args = parser.parse_args()
    try:
        require(len(args.request_json.encode()) <= MAX_REQUEST_BYTES, "request_too_large", "Request is too large")
        request = json.loads(args.request_json)
        config = json.loads(Path(args.config).read_bytes())
        require(isinstance(request, dict) and isinstance(config, dict), "invalid_request", "Expected request and configuration objects")
        if request.get("phase") == "run":
            require(set(request) == {"phase", "intent", "operation_id"}, "invalid_request", "Unknown run fields")
            expected = preflight(request["intent"], config)
            return execute(request["intent"], expected, request["operation_id"], config)
        if request["phase"] == "preflight":
            print(encoded(preflight(request["intent"], config)).decode())
            return 0
        require(request["phase"] == "execute", "invalid_phase", "Unsupported execution phase")
        return execute(request["intent"], request["preflight"], request["operation_id"], config)
    except (CheckError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        result = {"ok": False, "code": getattr(exc, "code", "preflight_failed"), "error": str(exc),
            "command_started": False, **getattr(exc, "details", {})}
        print(encoded(result).decode(), file=sys.stderr)
        return 75 if result["code"] == "outcome_unknown" else 126


if __name__ == "__main__":
    raise SystemExit(main())
