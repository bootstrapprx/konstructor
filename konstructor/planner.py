from __future__ import annotations
import hashlib, re, unicodedata
from pathlib import Path
from .models import Plan
from .repository import KodooRepositoryAdapter
from .state import save_json, workspace_dir

def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:40]

def make_plan(root: Path, request: str, catalog: dict) -> Plan:
    request_slug = slugify(request) or "feature"
    pid = hashlib.sha256(f"{root}:{request}".encode()).hexdigest()[:12]
    findings = catalog.get("findings", [])
    terms = set(re.findall(r"[a-z0-9_]+", request.lower()))
    reused = [f for f in findings if f["kind"] in {"module", "symbol"} and any(t in (f["detail"] + f["path"]).lower() for t in terms)][:12]
    facts = [f"workspace descoberto em {root}", "catálogo derivado dos manifests e fontes atuais", "geração será feita fora do workspace ativo"]
    questions = []
    if not reused: questions.append("Qual use case existente deve ser reutilizado, caso o requisito corresponda a uma regra já existente?")
    files = [{"path": f"modules/{request_slug}/Cargo.toml", "action": "add", "reason": "manifest do módulo"}, {"path": f"modules/{request_slug}/src/lib.rs", "action": "add", "reason": "ponto de entrada da capacidade"}, {"path": f"modules/{request_slug}/src/domain.rs", "action": "add", "reason": "regras de domínio sem I/O"}, {"path": f"modules/{request_slug}/src/application.rs", "action": "add", "reason": "caso de uso que orquestra o domínio"}]
    return Plan(pid, str(root), request, f"Implementar: {request}", files, reused, ["Cargo workspace", "Kodoo core (a confirmar)"], ["domain", "application", "CLI/MCP se o módulo exigir"], ["unit tests de domínio", "check estrutural do workspace", "cargo fmt/check se disponíveis"], ["dependências reais do módulo podem exigir revisão", "módulo não deve duplicar regra encontrada"], facts, ["superfícies adicionais dependem do metadata e das convenções do módulo"], questions)

def write_plan(root: Path, plan: Plan, output: str | None = None) -> Path:
    path = Path(output).expanduser() if output else workspace_dir(root) / f"PLAN-{plan.id}.md"
    body = [f"# Plano {plan.id}", "", f"## Objetivo\n{plan.objective}", f"\n## Requisito\n{plan.request}", "\n## Arquivos candidatos"]
    body += [f"- `{x['path']}` — {x['action']}: {x['reason']}" for x in plan.files]
    body += ["\n## Reutilização encontrada"] + ([f"- `{x['path']}` — {x['detail']}" for x in plan.reused] or ["- Nenhuma evidência correspondente; confirmar antes de gerar."])
    for title, values in [("Dependências", plan.dependencies), ("Superfícies", plan.surfaces), ("Testes", plan.tests), ("Riscos", plan.risks), ("Fatos", plan.facts), ("Inferências", plan.inferences), ("Perguntas em aberto", plan.questions)]:
        body += [f"\n## {title}"] + ([f"- {v}" for v in values] or ["- Nenhum item."])
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    save_json(root, f"plan-{plan.id}.json", plan.__dict__)
    return path
