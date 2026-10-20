from __future__ import annotations

import html
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .tui import GeneratorTUI, MENU, TABS


HTML = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Konstructor · Web Terminal</title>
<style>
:root{color-scheme:dark;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}body{margin:0;background:#0b1020;color:#d9e2ff}header{padding:14px 18px;border-bottom:1px solid #32405f}main{display:grid;grid-template-columns:210px 1fr;min-height:calc(100vh - 55px)}nav{padding:12px;border-right:1px solid #32405f}button{width:100%;margin:3px 0;padding:8px;border:1px solid #405479;background:#16213b;color:inherit;text-align:left;cursor:pointer}button.sel{background:#12566b;border-color:#5eead4}button:hover{background:#254365}.tabs{display:flex;gap:8px;padding:10px;border-bottom:1px solid #32405f}.tabs button{width:auto}.tabs button.sel{color:#5eead4}.panels{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;padding:8px;height:calc(100vh - 130px);box-sizing:border-box}.panel{border:1px solid #32405f;overflow:auto;padding:10px;white-space:pre-wrap}.panel h3{color:#5eead4;margin-top:0}.hidden{display:none}.add{color:#86efac}.del{color:#fca5a5}.status{padding:8px 12px;border-top:1px solid #32405f;color:#93a4c7}#prompt{width:100%;box-sizing:border-box;background:#0b1020;border:1px solid #405479;color:inherit;padding:8px;margin-top:7px}
</style></head><body><header><strong>KONSTRUCTOR · WEB TERMINAL</strong> <span id="workspace"></span></header>
<main><nav id="menu"></nav><section><div class="tabs" id="tabs"></div><div class="panels"><div class="panel" id="workspace-panel"><h3>WORKSPACE</h3><code></code></div><div class="panel" id="diff-panel"><h3>DIFF / DETALHE</h3><code></code></div><div class="panel" id="logs-panel"><h3>LOGS · LIVE</h3><code></code></div></div><div class="status" id="status"></div><input id="prompt" placeholder="Para plan, generate, validate, diff ou apply: dados JSON opcionais" /></section></main>
<script>
const menu=document.querySelector('#menu'), tabs=document.querySelector('#tabs'), prompt=document.querySelector('#prompt'); let state=null;
function post(path, body){return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})})}
function run(action){let extra={};try{if(prompt.value.trim())extra=JSON.parse(prompt.value)}catch(e){alert('JSON inválido: '+e)}post('/action',{action,...extra})}
function draw(s){state=s;document.querySelector('#workspace').textContent=' · '+s.workspace;
 menu.innerHTML=s.menu.map((x,i)=>`<button class="${i===s.selected?'sel':''}" onclick="run('${x.action}')">${x.label}</button>`).join('');
 tabs.innerHTML=s.tabs.map((x,i)=>`<button class="${x===s.view?'sel':''}" onclick="post('/input',{key:'${i+1}'})">${x.toUpperCase()}</button>`).join('');
 document.querySelector('#workspace-panel code').textContent=s.workspace_lines.join('\n');
 let diff=document.querySelector('#diff-panel code');diff.innerHTML=s.diff_lines.map(x=>`<span class="${x.startsWith('+')?'add':x.startsWith('-')?'del':''}">${esc(x)}</span>`).join('\n');
 document.querySelector('#logs-panel code').textContent=s.logs.join('\n'); document.querySelector('#status').textContent=s.messages.join(' · ');}
function esc(x){return x.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
window.addEventListener('keydown',e=>{if(e.target===prompt)return;let key=e.key==='Tab'?'TAB':e.key==='Enter'?'ENTER':e.key==='PageUp'?'PAGEUP':e.key==='PageDown'?'PAGEDOWN':e.key;post('/input',{key})});
new EventSource('/events').onmessage=e=>draw(JSON.parse(e.data));
</script></body></html>'''


class WebTerminalApp:
    def __init__(self, root: str | Path, theme: str | Path | None = None, config: str | Path | None = None):
        self.controller = GeneratorTUI(Path(root), theme, config)
        self.lock = threading.RLock()
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
        length = int(self.headers.get("Content-Length", "0")); return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            data = HTML.encode("utf-8"); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        elif path == "/state": self._json(200, self.server.app.snapshot())
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
            else: self._json(404, {"error": "not found"})
        except Exception as exc: self._json(400, {"error": str(exc)})

    def log_message(self, fmt: str, *args: Any) -> None: return


class WebTerminalServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], app: WebTerminalApp):
        self.app = app; super().__init__(address, _Handler)


def make_server(host: str, port: int, root: str | Path, theme: str | Path | None = None, config: str | Path | None = None) -> WebTerminalServer:
    return WebTerminalServer((host, port), WebTerminalApp(root, theme, config))


def serve_web(host: str, port: int, root: str | Path, theme: str | Path | None = None, config: str | Path | None = None) -> None:
    server = make_server(host, port, root, theme, config)
    print(f"Web Terminal: http://{host}:{server.server_port}/", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
