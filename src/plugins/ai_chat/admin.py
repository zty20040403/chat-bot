from __future__ import annotations

import hmac
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
    .metrics {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
      border:1px solid var(--line); background:var(--white); margin-bottom:18px; }}
    .metric {{ padding:16px; min-width:0; border-right:1px solid var(--line); }}
    .metric:last-child {{ border-right:0; }}
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
    @media (max-width:760px) {{ .metrics {{ grid-template-columns:repeat(2,1fr); }}
      .metric:nth-child(2) {{ border-right:0; }} .metric:nth-child(-n+2) {{ border-bottom:1px solid var(--line); }} }}
  </style>
</head>
<body>
  <header><div class="bar"><h1>QQ Bot 管理台</h1><span class="version">v{safe_version}</span></div></header>
  <nav><div class="tabs">
    <button class="tab active" data-view="overview">概览</button>
    <button class="tab" data-view="deliveries">投递</button>
    <button class="tab" data-view="usage">用量</button>
    <button class="tab" data-view="tasks">任务</button>
    <button class="tab" data-view="models">模型</button>
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
    async function load(){{
      document.querySelector('#error').textContent='';
      try{{
        const [o,d,u,t]=await Promise.all([api('/overview'),api('/deliveries'),api('/usage'),api('/tasks')]);
        const stats=o.deliveries||{{}};
        document.querySelector('#metrics').innerHTML=[['运行任务',o.running_tasks],['待投递',stats.pending||0],['回执不明',stats.ambiguous||0],['运行秒数',o.uptime_seconds]].map(x=>`<div class="metric"><b>${{esc(x[1])}}</b><span>${{esc(x[0])}}</span></div>`).join('');
        document.querySelector('#status-body').innerHTML=Object.entries(stats).map(([k,v])=>`<tr><td class="status ${{esc(k)}}">${{esc(k)}}</td><td>${{esc(v)}}</td></tr>`).join('')||'<tr><td colspan="2" class="empty">尚无投递记录</td></tr>';
        document.querySelector('#delivery-body').innerHTML=d.items.map(x=>`<tr><td><code>${{esc(x.handle)}}</code></td><td>${{esc(x.target_platform)}} · ${{esc(x.target_kind)}} · <code>${{esc(x.target_native_conversation_id)}}</code></td><td class="status ${{esc(x.status)}}">${{esc(x.status)}}</td><td>${{esc(x.attempts)}}</td><td>${{fmt(x.updated_at)}}</td><td>${{['ambiguous','failed'].includes(x.status)?`<button class="action" data-retry="${{x.delivery_id}}">重试</button>`:''}}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">尚无投递记录</td></tr>';
        document.querySelector('#usage-body').innerHTML=u.items.map(x=>`<tr><td>${{esc(x.day)}}</td><td><code>${{esc(x.scope_key)}}</code></td><td>${{esc(x.source)}}</td><td>${{esc(x.calls)}}</td><td>${{esc(x.input_tokens)}}</td><td>${{esc(x.output_tokens)}}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">尚无用量记录</td></tr>';
        document.querySelector('#task-body').innerHTML=t.items.map(x=>`<tr><td><code>${{esc(x.task_id)}}</code></td><td><code>${{esc(x.conversation_id)}}</code></td><td>${{esc(x.summary)}}</td><td>${{esc(x.elapsed_seconds)}} 秒</td><td><button class="action" data-kill="${{esc(x.task_id)}}">停止</button></td></tr>`).join('')||'<tr><td colspan="5" class="empty">当前没有运行任务</td></tr>';
        const models=o.models?.profiles||[];
        document.querySelector('#model-body').innerHTML=models.map(x=>{{const caps=Object.entries(x.capabilities||{{}}).filter(([,enabled])=>enabled).map(([name])=>name).join(', ')||'text';const isDefault=x.name===o.models.default;return `<tr><td><code>${{esc(x.name)}}</code>${{isDefault?' · 默认':''}}</td><td>${{esc(x.provider)}}</td><td><code>${{esc(x.protocol)}}</code></td><td><code>${{esc(x.model)}}</code></td><td>${{esc(caps)}}</td><td class="status ${{x.configured?'committed':'failed'}}">${{x.configured?'已配置':'缺少密钥'}}</td></tr>`;}}).join('')||'<tr><td colspan="6" class="empty">没有模型配置</td></tr>';
      }}catch(e){{document.querySelector('#error').textContent=e.message;}}
    }}
    document.addEventListener('click',async e=>{{
      const tab=e.target.closest('.tab');
      if(tab){{document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===tab));document.querySelectorAll('main section').forEach(x=>x.hidden=x.id!==tab.dataset.view);}}
      const retry=e.target.closest('[data-retry]'); if(retry){{await api(`/deliveries/${{retry.dataset.retry}}/retry`,{{method:'POST'}});await load();}}
      const kill=e.target.closest('[data-kill]'); if(kill){{await api(`/tasks/${{kill.dataset.kill}}/cancel`,{{method:'POST'}});await load();}}
    }});
    document.querySelector('#refresh').addEventListener('click',load); load();
  </script>
</body></html>"""
