from __future__ import annotations
import hashlib, json, os
from pathlib import Path


def home() -> Path:
    # KONSTRUCTOR_HOME is canonical; keep the previous variable as migration fallback.
    configured = os.environ.get("KONSTRUCTOR_HOME") or os.environ.get("KODOO_GEN_HOME")
    return Path(configured or (Path.home() / ".konstructor")).expanduser().resolve()

def workspace_key(root: Path) -> str:
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]

def workspace_dir(root: Path) -> Path:
    p = home() / "workspaces" / workspace_key(root)
    p.mkdir(parents=True, exist_ok=True)
    return p

def save_json(root: Path, name: str, value: dict) -> Path:
    p = workspace_dir(root) / name
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p

def load_json(root: Path, name: str) -> dict:
    p = workspace_dir(root) / name
    return json.loads(p.read_text(encoding="utf-8"))
