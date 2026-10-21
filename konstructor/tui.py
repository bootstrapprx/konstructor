from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from .planner import make_plan, write_plan
from .config import TUIConfig, load_tui_config
from .repository import KodooRepositoryAdapter
from .themes import Theme, load_theme
from .workflow import apply, diff, generate, status, validate

MENU = (
    ("Inspect workspace", "inspect"), ("Index workspace", "index"),
    ("Create plan", "plan"), ("Generate staging", "generate"),
    ("Validate changeset", "validate"), ("Review diff", "diff"),
    ("Apply approved changeset", "apply"), ("Status", "status"), ("Quit", "quit"),
)
TABS = ("workspace", "diff", "logs", "split")
COMMANDS = {action for _, action in MENU} - {"quit"}


class LiveLogBuffer:
    """Buffer thread-safe para eventos exibidos pela TUI sem bloquear operações."""

    def __init__(self, limit: int = 500):
        self._lines: deque[str] = deque(maxlen=limit)
        self._lock = threading.Lock()

    def append(self, message: str) -> None:
        with self._lock:
            self._lines.append(message)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._lines)


class TUILogHandler(logging.Handler):
    def __init__(self, buffer: LiveLogBuffer):
        super().__init__()
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(self.format(record))
        except Exception:
            self.handleError(record)


class GeneratorTUI:
    """Estado e controlador da TUI; métodos de navegação e comandos são testáveis sem curses."""

    def __init__(self, root: Path, theme: str | Path | None = None, config: str | Path | None = None):
        self.root = root.resolve()
        self.config: TUIConfig = load_tui_config(config)
        self.theme: Theme = load_theme(theme or self.config.theme)
        self.log_buffer = LiveLogBuffer()
        self.selected = 0
        self.view = "split"
        self.plan_id = ""
        self.changeset_id = ""
        self.request = ""
        self.messages: list[str] = ["Pronto. Selecione uma operação."]
        self.detail: list[str] = []
        self.workspace_lines: list[str] = [f"workspace: {self.root}"]
        self.diff_lines: list[str] = ["Nenhum diff carregado."]
        self.scroll = 0

    def move_selection(self, delta: int) -> int:
        self.selected = (self.selected + delta) % len(MENU)
        return self.selected

    def cycle_view(self, delta: int = 1) -> str:
        self.view = TABS[(TABS.index(self.view) + delta) % len(TABS)]
        self.scroll = 0
        return self.view

    def select_view(self, view: str) -> str:
        if view not in TABS:
            raise ValueError(f"aba desconhecida: {view}")
        self.view = view
        self.scroll = 0
        return self.view

    def shortcut_matches(self, key: int | str, action: str) -> bool:
        expected = self.config.key_for(action)
        aliases = {"TAB": "\t", "ENTER": "\n", "PAGEUP": "PAGEUP", "PAGEDOWN": "PAGEDOWN"}
        if isinstance(key, int):
            special = {259: "UP", 258: "DOWN", 339: "PAGEUP", 338: "PAGEDOWN", 10: "ENTER", 13: "ENTER", 9: "TAB"}
            actual = special.get(key, chr(key) if 0 <= key < 256 else "")
        else:
            actual = aliases.get(key.upper(), key) if len(key) > 1 else key
        return actual == aliases.get(expected, expected)

    def handle_web_key(self, key: str) -> dict[str, Any]:
        """Processa uma tecla/ação do Web Terminal sem acessar curses."""
        if self.shortcut_matches(key, "quit"): return {"quit": True}
        if self.shortcut_matches(key, "up"): self.move_selection(-1)
        elif self.shortcut_matches(key, "down"): self.move_selection(1)
        elif self.shortcut_matches(key, "next_view"): self.cycle_view()
        elif self.shortcut_matches(key, "workspace_view"): self.select_view("workspace")
        elif self.shortcut_matches(key, "diff_view"): self.select_view("diff")
        elif self.shortcut_matches(key, "logs_view"): self.select_view("logs")
        elif self.shortcut_matches(key, "split_view"): self.select_view("split")
        elif self.shortcut_matches(key, "page_up"): self.scroll = max(0, self.scroll - 5)
        elif self.shortcut_matches(key, "page_down"): self.scroll += 5
        return {"quit": False, "selected": self.selected, "view": self.view}

    def log(self, message: str) -> None:
        stamped = f"{datetime.now().strftime('%H:%M:%S')}  {message}"
        self.messages.append(message)
        self.messages = self.messages[-8:]
        self.log_buffer.append(stamped)

    def _set_content(self, title: str, content: str) -> None:
        self.detail = [title, ""] + content.splitlines()
        self.scroll = 0

    def _set_workspace(self, data: dict[str, Any]) -> None:
        self.workspace_lines = []
        for key, value in data.items():
            if isinstance(value, dict):
                self.workspace_lines.append(f"{key}:")
                self.workspace_lines.extend(f"  {k}: {v}" for k, v in value.items())
            elif isinstance(value, list):
                self.workspace_lines.append(f"{key}: {len(value)} itens")
                self.workspace_lines.extend(f"  - {item}" for item in value[:30])
            else:
                self.workspace_lines.append(f"{key}: {value}")

    def perform(self, action: str, *, request: str | None = None, plan_id: str | None = None,
                changeset_id: str | None = None, approve: bool = False) -> dict[str, Any]:
        """Executa um comando sem interação de terminal; usado pela TUI e pelos testes."""
        if action not in COMMANDS:
            raise ValueError(f"comando TUI desconhecido: {action}")
        if action == "inspect":
            data = KodooRepositoryAdapter(self.root).inspect(); self._set_workspace(data)
            self._set_content("INSPEÇÃO", "\n".join(f"{k}: {v}" for k, v in data.items()))
            self.log("Inspeção concluída sem alterar o workspace."); return data
        if action == "index":
            data = KodooRepositoryAdapter(self.root).index(); findings = data.get("findings", [])
            self._set_content("ÍNDICE", "\n".join(f"{x['kind']}  {x['path']}  {x['detail']}" for x in findings) or "Nenhuma evidência encontrada.")
            self.log(f"Índice atualizado: {len(findings)} evidências."); return data
        if action == "plan":
            self.request = request or self.request
            if not self.request: raise ValueError("requisito vazio")
            adapter = KodooRepositoryAdapter(self.root); plan = make_plan(adapter.root, self.request, adapter.index()); path = write_plan(adapter.root, plan)
            self.plan_id = plan.id; result = {"plan_id": plan.id, "path": str(path), "questions": plan.questions}
            self._set_content("PLANO", f"ID: {plan.id}\nArquivo: {path}\n\n" + "\n".join(plan.questions or ["Sem perguntas essenciais."]))
            self.log(f"Plano {plan.id} salvo fora do workspace."); return result
        if action == "generate":
            self.plan_id = plan_id or self.plan_id
            if not self.plan_id: raise ValueError("ID do plano ausente")
            cs = generate(self.root, self.plan_id); self.changeset_id = cs.id
            result = {"changeset_id": cs.id, "staging": cs.staging, "files": cs.files}
            self._set_content("STAGING", f"Changeset: {cs.id}\nStaging: {cs.staging}\n\n" + "\n".join(x["path"] for x in cs.files))
            self.log(f"Changeset {cs.id} gerado em staging; nada aplicado."); return result
        if action == "validate":
            self.changeset_id = changeset_id or self.changeset_id
            if not self.changeset_id: raise ValueError("ID do changeset ausente")
            result = validate(self.root, self.changeset_id)
            self._set_content("VALIDAÇÃO", "\n".join(f"{x['status'].upper():9} {x['name']}: {x.get('summary', '')}" for x in result["checks"]))
            self.log("Validação registrada; revise estados blocked/failed antes de aplicar."); return result
        if action == "diff":
            self.changeset_id = changeset_id or self.changeset_id
            if not self.changeset_id: raise ValueError("ID do changeset ausente")
            text = diff(self.root, self.changeset_id) or "Diff vazio."; self.diff_lines = text.splitlines() or ["Diff vazio."]
            self._set_content("DIFF", text); self.log("Diff exibido em modo somente leitura."); return {"diff": text}
        if action == "apply":
            self.changeset_id = changeset_id or self.changeset_id
            if not self.changeset_id: raise ValueError("ID do changeset ausente")
            if not approve: raise PermissionError("aplicação exige confirmação explícita")
            applied = apply(self.root, self.changeset_id, yes=True); self._set_content("APLICADO", "\n".join(applied))
            self.log(f"Aplicação concluída: {len(applied)} arquivos."); return {"applied": applied}
        data = status(self.root); self._set_content("STATUS", "\n".join(f"{k}: {v}" for k, v in data.items())); self.log("Status carregado."); return data

def run_tui(root: str | Path, theme: str | Path | None = None, config: str | Path | None = None) -> None:
    from .textual_tui import run_textual_tui
    run_textual_tui(root, theme, config)
