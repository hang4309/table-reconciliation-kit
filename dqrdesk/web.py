from __future__ import annotations

import html
import json
import mimetypes
import secrets
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .audit import read_events, verify_events
from .engine import load_state, verify_run
from .errors import DeskError
from .invariants import compute_invariants, summarize
from .review import ignore, merge, queue, rollback, split
from .util import canonical_json, verify_within


MAX_BODY = 1024 * 1024
REPORT_FILES = {
    "report.html",
    "report.json",
    "report.xlsx",
    "entities.csv",
    "source_records.csv",
    "provenance.csv",
    "issues.csv",
    "invariants.csv",
}


def _entity_summary(entity: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_id": entity["entity_id"],
        "status": entity["status"],
        "review_disposition": entity.get("review_disposition", "pending"),
        "member_count": entity["member_count"],
        "member_record_ids": entity["member_record_ids"],
        "match_method": entity["match"]["method"],
        "match_evidence": entity["match"]["evidence"],
        "selected_values": entity["selected_values"],
        "conflict_fields": entity["conflict_fields"],
    }


def _public_state(run_dir: Path) -> dict[str, Any]:
    state = load_state(run_dir)
    events = read_events(run_dir / "audit.ndjson")
    audit = verify_events(events)
    batches = [
        {key: value for key, value in batch.items() if key not in {"before_entities", "after_entities"}}
        for batch in state.get("batches", [])
    ]
    return {
        "run_id": state["run_id"],
        "project": state["config"]["project"],
        "summary": summarize(state),
        "invariants": compute_invariants(state),
        "queue": [_entity_summary(entity) for entity in queue(state)],
        "entities": [_entity_summary(entity) for entity in state["entities"]],
        "batches": batches,
        "audit": {**audit, "events": events},
        "downloads": sorted(REPORT_FILES),
    }


def _page(token: str) -> str:
    safe_token = json.dumps(token).replace("<", "\\u003c").replace(">", "\\u003e")
    return f'''<!doctype html><html lang="zh-Hans"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Data Quality Reconciliation Desk</title><style>
:root{{--nav:#111a2e;--nav2:#182542;--bg:#f4f6fa;--panel:#fff;--ink:#182033;--muted:#667085;--line:#e1e6ef;--blue:#315efb;--teal:#0b8f76;--amber:#b66a00;--red:#c53030}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 Inter,system-ui,"Microsoft YaHei",sans-serif}}button,input,textarea{{font:inherit}}
.shell{{display:grid;grid-template-columns:250px 1fr;min-height:100vh}}aside{{background:linear-gradient(180deg,var(--nav),var(--nav2));color:#fff;padding:26px 18px;position:sticky;top:0;height:100vh}}
.brand{{font-weight:750;font-size:17px;line-height:1.2;margin:0 8px 28px}}.brand small{{display:block;color:#9fb0d4;font-size:11px;letter-spacing:.12em;margin-bottom:8px}}nav button{{display:block;width:100%;border:0;background:transparent;color:#b8c3dc;text-align:left;padding:11px 12px;border-radius:7px;cursor:pointer;margin:3px 0}}nav button.active,nav button:hover{{background:#ffffff14;color:#fff}}
main{{padding:28px;min-width:0}}.top{{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:22px}}h1{{font-size:25px;margin:0}}.subtitle{{color:var(--muted);margin-top:4px}}.badge{{border:1px solid #cbd5e1;border-radius:999px;padding:6px 10px;background:white}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:12px}}
.card,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:10px;box-shadow:0 1px 2px #1018280c}}.card{{padding:18px;border-top:3px solid var(--blue)}}.card span{{color:var(--muted)}}.card b{{font-size:28px;display:block;margin-top:4px}}.card.warn{{border-top-color:var(--amber)}}.card.bad{{border-top-color:var(--red)}}
.panel{{margin-top:16px;padding:18px;overflow:auto}}.panel h2{{font-size:16px;margin:0 0 14px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}}tr:hover td{{background:#f8faff}}
.pill{{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef1f6;font-size:12px}}.matched,.pass{{color:var(--teal);background:#dcf7ef}}.ambiguous,.unmatched{{color:var(--amber);background:#fff1d6}}.conflict,.fail{{color:var(--red);background:#ffe5e5}}
.toolbar{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}}button.primary,button.secondary,button.danger{{border:0;border-radius:7px;padding:8px 12px;cursor:pointer}}button.primary{{background:var(--blue);color:#fff}}button.secondary{{background:#e9edf5;color:var(--ink)}}button.danger{{background:#ffe5e5;color:var(--red)}}button:disabled{{opacity:.45;cursor:not-allowed}}input[type=text],textarea{{border:1px solid #cbd3df;border-radius:7px;padding:8px;background:white}}input.reason{{min-width:300px;flex:1}}code,pre{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}}pre{{background:#f6f8fb;border-radius:7px;padding:10px;white-space:pre-wrap;word-break:break-word;max-width:900px}}
.view{{display:none}}.view.active{{display:block}}.downloads a{{display:inline-block;margin:5px 7px 5px 0;color:var(--blue)}}.toast{{position:fixed;right:20px;bottom:20px;background:#172033;color:white;padding:12px 16px;border-radius:8px;display:none;max-width:440px}}.toast.error{{background:var(--red)}}details summary{{cursor:pointer;color:var(--blue)}}
@media(max-width:850px){{.shell{{grid-template-columns:1fr}}aside{{height:auto;position:static}}nav{{display:flex;overflow:auto}}nav button{{width:auto;white-space:nowrap}}main{{padding:16px}}.grid{{grid-template-columns:1fr 1fr}}.top{{display:block}}}}
</style></head><body><div class="shell"><aside><div class="brand"><small>OFFLINE · AUDITABLE</small>Data Quality<br>Reconciliation Desk</div><nav>
<button data-view="dashboard" class="active">质量仪表盘</button><button data-view="queue">人工审核队列</button><button data-view="entities">统一实体</button><button data-view="batches">批次与回滚</button><button data-view="audit">审计账本</button><button data-view="exports">结果下载</button>
</nav></aside><main><div class="top"><div><h1 id="project">正在载入…</h1><div class="subtitle" id="runid"></div></div><span class="badge" id="quality">—</span></div>
<section id="dashboard" class="view active"><div id="cards" class="grid"></div><div class="panel"><h2>平衡与完整性不变量</h2><table><thead><tr><th>检查</th><th>类型</th><th>结果</th><th>观测</th></tr></thead><tbody id="invariants"></tbody></table></div></section>
<section id="queue" class="view"><div class="panel"><h2>待处理记录</h2><div class="toolbar"><input id="reason" class="reason" type="text" placeholder="本批次原因（必填）"><button class="primary" id="merge">合并所选实体</button><button class="secondary" id="ignore">忽略所选实体</button></div><table><thead><tr><th></th><th>状态</th><th>实体</th><th>成员</th><th>匹配解释</th><th>值</th></tr></thead><tbody id="queueRows"></tbody></table></div></section>
<section id="entities" class="view"><div class="panel"><h2>全部统一实体</h2><table><thead><tr><th>状态</th><th>实体</th><th>成员</th><th>方法</th><th>已选值 / 来源</th><th>操作</th></tr></thead><tbody id="entityRows"></tbody></table></div></section>
<section id="batches" class="view"><div class="panel"><h2>可逆人工批次</h2><table><thead><tr><th>批次</th><th>动作</th><th>状态</th><th>操作者</th><th>原因</th><th>实体变化</th><th></th></tr></thead><tbody id="batchRows"></tbody></table></div></section>
<section id="audit" class="view"><div class="panel"><h2>哈希链审计账本</h2><div id="auditMeta"></div><table><thead><tr><th>#</th><th>事件</th><th>时间</th><th>操作者</th><th>批次</th><th>哈希</th></tr></thead><tbody id="auditRows"></tbody></table></div></section>
<section id="exports" class="view"><div class="panel"><h2>确定性结果包</h2><p>HTML / CSV / JSON / XLSX 均从同一状态生成。CSV 与 XLSX 中的不可信公式前缀已转义。</p><div id="downloads" class="downloads"></div></div></section>
</main></div><div id="toast" class="toast"></div><script>
const TOKEN={safe_token}; let model=null;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const pretty=o=>esc(JSON.stringify(o,null,2));
function toast(message,error=false){{const el=document.querySelector('#toast');el.textContent=message;el.className='toast'+(error?' error':'');el.style.display='block';setTimeout(()=>el.style.display='none',4200)}}
async function api(path,options={{}}){{options.headers={{...(options.headers||{{}}),'X-DQR-Token':TOKEN,'Content-Type':'application/json'}};const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw new Error(data.message||'请求失败');return data}}
function statusPill(s){{return `<span class="pill ${{esc(s)}}">${{esc(s)}}</span>`}}
function render(){{document.querySelector('#project').textContent=model.project;document.querySelector('#runid').textContent='Run '+model.run_id;document.querySelector('#quality').textContent=model.summary.quality_state;
const sc=model.summary.status_counts;document.querySelector('#cards').innerHTML=[['matched',sc.matched,''],['unmatched',sc.unmatched,'warn'],['ambiguous',sc.ambiguous,'warn'],['conflict',sc.conflict,'bad']].map(x=>`<div class="card ${{x[2]}}"><span>${{x[0]}}</span><b>${{x[1]}}</b></div>`).join('');
document.querySelector('#invariants').innerHTML=model.invariants.map(x=>`<tr><td>${{esc(x.name)}}</td><td>${{esc(x.kind)}}</td><td>${{statusPill(x.passed?'pass':'fail')}}</td><td><code>${{pretty(x.observed)}}</code></td></tr>`).join('');
document.querySelector('#queueRows').innerHTML=model.queue.map(e=>`<tr><td><input type="checkbox" class="pick" value="${{esc(e.entity_id)}}"></td><td>${{statusPill(e.status)}}</td><td><code>${{esc(e.entity_id)}}</code></td><td>${{e.member_count}}<br><small>${{esc(e.member_record_ids.join(', '))}}</small></td><td><details><summary>${{esc(e.match_method)}}</summary><pre>${{pretty(e.match_evidence)}}</pre></details></td><td><details><summary>查看</summary><pre>${{pretty(e.selected_values)}}</pre></details></td></tr>`).join('')||'<tr><td colspan="6">队列已清空</td></tr>';
document.querySelector('#entityRows').innerHTML=model.entities.map(e=>`<tr><td>${{statusPill(e.status)}}</td><td><code>${{esc(e.entity_id)}}</code></td><td>${{e.member_count}}<br><small>${{esc(e.member_record_ids.join(', '))}}</small></td><td>${{esc(e.match_method)}}</td><td><details><summary>字段级详情</summary><pre>${{pretty(e.selected_values)}}</pre><button class="secondary detail" data-id="${{esc(e.entity_id)}}">载入完整来源</button><div class="detailbox"></div></details></td><td>${{e.member_count>1?`<button class="secondary split" data-id="${{esc(e.entity_id)}}">拆分为单条</button>`:''}}</td></tr>`).join('');
const active=model.batches.filter(b=>b.status==='active');const latest=active.length?active[active.length-1].batch_id:null;document.querySelector('#batchRows').innerHTML=model.batches.map(b=>`<tr><td><code>${{esc(b.batch_id)}}</code></td><td>${{esc(b.action)}}</td><td>${{esc(b.status)}}</td><td>${{esc(b.actor)}}</td><td>${{esc(b.reason)}}</td><td>${{esc(b.before_entity_ids.join(','))}} → ${{esc(b.after_entity_ids.join(','))}}</td><td>${{b.batch_id===latest?`<button class="danger rollback" data-id="${{esc(b.batch_id)}}">回滚</button>`:''}}</td></tr>`).join('')||'<tr><td colspan="7">尚无人工批次</td></tr>';
document.querySelector('#auditMeta').innerHTML=`事件 ${{model.audit.event_count}} · 链头 <code>${{esc(model.audit.head_hash)}}</code>`;document.querySelector('#auditRows').innerHTML=model.audit.events.map(e=>`<tr><td>${{e.sequence}}</td><td>${{esc(e.event_type)}}</td><td>${{esc(e.occurred_at)}}</td><td>${{esc(e.actor)}}</td><td>${{esc(e.batch_id||'—')}}</td><td><code>${{esc(e.event_hash.slice(0,16))}}…</code></td></tr>`).join('');
document.querySelector('#downloads').innerHTML=model.downloads.map(n=>`<a href="/reports/${{encodeURIComponent(n)}}" target="_blank">${{esc(n)}}</a>`).join('');bindDynamic()}}
function selected(){{return [...document.querySelectorAll('.pick:checked')].map(x=>x.value)}}
async function action(body){{try{{await api('/api/action',{{method:'POST',body:JSON.stringify(body)}});toast('批次已提交并重新生成报告');await load()}}catch(e){{toast(e.message,true)}}}}
function reason(){{const r=document.querySelector('#reason').value.trim();if(!r)throw new Error('请填写批次原因');return r}}
function bindDynamic(){{document.querySelectorAll('.rollback').forEach(b=>b.onclick=()=>{{const why=prompt('回滚原因');if(why)action({{action:'rollback',batch_id:b.dataset.id,actor:'web-reviewer',reason:why}})}});document.querySelectorAll('.split').forEach(b=>b.onclick=()=>{{const why=prompt('拆分原因（将拆为单条）');if(why)action({{action:'split',entity_id:b.dataset.id,groups:null,actor:'web-reviewer',reason:why}})}});document.querySelectorAll('.detail').forEach(b=>b.onclick=async()=>{{try{{const d=await api('/api/entity?id='+encodeURIComponent(b.dataset.id));b.nextElementSibling.innerHTML='<pre>'+pretty(d)+'</pre>'}}catch(e){{toast(e.message,true)}}}})}}
async function load(){{model=await api('/api/state');render()}}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{{document.querySelectorAll('nav button,.view').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.querySelector('#'+b.dataset.view).classList.add('active')}});
document.querySelector('#merge').onclick=()=>{{try{{const ids=selected();if(ids.length<2)throw new Error('至少选择两个实体');action({{action:'merge',entity_ids:ids,actor:'web-reviewer',reason:reason()}})}}catch(e){{toast(e.message,true)}}}};
document.querySelector('#ignore').onclick=()=>{{try{{const ids=selected();if(!ids.length)throw new Error('至少选择一个实体');action({{action:'ignore',entity_ids:ids,actor:'web-reviewer',reason:reason()}})}}catch(e){{toast(e.message,true)}}}};
load().catch(e=>toast(e.message,true));
</script></body></html>'''


def make_handler(run_dir: Path, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DQRDesk/1.0"

        def _headers(self, status: int, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()

        def _bytes(self, data: bytes, content_type: str = "application/json; charset=utf-8", status: int = 200) -> None:
            self._headers(status, content_type, len(data))
            self.wfile.write(data)

        def _json(self, value: Any, status: int = 200) -> None:
            self._bytes(canonical_json(value, pretty=True).encode("utf-8"), status=status)

        def _authorized(self) -> bool:
            return self.headers.get("X-DQR-Token") == token

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/":
                self._bytes(_page(token).encode("utf-8"), "text/html; charset=utf-8")
                return
            if parsed.path.startswith("/api/") and not self._authorized():
                self._json({"message": "invalid local session token"}, HTTPStatus.FORBIDDEN)
                return
            try:
                if parsed.path == "/api/state":
                    self._json(_public_state(run_dir))
                    return
                if parsed.path == "/api/entity":
                    entity_id = urllib.parse.parse_qs(parsed.query).get("id", [""])[0]
                    state = load_state(run_dir)
                    entity = next((item for item in state["entities"] if item["entity_id"] == entity_id), None)
                    if entity is None:
                        self._json({"message": "entity not found"}, HTTPStatus.NOT_FOUND)
                        return
                    member_ids = set(entity["member_record_ids"])
                    self._json({"entity": entity, "source_records": [item for item in state["records"] if item["record_id"] in member_ids]})
                    return
                if parsed.path.startswith("/reports/"):
                    name = urllib.parse.unquote(parsed.path.removeprefix("/reports/"))
                    if name not in REPORT_FILES:
                        self._json({"message": "report not found"}, HTTPStatus.NOT_FOUND)
                        return
                    path = verify_within(run_dir / "reports", run_dir / "reports" / name)
                    data = path.read_bytes()
                    media = mimetypes.guess_type(name)[0] or "application/octet-stream"
                    self._bytes(data, media)
                    return
                self._json({"message": "not found"}, HTTPStatus.NOT_FOUND)
            except (DeskError, OSError) as exc:
                self._json({"message": str(exc), "error_type": type(exc).__name__}, HTTPStatus.BAD_REQUEST)

        def do_POST(self):
            if not self._authorized():
                self._json({"message": "invalid local session token"}, HTTPStatus.FORBIDDEN)
                return
            if self.path != "/api/action":
                self._json({"message": "not found"}, HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > MAX_BODY:
                    raise ValueError("request body size is invalid")
                payload = json.loads(self.rfile.read(length))
                action = payload.get("action")
                actor = str(payload.get("actor", "web-reviewer"))[:80]
                reason = str(payload.get("reason", "")).strip()
                if not reason:
                    raise ValueError("reason is required")
                if action == "merge":
                    result = merge(run_dir, payload.get("entity_ids", []), actor=actor, reason=reason)
                elif action == "split":
                    result = split(run_dir, payload.get("entity_id", ""), payload.get("groups"), actor=actor, reason=reason)
                elif action == "ignore":
                    result = ignore(run_dir, payload.get("entity_ids", []), actor=actor, reason=reason)
                elif action == "rollback":
                    result = rollback(run_dir, payload.get("batch_id", ""), actor=actor, reason=reason)
                else:
                    raise ValueError("unknown action")
                self._json(result)
            except (DeskError, ValueError, json.JSONDecodeError, OSError) as exc:
                self._json({"message": str(exc), "error_type": type(exc).__name__}, HTTPStatus.BAD_REQUEST)

        def log_message(self, fmt: str, *args: Any) -> None:
            print(f"[web] {self.address_string()} {fmt % args}")

    return Handler


def serve(run_dir: str | Path, host: str, port: int, *, open_browser: bool = True) -> None:
    if host.casefold() not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("this desktop review server only accepts loopback hosts (127.0.0.1, localhost, ::1)")
    run_path = Path(run_dir).resolve()
    verify_run(run_path)
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer((host, port), make_handler(run_path, token))
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"Data Quality Reconciliation Desk: {url}")
    print("The server is loopback-only. The in-page random token prevents cross-site write requests; it is not a user login.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
