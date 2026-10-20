from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from .repository import KodooRepositoryAdapter
from .planner import make_plan, write_plan
from .workflow import generate, validate, diff, apply, status
from .state import load_json
from .tui import run_tui
from .web_terminal import serve_web

def emit(value, machine=False):
    if machine: print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    elif isinstance(value, str): print(value)
    else: print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))

def main(argv=None):
    p=argparse.ArgumentParser(prog="konstructor", description="Gerador seguro independente para workspaces Kodoo")
    p.add_argument("--json", action="store_true", help="saída estruturada")
    sub=p.add_subparsers(dest="command", required=True)
    for name in ("inspect","index"): 
        q=sub.add_parser(name); q.add_argument("path", nargs="?", default=".")
    q=sub.add_parser("plan"); q.add_argument("path"); q.add_argument("request"); q.add_argument("--output")
    app=sub.add_parser("app", help="assistente para criação manual de módulo Kodoo"); appsub=app.add_subparsers(dest="app_command", required=True)
    wizard=appsub.add_parser("wizard", help="coletar decisões e salvar proposta de criação"); wizard.add_argument("path", nargs="?", default="/mnt/toshiba/git/kodoo"); wizard.add_argument("--name", help="nome ou slug do módulo"); wizard.add_argument("--summary", help="objetivo do módulo"); wizard.add_argument("--direct", choices=("yes", "no"), help="se terá superfície de produto Dioxus Direct")
    q=sub.add_parser("generate"); q.add_argument("path"); q.add_argument("plan_id")
    for name in ("validate","diff","apply"): 
        q=sub.add_parser(name); q.add_argument("path"); q.add_argument("changeset_id")
    sub.choices["apply"].add_argument("--yes", action="store_true")
    q=sub.add_parser("status"); q.add_argument("path", nargs="?", default=".")
    q=sub.add_parser("tui", help="abrir interface interativa de terminal"); q.add_argument("path", nargs="?", default="."); q.add_argument("--theme", help="preset (kodoo-dark, high-contrast, mono) ou caminho JSON"); q.add_argument("--config", help="arquivo JSON de configuração da TUI")
    q=sub.add_parser("web", help="abrir a TUI via Web Terminal HTTP/SSE (opt-in)"); q.add_argument("path", nargs="?", default="."); q.add_argument("--enable", action="store_true", help="habilita explicitamente o servidor web local"); q.add_argument("--host", default="127.0.0.1"); q.add_argument("--port", type=int, default=8765); q.add_argument("--theme"); q.add_argument("--config", help="arquivo JSON de configuração da TUI")
    q=sub.add_parser("config"); q.add_argument("action", choices=["show"])
    args=p.parse_args(argv)
    try:
        if args.command=="inspect": emit(KodooRepositoryAdapter(args.path).inspect(),p.parse_args(argv).json if False else args.json)
        elif args.command=="index": emit(KodooRepositoryAdapter(args.path).index(),args.json)
        elif args.command=="plan":
            adapter=KodooRepositoryAdapter(args.path); catalog=adapter.index(); plan=make_plan(adapter.root,args.request,catalog); out=write_plan(adapter.root,plan,args.output); emit({"plan_id":plan.id,"path":str(out),"questions":plan.questions},args.json)
        elif args.command=="app":
            from .wizard import create_module_proposal
            proposal=create_module_proposal(args.path, args.name, args.summary, args.direct, interactive=sys.stdin.isatty())
            emit(proposal,args.json)
        elif args.command=="generate":
            cs=generate(Path(args.path),args.plan_id); emit({"changeset_id":cs.id,"staging":cs.staging,"files":cs.files},args.json)
        elif args.command=="validate": emit(validate(Path(args.path),args.changeset_id),args.json)
        elif args.command=="diff": emit(diff(Path(args.path),args.changeset_id))
        elif args.command=="apply": emit({"applied":apply(Path(args.path),args.changeset_id,args.yes)},args.json)
        elif args.command=="status": emit(status(Path(args.path)),args.json)
        elif args.command=="tui": run_tui(args.path, args.theme, args.config)
        elif args.command=="web":
            if not args.enable: raise PermissionError("Web Terminal desabilitado; passe --enable para habilitar acesso local")
            serve_web(args.host, args.port, args.path, args.theme, args.config)
        elif args.command=="config": emit({"home":str(__import__('konstructor.state',fromlist=['home']).home()),"provider":"deterministic-local","policy":"approval-required"},args.json)
        return 0
    except (OSError, ValueError, PermissionError, RuntimeError) as exc:
        print(f"konstructor: erro: {exc}",file=sys.stderr); return 2
