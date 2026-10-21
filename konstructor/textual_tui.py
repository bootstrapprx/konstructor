from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Footer, Header, Input, Label, ListItem, ListView, RichLog, Static, TabbedContent, TabPane

from .provider import OpenAICompatibleProvider
from .tui import GeneratorTUI, MENU, TUILogHandler

ACTION_LABELS = {
    "inspect": "Inspecionar workspace", "index": "Indexar workspace", "plan": "Criar plano",
    "generate": "Gerar staging", "validate": "Validar changeset", "diff": "Revisar diff",
    "apply": "Aplicar changeset aprovado", "status": "Status", "quit": "Sair",
}


def _terminal_color(value: str) -> str:
    colors = {"black", "red", "green", "yellow", "blue", "magenta", "cyan", "white", "gray"}
    if value.lower() in colors:
        return value.lower()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return value
    return "white"


class KonstructorTUI(App[None]):
    TITLE = "Konstructor"
    BINDINGS = [Binding("q", "quit", "Sair"), Binding("1", "show_tab('workspace')", "Workspace"),
                Binding("2", "show_tab('diff')", "Diff"), Binding("3", "show_tab('logs')", "Logs"),
                Binding("4", "show_tab('chat')", "Chat")]

    def __init__(self, controller: GeneratorTUI):
        t = controller.theme
        self.CSS = f"""
        Screen {{ background: #101820; color: {_terminal_color(t.text)}; }}
        Header {{ background: #101820; color: {_terminal_color(t.header)}; }}
        Footer {{ background: #101820; }}
        #body {{ height: 1fr; padding: 0 1; }}
        #actions {{ width: 28%; min-width: 24; border: round {_terminal_color(t.accent)}; padding: 0 1; }}
        #main {{ width: 1fr; border: round {_terminal_color(t.accent)}; }}
        #action-list {{ height: 1fr; }}
        #action-input {{ margin-top: 1; }}
        #tabs {{ height: 1fr; }}
        #workspace-pane {{ layout: horizontal; }}
        #workspace-tree {{ width: 45%; border: round {_terminal_color(t.panel)}; }}
        #workspace-summary {{ width: 1fr; padding: 1; overflow: auto; }}
        #diff-log, #event-log {{ height: 1fr; border: round {_terminal_color(t.panel)}; }}
        #chat-log {{ height: 1fr; border: round {_terminal_color(t.panel)}; }}
        #chat-actions {{ height: auto; padding: 0 1; }}
        #status {{ height: 3; padding: 0 1; color: {_terminal_color(t.muted)}; }}
        Button {{ margin: 0 1; }}
        """
        super().__init__()
        self.controller = controller
        self.sub_title = str(controller.root)
        self.last_request = ""
        self.chat_history: list[dict[str, str]] = []
        self._log_handler = TUILogHandler(self.controller.log_buffer)
        self._log_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%H:%M:%S"))
        self._shown_logs = 0
        self._pending_apply = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="actions"):
                yield Label("WORKFLOW")
                yield ListView(*(ListItem(Label(ACTION_LABELS[action]), id=f"action-{action}") for _, action in MENU), id="action-list")
                yield Input(placeholder="Requisito ou ID do plano/changeset", id="action-input")
                yield Button("Executar ação selecionada", id="run-action", variant="primary")
            with Vertical(id="main"):
                with TabbedContent(initial="workspace", id="tabs"):
                    with TabPane("Workspace", id="workspace"):
                        with Horizontal(id="workspace-pane"):
                            yield DirectoryTree(str(self.controller.root), id="workspace-tree")
                            yield Static("Workspace ainda não inspecionado.\n\nSelecione Inspecionar workspace para carregar o inventário.", id="workspace-summary")
                    with TabPane("Diff / detalhe", id="diff"):
                        yield RichLog(id="diff-log", wrap=False, markup=False)
                    with TabPane("Logs", id="logs"):
                        yield RichLog(id="event-log", wrap=False, markup=False)
                    with TabPane("Chat", id="chat"):
                        yield RichLog(id="chat-log", wrap=True, markup=False)
                        with Horizontal(id="chat-actions"):
                            yield Input(placeholder="Converse sobre este workspace…", id="chat-input")
                            yield Button("Enviar", id="send-chat", variant="primary")
                            yield Button("Criar plano", id="create-plan")
                yield Static(f"Workspace: {self.controller.root}\nSelecione uma ação; apply exige confirmação digitando APPLY <ID>.", id="status")
        yield Footer()

    def on_mount(self) -> None:
        logging.getLogger().addHandler(self._log_handler)
        self.set_interval(self.controller.config.refresh_ms / 1000, self._refresh_logs)
        self.query_one("#action-list", ListView).index = 0
        self.query_one("#diff-log", RichLog).write("Nenhum diff carregado.")
        self.query_one("#chat-log", RichLog).write("Chat do workspace. Mensagens são enviadas ao provedor OpenAI-compatible configurado.\nA conversa não aplica alterações; use o workflow e revise o diff.")

    def on_unmount(self) -> None:
        logging.getLogger().removeHandler(self._log_handler)
        self._log_handler.close()

    def action_show_tab(self, tab: str) -> None:
        self.query_one(TabbedContent).active = tab

    def on_key(self, event) -> None:
        for action, key in self.controller.config.shortcuts.items():
            if event.key != key.lower():
                continue
            if action == "quit":
                self.exit()
            elif action == "next_view":
                tabs = ("workspace", "diff", "logs", "chat")
                current = self.query_one(TabbedContent).active
                self.query_one(TabbedContent).active = tabs[(tabs.index(current) + 1) % len(tabs)]
            elif action in {"workspace_view", "diff_view", "logs_view", "split_view"}:
                self.query_one(TabbedContent).active = {"workspace_view": "workspace", "diff_view": "diff", "logs_view": "logs", "split_view": "chat"}[action]

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        action = (event.item.id or "").removeprefix("action-")
        self._dispatch(action)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-action":
            item = self.query_one(ListView).highlighted_child
            if item:
                self._dispatch((item.id or "").removeprefix("action-"))
        elif event.button.id == "send-chat":
            self._send_chat()
        elif event.button.id == "create-plan":
            self._create_plan()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat-input":
            self._send_chat()
        else:
            item = self.query_one(ListView).highlighted_child
            if item:
                self._dispatch((item.id or "").removeprefix("action-"))

    def _dispatch(self, action: str) -> None:
        if action == "quit":
            self.exit()
            return
        value = self.query_one("#action-input", Input).value.strip()
        if action == "plan" and not value:
            self.query_one("#action-input", Input).focus()
            self.notify("Descreva o requisito no campo abaixo do menu.")
            return
        kwargs: dict[str, Any] = {}
        if action == "plan":
            kwargs["request"] = value
        elif action == "generate":
            kwargs["plan_id"] = value or self.controller.plan_id
        elif action in {"validate", "diff", "apply"}:
            if action == "apply":
                confirmed = bool(self._pending_apply) and value == f"APPLY {self._pending_apply}"
                cid = self._pending_apply if confirmed else (value if value and not value.startswith("APPLY ") else self.controller.changeset_id)
                if not cid:
                    self.notify("Gere ou informe um changeset antes de aplicar.", severity="warning")
                    return
                if not confirmed:
                    self._pending_apply = cid
                    self.query_one("#action-input", Input).placeholder = f"Digite exatamente: APPLY {cid}"
                    self.query_one("#status", Static).update(f"Revisão explícita necessária. Digite APPLY {cid} no campo de ação e execute Apply novamente.")
                    self.notify(f"Confirme digitando APPLY {cid}", severity="warning")
                    return
                kwargs["approve"] = True
                cid = self._pending_apply
                self._pending_apply = ""
                self.query_one("#action-input", Input).placeholder = "Requisito ou ID do plano/changeset"
            else:
                cid = value or self.controller.changeset_id
            kwargs["changeset_id"] = cid
        self.run_worker(lambda: self._perform(action, kwargs), thread=True, exclusive=True)

    def _perform(self, action: str, kwargs: dict[str, Any]) -> None:
        try:
            result = self.controller.perform(action, **kwargs)
            error = None
        except Exception as exc:
            result, error = None, str(exc)
            self.controller.log(f"ERRO: {error}")
            self.controller._set_content("ERRO", error)
        self.call_from_thread(self._render_result, action, result, error)

    def _render_result(self, action: str, result: Any, error: str | None) -> None:
        detail = self.controller.detail
        self.query_one("#diff-log", RichLog).clear()
        self.query_one("#diff-log", RichLog).write("\n".join(detail) or "Sem detalhes.")
        self.query_one("#status", Static).update("ERRO: " + error if error else " · ".join(self.controller.messages[-3:]))
        self.query_one("#workspace-summary", Static).update("\n".join(self.controller.workspace_lines))
        if action == "diff" and result:
            self.query_one("#diff-log", RichLog).clear()
            self.query_one("#diff-log", RichLog).write(result["diff"] or "Diff vazio.")
            self.query_one(TabbedContent).active = "diff"
        elif action in {"inspect", "index", "status"}:
            self.query_one(TabbedContent).active = "workspace"
        elif action in {"plan", "generate", "validate", "apply"}:
            self.query_one(TabbedContent).active = "diff"
        if action == "plan" and result:
            self.query_one("#action-input", Input).value = ""
        elif action == "generate" and result:
            self.query_one("#action-input", Input).value = result["changeset_id"]

    def _refresh_logs(self) -> None:
        lines = self.controller.log_buffer.snapshot()
        widget = self.query_one("#event-log", RichLog)
        if len(lines) < self._shown_logs:
            widget.clear()
            self._shown_logs = 0
        for line in lines[self._shown_logs:]:
            widget.write(line)
        self._shown_logs = len(lines)

    def _send_chat(self) -> None:
        entry = self.query_one("#chat-input", Input)
        prompt = entry.value.strip()
        if not prompt:
            return
        entry.value = ""
        self.last_request = prompt
        self.chat_history.append({"role": "user", "content": prompt})
        log = self.query_one("#chat-log", RichLog)
        log.write(f"Você: {prompt}")
        self._chat_request(list(self.chat_history))

    @work(thread=True, exclusive=True)
    def _chat_request(self, history: list[dict[str, str]]) -> None:
        try:
            provider = OpenAICompatibleProvider()
            context = {
                "workspace": str(self.controller.root),
                "inventory": self.controller.workspace_lines,
                "conversation": history[-12:],
            }
            schema = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"], "additionalProperties": False}
            reply = provider.complete_structured(
                "You assist a developer using Konstructor. Do not claim to execute commands or modify files. "
                "Explain the next safe Konstructor workflow step. Context: " + json.dumps(context, ensure_ascii=False), schema
            )["reply"]
        except Exception as exc:
            reply = f"Erro do provedor: {exc}"
        self.call_from_thread(self._show_reply, reply)

    def _show_reply(self, reply: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.write(f"Konstructor: {reply}")
        if not reply.startswith("Erro do provedor:"):
            self.chat_history.append({"role": "assistant", "content": reply})

    def _create_plan(self) -> None:
        if not self.last_request:
            self.notify("Envie uma solicitação no chat antes de criar o plano.", severity="warning")
            return
        self.run_worker(lambda: self._perform("plan", {"request": self.last_request}), thread=True, exclusive=True)


def run_textual_tui(root: str | Path, theme: str | Path | None = None, config: str | Path | None = None) -> None:
    controller = GeneratorTUI(Path(root), theme, config)
    KonstructorTUI(controller).run()
