from __future__ import annotations
import difflib, hashlib, json, shutil, subprocess, tempfile, os
from pathlib import Path
from .models import ChangeSet, CheckResult
from .planner import slugify
from .repository import KodooRepositoryAdapter
from .safety import SafetyError, ensure_root, file_hash, safe_relative
from .state import home, load_json, save_json, workspace_dir

def _contents(plan: dict) -> dict[str, str]:
    slug = Path(plan["files"][0]["path"]).parts[1]
    return {
        f"modules/{slug}/Cargo.toml": f'''[package]\nname = "{slug}"\nversion.workspace = true\nedition.workspace = true\n\n[package.metadata.kodoo]\ndirect = false\ndirect_reason = "Gerado como proposta inicial; superfície de produto pendente de decisão."\n\n[lib]\npath = "src/lib.rs"\n''',
        f"modules/{slug}/src/lib.rs": f'''//! {slug} — proposta gerada em staging.\n//! Regra: superfícies devem delegar aos casos de uso, sem duplicar domínio.\n\npub mod application;\npub mod domain;\n''',
        f"modules/{slug}/src/domain.rs": '''//! Regras puras do domínio; sem persistência ou transporte.\n\n#[derive(Debug, Clone, PartialEq, Eq)]\npub struct Requirement {\n    pub description: String,\n}\n\nimpl Requirement {\n    pub fn new(description: impl Into<String>) -> Self {\n        Self { description: description.into() }\n    }\n}\n\n#[cfg(test)]\nmod tests {\n    use super::*;\n    #[test]\n    fn preserves_requirement_text() {\n        assert_eq!(Requirement::new("x").description, "x");\n    }\n}\n''',
        f"modules/{slug}/src/application.rs": '''//! Casos de uso; views e CLI devem chamar esta camada.\n\nuse super::domain::Requirement;\n\npub fn prepare(description: impl Into<String>) -> Requirement {\n    Requirement::new(description)\n}\n''',
    }

def generate(root: Path, plan_id: str) -> ChangeSet:
    root = ensure_root(root); plan = load_json(root, f"plan-{plan_id}.json")
    cid = hashlib.sha256(f"{plan_id}:{root}".encode()).hexdigest()[:12]
    staging = workspace_dir(root) / "staging" / cid
    if staging.exists(): shutil.rmtree(staging)
    staging.mkdir(parents=True)
    contents = _contents(plan); files = []
    for rel, content in contents.items():
        safe_relative(root, rel)
        out = staging / rel; out.parent.mkdir(parents=True, exist_ok=True); out.write_text(content, encoding="utf-8")
        files.append({"path": rel, "action": "add", "reason": next((x["reason"] for x in plan["files"] if x["path"] == rel), "arquivo da proposta")})
    hashes = {rel: file_hash(staging / rel) for rel in contents}
    cs = ChangeSet(cid, str(root), plan_id, str(staging), files, {rel: file_hash(root / rel) for rel in contents})
    save_json(root, f"changeset-{cid}.json", {**cs.__dict__, "artifact_hashes": hashes, "checks": []})
    return cs


def stage_edits(root: Path, request: str, files: list[str], provider) -> ChangeSet:
    """Ask a provider to edit only explicitly selected existing files."""
    root = ensure_root(root)
    if not files: raise ValueError("selecione ao menos um arquivo para editar")
    sources = {}
    for rel in files:
        target = safe_relative(root, rel)
        if not target.is_file(): raise ValueError(f"arquivo para edição não existe: {rel}")
        sources[rel] = target.read_text(encoding="utf-8")
    schema = {"type":"object", "properties":{"files":{"type":"array", "items":{"type":"object", "properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"],"additionalProperties":False}}}, "required":["files"], "additionalProperties":False}
    output = provider.complete_structured(json.dumps({"request": request, "source_files": sources}, ensure_ascii=False), schema)
    changes = output["files"]
    if not changes: raise RuntimeError("provider não retornou alterações")
    contents = {}
    for change in changes:
        rel = change["path"]
        if rel not in sources: raise SafetyError(f"provider tentou editar arquivo não selecionado: {rel}")
        if rel in contents: raise ValueError(f"provider retornou arquivo duplicado: {rel}")
        contents[rel] = change["content"]
    if set(contents) != set(sources): raise ValueError("provider deve retornar exatamente os arquivos selecionados")
    plan_id = hashlib.sha256(f"{root}:{request}:{','.join(files)}".encode()).hexdigest()[:12]
    cid = hashlib.sha256(f"edit:{plan_id}".encode()).hexdigest()[:12]
    staging = workspace_dir(root) / "staging" / cid
    if staging.exists(): shutil.rmtree(staging)
    staging.mkdir(parents=True)
    items = []
    for rel, content in contents.items():
        out = staging / rel; out.parent.mkdir(parents=True, exist_ok=True); out.write_text(content, encoding="utf-8")
        items.append({"path": rel, "action": "modify", "reason": request})
    hashes = {rel:file_hash(staging / rel) for rel in contents}
    base = {rel:file_hash(safe_relative(root, rel)) for rel in contents}
    cs = {"id":cid,"workspace":str(root),"plan_id":plan_id,"staging":str(staging),"files":items,"base_hashes":base,"artifact_hashes":hashes,"checks":[]}
    save_json(root, f"changeset-{cid}.json", cs)
    return ChangeSet(**{k:v for k,v in cs.items() if k in ChangeSet.__dataclass_fields__})

def stage_artifacts(root: Path, request: str, artifacts: list[dict]) -> ChangeSet:
    """Stage caller-authored file contents without invoking an LLM or touching the target."""
    root = ensure_root(root)
    if not artifacts: raise ValueError("changeset vazio")
    cid = hashlib.sha256(f"mcp:{root}:{request}:{json.dumps(artifacts,sort_keys=True)}".encode()).hexdigest()[:12]
    staging = workspace_dir(root) / "staging" / cid
    if staging.exists(): shutil.rmtree(staging)
    staging.mkdir(parents=True)
    files, base, hashes, seen = [], {}, {}, set()
    for item in artifacts:
        rel, action, content = item["path"], item["action"], item["content"]
        target = safe_relative(root, rel)
        if rel in seen: raise ValueError(f"path duplicado: {rel}")
        seen.add(rel)
        if action not in {"add", "modify"}: raise ValueError(f"ação inválida: {action}")
        if action == "add" and target.exists(): raise ValueError(f"destino de add já existe: {rel}")
        if action == "modify" and not target.is_file(): raise ValueError(f"arquivo para edição não existe: {rel}")
        out = staging / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        files.append({"path": rel, "action": action, "reason": request})
        base[rel] = file_hash(target)
        hashes[rel] = file_hash(out)
    save_json(root, f"changeset-{cid}.json", {"id":cid,"workspace":str(root),"plan_id":cid,"staging":str(staging),"files":files,"base_hashes":base,"artifact_hashes":hashes,"checks":[]})
    return ChangeSet(cid, str(root), cid, str(staging), files, base)

def load_changeset(root: Path, cid: str) -> dict:
    return load_json(ensure_root(root), f"changeset-{cid}.json")

def diff(root: Path, cid: str) -> str:
    root = ensure_root(root); cs = load_changeset(root, cid); result=[]
    for item in cs["files"]:
        rel=item["path"]; old=(root/rel).read_text(encoding="utf-8") if (root/rel).exists() else ""; new=(Path(cs["staging"])/rel).read_text(encoding="utf-8")
        result.extend(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    return "".join(result)

def validate(root: Path, cid: str) -> dict:
    root=ensure_root(root); cs=load_changeset(root,cid); checks=[]
    for item in cs["files"]:
        try: safe_relative(root,item["path"]); status="passed"; summary="path dentro da raiz"
        except SafetyError as e: status="failed"; summary=str(e)
        checks.append(CheckResult("path-policy", status, summary=summary).__dict__)
        staged=Path(cs["staging"])/item["path"]
        checks.append(CheckResult("staging-file", "passed" if staged.is_file() else "failed", summary="arquivo presente em staging").__dict__)
    cargo=shutil.which("cargo")
    is_cargo_workspace = (root / "Cargo.toml").is_file() and "[workspace]" in (root / "Cargo.toml").read_text(encoding="utf-8", errors="replace")
    if cargo and is_cargo_workspace:
        try:
            with tempfile.TemporaryDirectory(prefix="konstructor-validate-", dir=os.environ.get("KONSTRUCTOR_TMPDIR")) as tmp:
                isolated = Path(tmp) / "workspace"
                shutil.copytree(root, isolated, ignore=shutil.ignore_patterns(".git", "target", ".native", "node_modules", ".venv", "__pycache__"))
                for item in cs["files"]:
                    rel = item["path"]
                    staged = Path(cs["staging"]) / rel
                    target = safe_relative(isolated, rel)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(staged, target)
                timeout = float(os.environ.get("KONSTRUCTOR_VALIDATE_TIMEOUT", "300"))
                p=subprocess.run([cargo,"fmt","--all","--","--check"],cwd=isolated,text=True,capture_output=True,timeout=timeout,env={**os.environ,"CARGO_TARGET_DIR":str(Path(tmp)/"target"),"CARGO_NET_OFFLINE":"true"})
                checks.append(CheckResult("cargo-fmt", "passed" if p.returncode==0 else "failed", "cargo fmt --all -- --check (isolated)", p.stdout[-1000:]+p.stderr[-1000:], p.returncode).__dict__)
        except subprocess.TimeoutExpired as exc:
            checks.append(CheckResult("cargo-fmt", "blocked", "cargo fmt --all -- --check (isolated)", f"timeout: {(exc.stderr or b'').decode(errors='replace')[-500:]}").__dict__)
        except OSError as exc:
            checks.append(CheckResult("cargo-fmt", "blocked", "cargo fmt --all -- --check (isolated)", str(exc)).__dict__)
    else: checks.append(CheckResult("cargo-fmt", "blocked", "cargo fmt --all -- --check", "workspace Cargo ou cargo indisponível").__dict__)
    cs["checks"]=checks
    cs["validated_artifact_hashes"] = {item["path"]: file_hash(Path(cs["staging"]) / item["path"]) for item in cs["files"]}
    cs["validated_base_hashes"] = {rel: file_hash(safe_relative(root, rel)) for rel in cs["base_hashes"]}
    cs["validation_passed"] = all(c["status"] == "passed" for c in checks)
    save_json(root,f"changeset-{cid}.json",cs); return cs

def apply(root: Path, cid: str, yes: bool=False) -> list[str]:
    root=ensure_root(root); cs=load_changeset(root,cid)
    if not yes: raise PermissionError("aplicação exige aprovação explícita: use --yes após revisar diff")
    if not cs.get("validation_passed") or not cs.get("checks"):
        raise RuntimeError("aplicação bloqueada: validação aprovada ausente ou falhou")
    if cs.get("validated_artifact_hashes") != cs.get("artifact_hashes"):
        raise RuntimeError("aplicação bloqueada: artefatos staged mudaram desde a validação")
    if cs.get("validated_base_hashes") != cs.get("base_hashes"):
        raise RuntimeError("aplicação bloqueada: alvo mudou desde a validação")
    if {item["path"]: file_hash(Path(cs["staging"]) / item["path"]) for item in cs["files"]} != cs["artifact_hashes"]:
        raise RuntimeError("aplicação bloqueada: artefatos staged adulterados")
    for rel, expected in cs["base_hashes"].items():
        if file_hash(safe_relative(root,rel)) != expected: raise RuntimeError(f"conflito: arquivo mudou desde a revisão: {rel}")
    applied=[]; backups={}
    try:
        for item in cs["files"]:
            target=safe_relative(root,item["path"]); source=Path(cs["staging"])/item["path"]
            if item["action"] == "add" and target.exists(): raise RuntimeError(f"conflito: destino de add já existe: {item['path']}")
            if item["action"] == "modify" and not target.is_file(): raise RuntimeError(f"conflito: destino de modify ausente: {item['path']}")
            if item["action"] == "modify": backups[item["path"]] = target.read_bytes()
            target.parent.mkdir(parents=True,exist_ok=True)
            tmp=target.with_name(target.name+".konstructor-tmp"); shutil.copyfile(source,tmp); tmp.replace(target); applied.append(item["path"])
    except Exception:
        for rel in reversed(applied):
            target=safe_relative(root, rel)
            if rel in backups: target.write_bytes(backups[rel])
            else: target.unlink(missing_ok=True)
        raise
    cs["approved"]=True; cs["applied"]=True; save_json(root,f"changeset-{cid}.json",cs); return applied

def status(root: Path) -> dict:
    root=ensure_root(root); directory=workspace_dir(root); return {"workspace":str(root),"state":str(directory),"plans":sorted(p.name for p in directory.glob("plan-*.json")),"changesets":sorted(p.name for p in directory.glob("changeset-*.json"))}
