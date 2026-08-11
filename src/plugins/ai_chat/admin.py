from __future__ import annotations

import hmac
import re
import time
from dataclasses import asdict, dataclass
from html import escape
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse


@dataclass(frozen=True)
class AdminServices:
    version: str
    started_at: int
    delivery_store: Any = None
    usage_store: Any = None
    running_tasks: Any = None
    bridge_router: Any = None
    bridge_state: Any = None
    browser_manager: Any = None
    background_tasks: Any = None
    model_catalog: Any = None
    model_preferences: Any = None
    message_ledger: Any = None
    settings: Any = None
    sandbox_manager: Any = None
    sticker_inventory: Any = None


def register_admin(
    app: Any,
    services: AdminServices,
    *,
    path: str = "/bot-admin",
    token: str = "",
) -> None:
    prefix = "/" + path.strip("/")
    router = APIRouter(prefix=prefix)
    expected_token = token.strip()

    def authorize(authorization: Optional[str] = Header(default=None)) -> None:
        if not expected_token:
            return
        supplied = ""
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        if not hmac.compare_digest(supplied, expected_token):
            raise HTTPException(status_code=401, detail="invalid admin token")

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard() -> str:
        return _dashboard_html(prefix, services.version, bool(expected_token))

    @router.get("/api/overview", dependencies=[])
    async def overview(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        deliveries = (
            services.delivery_store.stats()
            if services.delivery_store is not None
            else {}
        )
        tasks = (
            len(services.running_tasks.list_all())
            if services.running_tasks is not None
            else 0
        )
        return {
            "version": services.version,
            "uptime_seconds": max(int(time.time()) - services.started_at, 0),
            "deliveries": deliveries,
            "running_tasks": tasks,
            "background_tasks": (
                {
                    "running": list(services.background_tasks.running()),
                    "failures": services.background_tasks.failures(),
                }
                if services.background_tasks is not None
                else {"running": [], "failures": {}}
            ),
            "models": _model_overview(services.model_catalog),
            "bridges": (
                services.bridge_state.stats()
                if services.bridge_state is not None
                else {}
            ),
            "browser": (
                services.browser_manager.stats()
                if services.browser_manager is not None
                else {"active_sessions": 0, "persistent_profiles": 0}
            ),
        }

    @router.get("/api/platforms")
    async def platforms(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        return {
            "mirrors": (
                services.bridge_router.describe()
                if services.bridge_router is not None
                else []
            ),
            "evidence": (
                services.bridge_state.stats()
                if services.bridge_state is not None
                else {}
            ),
        }

    @router.get("/api/deliveries")
    async def deliveries(
        limit: int = Query(default=100, ge=1, le=500),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.delivery_store is None:
            return {"items": [], "configured": False}
        items = []
        for item in services.delivery_store.recent(limit=limit):
            payload = asdict(item)
            payload.pop("body", None)
            payload["handle"] = item.handle
            items.append(payload)
        return {"items": items, "configured": True}

    @router.post("/api/deliveries/{delivery_id}/retry")
    async def retry_delivery(
        delivery_id: int,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        changed = bool(
            services.delivery_store is not None
            and services.delivery_store.requeue(delivery_id)
        )
        if not changed:
            raise HTTPException(
                status_code=409,
                detail="delivery cannot be retried from its current state",
            )
        return {"ok": True, "delivery_id": delivery_id}

    @router.post("/api/deliveries/{delivery_id}/cancel")
    async def cancel_delivery(
        delivery_id: int,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        changed = bool(
            services.delivery_store is not None
            and services.delivery_store.cancel(delivery_id)
        )
        if not changed:
            raise HTTPException(
                status_code=409,
                detail="delivery cannot be cancelled from its current state",
            )
        return {"ok": True, "delivery_id": delivery_id}

    @router.get("/api/usage")
    async def usage(
        days: int = Query(default=14, ge=1, le=365),
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.usage_store is None:
            return {"items": [], "configured": False}
        return {
            "items": services.usage_store.daily_summary(days=days),
            "configured": True,
        }

    @router.get("/api/tasks")
    async def tasks(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.running_tasks is None:
            return {"items": []}
        return {
            "items": [
                {
                    "task_id": item.task_id,
                    "conversation_id": item.conversation_id,
                    "group_id": item.group_id,
                    "user_id": item.user_id,
                    "message_id": item.message_id,
                    "summary": item.summary,
                    "elapsed_seconds": item.elapsed_seconds,
                }
                for item in services.running_tasks.list_all()
            ]
        }

    @router.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(
        task_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        changed = bool(
            services.running_tasks is not None
            and services.running_tasks.cancel_any(task_id) is not None
        )
        if not changed:
            raise HTTPException(status_code=404, detail="task not found")
        return {"ok": True, "task_id": task_id}

    @router.get("/api/sandboxes")
    async def sandboxes(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if services.sandbox_manager is None:
            return {
                "items": [],
                "active_commands": 0,
                "configured": False,
                "available": False,
            }
        try:
            snapshot = await services.sandbox_manager.admin_snapshot()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "items": [],
                "active_commands": 0,
                "configured": True,
                "available": False,
                "error": str(exc)[:500],
            }

        tasks_by_conversation: dict[str, list[dict[str, object]]] = {}
        if services.running_tasks is not None:
            for item in services.running_tasks.list_all():
                tasks_by_conversation.setdefault(
                    str(item.conversation_id), []
                ).append(
                    {
                        "task_id": item.task_id,
                        "summary": item.summary,
                        "elapsed_seconds": item.elapsed_seconds,
                    }
                )
        for raw_item in snapshot.get("items", []):
            if not isinstance(raw_item, dict):
                continue
            owner = str(raw_item.get("owner", ""))
            raw_item["agent_tasks"] = tasks_by_conversation.get(owner, [])
        return {
            **snapshot,
            "configured": True,
            "available": True,
        }

    @router.get("/api/stickers")
    async def stickers(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        if not callable(services.sticker_inventory):
            return {
                "counts": {},
                "items": [],
                "configured": False,
                "available": False,
            }
        try:
            inventory = services.sticker_inventory()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {
                "counts": {},
                "items": [],
                "configured": True,
                "available": False,
                "error": str(exc)[:500],
            }
        return {
            **inventory,
            "configured": True,
            "available": True,
        }

    @router.get("/api/group-models")
    async def group_models(
        authorization: Optional[str] = Header(default=None),
    ) -> dict[str, object]:
        authorize(authorization)
        return _group_model_overview(
            services.model_catalog,
            services.model_preferences,
            services.settings,
            services.message_ledger,
        )

    app.include_router(router)


def _model_overview(catalog: Any) -> dict[str, object]:
    if catalog is None:
        return {"default": "", "profiles": []}
    return {
        "default": str(catalog.default_name),
        "profiles": [
            {
                "name": profile.name,
                "provider": profile.provider,
                "protocol": profile.protocol,
                "model": profile.model,
                "configured": profile.configured,
                "capabilities": {
                    "tools": profile.capabilities.tools,
                    "streaming": profile.capabilities.streaming,
                    "json_mode": profile.capabilities.json_mode,
                    "vision": profile.capabilities.vision,
                },
            }
            for profile in catalog.profiles
        ],
    }


_GROUP_CONVERSATION_PATTERN = re.compile(r"^group:(\d+):user:(\d+)$")


def _group_model_overview(
    catalog: Any,
    preferences: Any,
    settings: Any,
    message_ledger: Any,
) -> dict[str, object]:
    if catalog is None:
        return {"default": {}, "items": [], "configured": False}

    default_profile = catalog.default
    group_ids: set[int] = set()
    if settings is not None:
        group_ids.update(getattr(settings, "enabled_groups", set()) or set())
        group_ids.update(getattr(settings, "disabled_groups", set()) or set())

    if message_ledger is not None:
        try:
            for scope in message_ledger.list_scopes():
                if scope.kind != "group" or scope.platform != "onebot-v11":
                    continue
                try:
                    group_ids.add(int(scope.native_conversation_id))
                except (TypeError, ValueError):
                    continue
        except (OSError, RuntimeError, TypeError, ValueError):
            pass

    overrides_by_group: dict[int, list[dict[str, object]]] = {}
    preference_items = preferences.items() if preferences is not None else []
    for conversation_id, stored_preference in preference_items:
        match = _GROUP_CONVERSATION_PATTERN.fullmatch(str(conversation_id))
        if match is None:
            continue
        group_id = int(match.group(1))
        user_id = int(match.group(2))
        group_ids.add(group_id)
        resolved = catalog.resolve_preference(str(stored_preference))
        direct = catalog.try_resolve(str(stored_preference))
        recognized = direct is not None or any(
            profile.model == str(stored_preference)
            for profile in catalog.profiles
        )
        overrides_by_group.setdefault(group_id, []).append(
            {
                "user_id": user_id,
                "stored_preference": str(stored_preference),
                "profile": resolved.name,
                "provider": resolved.provider,
                "model": resolved.model,
                "recognized": recognized,
            }
        )

    rows: list[dict[str, object]] = []
    for group_id in sorted(group_ids):
        overrides = sorted(
            overrides_by_group.get(group_id, []),
            key=lambda item: int(item["user_id"]),
        )
        enabled = (
            bool(settings.is_group_enabled(group_id))
            if settings is not None
            else True
        )
        rows.append(
            {
                "group_id": group_id,
                "enabled": enabled,
                "default_profile": default_profile.name,
                "default_provider": default_profile.provider,
                "default_model": default_profile.model,
                "overrides": overrides,
            }
        )

    return {
        "default": {
            "profile": default_profile.name,
            "provider": default_profile.provider,
            "model": default_profile.model,
        },
        "items": rows,
        "configured": True,
    }


def _dashboard_html(prefix: str, version: str, requires_token: bool) -> str:
    safe_prefix = escape(prefix, quote=True)
    safe_version = escape(version)
    token_state = "true" if requires_token else "false"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>QQ Bot 管理台</title>
  <style>
    :root {{ color-scheme: light; --ink:#182026; --muted:#66727a;
      --line:#d8dee2; --paper:#f7f9fa; --white:#fff; --green:#16794f;
      --amber:#9a5b00; --red:#ad2e24; --blue:#1668a8; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; color:var(--ink); background:var(--paper);
      font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"PingFang SC",sans-serif;
      letter-spacing:0; }}
    header {{ background:var(--white); border-bottom:1px solid var(--line); }}
    .bar {{ max-width:1180px; margin:auto; min-height:64px; padding:12px 20px;
      display:flex; align-items:center; justify-content:space-between; gap:16px; }}
    h1 {{ font-size:19px; margin:0; font-weight:650; }}
    .version {{ color:var(--muted); font-variant-numeric:tabular-nums; }}
    nav {{ background:var(--white); border-bottom:1px solid var(--line); }}
    .tabs {{ max-width:1180px; margin:auto; padding:0 20px; display:flex;
      gap:24px; overflow-x:auto; }}
    .tab {{ border:0; border-bottom:2px solid transparent; background:none;
      padding:12px 0 10px; color:var(--muted); cursor:pointer; white-space:nowrap; }}
    .tab.active {{ color:var(--ink); border-color:var(--blue); }}
    main {{ max-width:1180px; margin:auto; padding:20px; }}
    .toolbar {{ display:flex; align-items:end; gap:10px; margin-bottom:16px;
      flex-wrap:wrap; }}
    label {{ display:grid; gap:5px; color:var(--muted); font-size:12px; }}
    input {{ width:min(360px,80vw); height:36px; border:1px solid var(--line);
      border-radius:4px; padding:0 10px; background:var(--white); }}
    button.action {{ height:36px; border:1px solid var(--line); border-radius:4px;
      padding:0 12px; background:var(--white); color:var(--ink); cursor:pointer; }}
    button.action:hover {{ border-color:#9aa7af; }}
    .metrics {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr));
      border:1px solid var(--line); gap:1px; background:var(--line); margin-bottom:18px; }}
    .metric {{ padding:16px; min-width:0; background:var(--white); }}
    .metric b {{ display:block; font-size:22px; font-variant-numeric:tabular-nums;
      overflow-wrap:anywhere; }}
    .metric span {{ color:var(--muted); }}
    section[hidden] {{ display:none; }}
    .table-wrap {{ overflow:auto; border:1px solid var(--line); background:var(--white); }}
    table {{ width:100%; border-collapse:collapse; min-width:760px; }}
    th,td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line);
      vertical-align:top; }}
    th {{ background:#f1f4f5; color:var(--muted); font-weight:600; position:sticky; top:0; }}
    tbody tr:last-child td {{ border-bottom:0; }}
    code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }}
    .status {{ font-weight:650; }} .committed {{ color:var(--green); }}
    .ambiguous {{ color:var(--amber); }} .failed {{ color:var(--red); }}
    .pending,.sending {{ color:var(--blue); }}
    .empty {{ padding:24px; color:var(--muted); text-align:center; }}
    .error {{ color:var(--red); min-height:21px; }}
    .subtle {{ color:var(--muted); font-size:12px; }}
    .stack {{ display:grid; gap:4px; min-width:180px; }}
    .command {{ max-width:360px; white-space:pre-wrap; overflow-wrap:anywhere; }}
    details summary {{ color:var(--blue); cursor:pointer; white-space:nowrap; }}
    .file-list {{ display:grid; gap:3px; margin-top:6px; max-width:320px; }}
    .file-list code {{ overflow-wrap:anywhere; }}
    @media (max-width:980px) {{ .metrics {{ grid-template-columns:repeat(3,1fr); }} }}
    @media (max-width:620px) {{ .metrics {{ grid-template-columns:repeat(2,1fr); }}
      main {{ padding:14px; }} .bar,.tabs {{ padding-left:14px; padding-right:14px; }} }}
  </style>
</head>
<body>
  <header><div class="bar"><h1>QQ Bot 管理台</h1><span class="version">v{safe_version}</span></div></header>
  <nav><div class="tabs">
    <button class="tab active" data-view="overview">概览</button>
    <button class="tab" data-view="deliveries">投递</button>
    <button class="tab" data-view="usage">用量</button>
    <button class="tab" data-view="tasks">任务</button>
    <button class="tab" data-view="sandboxes">沙盒</button>
    <button class="tab" data-view="stickers">表情包</button>
    <button class="tab" data-view="group-models">群模型</button>
    <button class="tab" data-view="models">模型配置</button>
  </div></nav>
  <main>
    <div class="toolbar">
      <label id="token-wrap">管理 Token<input id="token" type="password" autocomplete="off"></label>
      <button class="action" id="refresh">刷新</button>
      <span class="error" id="error"></span>
    </div>
    <section id="overview">
      <div class="metrics" id="metrics"></div>
      <div class="table-wrap"><table><thead><tr><th>状态</th><th>数量</th></tr></thead><tbody id="status-body"></tbody></table></div>
    </section>
    <section id="deliveries" hidden><div class="table-wrap"><table><thead><tr>
      <th>ID</th><th>目标</th><th>状态</th><th>尝试</th><th>更新时间</th><th>操作</th>
    </tr></thead><tbody id="delivery-body"></tbody></table></div></section>
    <section id="usage" hidden><div class="table-wrap"><table><thead><tr>
      <th>日期</th><th>Scope</th><th>来源</th><th>调用</th><th>输入</th><th>输出</th>
    </tr></thead><tbody id="usage-body"></tbody></table></div></section>
    <section id="tasks" hidden><div class="table-wrap"><table><thead><tr>
      <th>任务</th><th>会话</th><th>摘要</th><th>耗时</th><th>操作</th>
    </tr></thead><tbody id="task-body"></tbody></table></div></section>
    <section id="sandboxes" hidden><div class="table-wrap"><table><thead><tr>
      <th>沙盒</th><th>所属会话</th><th>状态</th><th>当前或最近任务</th><th>资源</th><th>工作区</th>
    </tr></thead><tbody id="sandbox-body"></tbody></table></div></section>
    <section id="stickers" hidden><div class="table-wrap"><table><thead><tr>
      <th>来源</th><th>类型</th><th>名称</th><th>保存的引用</th><th>大小</th>
    </tr></thead><tbody id="sticker-body"></tbody></table></div></section>
    <section id="group-models" hidden><div class="table-wrap"><table><thead><tr>
      <th>群号</th><th>启用状态</th><th>默认配置</th><th>默认底层模型</th><th>群友单独选择</th>
    </tr></thead><tbody id="group-model-body"></tbody></table></div></section>
    <section id="models" hidden><div class="table-wrap"><table><thead><tr>
      <th>Profile</th><th>Provider</th><th>协议</th><th>模型</th><th>能力</th><th>状态</th>
    </tr></thead><tbody id="model-body"></tbody></table></div></section>
  </main>
  <script>
    const prefix={safe_prefix!r}, requiresToken={token_state};
    const tokenInput=document.querySelector('#token');
    tokenInput.value=localStorage.getItem('qqbot-admin-token')||'';
    document.querySelector('#token-wrap').hidden=!requiresToken;
    const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    const headers=()=>tokenInput.value?{{Authorization:`Bearer ${{tokenInput.value}}`}}:{{}};
    async function api(path,opts={{}}){{
      localStorage.setItem('qqbot-admin-token',tokenInput.value);
      const response=await fetch(prefix+'/api'+path,{{...opts,headers:{{...headers(),...(opts.headers||{{}})}}}});
      if(!response.ok) throw new Error((await response.json().catch(()=>({{}}))).detail||`HTTP ${{response.status}}`);
      return response.json();
    }}
    const fmt=t=>t?new Date(t*1000).toLocaleString():'-';
    const fmtBytes=value=>{{
      const n=Number(value); if(!Number.isFinite(n)||n<0)return '-';
      const units=['B','KiB','MiB','GiB','TiB']; let size=n,index=0;
      while(size>=1024&&index<units.length-1){{size/=1024;index++;}}
      return `${{index===0?Math.round(size):size.toFixed(size>=10?1:2)}} ${{units[index]}}`;
    }};
    const ownerHtml=x=>{{
      const owner=String(x.owner||'');
      const matched=/^group:(\\d+):user:(\\d+)$/.exec(owner);
      if(matched)return `<div class="stack"><span>群 <code>${{esc(matched[1])}}</code></span><span>QQ <code>${{esc(matched[2])}}</code></span></div>`;
      return `<code>${{esc(owner||x.owner_hash||'-')}}</code>`;
    }};
    const activityHtml=x=>{{
      const active=x.activities||[],agent=x.agent_tasks||[];
      if(active.length){{
        const commands=active.map(a=>`<div><span class="status sending">执行中 · ${{esc(a.elapsed_seconds)}} 秒</span><div class="command"><code>${{esc(a.command)}}</code></div></div>`).join('');
        const summary=agent.length?`<div class="subtle">群聊任务：${{esc(agent[0].summary)}}</div>`:'';
        return `<div class="stack">${{commands}}${{summary}}</div>`;
      }}
      if(agent.length)return `<div class="stack"><span class="status pending">模型处理中 · ${{esc(agent[0].elapsed_seconds)}} 秒</span><span>${{esc(agent[0].summary)}}</span></div>`;
      const last=x.last_activity;
      if(last){{const ok=last.status==='completed';return `<div class="stack"><span class="status ${{ok?'committed':'failed'}}">最近：${{esc(last.status)}} · ${{esc(last.elapsed_seconds)}} 秒</span><div class="command"><code>${{esc(last.command)}}</code></div><span class="subtle">结束于 ${{fmt(last.finished_at)}}</span></div>`;}}
      return '<span class="subtle">等待任务</span>';
    }};
    const workspaceHtml=x=>{{
      if(x.workspace_error)return `<span class="failed">${{esc(x.workspace_error)}}</span>`;
      const files=x.workspace_files||[];
      const summary=`${{esc(x.workspace_file_count||0)}} 个文件 · ${{fmtBytes(x.workspace_size_bytes||0)}}`;
      if(!files.length)return `<span class="subtle">${{summary}}</span>`;
      return `<details><summary>${{summary}}</summary><div class="file-list">${{files.map(file=>`<span><code>${{esc(file.path)}}</code> <span class="subtle">${{fmtBytes(file.size_bytes)}}</span></span>`).join('')}}</div></details>`;
    }};
    let loading=false;
    async function load(){{
      if(loading)return; loading=true;
      document.querySelector('#error').textContent='';
      try{{
        const [o,d,u,t,s,st,g]=await Promise.all([api('/overview'),api('/deliveries'),api('/usage'),api('/tasks'),api('/sandboxes'),api('/stickers'),api('/group-models')]);
        const stats=o.deliveries||{{}},sandboxItems=s.items||[],stickerCounts=st.counts||{{}};
        document.querySelector('#metrics').innerHTML=[['运行任务',o.running_tasks],['沙盒命令',s.active_commands||0],['沙盒数量',sandboxItems.length],['表情库存',stickerCounts.total||0],['待投递',stats.pending||0],['运行秒数',o.uptime_seconds]].map(x=>`<div class="metric"><b>${{esc(x[1])}}</b><span>${{esc(x[0])}}</span></div>`).join('');
        document.querySelector('#status-body').innerHTML=Object.entries(stats).map(([k,v])=>`<tr><td class="status ${{esc(k)}}">${{esc(k)}}</td><td>${{esc(v)}}</td></tr>`).join('')||'<tr><td colspan="2" class="empty">尚无投递记录</td></tr>';
        document.querySelector('#delivery-body').innerHTML=d.items.map(x=>`<tr><td><code>${{esc(x.handle)}}</code></td><td>${{esc(x.target_platform)}} · ${{esc(x.target_kind)}} · <code>${{esc(x.target_native_conversation_id)}}</code></td><td class="status ${{esc(x.status)}}">${{esc(x.status)}}</td><td>${{esc(x.attempts)}}</td><td>${{fmt(x.updated_at)}}</td><td>${{['ambiguous','failed'].includes(x.status)?`<button class="action" data-retry="${{x.delivery_id}}">重试</button>`:''}}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">尚无投递记录</td></tr>';
        document.querySelector('#usage-body').innerHTML=u.items.map(x=>`<tr><td>${{esc(x.day)}}</td><td><code>${{esc(x.scope_key)}}</code></td><td>${{esc(x.source)}}</td><td>${{esc(x.calls)}}</td><td>${{esc(x.input_tokens)}}</td><td>${{esc(x.output_tokens)}}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">尚无用量记录</td></tr>';
        document.querySelector('#task-body').innerHTML=t.items.map(x=>`<tr><td><code>${{esc(x.task_id)}}</code></td><td><code>${{esc(x.conversation_id)}}</code></td><td>${{esc(x.summary)}}</td><td>${{esc(x.elapsed_seconds)}} 秒</td><td><button class="action" data-kill="${{esc(x.task_id)}}">停止</button></td></tr>`).join('')||'<tr><td colspan="5" class="empty">当前没有运行任务</td></tr>';
        document.querySelector('#sandbox-body').innerHTML=!s.available?`<tr><td colspan="6" class="empty">${{esc(s.error||'沙盒功能未启用')}}</td></tr>`:sandboxItems.map(x=>`<tr><td><code>${{esc(x.sandbox_id)}}</code><div class="subtle">${{esc(x.runtime)}}</div></td><td>${{ownerHtml(x)}}</td><td><span class="status ${{x.running?'committed':'failed'}}">${{esc(x.status)}}</span></td><td>${{activityHtml(x)}}</td><td><div class="stack"><span>CPU ${{esc(x.cpu_percent)}}</span><span>内存 ${{esc(x.memory_usage)}}</span><span class="subtle">${{esc(x.memory_percent)}}</span></div></td><td>${{workspaceHtml(x)}}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">当前没有沙盒</td></tr>';
        document.querySelector('#sticker-body').innerHTML=!st.available?`<tr><td colspan="5" class="empty">${{esc(st.error||'表情库存不可用')}}</td></tr>`:(st.items||[]).map(x=>`<tr><td>${{x.source==='local'?'机器人本地':'从 QQ 学习'}}</td><td>${{x.kind==='qq-face'?'QQ 自带表情':'图片表情'}}</td><td>${{esc(x.name)}}</td><td><code>${{esc(x.reference)}}</code></td><td>${{fmtBytes(x.size_bytes)}}</td></tr>`).join('')||'<tr><td colspan="5" class="empty">还没有保存表情包</td></tr>';
        document.querySelector('#group-model-body').innerHTML=(g.items||[]).map(x=>{{const overrides=(x.overrides||[]).map(v=>`<div><code>QQ ${{esc(v.user_id)}}</code> → <code>${{esc(v.profile)}}</code> / ${{esc(v.model)}}${{v.recognized?'':' <span class="failed">配置已失效</span>'}}</div>`).join('')||'<span class="subtle">全部使用默认配置</span>';return `<tr><td><code>${{esc(x.group_id)}}</code></td><td class="status ${{x.enabled?'committed':'failed'}}">${{x.enabled?'已启用':'已禁用'}}</td><td><code>${{esc(x.default_profile)}}</code></td><td>${{esc(x.default_provider)}} / <code>${{esc(x.default_model)}}</code></td><td><div class="stack">${{overrides}}</div></td></tr>`;}}).join('')||'<tr><td colspan="5" class="empty">还没有观察到 QQ 群</td></tr>';
        const models=o.models?.profiles||[];
        document.querySelector('#model-body').innerHTML=models.map(x=>{{const caps=Object.entries(x.capabilities||{{}}).filter(([,enabled])=>enabled).map(([name])=>name).join(', ')||'text';const isDefault=x.name===o.models.default;return `<tr><td><code>${{esc(x.name)}}</code>${{isDefault?' · 默认':''}}</td><td>${{esc(x.provider)}}</td><td><code>${{esc(x.protocol)}}</code></td><td><code>${{esc(x.model)}}</code></td><td>${{esc(caps)}}</td><td class="status ${{x.configured?'committed':'failed'}}">${{x.configured?'已配置':'缺少密钥'}}</td></tr>`;}}).join('')||'<tr><td colspan="6" class="empty">没有模型配置</td></tr>';
      }}catch(e){{document.querySelector('#error').textContent=e.message;}}
      finally{{loading=false;}}
    }}
    document.addEventListener('click',async e=>{{
      const tab=e.target.closest('.tab');
      if(tab){{document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===tab));document.querySelectorAll('main section').forEach(x=>x.hidden=x.id!==tab.dataset.view);}}
      const retry=e.target.closest('[data-retry]'); if(retry){{await api(`/deliveries/${{retry.dataset.retry}}/retry`,{{method:'POST'}});await load();}}
      const kill=e.target.closest('[data-kill]'); if(kill){{await api(`/tasks/${{kill.dataset.kill}}/cancel`,{{method:'POST'}});await load();}}
    }});
    document.querySelector('#refresh').addEventListener('click',load);
    setInterval(()=>{{if(!document.hidden)load();}},5000); load();
  </script>
</body></html>"""
