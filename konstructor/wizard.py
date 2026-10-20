from __future__ import annotations

import re
from pathlib import Path

from .safety import ensure_root
from .state import save_json
from .state import load_json
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
    proposal = {"kind": "dioxus-app", "workspace": str(root), "name": name,
                "path": f"apps/{name}", "summary": summary, "writes_target": False,
                "status": "proposal-only", "reason": "Kodoo não define template/comando oficial para criar apps novos",
                "base_hash": file_hash(root / "Cargo.toml")}
    proposal_path = save_json(root, f"app-proposal-{name}.json", proposal)
    proposal["proposal_id"] = proposal_path.stem
    save_json(root, f"{proposal_path.stem}.json", proposal)
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
    result["passed"] = all(result.values())
    return result
