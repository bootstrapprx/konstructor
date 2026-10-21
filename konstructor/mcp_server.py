from __future__ import annotations
import json, os, sys
from pathlib import Path
from .safety import ensure_root
from .repository import KodooRepositoryAdapter
from .planner import make_plan, write_plan
from .workflow import stage_artifacts, diff, validate
from .approvals import apply_approved
from .state import workspace_dir
from . import roadmap

LIMIT=2*1024*1024

class MCPServer:
    def __init__(self, root): self.root=ensure_root(root)
    def tools(self):
        defs=[("konstructor_workspace_inspect",{},"Inspeciona workspace Kodoo",True),("konstructor_repository_index",{},"Indexa código e módulos",True),
        ("konstructor_plan_create",{"request":"string"},"Cria plano a partir de requisito",False),
        ("konstructor_app_proposal",{"kind":"string","name":"string","summary":"string","direct":"string"},"Cria proposta de módulo ou app",False),
        ("konstructor_changeset_stage",{"request":"string","artifacts":"array"},"Estagia conteúdo fornecido pelo cliente",False),
        ("konstructor_changeset_diff",{"changeset_id":"string"},"Mostra diff",True),("konstructor_changeset_validate",{"changeset_id":"string"},"Valida changeset",True),
        ("konstructor_changeset_apply",{"changeset_id":"string","approval_token":"string"},"Aplica changeset aprovado no terminal",False),
        ("konstructor_roadmap_dashboard",{},"Resumo do roadmap",True),("konstructor_roadmap_list",{"sheet":"string","status":"string","priority":"string"},"Lista itens do roadmap",True),
        ("konstructor_roadmap_get",{"id":"string"},"Mostra item do roadmap",True),("konstructor_roadmap_update",{"id":"string","status":"string","evidence":"string","note":"string"},"Atualiza estado do roadmap",False),
        ("konstructor_roadmap_import",{"workbook":"string"},"Importa planilha XLSX ou seed JSON",False),("konstructor_roadmap_seed",{},"Carrega seed inicial do Konstructor",False),("konstructor_roadmap_export",{"filename":"string"},"Exporta backup JSON para estado local",False)]
        return [{"name":n,"description":d,"inputSchema":{"type":"object","properties":{k:{"type":v} for k,v in props.items()},"required":[k for k in props if k in {"request","changeset_id","approval_token","id","workbook"}],"additionalProperties":False},"annotations":{"readOnlyHint":ro,"destructiveHint":not ro}} for n,props,d,ro in defs]
    def call(self,name,a):
        if name=="konstructor_workspace_inspect": return KodooRepositoryAdapter(self.root).inspect()
        if name=="konstructor_repository_index": return KodooRepositoryAdapter(self.root).index()
        if name=="konstructor_plan_create":
            plan=make_plan(self.root,a["request"],KodooRepositoryAdapter(self.root).index()); out=write_plan(self.root,plan); return {"plan_id":plan.id,"path":str(out),"questions":plan.questions}
        if name=="konstructor_app_proposal":
            from .wizard import create_app_proposal,create_module_proposal
            if a["kind"]=="module": return create_module_proposal(self.root,a.get("name"),a.get("summary"),a.get("direct"),False)
            if a["kind"]=="dioxus-app": return create_app_proposal(self.root,a.get("name"),a.get("summary"),False)
            raise ValueError("kind deve ser module ou dioxus-app")
        if name=="konstructor_changeset_stage":
            cs=stage_artifacts(self.root,a["request"],a["artifacts"]); return {"changeset_id":cs.id,"files":cs.files,"staging":cs.staging}
        if name=="konstructor_changeset_diff": return {"diff":diff(self.root,a["changeset_id"])}
        if name=="konstructor_changeset_validate": return validate(self.root,a["changeset_id"])
        if name=="konstructor_changeset_apply": return {"applied":apply_approved(self.root,a["changeset_id"],a["approval_token"])}
        if name=="konstructor_roadmap_dashboard": return roadmap.dashboard(self.root)
        if name=="konstructor_roadmap_list": return roadmap.list_records(self.root,a.get("sheet"),a.get("status"),a.get("priority"))
        if name=="konstructor_roadmap_get": return roadmap.show_record(self.root,a["id"])
        if name=="konstructor_roadmap_update": return roadmap.update_record(self.root,a["id"],a.get("status"),a.get("evidence"),a.get("note"))
        if name=="konstructor_roadmap_import":
            importer=roadmap.import_json if Path(a["workbook"]).suffix.lower()==".json" else roadmap.import_workbook
            return importer(self.root,a["workbook"])
        if name=="konstructor_roadmap_seed": return roadmap.import_json(self.root,Path(__file__).with_name("roadmap_seed.json"))
        if name=="konstructor_roadmap_export":
            filename=a.get("filename","roadmap-backup.json")
            if Path(filename).name!=filename: raise ValueError("filename deve ser nome simples")
            return {"path":str(roadmap.export_json(self.root,workspace_dir(self.root)/filename))}
        raise KeyError(f"ferramenta desconhecida: {name}")
    def handle(self,msg):
        mid=msg.get("id")
        try:
            method=msg.get("method")
            if method=="notifications/initialized": return None
            if method=="initialize": result={"protocolVersion":"2025-03-26","capabilities":{"tools":{}},"serverInfo":{"name":"konstructor","version":"0.1.0"}}
            elif method=="ping": result={}
            elif method=="tools/list": result={"tools":self.tools()}
            elif method=="tools/call":
                params=msg.get("params",{}); name=params.get("name"); args=params.get("arguments",{})
                schema=next((t for t in self.tools() if t["name"]==name),None)
                if not schema: raise KeyError(f"ferramenta desconhecida: {name}")
                props=schema["inputSchema"]["properties"]
                if not isinstance(args,dict) or set(args)-set(props) or any(k not in args for k in schema["inputSchema"]["required"]): raise ValueError("argumentos inválidos")
                for k,v in args.items():
                    if not isinstance(v,dict if props[k]["type"]=="object" else list if props[k]["type"]=="array" else str): raise ValueError(f"argumento inválido: {k}")
                value=self.call(name,args); return {"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":json.dumps(value,ensure_ascii=False)}],"structuredContent":value if isinstance(value,dict) else {"items":value}}}
            else: return {"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":"método desconhecido"}}
            return {"jsonrpc":"2.0","id":mid,"result":result} if mid is not None else None
        except KeyError as e: error={"code":-32602,"message":str(e)}
        except Exception as e: error={"code":-32000,"message":str(e)}
        return {"jsonrpc":"2.0","id":mid,"error":error}

def serve(root, inp=None, out=None, err=None):
    inp=inp or sys.stdin; out=out or sys.stdout; err=err or sys.stderr; server=MCPServer(root)
    while True:
        line=inp.readline(LIMIT+1)
        if not line: break
        if len(line)>LIMIT and not line.endswith("\n"):
            while line and not line.endswith("\n"): line=inp.readline(LIMIT+1)
            response={"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"mensagem excede 2 MiB"}}
        else:
            try: response=server.handle(json.loads(line))
            except (ValueError,TypeError): response={"jsonrpc":"2.0","id":None,"error":{"code":-32700,"message":"JSON inválido"}}
        if response is not None: out.write(json.dumps(response,ensure_ascii=False)+"\n"); out.flush()

def main():
    root=os.environ.get("KONSTRUCTOR_WORKSPACE")
    if not root: raise ValueError("defina KONSTRUCTOR_WORKSPACE antes de iniciar o servidor MCP")
    serve(root)
