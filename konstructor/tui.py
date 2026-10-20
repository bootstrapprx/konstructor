from __future__ import annotations

import curses
import logging
import threading
import textwrap
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


def _clip(value: object, width: int) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


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
            special = {curses.KEY_UP: "UP", curses.KEY_DOWN: "DOWN", curses.KEY_PPAGE: "PAGEUP", curses.KEY_NPAGE: "PAGEDOWN", 10: "ENTER", 13: "ENTER", 9: "TAB"}
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

    def prompt(self, screen: curses.window, label: str, default: str = "") -> str:
        height, width = screen.getmaxyx(); screen.move(height - 2, 0); screen.clrtoeol()
        screen.addstr(height - 2, 0, _clip(f"{label} [{default}]: ", width - 1), curses.A_BOLD); screen.refresh(); curses.echo()
        try:
            raw = screen.getstr(height - 2, min(len(label) + len(default) + 4, width - 2), max(1, width - len(label) - 6))
            return raw.decode("utf-8", "replace").strip() or default
        finally: curses.noecho()

    def confirm(self, screen: curses.window, message: str) -> bool:
        return self.prompt(screen, f"{message} Digite 'sim' para confirmar").lower() == "sim"

    def execute(self, screen: curses.window, action: str) -> None:
        try:
            if action == "plan": result = self.perform(action, request=self.prompt(screen, "Requisito"))
            elif action == "generate": result = self.perform(action, plan_id=self.prompt(screen, "ID do plano", self.plan_id))
            elif action in {"validate", "diff"}: result = self.perform(action, changeset_id=self.prompt(screen, "ID do changeset", self.changeset_id))
            elif action == "apply":
                cid = self.prompt(screen, "ID exato do changeset", self.changeset_id)
                result = self.perform(action, changeset_id=cid, approve=self.confirm(screen, f"Aplicar {cid}?"))
            else: result = self.perform(action)
            if action == "index": self._set_content("ÍNDICE", "\n".join(f"{x['kind']}  {x['path']}  {x['detail']}" for x in result.get("findings", [])) or "Nenhuma evidência encontrada.")
        except Exception as exc:
            self.log(f"ERRO: {exc}"); self._set_content("ERRO", str(exc))

    def _color(self, name: str) -> int:
        return {"black": curses.COLOR_BLACK, "red": curses.COLOR_RED, "green": curses.COLOR_GREEN, "yellow": curses.COLOR_YELLOW, "blue": curses.COLOR_BLUE, "magenta": curses.COLOR_MAGENTA, "cyan": curses.COLOR_CYAN, "white": curses.COLOR_WHITE, "gray": curses.COLOR_WHITE, "default": -1}.get(name, curses.COLOR_WHITE)

    def draw(self, screen: curses.window) -> None:
        screen.erase(); height, width = screen.getmaxyx()
        if height < 14 or width < 70: screen.addstr(0, 0, "Aumente o terminal para pelo menos 70x14."); screen.refresh(); return
        screen.bkgd(" ", curses.color_pair(8)); screen.attron(curses.color_pair(1) | curses.A_BOLD); screen.addstr(0, 0, _clip("KONSTRUCTOR · TUI", width - 1)); screen.attroff(curses.color_pair(1) | curses.A_BOLD)
        screen.addstr(1, 0, _clip(f"workspace: {self.root}  ·  tema: {self.theme.name}", width - 1))
        tab_text = "  ".join((f"[{tab.upper()}]" if tab == self.view else tab.upper()) for tab in TABS); screen.addstr(2, 1, _clip(tab_text + "  (Tab/1/2/3/4 alterna)", width - 2), curses.A_BOLD)
        menu_width = min(29, width // 3); screen.vline(3, menu_width, curses.ACS_VLINE, height - 6)
        for i, (label, _) in enumerate(MENU): screen.addstr(4 + i, 1, _clip(label, menu_width - 3), curses.color_pair(2) if i == self.selected else curses.A_NORMAL)
        content_top, content_bottom = 4, height - 3
        if self.view == "split":
            available = width - menu_width - 2
            first = min(menu_width + max(20, available // 3), width - 30)
            second = min(max(menu_width + max(40, (available * 2) // 3), first + 15), width - 15)
            screen.vline(3, first, curses.ACS_VLINE, height - 6); screen.vline(3, second, curses.ACS_VLINE, height - 6)
            self._draw_lines(screen, self.workspace_lines, menu_width + 2, content_top, first - 2, content_bottom, "WORKSPACE")
            self._draw_lines(screen, self.diff_lines if self.diff_lines != ["Nenhum diff carregado."] else self.detail, first + 2, content_top, second - 2, content_bottom, "DIFF / DETALHE")
            self._draw_lines(screen, self.log_buffer.snapshot(), second + 2, content_top, width - 2, content_bottom, "LOGS · LIVE")
        elif self.view == "workspace": self._draw_lines(screen, self.workspace_lines, menu_width + 2, content_top, width - 2, content_bottom, "WORKSPACE")
        elif self.view == "logs": self._draw_lines(screen, self.log_buffer.snapshot(), menu_width + 2, content_top, width - 2, content_bottom, "LOGS · LIVE")
        else: self._draw_lines(screen, self.diff_lines if self.diff_lines != ["Nenhum diff carregado."] else self.detail, menu_width + 2, content_top, width - 2, content_bottom, "DIFF")
        screen.hline(height - 4, 0, curses.ACS_HLINE, width); screen.addstr(height - 3, 1, _clip(" · ".join(self.messages[-3:]), width - 2)); screen.addstr(height - 1, 1, "↑↓ menu  Enter executar  Tab/1/2/3/4 abas  PgUp/PgDn rolar  q sair"); screen.refresh()

    def _draw_lines(self, screen: curses.window, lines: list[str], left: int, top: int, right: int, bottom: int, title: str) -> None:
        width = max(1, right - left); screen.addstr(top - 1, left, _clip(title, width), curses.A_BOLD); row = top
        flattened = []
        for line in lines[self.scroll:]: flattened.extend(textwrap.wrap(line, width) or [""])
        for line in flattened:
            if row >= bottom: break
            attr = curses.color_pair(6) if line.startswith("+") else curses.color_pair(7) if line.startswith("-") else curses.A_NORMAL
            screen.addstr(row, left, _clip(line, width), attr); row += 1

    def run(self, screen: curses.window) -> None:
        curses.curs_set(0); curses.start_color(); curses.use_default_colors()
        colors = {"header": 1, "selected": 2, "muted": 3, "success": 4, "error": 5, "diff_add": 6, "diff_del": 7, "panel": 8}
        for name, pair in colors.items():
            fg = self._color(getattr(self.theme, name)); curses.init_pair(pair, fg, -1)
        handler = TUILogHandler(self.log_buffer)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%H:%M:%S"))
        root_logger = logging.getLogger(); root_logger.addHandler(handler)
        screen.keypad(True); screen.timeout(self.config.refresh_ms)
        try:
            while True:
                self.draw(screen); key = screen.getch()
                if key == -1: continue
                if self.shortcut_matches(key, "quit"): return
                if self.shortcut_matches(key, "up"): self.move_selection(-1)
                elif self.shortcut_matches(key, "down"): self.move_selection(1)
                elif self.shortcut_matches(key, "page_up"): self.scroll = max(0, self.scroll - 5)
                elif self.shortcut_matches(key, "page_down"): self.scroll += 5
                elif self.shortcut_matches(key, "next_view"): self.cycle_view()
                elif self.shortcut_matches(key, "workspace_view"): self.select_view("workspace")
                elif self.shortcut_matches(key, "diff_view"): self.select_view("diff")
                elif self.shortcut_matches(key, "logs_view"): self.select_view("logs")
                elif self.shortcut_matches(key, "split_view"): self.select_view("split")
                elif self.shortcut_matches(key, "execute"):
                    action = MENU[self.selected][1]
                    if action == "quit": return
                    self.execute(screen, action)
        finally:
            root_logger.removeHandler(handler); handler.close()


def run_tui(root: str | Path, theme: str | Path | None = None, config: str | Path | None = None) -> None:
    import curses
    curses.wrapper(GeneratorTUI(Path(root), theme, config).run)
