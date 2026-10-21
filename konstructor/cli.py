from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from .repository import KodooRepositoryAdapter
from .planner import make_plan, write_plan
from .workflow import generate, validate, diff, apply, status, stage_edits
from .state import load_json
from .provider import OpenAICompatibleProvider
from .tui import run_tui
from .web_terminal import serve_web
from . import roadmap as roadmap_store

WORKSPACE_DEFAULT=os.environ.get("KONSTRUCTOR_WORKSPACE", ".")
KODOO_DEFAULT=os.environ.get("KONSTRUCTOR_WORKSPACE", "/mnt/toshiba/git/kodoo")

def emit(value, machine=False):
    if machine: print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    elif isinstance(value, str): print(value)
    else: print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))

def main(argv=None):
    p=argparse.ArgumentParser(prog="konstructor", description="Gerador seguro independente para workspaces Kodoo")
    p.add_argument("--json", action="store_true", help="saída estruturada")
    sub=p.add_subparsers(dest="command", required=True)
    for name in ("inspect","index"): 
        q=sub.add_parser(name); q.add_argument("path", nargs="?", default=WORKSPACE_DEFAULT)
    q=sub.add_parser("plan"); q.add_argument("path"); q.add_argument("request"); q.add_argument("--output")
    app=sub.add_parser("app", help="assistente para criação manual de módulo Kodoo"); appsub=app.add_subparsers(dest="app_command", required=True)
    wizard=appsub.add_parser("wizard", help="coletar decisões e salvar proposta de criação"); wizard.add_argument("path", nargs="?", default=KODOO_DEFAULT); wizard.add_argument("--name", help="nome ou slug do módulo"); wizard.add_argument("--summary", help="objetivo do módulo"); wizard.add_argument("--direct", choices=("yes", "no"), help="se terá superfície de produto Dioxus Direct"); wizard.add_argument("--llm", action="store_true"); wizard.add_argument("--apply", action="store_true")
    appsub.add_parser("new-app", help="criar proposta de scaffold para app Dioxus").add_argument("path", nargs="?", default=KODOO_DEFAULT)
    appsub.choices["new-app"].add_argument("--name"); appsub.choices["new-app"].add_argument("--summary")
    for action in ("review", "diff", "validate", "apply"):
        item=appsub.add_parser(action, help=f"{action} app proposal"); item.add_argument("path"); item.add_argument("proposal_id")
    provider=sub.add_parser("provider", help="LLM provider status and structured generation"); provider.add_argument("action", choices=("status", "complete")); provider.add_argument("--prompt"); provider.add_argument("--schema", help="JSON schema inline"); provider.add_argument("--model"); provider.add_argument("--base-url")
    edit=sub.add_parser("edit", help="propor edição de arquivos existentes com LLM"); edit.add_argument("path"); edit.add_argument("request"); edit.add_argument("files", nargs="*"); edit.add_argument("--file", dest="selected_files", action="append", default=[]); edit.add_argument("--model"); edit.add_argument("--base-url")
    roadmap=sub.add_parser("roadmap", help="roadmap dinâmico importado da planilha"); rsub=roadmap.add_subparsers(dest="roadmap_command", required=True)
    imp=rsub.add_parser("import", help="importar planilha XLSX"); imp.add_argument("path", nargs="?", default=KODOO_DEFAULT); imp.add_argument("workbook", nargs="?", default=str(Path(__file__).resolve().parent.parent / "context/kodoo-roadmap.xlsx"))
    dash=rsub.add_parser("dashboard"); dash.add_argument("path", nargs="?", default=KODOO_DEFAULT)
    listing=rsub.add_parser("list"); listing.add_argument("path", nargs="?", default=KODOO_DEFAULT); listing.add_argument("--sheet"); listing.add_argument("--status"); listing.add_argument("--priority")
    show=rsub.add_parser("show"); show.add_argument("path"); show.add_argument("id")
    update=rsub.add_parser("update"); update.add_argument("path"); update.add_argument("id"); update.add_argument("--status"); update.add_argument("--evidence"); update.add_argument("--note")
    exp=rsub.add_parser("export"); exp.add_argument("path",nargs="?",default=KODOO_DEFAULT); exp.add_argument("destination")
    q=sub.add_parser("generate"); q.add_argument("path"); q.add_argument("plan_id")
    for name in ("validate","diff","apply"): 
        q=sub.add_parser(name); q.add_argument("path"); q.add_argument("changeset_id")
    sub.choices["apply"].add_argument("--yes", action="store_true")
    q=sub.add_parser("status"); q.add_argument("path", nargs="?", default=WORKSPACE_DEFAULT)
    q=sub.add_parser("tui", help="abrir interface interativa de terminal"); q.add_argument("path", nargs="?", default=WORKSPACE_DEFAULT); q.add_argument("--theme", help="preset (kodoo-dark, high-contrast, mono) ou caminho JSON"); q.add_argument("--config", help="arquivo JSON de configuração da TUI")
    q=sub.add_parser("web", help="abrir a TUI via Web Terminal HTTP/SSE (opt-in)"); q.add_argument("path", nargs="?", default=WORKSPACE_DEFAULT); q.add_argument("--enable", action="store_true", help="habilita explicitamente o servidor web local"); q.add_argument("--host", default="127.0.0.1"); q.add_argument("--port", type=int, default=8765); q.add_argument("--theme"); q.add_argument("--config", help="arquivo JSON de configuração da TUI")
    q=sub.add_parser("config"); q.add_argument("action", choices=["show"])
    q=sub.add_parser("mcp", help="servidor MCP stdio para Codex"); q.add_argument("--workspace", default=None, help=argparse.SUPPRESS)
    q=sub.add_parser("approve", help="aprova changeset para uso pelo MCP"); q.add_argument("path"); q.add_argument("changeset_id")
    for parser in sub.choices.values():
        parser.add_argument("--json", action="store_true", dest="json_sub", help=argparse.SUPPRESS)
    for parser in rsub.choices.values():
        parser.add_argument("--json", action="store_true", dest="json_sub", help=argparse.SUPPRESS)
    args=p.parse_args(argv)
    args.json = args.json or getattr(args, "json_sub", False)
    try:
        if args.command=="inspect": emit(KodooRepositoryAdapter(args.path).inspect(),p.parse_args(argv).json if False else args.json)
        elif args.command=="index": emit(KodooRepositoryAdapter(args.path).index(),args.json)
        elif args.command=="plan":
            adapter=KodooRepositoryAdapter(args.path); catalog=adapter.index(); plan=make_plan(adapter.root,args.request,catalog); out=write_plan(adapter.root,plan,args.output); emit({"plan_id":plan.id,"path":str(out),"questions":plan.questions},args.json)
        elif args.command=="app":
            from .wizard import create_app_proposal, create_module_proposal, load_proposal, validate_proposal, apply_app_proposal
            if args.app_command == "wizard":
                proposal=create_module_proposal(args.path, args.name, args.summary, args.direct, interactive=sys.stdin.isatty())
                if args.llm:
                    schema={"type":"object","properties":{"summary":{"type":"string"},"questions":{"type":"array","items":{"type":"string"}}},"required":["summary","questions"],"additionalProperties":False}
                    enrichment=OpenAICompatibleProvider().complete_structured(f"Refine this Kodoo module proposal concisely: {proposal['summary']}",schema)
                    proposal["summary"]=enrichment["summary"]; proposal["open_questions"]=enrichment["questions"]
                from .state import save_json
                save_json(Path(proposal["workspace"]), f"{proposal['proposal_id']}.json", proposal)
                proposal["validation"]=validate_proposal(proposal)
                from .state import save_json
                save_json(Path(proposal["workspace"]), f"{proposal['proposal_id']}.json", proposal)
                if args.apply:
                    if not proposal["validation"]["passed"]: raise RuntimeError("proposta inválida ou stale; crie uma nova proposta")
                    if not sys.stdin.isatty(): raise PermissionError("scaffold apply exige terminal interativo")
                    approval=input(f"Executar {proposal['scaffold_command']}? Digite APPLY: ")
                    if approval != "APPLY": raise PermissionError("aprovação não confirmada")
                    import subprocess
                    run=subprocess.run(["python3","scripts/standard.py","new",proposal["name"]],cwd=proposal["workspace"],text=True,capture_output=True,timeout=120)
                    if run.returncode: raise RuntimeError(run.stderr.strip() or run.stdout.strip() or "scaffold falhou")
                    proposal["applied"]=True
                    save_json(Path(proposal["workspace"]), f"{proposal['proposal_id']}.json", proposal)
            elif args.app_command == "new-app": proposal=create_app_proposal(args.path, args.name, args.summary, interactive=sys.stdin.isatty())
            else:
                proposal=load_proposal(args.path,args.proposal_id)
                proposal["validation"]=validate_proposal(proposal)
                if args.app_command == "diff":
                    emit({"diff": proposal.get("diff", "")}, args.json); return 0
                if args.app_command == "validate":
                    emit(proposal["validation"], args.json); return 0
                if args.app_command == "apply":
                    if not proposal["validation"]["passed"]: raise RuntimeError("proposta inválida ou stale")
                    if not sys.stdin.isatty(): raise PermissionError("app apply exige aprovação interativa")
                    print(proposal.get("diff", ""))
                    if input("Aplicar exatamente este diff? Digite APPLY: ") != "APPLY": raise PermissionError("aprovação não confirmada")
                    emit({"applied": apply_app_proposal(proposal, True)}, args.json); return 0
                emit(proposal,args.json)
                return 0
            emit(proposal,args.json)
        elif args.command=="provider":
            llm=OpenAICompatibleProvider(args.base_url,args.model)
            if args.action == "status": emit(llm.health_check(), args.json)
            else:
                if not args.prompt or not args.schema: raise ValueError("complete exige --prompt e --schema")
                emit(llm.complete_structured(args.prompt, json.loads(args.schema)), args.json)
        elif args.command=="edit":
            provider=OpenAICompatibleProvider(args.base_url,args.model)
            selected=args.files+args.selected_files
            if not selected: raise ValueError("informe arquivos como argumentos ou com --file")
            cs=stage_edits(Path(args.path),args.request,selected,provider)
            emit({"changeset_id":cs.id,"staging":cs.staging,"files":cs.files,"next":"review diff, validate, then apply --yes"},args.json)
        elif args.command=="roadmap":
            if args.roadmap_command == "import": result=roadmap_store.import_workbook(args.path,args.workbook)
            elif args.roadmap_command == "dashboard": result=roadmap_store.dashboard(args.path)
            elif args.roadmap_command == "list": result=roadmap_store.list_records(args.path,args.sheet,args.status,args.priority)
            elif args.roadmap_command == "show": result=roadmap_store.show_record(args.path,args.id)
            elif args.roadmap_command == "export": result={"path":str(roadmap_store.export_json(args.path,args.destination))}
            else:
                if not args.status and not args.evidence and not args.note: raise ValueError("informe --status, --evidence ou --note")
                result=roadmap_store.update_record(args.path,args.id,args.status,args.evidence,args.note)
            emit(result,args.json)
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
        elif args.command=="config": emit({"home":str(__import__('konstructor.state',fromlist=['home']).home()),"provider":"openai-compatible" if __import__('os').environ.get("KONSTRUCTOR_LLM_MODEL") else "deterministic-local","policy":"approval-required","llm_base_url":__import__('os').environ.get("KONSTRUCTOR_LLM_BASE_URL", "https://api.openai.com/v1"),"llm_model_configured":bool(__import__('os').environ.get("KONSTRUCTOR_LLM_MODEL")),"llm_api_key_configured":bool(__import__('os').environ.get("KONSTRUCTOR_LLM_API_KEY"))},args.json)
        elif args.command=="mcp":
            from .mcp_server import serve
            import os
            workspace=args.workspace or os.environ.get("KONSTRUCTOR_WORKSPACE")
            if not workspace: raise ValueError("defina KONSTRUCTOR_WORKSPACE para fixar o workspace do servidor")
            serve(workspace)
        elif args.command=="approve":
            if not sys.stdin.isatty(): raise PermissionError("aprovação MCP exige terminal interativo")
            from .approvals import approve_changeset
            print(diff(args.path,args.changeset_id))
            token=approve_changeset(Path(args.path),args.changeset_id,input(f"Digite APPLY {args.changeset_id}: "))
            print(f"Token de uso único (válido por 5 minutos): {token}")
        return 0
    except (OSError, ValueError, PermissionError, RuntimeError) as exc:
        print(f"konstructor: erro: {exc}",file=sys.stderr); return 2
