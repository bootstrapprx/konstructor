from __future__ import annotations

import re
from pathlib import Path

from .safety import ensure_root
from .state import save_json


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
    }
    save_json(root, f"module-proposal-{name.replace('/', '-')}.json", proposal)
    return proposal
