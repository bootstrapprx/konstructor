from __future__ import annotations

import json
from pathlib import Path

from .repository import KodooRepositoryAdapter
from .safety import safe_relative


def suggest_location(root: str | Path, item: dict, provider, location_hint: str = "") -> dict:
    """Suggest a location from the indexed Rust modules; accept only indexed files."""
    adapter = KodooRepositoryAdapter(root)
    index = adapter.index()
    findings = index["findings"]
    modules = [row["path"] for row in findings if row["kind"] == "module"]
    files = sorted({row["path"] for row in findings if row["kind"] == "symbol" and row["path"].endswith(".rs")})
    if not modules or not files:
        raise ValueError("o índice não encontrou módulos Rust com arquivos-fonte")
    if location_hint:
        safe_relative(adapter.root, location_hint)
        if location_hint not in files:
            raise ValueError("o caminho indicado não está entre os arquivos Rust indexados")
    schema = {
        "type": "object",
        "properties": {
            "module_path": {"type": "string"},
            "target_file": {"type": "string"},
            "reason": {"type": "string"},
            "confidence": {"type": "string"},
            "questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["module_path", "target_file", "reason", "confidence", "questions"],
        "additionalProperties": False,
    }
    prompt = {
        "task": "Recommend where this roadmap item belongs in the existing repository.",
        "work_item": item,
        "location_hint": location_hint,
        "indexed_modules": modules,
        "indexed_rust_files_and_symbols": [row for row in findings if row["kind"] == "symbol"],
        "allowed_target_files": files,
        "rules": ["Choose one existing module and one allowed target file.", "Do not invent paths.", "If evidence is insufficient, state that in questions and lower confidence."],
    }
    result = provider.complete_structured(json.dumps(prompt, ensure_ascii=False), schema)
    if result["module_path"] not in modules:
        raise ValueError("a sugestão escolheu um módulo que não consta no índice")
    if result["target_file"] not in files:
        raise ValueError("a sugestão escolheu um arquivo que não consta no índice")
    target = safe_relative(adapter.root, result["target_file"])
    if not target.is_file() or not result["target_file"].startswith(result["module_path"].rstrip("/") + "/"):
        raise ValueError("o arquivo sugerido não pertence ao módulo indicado")
    return result
