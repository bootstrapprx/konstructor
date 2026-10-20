from __future__ import annotations
import difflib, hashlib, json, shutil, subprocess, tempfile
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
    cs = ChangeSet(cid, str(root), plan_id, str(staging), files, {rel: file_hash(root / rel) for rel in contents})
    save_json(root, f"changeset-{cid}.json", {**cs.__dict__, "checks": []})
    return cs

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
    if cargo:
        p=subprocess.run([cargo,"fmt","--all","--","--check"],cwd=root,text=True,capture_output=True)
        checks.append(CheckResult("cargo-fmt", "passed" if p.returncode==0 else "failed", "cargo fmt --all -- --check", p.stdout[-1000:]+p.stderr[-1000:], p.returncode).__dict__)
    else: checks.append(CheckResult("cargo-fmt", "blocked", "cargo fmt --all -- --check", "cargo não disponível no ambiente").__dict__)
    cs["checks"]=checks; save_json(root,f"changeset-{cid}.json",cs); return cs

def apply(root: Path, cid: str, yes: bool=False) -> list[str]:
    root=ensure_root(root); cs=load_changeset(root,cid)
    if not yes: raise PermissionError("aplicação exige aprovação explícita: use --yes após revisar diff")
    for rel, expected in cs["base_hashes"].items():
        if file_hash(safe_relative(root,rel)) != expected: raise RuntimeError(f"conflito: arquivo mudou desde a revisão: {rel}")
    applied=[]
    for item in cs["files"]:
        target=safe_relative(root,item["path"]); source=Path(cs["staging"])/item["path"]; target.parent.mkdir(parents=True,exist_ok=True)
        tmp=target.with_name(target.name+".kodoo-tmp"); shutil.copyfile(source,tmp); tmp.replace(target); applied.append(item["path"])
    cs["approved"]=True; cs["applied"]=True; save_json(root,f"changeset-{cid}.json",cs); return applied

def status(root: Path) -> dict:
    root=ensure_root(root); directory=workspace_dir(root); return {"workspace":str(root),"state":str(directory),"plans":sorted(p.name for p in directory.glob("plan-*.json")),"changesets":sorted(p.name for p in directory.glob("changeset-*.json"))}
