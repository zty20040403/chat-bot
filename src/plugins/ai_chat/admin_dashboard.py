from __future__ import annotations

import json
from html import escape
from pathlib import Path


ADMIN_FAVICON_SVG = r"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect x="2" y="2" width="60" height="60" rx="14" fill="#18181b"/>
  <path d="M32 18v-6" stroke="#fafafa" stroke-width="4" stroke-linecap="round"/>
  <circle cx="32" cy="10" r="4" fill="#22c55e"/>
  <rect x="11" y="19" width="42" height="32" rx="10" fill="#fafafa"/>
  <circle cx="25" cy="34" r="3.5" fill="#18181b"/>
  <circle cx="39" cy="34" r="3.5" fill="#18181b"/>
  <path d="M25 43h14" stroke="#18181b" stroke-width="3" stroke-linecap="round"/>
</svg>
"""


def dashboard_html(prefix: str, version: str, requires_token: bool) -> str:
    runtime = json.dumps(
        {
            "prefix": prefix,
            "apiBase": f"{prefix}/api/v1",
            "version": version,
            "requiresToken": requires_token,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="light">
  <meta name="theme-color" content="#18181b">
  <link rel="icon" type="image/svg+xml" href="{escape(prefix)}/favicon.svg?v={escape(version)}">
  <link rel="stylesheet" href="{escape(prefix)}/assets/app.css?v={escape(version)}">
  <title>Kennethbot Control</title>
</head>
<body>
  <div id="root"><div class="boot-state">Kennethbot 控制台正在加载</div></div>
  <script>window.__KENNETHBOT_ADMIN__={runtime};</script>
  <script type="module" src="{escape(prefix)}/assets/app.js?v={escape(version)}"></script>
</body>
</html>"""


def admin_asset_path(asset_path: str) -> Path | None:
    normalized = asset_path.strip().lstrip("/")
    if not normalized or ".." in Path(normalized).parts:
        return None
    module_dist = Path(__file__).with_name("admin_ui_dist")
    repository_dist = Path(__file__).resolve().parents[3] / "admin-ui" / "dist"
    for root in (module_dist, repository_dist):
        candidate = (root / normalized).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None
