from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .state import home

DEFAULT_SHORTCUTS = {
    "up": "UP",
    "down": "DOWN",
    "execute": "ENTER",
    "quit": "q",
    "next_view": "TAB",
    "workspace_view": "1",
    "diff_view": "2",
    "logs_view": "3",
    "split_view": "4",
    "page_up": "PAGEUP",
    "page_down": "PAGEDOWN",
}

@dataclass(frozen=True)
class TUIConfig:
    theme: str | None = None
    shortcuts: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SHORTCUTS))
    refresh_ms: int = 200

    def key_for(self, action: str) -> str:
        return self.shortcuts[action]


def config_path(spec: str | Path | None = None) -> Path:
    return Path(spec).expanduser() if spec else home() / "tui.json"


def load_tui_config(spec: str | Path | None = None) -> TUIConfig:
    path = config_path(spec)
    if not path.is_file():
        if spec:
            raise FileNotFoundError(f"configuração TUI não encontrada: {path}")
        return TUIConfig()
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("configuração TUI deve ser um objeto JSON")
    shortcuts = dict(DEFAULT_SHORTCUTS)
    custom = data.get("shortcuts", {})
    if not isinstance(custom, dict):
        raise ValueError("shortcuts deve ser um objeto JSON")
    for action, key in custom.items():
        if action not in DEFAULT_SHORTCUTS:
            raise ValueError(f"atalho desconhecido: {action}")
        if not isinstance(key, str) or not key:
            raise ValueError(f"atalho inválido para {action}")
        shortcuts[action] = key.upper() if len(key) > 1 else key
    refresh = data.get("refresh_ms", 200)
    if not isinstance(refresh, int) or not 50 <= refresh <= 5000:
        raise ValueError("refresh_ms deve ser um inteiro entre 50 e 5000")
    theme = data.get("theme")
    if theme is not None and not isinstance(theme, str):
        raise ValueError("theme deve ser uma string")
    return TUIConfig(theme=theme, shortcuts=shortcuts, refresh_ms=refresh)


def save_tui_config(config: TUIConfig, spec: str | Path | None = None) -> Path:
    path = config_path(spec); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"theme": config.theme, "refresh_ms": config.refresh_ms, "shortcuts": config.shortcuts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
