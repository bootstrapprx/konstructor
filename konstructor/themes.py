from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path

from .state import home


@dataclass(frozen=True)
class Theme:
    name: str = "kodoo-dark"
    header: str = "cyan"
    accent: str = "blue"
    selected: str = "cyan"
    text: str = "white"
    muted: str = "gray"
    panel: str = "blue"
    success: str = "green"
    error: str = "red"
    diff_add: str = "green"
    diff_del: str = "red"


BUILTIN_THEMES = {
    "kodoo-dark": Theme(),
    "high-contrast": Theme("high-contrast", "yellow", "white", "yellow", "white", "white", "white", "green", "red", "green", "red"),
    "mono": Theme("mono", "white", "white", "white", "white", "white", "white", "white", "white", "white", "white"),
}


def _from_dict(data: dict, fallback: Theme) -> Theme:
    allowed = {field.name for field in fields(Theme)}
    values = {key: value for key, value in data.items() if key in allowed and isinstance(value, str)}
    return Theme(**{field.name: values.get(field.name, getattr(fallback, field.name)) for field in fields(Theme)})


def load_theme(spec: str | Path | None = None) -> Theme:
    """Carrega preset ou JSON. Sem especificação, usa theme.json no diretório de estado."""
    if spec is None:
        candidate = home() / "theme.json"
        if not candidate.is_file():
            return BUILTIN_THEMES["kodoo-dark"]
    else:
        if str(spec) in BUILTIN_THEMES:
            return BUILTIN_THEMES[str(spec)]
        candidate = Path(spec).expanduser()
        if not candidate.is_file():
            raise FileNotFoundError(f"tema não encontrado: {candidate}")
    data = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("tema deve ser um objeto JSON")
    fallback = BUILTIN_THEMES["kodoo-dark"]
    return _from_dict(data, fallback)


def save_theme(theme: Theme, path: str | Path | None = None) -> Path:
    target = Path(path).expanduser() if path else home() / "theme.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(theme.__dict__, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
