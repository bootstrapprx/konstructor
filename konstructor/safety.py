from __future__ import annotations
import hashlib
from pathlib import Path

class SafetyError(ValueError):
    pass

def ensure_root(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise SafetyError(f"workspace não encontrado: {root}")
    return root

def safe_relative(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise SafetyError(f"path fora da raiz autorizada: {relative}") from exc
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink() and current.resolve() != candidate:
            raise SafetyError(f"symlink escapando da raiz: {relative}")
    return candidate

def file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise SafetyError(f"alvo não é arquivo: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()

def redact(text: str) -> str:
    lines = []
    for line in text.splitlines():
        low = line.lower()
        if any(x in low for x in ("api_key", "secret", "password", "token=")):
            lines.append("[REDACTED]")
        else:
            lines.append(line)
    return "\n".join(lines)
