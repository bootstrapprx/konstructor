from __future__ import annotations

import getpass
import ipaddress
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import roadmap
from .tui import GeneratorTUI, MENU, TABS


HTML = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Konstructor · Workbench</title><style>
:root{color-scheme:dark;font:14px ui-monospace,SFMono-Regular,Menlo,monospace;background:#101414;color:#e4e9df;--line:#343d38;--muted:#9aa69c;--green:#b9e769;--orange:#ffad66}*{box-sizing:border-box}body{margin:0}header{height:66px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 26px}header b{color:var(--green);letter-spacing:.08em}header span,.muted{color:var(--muted)}main{max-width:1500px;margin:auto;padding:24px;display:grid;grid-template-columns:290px minmax(0,1fr);gap:24px}.rail,.card{border:1px solid var(--line);background:#171d1a;border-radius:9px}.rail{padding:16px;height:calc(100vh - 115px);overflow:auto}.eyebrow{font-size:11px;letter-spacing:.13em;color:var(--muted);text-transform:uppercase}.counts{display:flex;gap:8px;margin:14px 0 20px}.pill{border:1px solid var(--line);padding:6px 8px;border-radius:99px;color:var(--muted);font-size:11px}.work{display:block;width:100%;text-align:left;background:none;border:0;border-top:1px solid #28302b;color:inherit;padding:12px 6px;cursor:pointer}.work:hover,.work.sel{background:#222b25}.work strong{display:block;margin-bottom:7px}.work small{color:var(--muted)}.dot{color:var(--orange);padding-right:7px}.complete .dot{color:var(--green)}.content{min-width:0}.topline{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}.topline h1{font-size:22px;margin:5px 0}.grid{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(270px,1fr);gap:16px}.card{padding:20px;margin-bottom:16px}.card h2{font-size:14px;margin:0 0 16px}.criterion{display:flex;gap:12px;padding:12px 0;border-top:1px solid #29312d}.criterion input{accent-color:var(--green)}.criterion div{flex:1}.criterion small{display:block;margin-top:5px;color:var(--muted)}.badge{font-size:10px;text-transform:uppercase;color:var(--orange)}.badge.passed{color:var(--green)}input,textarea,select{font:inherit;color:inherit;background:#101512;border:1px solid #424d46;border-radius:5px;padding:10px;width:100%;margin:6px 0 12px}textarea{min-height:90px;resize:vertical}label{font-size:11px;color:var(--muted)}button.primary,button.secondary{font:inherit;cursor:pointer;border-radius:5px;padding:10px 13px}.primary{background:var(--green);color:#172012;border:0;font-weight:bold}.secondary{background:#202823;color:inherit;border:1px solid #48544b}.row{display:flex;gap:8px;align-items:center}.row>*{width:auto}.evidence{border-top:1px solid #29312d;padding:10px 0}.evidence b{color:var(--green)}.empty{padding:30px;color:var(--muted);text-align:center}.notice{position:fixed;bottom:18px;right:20px;background:#242e28;border:1px solid #596955;padding:12px 18px;border-radius:6px;max-width:500px;display:none;white-space:pre-wrap}.section-title{display:flex;justify-content:space-between;align-items:center}.section-title small{color:var(--muted)}@media(max-width:850px){main{grid-template-columns:1fr;padding:12px}.rail{height:auto;max-height:250px}.grid{grid-template-columns:1fr}header{padding:0 14px}}
</style></head><body><header><b>KONSTRUCTOR <span style="color:#e4e9df">/ WORKBENCH</span></b><span><a href="/terminal" style="color:var(--green)">Operações</a> · <span id="workspace"></span></span></header>
<main><aside class="rail"><div class="eyebrow">Fonte de trabalho · Roadmap</div><div class="counts" id="counts"></div><div id="items"></div><hr style="border-color:var(--line);margin:20px 0"><div class="eyebrow">Adicionar trabalho</div><form id="new-item"><input name="title" placeholder="O que precisa ser feito?" required maxlength="300"><textarea name="criteria" placeholder="Critérios de aceite, um por linha"></textarea><button class="primary">Adicionar item</button></form></aside>
<section class="content"><div class="topline"><div><div class="eyebrow">Construção guiada pelo roadmap</div><h1 id="title">Selecione um trabalho</h1></div><span class="pill" id="progress"></span></div><div id="detail" class="empty">Escolha um item existente ou adicione trabalho ao roadmap.</div></section></main><div class="notice" id="notice"></div>
<script>
let items=[],selected='';const $=s=>document.querySelector(s),api=async(path,data)=>{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}),v=await r.json();if(!r.ok||v.error)throw Error(v.error||r.statusText);return v};function notice(s){$('#notice').textContent=s;$('#notice').style.display='block';setTimeout(()=>$('#notice').style.display='none',5000)}function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function title(x){return Object.entries(x.data).find(([k])=>k.toLowerCase()==='title')?.[1]||Object.values(x.data)[0]||x.id}function render(){const counts={complete:0,in_progress:0,other:0};items.forEach(x=>{const s=x.completion.state;counts[s==='complete'?'complete':s==='in_progress'?'in_progress':'other']++});$('#workspace').textContent=window.workspace||'';$('#counts').innerHTML=`<span class="pill">${counts.complete} concluídos</span><span class="pill">${counts.in_progress} em curso</span><span class="pill">${counts.other} a iniciar</span>`;$('#items').innerHTML=items.map(x=>`<button class="work ${x.id===selected?'sel':''} ${x.completion.state==='complete'?'complete':''}" data-id="${esc(x.id)}"><strong><span class="dot">●</span>${esc(title(x))}</strong><small>${esc(x.id)} · ${esc(x.completion.state)} · ${x.completion.criteria_passed}/${x.completion.criteria_total} critérios</small></button>`).join('');const x=items.find(i=>i.id===selected);if(!x){$('#detail').className='empty';$('#detail').textContent=items.length?'Selecione um item para ver critérios e evidências.':'Roadmap vazio. Crie um item para começar.';$('#title').textContent='Selecione um trabalho';$('#progress').textContent='';return}$('#detail').className='grid';$('#title').textContent=title(x);$('#progress').textContent=`${x.completion.criteria_passed} / ${x.completion.criteria_total} critérios`;$('#detail').innerHTML=`<div><div class="card"><div class="section-title"><h2>Critérios de aceite</h2><small>${esc(x.completion.state)}</small></div><div id="criteria">${x.completion.criteria.map(c=>`<div class="criterion"><div><span>${esc(c.text)}</span><small class="badge ${c.state==='passed'?'passed':''}">${esc(c.state)}${c.reviewer?' · revisto por '+esc(c.reviewer):''}</small><label>Evidências para esta revisão</label><select class="review-evidence" data-for="${esc(c.id)}" multiple>${x.completion.available_evidence.map(ev=>`<option value="${esc(ev.evidence_id)}" ${c.evidence.some(linked=>linked.evidence_id===ev.evidence_id)?'selected':''}>${esc(ev.evidence_id)} · ${esc(ev.kind)} · ${esc(ev.verification)}</option>`).join('')}</select></div><button class="secondary review" data-criterion="${esc(c.id)}" data-decision="${c.state==='passed'?'needs_work':'passed'}">${c.state==='passed'?'Reabrir':'Revisar'}</button></div>`).join('')||'<p class="muted">Este item ainda não tem critérios.</p>'}</div><form id="criteria-form"><label>Editar critérios · um por linha</label><textarea name="criteria">${esc(x.completion.criteria.map(c=>c.text).join('\n'))}</textarea><button class="secondary">Salvar critérios</button></form></div><div class="card"><h2>Evidências registradas</h2><div id="evidence">${x.completion.available_evidence.map(e=>`<div class="evidence"><b>${esc(e.kind)}</b> · ${esc(e.verification)}<div>${esc(e.reference)}</div><small class="muted">${esc(e.summary)}</small></div>`).join('')||'<p class="muted">Nenhuma evidência ainda.</p>'}</div></div></div><div><div class="card"><h2>Registrar evidência</h2><form id="evidence-form"><label>Tipo</label><select name="kind"><option value="changeset">Changeset aplicado e validado</option><option value="file">Arquivo (hash verificado)</option><option value="command">Comando executado</option><option value="manual">Verificação manual</option></select><label>Referência</label><input name="reference" placeholder="ID do changeset, caminho ou comando" required><label>Resumo / resultado</label><textarea name="summary" placeholder="O que esta evidência demonstra?"></textarea><button class="primary">Registrar evidência</button></form></div><div class="card"><h2>Como a conclusão é decidida</h2><p class="muted">Cada critério precisa de uma revisão aprovada ligada a evidência válida. A aprovação pede confirmação no terminal que iniciou o servidor.</p></div></div>`;$('#criteria-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/criteria',{id:x.id,criteria:new FormData(e.target).get('criteria').split('\n').map(v=>v.trim()).filter(Boolean)});await refresh();notice('Critérios salvos. Revisões antigas podem precisar ser refeitas.')}catch(err){notice(err.message)}};$('#evidence-form').onsubmit=async e=>{e.preventDefault();try{const f=new FormData(e.target);await api('/api/evidence',{id:x.id,kind:f.get('kind'),reference:f.get('reference'),summary:f.get('summary')});await refresh();notice('Evidência registrada.')}catch(err){notice(err.message)}};document.querySelectorAll('.review').forEach(b=>b.onclick=async()=>{if(b.dataset.decision==='passed'&&!confirm('Solicitar revisão aprovada? O servidor pedirá a confirmação no terminal local.'))return;try{await api('/api/review',{id:x.id,criterion_id:b.dataset.criterion,decision:b.dataset.decision,evidence_ids:[...document.querySelector(`.review-evidence[data-for="${b.dataset.criterion}"]`).selectedOptions].map(o=>o.value)});await refresh();notice('Revisão registrada.')}catch(err){notice(err.message)}})}async function refresh(){const r=await fetch('/api/roadmap'),v=await r.json();if(!r.ok)throw Error(v.error||'Falha ao carregar roadmap');items=v.items;window.workspace=v.workspace;if(!selected||!items.some(x=>x.id===selected))selected=items[0]?.id||'';render()}$('#items').onclick=e=>{const b=e.target.closest('[data-id]');if(b){selected=b.dataset.id;render()}};$('#new-item').onsubmit=async e=>{e.preventDefault();try{const f=new FormData(e.target),x=await api('/api/work',{title:f.get('title'),criteria:f.get('criteria')});e.target.reset();selected=x.id;await refresh();notice('Item adicionado ao roadmap.')}catch(err){notice(err.message)}};refresh().catch(e=>notice(e.message));
</script></body></html>'''

TERMINAL_HTML = r'''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Konstructor · Operações</title><style>body{background:#101414;color:#e4e9df;font:14px monospace;margin:20px}a{color:#b9e769}main{display:grid;grid-template-columns:220px 1fr;gap:16px}button{display:block;width:100%;margin:4px 0;padding:9px;background:#202823;color:inherit;border:1px solid #48544b;cursor:pointer}.cols{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.box{height:70vh;overflow:auto;white-space:pre-wrap;border:1px solid #343d38;padding:12px}input{width:98%;padding:9px;background:#101512;color:inherit;border:1px solid #48544b}</style><h2>KONSTRUCTOR · <a href="/">WORKBENCH</a> <small id="workspace"></small></h2><main><nav id="menu"></nav><section><div class="cols"><pre class="box" id="files"></pre><pre class="box" id="diff"></pre><pre class="box" id="logs"></pre></div><p id="status"></p><input id="prompt" placeholder="JSON opcional para a operação selecionada"></section></main><script>const $=s=>document.querySelector(s);function post(path,body){return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})})}function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}function draw(s){$('#workspace').textContent=s.workspace;$('#menu').innerHTML=s.menu.map(x=>`<button data-action="${esc(x.action)}">${esc(x.label)}</button>`).join('');$('#files').textContent=s.workspace_lines.join('\n');$('#diff').textContent=s.diff_lines.join('\n');$('#logs').textContent=s.logs.join('\n');$('#status').textContent=s.messages.join(' · ')}$('#menu').onclick=e=>{const b=e.target.closest('[data-action]');if(!b)return;let args={};try{if($('#prompt').value.trim())args=JSON.parse($('#prompt').value)}catch(err){alert('JSON inválido');return}post('/action',{action:b.dataset.action,...args})};new EventSource('/events').onmessage=e=>draw(JSON.parse(e.data));</script></html>'''


class WebTerminalApp:
    def __init__(self, root: str | Path, theme: str | Path | None = None, config: str | Path | None = None):
        self.controller = GeneratorTUI(Path(root), theme, config)
        self.lock = threading.RLock()
        self.review_lock = threading.Lock()
        self.revision = 0

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            selected = self.controller.selected
            return {
                "workspace": str(self.controller.root), "selected": selected,
                "menu": [{"label": label, "action": action} for label, action in MENU if action != "quit"],
                "tabs": list(TABS), "view": self.controller.view,
                "workspace_lines": list(self.controller.workspace_lines),
                "diff_lines": list(self.controller.diff_lines if self.controller.diff_lines != ["Nenhum diff carregado."] else self.controller.detail),
                "logs": self.controller.log_buffer.snapshot(), "messages": list(self.controller.messages[-3:]),
                "shortcuts": dict(self.controller.config.shortcuts), "revision": self.revision,
            }

    def roadmap_snapshot(self) -> dict[str, Any]:
        roadmap.initialize_empty(self.controller.root)
        return {"workspace": str(self.controller.root), "items": roadmap.list_records(self.controller.root, "Roadmap")}

    def roadmap_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        if action == "work":
            title = payload.get("title", "")
            criteria = payload.get("criteria", "")
            if not isinstance(title, str) or not title.strip() or not isinstance(criteria, str):
                raise ValueError("informe título e critérios em texto")
            item = roadmap.create_work_item(self.controller.root, title.strip(), criteria)
            return {"id": item["id"]}
        if action == "criteria":
            values = payload.get("criteria")
            if not isinstance(values, list): raise ValueError("critérios inválidos")
            return roadmap.set_acceptance_criteria(self.controller.root, payload.get("id", ""), values)
        if action == "evidence":
            return roadmap.add_evidence(self.controller.root, payload.get("id", ""), payload.get("kind", ""), payload.get("reference", ""), payload.get("summary", ""))
        if action == "review":
            if not sys.stdin.isatty(): raise PermissionError("revisão de aceite exige o terminal local que iniciou o servidor")
            if not self.review_lock.acquire(blocking=False): raise RuntimeError("outra revisão está aguardando confirmação no terminal")
            try:
                item = roadmap.show_record(self.controller.root, payload.get("id", ""))
                criterion_id, decision = payload.get("criterion_id", ""), payload.get("decision", "")
                criterion = next((row for row in item["completion"]["criteria"] if row["id"] == criterion_id), None)
                if criterion is None: raise ValueError("critério não encontrado")
                evidence_ids = payload.get("evidence_ids", [])
                if not isinstance(evidence_ids, list): raise ValueError("evidências inválidas")
                available = {row["evidence_id"]: row for row in item["completion"]["available_evidence"]}
                print(f"\nItem: {item['id']}\nCritério: {criterion['text']}\nDecisão: {decision}", flush=True)
                for evidence_id in evidence_ids:
                    row = available.get(evidence_id)
                    print(f"Evidência {evidence_id}: {row['kind']} · {row['reference']} · {row['verification']} · {row['summary']}" if row else f"Evidência ausente: {evidence_id}", flush=True)
                confirmation = f"REVIEW {item['id']} {criterion_id} {decision}"
                if input(f"Confirme digitando {confirmation}: ") != confirmation: raise PermissionError("revisão não confirmada")
                return roadmap.review_criterion(self.controller.root, item["id"], criterion_id, decision, evidence_ids, getpass.getuser())
            finally:
                self.review_lock.release()
        raise ValueError("ação de roadmap desconhecida")

    def input(self, key: str) -> dict[str, Any]:
        with self.lock:
            result = self.controller.handle_web_key(key); self.revision += 1; return result

    def action(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            action = payload.get("action", "")
            kwargs = {key: payload[key] for key in ("request", "plan_id", "changeset_id", "approve") if key in payload}
            try:
                result = self.controller.perform(action, **kwargs); self.revision += 1
                return {"ok": True, "result": result, "snapshot": self.snapshot()}
            except Exception as exc:
                self.controller.log(f"ERRO WEB: {exc}"); self.revision += 1
                return {"ok": False, "error": str(exc), "snapshot": self.snapshot()}


class _Handler(BaseHTTPRequestHandler):
    server: "WebTerminalServer"

    def _json(self, status: int, value: Any) -> None:
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_048_576: raise ValueError("request body exceeds 1 MiB")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict): raise ValueError("request body must be a JSON object")
        return data

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            data = HTML.encode("utf-8"); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        elif path == "/terminal":
            data = TERMINAL_HTML.encode("utf-8"); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        elif path == "/state": self._json(200, self.server.app.snapshot())
        elif path == "/api/roadmap":
            try: self._json(200, self.server.app.roadmap_snapshot())
            except Exception as exc: self._json(400, {"error": str(exc)})
        elif path == "/events":
            self.send_response(200); self.send_header("Content-Type", "text/event-stream; charset=utf-8"); self.send_header("Cache-Control", "no-cache"); self.send_header("Connection", "keep-alive"); self.end_headers()
            last = -1
            try:
                while True:
                    state = self.server.app.snapshot()
                    if state["revision"] != last:
                        self.wfile.write(f"data: {json.dumps(state, ensure_ascii=False)}\n\n".encode("utf-8")); self.wfile.flush(); last = state["revision"]
                    time.sleep(0.2)
            except (BrokenPipeError, ConnectionResetError): pass
        else: self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            if self.path == "/input": self._json(200, self.server.app.input(str(self._body().get("key", ""))))
            elif self.path == "/action": self._json(200, self.server.app.action(self._body()))
            elif self.path.startswith("/api/"):
                action = {"/api/work": "work", "/api/criteria": "criteria", "/api/evidence": "evidence", "/api/review": "review"}.get(self.path)
                if not action: self._json(404, {"error": "not found"}); return
                result = self.server.app.roadmap_action({**self._body(), "action": action})
                self._json(200, result)
            else: self._json(404, {"error": "not found"})
        except PermissionError as exc: self._json(403, {"error": str(exc)})
        except Exception as exc: self._json(400, {"error": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None: return


class WebTerminalServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], app: WebTerminalApp):
        self.app = app; super().__init__(address, _Handler)


def make_server(host: str, port: int, root: str | Path, theme: str | Path | None = None, config: str | Path | None = None) -> WebTerminalServer:
    try: loopback = ipaddress.ip_address(host).is_loopback
    except ValueError: loopback = host.lower() == "localhost"
    if not loopback: raise PermissionError("Web Terminal aceita somente bind loopback; acesso remoto indisponível")
    return WebTerminalServer((host, port), WebTerminalApp(root, theme, config))


def serve_web(host: str, port: int, root: str | Path, theme: str | Path | None = None, config: str | Path | None = None) -> None:
    server = make_server(host, port, root, theme, config)
    print(f"Web Terminal: http://{host}:{server.server_port}/", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
