from __future__ import annotations

import re
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath

from .safety import SafetyError, ensure_root, safe_relative
from .state import save_json, load_json, workspace_dir
from .safety import file_hash


def _ask(label: str, default: str | None = None) -> str:
    prompt = f"{label}" + (f" [{default}]" if default else "") + ": "
    value = input(prompt).strip()
    return value or (default or "")


def create_module_proposal(root: str | Path, name: str | None, summary: str | None,
                           direct: str | None, interactive: bool = False) -> dict:
    root = ensure_root(root)
    if not (root / "scripts/standard.py").is_file() or not (root / "modules/_template").is_dir():
        raise ValueError(f"alvo não contém o scaffold Kodoo esperado: {root}")
    if interactive:
        print(f"Konstructor · wizard de módulo manual\nAlvo: {root}")
        name = name or _ask("Nome do módulo (slug)")
        summary = summary or _ask("O que o módulo fará")
        direct = direct or _ask("Incluir produto Dioxus Direct? (yes/no)", "yes").lower()
    name = (name or "").strip().strip("/")
    summary = (summary or "").strip()
    if not name or any(not re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", part) for part in name.split("/")):
        raise ValueError("informe slug em minúsculas, com hífens; subgrupos podem usar /")
    if not summary:
        raise ValueError("informe o objetivo do módulo")
    if direct not in {"yes", "no"}:
        raise ValueError("escolha --direct yes ou --direct no")
    relative = f"modules/{name}"
    if (root / relative).exists():
        raise ValueError(f"destino já existe: {relative}")
    proposal = {
        "kind": "kodoo-module", "workspace": str(root), "name": name,
        "path": relative, "summary": summary, "direct": direct == "yes",
        "template": "modules/_template", "scaffold_command": f"python3 scripts/standard.py new {name}",
        "writes_target": False,
        "base_hash": file_hash(root / "Cargo.toml"),
    }
    proposal_path = save_json(root, f"module-proposal-{name.replace('/', '-')}.json", proposal)
    proposal["proposal_id"] = proposal_path.stem
    proposal["validation"] = {"destination_absent": True, "template_present": True,
                              "workspace_manifest_hash": proposal["base_hash"]}
    save_json(root, f"{proposal_path.stem}.json", proposal)
    return proposal


def create_app_proposal(root: str | Path, name: str | None, summary: str | None,
                        interactive: bool = False) -> dict:
    root = ensure_root(root)
    if not (root / "Cargo.toml").is_file() or not (root / "apps").is_dir():
        raise ValueError(f"alvo não parece workspace Kodoo com diretório apps: {root}")
    try:
        import tomllib
        cargo = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Cargo.toml inválido") from exc
    if not isinstance(cargo.get("workspace", {}).get("members"), list):
        raise ValueError("Cargo.toml precisa declarar [workspace].members como lista")
    if interactive:
        print(f"Konstructor · wizard de app\nAlvo: {root}")
        name = name or _ask("Nome do app (slug)")
        summary = summary or _ask("Objetivo e usuários do app")
    name = (name or "").strip()
    summary = (summary or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", name):
        raise ValueError("informe slug em minúsculas, com hífens")
    if not summary:
        raise ValueError("informe o objetivo do app")
    if (root / "apps" / name).exists():
        raise ValueError(f"destino já existe: apps/{name}")
    proposal_id = hashlib.sha256(f"{root}:app:{name}:{summary}".encode()).hexdigest()[:12]
    proposal = {"kind": "dioxus-app", "workspace": str(root), "name": name,
                "path": f"apps/{name}", "summary": summary, "writes_target": False,
                "status": "proposed", "template_version": 1,
                "base_hash": file_hash(root / "Cargo.toml"), "proposal_id": f"app-proposal-{name}"}
    proposal["artifacts"] = _app_artifacts(name, summary)
    proposal["diff"] = _proposal_diff(root, proposal)
    proposal_path = save_json(root, f"app-proposal-{name}.json", proposal)
    return proposal


def load_proposal(root: str | Path, proposal_id: str) -> dict:
    root = ensure_root(root)
    if not re.fullmatch(r"(?:module|app)-proposal-[a-z0-9-]+", proposal_id):
        raise ValueError("proposal ID inválido")
    proposal = load_json(root, f"{proposal_id}.json")
    if proposal.get("workspace") != str(root):
        raise ValueError("proposta pertence a outro workspace")
    return proposal


def validate_proposal(proposal: dict) -> dict:
    root = ensure_root(proposal["workspace"])
    manifest = root / "Cargo.toml"
    current_hash = file_hash(manifest)
    target = root / proposal["path"]
    result = {"workspace_unchanged": current_hash == proposal.get("base_hash"),
              "destination_absent": not target.exists()}
    if proposal["kind"] == "kodoo-module":
        result["template_present"] = (root / "modules/_template").is_dir()
        result["scaffold_present"] = (root / "scripts/standard.py").is_file()
    else:
        members = _workspace_members(manifest.read_text(encoding="utf-8"))
        result["workspace_member_absent"] = proposal["path"] not in members
        app_parts = PurePosixPath(proposal["path"]).parts
        def safe_artifact(rel):
            if not isinstance(rel, str) or "\\" in rel: return False
            parts = PurePosixPath(rel).parts
            if PurePosixPath(rel).is_absolute() or ".." in parts or parts[:len(app_parts)] != app_parts or len(parts) <= len(app_parts): return False
            try:
                safe_relative(root, rel).relative_to(safe_relative(root, proposal["path"]))
                return True
            except (SafetyError, ValueError):
                return False
        artifacts = proposal.get("artifacts")
        result["artifacts_safe"] = isinstance(artifacts, dict) and all(safe_artifact(rel) for rel in artifacts)
        result["diff_current"] = proposal.get("diff") == _proposal_diff(root, proposal)
    result["passed"] = all(result.values())
    return result


def _workspace_members(text: str) -> list[str]:
    import tomllib
    return tomllib.loads(text).get("workspace", {}).get("members", [])


def _app_artifacts(name: str, summary: str) -> dict[str, str]:
    title = " ".join(part.capitalize() for part in name.split("-"))
    escaped = json.dumps(summary, ensure_ascii=False)
    return {
        f"apps/{name}/Cargo.toml": f'''[package]\nname = "{name}"\nversion.workspace = true\nedition.workspace = true\n\n[dependencies]\ndioxus = {{ workspace = true, features = ["router", "lib", "launch", "desktop", "asset"] }}\n\n[features]\ndefault = ["desktop"]\ndesktop = ["dioxus/desktop"]\nweb = ["dioxus/web"]\nmobile = ["dioxus/mobile"]\n\n[dev-dependencies]\ndioxus-ssr = "0.7"\n''',
        f"apps/{name}/Dioxus.toml": f'''[application]\nname = "{title}"\ndefault_platform = "desktop"\nasset_dir = "assets"\n''',
        f"apps/{name}/src/main.rs": f'''fn main() {{\n    dioxus::launch({name.replace('-', '_')}::App);\n}}\n''',
        f"apps/{name}/src/lib.rs": f'''use dioxus::prelude::*;\n\nconst SUMMARY: &str = {escaped};\nstatic MAIN_CSS: Asset = asset!("/assets/main.css");\n\n#[component]\npub fn App() -> Element {{\n    rsx! {{\n        document::Meta {{ name: "viewport", content: "width=device-width, initial-scale=1" }}\n        document::Stylesheet {{ href: MAIN_CSS }}\n        main {{ class: "app-shell",\n            p {{ class: "eyebrow", "KODOO APP" }}\n            h1 {{ "{title}" }}\n            p {{ "{{SUMMARY}}" }}\n        }}\n    }}\n}}\n''',
        f"apps/{name}/assets/main.css": ":root { font-family: system-ui, sans-serif; color: #172033; background: #f5f7fb; }\nbody { margin: 0; }\n.app-shell { max-width: 72rem; margin: 4rem auto; padding: 2rem; }\n.eyebrow { color: #475569; font-size: .75rem; letter-spacing: .12em; }\n",
        f"apps/{name}/README.md": f'''# {title}\n\nApp Dioxus inicial. Para executar: `cd apps/{name} && dx serve --platform desktop`.\n\n## Integrações de produto pendentes\n\n- Montar os componentes Direct dos módulos escolhidos para este domínio.\n- Definir rotas e navegação do produto.\n- Exercitar o fluxo desktop contra backend e dados reais.\n''',
        f"apps/{name}/tests/render.rs": f'''use dioxus::prelude::*;\nuse {name.replace('-', '_')}::App;\n\n#[test]\nfn renders_app_name_and_summary() {{\n    let mut dom = VirtualDom::new(App);\n    dom.rebuild_in_place();\n    let html = dioxus_ssr::render(&dom);\n    assert!(html.contains("{title}"));\n    assert!(html.contains({escaped}.trim_matches('"')));\n}}\n''',
    }


def _proposal_diff(root: Path, proposal: dict) -> str:
    manifest = root / "Cargo.toml"
    text = manifest.read_text(encoding="utf-8")
    import tomllib
    data = tomllib.loads(text)
    members = data.get("workspace", {}).get("members", [])
    updated = text
    if proposal["path"] not in members:
        updated = _add_workspace_member(updated, proposal["path"])
    pieces = list(difflib.unified_diff(text.splitlines(True), updated.splitlines(True), fromfile="a/Cargo.toml", tofile="b/Cargo.toml"))
    for rel, content in sorted(proposal["artifacts"].items()):
        pieces.extend(difflib.unified_diff([], content.splitlines(True), fromfile="/dev/null", tofile=f"b/{rel}"))
    return "".join(pieces)


def apply_app_proposal(proposal: dict, approved: bool) -> list[str]:
    if not approved: raise PermissionError("aplicação exige aprovação explícita")
    checks = validate_proposal(proposal)
    if not checks["passed"]: raise RuntimeError(f"proposta inválida ou stale: {checks}")
    root = ensure_root(proposal["workspace"])
    manifest = root / "Cargo.toml"
    original = manifest.read_bytes()
    updated = _add_workspace_member(original.decode("utf-8"), proposal["path"])
    applied: list[Path] = []
    manifest_written = False
    try:
        for rel, content in proposal["artifacts"].items():
            path = safe_relative(root, rel)
            if path.exists(): raise FileExistsError(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".konstructor-tmp")
            temp.write_text(content, encoding="utf-8")
            temp.replace(path)
            applied.append(path)
        temp_manifest = manifest.with_suffix(".Cargo.toml.konstructor-tmp")
        temp_manifest.write_text(updated, encoding="utf-8")
        temp_manifest.replace(manifest)
        manifest_written = True
    except Exception:
        if manifest_written: manifest.write_bytes(original)
        for path in reversed(applied): path.unlink(missing_ok=True)
        raise
    save_json(root, f"{proposal['proposal_id']}.json", {**proposal, "status": "applied"})
    return [str(p.relative_to(root)) for p in applied] + ["Cargo.toml"]


def _add_workspace_member(text: str, member: str) -> str:
    marker = "members = ["
    at = text.find(marker)
    if at < 0: raise ValueError("Cargo.toml sem [workspace].members editável")
    end = text.find("]", at + len(marker))
    if end < 0: raise ValueError("lista [workspace].members inválida")
    return text[:end] + f'    "{member}",\n' + text[end:]
