from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit


LOG_PAGE_BYTES = 65536
_ERROR_DETAIL_CHARS = 900  # ExternalCalls persists at most 1000 error characters.
_REDACTED = "[REDACTED]"
_CREDENTIAL_KEY = re.compile(
    r"password|passwd|secret|token|api[_ -]?key|authorization|cookie|credential|signature|private[_ -]?key",
    re.IGNORECASE,
)


def _private_values(*values: Any, sensitive_only: bool = False) -> set[str]:
    pending = [(value, not sensitive_only) for value in values]
    private: set[str] = set()
    for _ in range(4096):
        if not pending:
            return private
        value, include = pending.pop()
        if isinstance(value, dict):
            pending.extend((item, include or bool(_CREDENTIAL_KEY.search(key)))
                           for key, item in value.items())
        elif isinstance(value, (list, tuple)):
            pending.extend((item, include) for item in value)
        elif include and isinstance(value, str) and value:
            private.update((value, json.dumps(value, ensure_ascii=True)[1:-1],
                            repr(value)[1:-1], quote(value, safe="")))
        elif include and type(value) in (int, float):
            private.add(str(value))
    raise ValueError("Too many values to redact safely")


def _redact_text(text: str, private: set[str], *, redact_urls: bool = True) -> str:
    # Redact before shortening: otherwise a partial credential could survive.
    for value in sorted(private, key=len, reverse=True):
        text = text.replace(value, _REDACTED)
    text = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
                  _REDACTED, text, flags=re.DOTALL)
    text = re.sub(r"(?im)\b(?:authorization|proxy-authorization|(?:set-)?cookie)\s*:\s*[^\r\n]+",
                  _REDACTED, text)
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+[^\s,;\"'<>]+", _REDACTED, text)
    text = re.sub(
        r"(?i)(\b[\w-]*(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|credential|signature)"
        r"[\w-]*[\"']?\s*[:=]\s*)(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)",
        lambda match: match[1] + _REDACTED, text,
    )
    def url(match: re.Match[str]) -> str:
        if not redact_urls:
            try:
                parsed = urlsplit(match[0])
                if not parsed.username and not parsed.password and not any(
                    _CREDENTIAL_KEY.search(key) or key.lower() in {"key", "sig", "auth"}
                    for key, _ in parse_qsl(parsed.query)
                ):
                    return match[0]
            except ValueError:
                pass
        return _REDACTED

    text = re.sub(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>]+", url, text)
    text = re.sub(r"\b(?:sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]+", _REDACTED, text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]", "", text)


def request_error_message(status: int, raw: bytes, *, payload: Any, secrets: tuple[str, ...]) -> str:
    message = f"Fleet control returned HTTP {status}"
    try:
        response = json.loads(raw)
        detail = response.get("detail") if isinstance(response, dict) else None
        entries = detail if isinstance(detail, list) else [detail]
        # Validation metadata can also echo secrets not present in the request.
        private = _private_values(payload, secrets, [
            {key: entry[key] for key in ("input", "ctx") if key in entry}
            for entry in entries if isinstance(entry, dict)
        ])
    except (ValueError, UnicodeError, RecursionError):
        return message

    truncated = False

    def text(value: str, limit: int) -> str:
        nonlocal truncated
        if len(value) > 8192:
            truncated = True
            return "[TRUNCATED]"
        clean = " ".join(_redact_text(value, private).split())
        if len(clean) > limit:
            truncated = True
            return clean[:limit] + "..."
        return clean

    def project(entry: Any) -> Any:
        nonlocal truncated
        if isinstance(entry, str):
            return text(entry, 400)
        if not isinstance(entry, dict):
            return None
        clean: dict[str, Any] = {}
        loc = entry.get("loc")
        if isinstance(loc, list):
            truncated |= len(loc) > 4
            clean["loc"] = [text(part, 48) if isinstance(part, str) else part
                            for part in loc[:4] if isinstance(part, str) or type(part) is int]
        for key in ("msg", "type", "message", "code"):
            if isinstance(entry.get(key), str):
                clean[key] = text(entry[key], 200 if key in {"msg", "message"} else 64)
        return clean or None

    def encode(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    clean_entries = []
    for entry in entries[:5]:
        clean = project(entry)
        if clean:
            clean_entries.append(clean)
    truncated |= len(entries) > 5
    summary = {"detail": clean_entries if isinstance(detail, list) else next(iter(clean_entries), None)}
    if summary["detail"] is None or summary["detail"] == []:
        return message
    while len(encode({**summary, "truncated": True})) > _ERROR_DETAIL_CHARS:
        truncated = True
        if isinstance(summary["detail"], list) and len(summary["detail"]) > 1:
            summary["detail"].pop()
        else:
            summary["detail"] = "[TRUNCATED]"
    if truncated:
        summary["truncated"] = True
    return message + ": " + encode(summary)


def project_jobs_logs(response: dict[str, Any], *, payload: Any, secrets: tuple[str, ...]) -> dict[str, Any]:
    if (not isinstance(payload, dict) or payload.get("operation") != "jobs.logs"
            or response.get("ok") is not True
            or response.get("operation", "jobs.logs") != "jobs.logs"):
        return response
    result = response.get("result")
    if not isinstance(result, dict) or result.get("encoding") != "base64":
        return response

    decoded: dict[str, str] = {}
    size = 0
    private = _private_values(secrets) | _private_values(payload, sensitive_only=True)
    for stream in ("stdout", "stderr"):
        encoded = result.get(stream + "_base64")
        if not isinstance(encoded, str):
            raise ValueError(f"jobs.logs {stream}_base64 must be a base64 string")
        if len(encoded) > 4 * ((LOG_PAGE_BYTES + 2) // 3):
            raise ValueError("jobs.logs page exceeds 65536 bytes; request a smaller page")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError(f"jobs.logs {stream}_base64 contains invalid base64") from None
        size += len(content)
        if size > LOG_PAGE_BYTES:
            raise ValueError("jobs.logs page exceeds 65536 bytes; request a smaller page")
        decoded[stream] = _redact_text(content.decode("utf-8", errors="replace"), private, redact_urls=False)

    projected = {key: value for key, value in result.items()
                 if key not in {"stdout_base64", "stderr_base64"}}
    projected.update(decoded, encoding="utf-8")
    params = payload.get("params")
    limit = params.get("limit", LOG_PAGE_BYTES) if isinstance(params, dict) else LOG_PAGE_BYTES
    if type(limit) is not int or not 1 <= limit <= LOG_PAGE_BYTES:
        limit = LOG_PAGE_BYTES
    # Upstream complete describes the job, not whether this page exhausted its output.
    projected["more_possible"] = bool(size >= limit or not result.get("complete")
                                      or (size and result.get("truncated")))
    projected["pagination_hint"] = (
        "Continue jobs.logs using next_stdout_offset as stdout_offset and "
        "next_stderr_offset as stderr_offset; request at most 65536 bytes per page. "
        "Offsets count original bytes, not displayed text. complete means the job "
        "finished, not that all log pages have been read. A full page may have a successor."
        if projected["more_possible"]
        else "Log page complete; no further page indicated."
    )
    return {**response, "result": projected}
