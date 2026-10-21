from __future__ import annotations

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Footer, Header, Input, Label, ListItem, ListView, MarkdownViewer, RichLog, Static, TabbedContent, TabPane, TextArea

from .provider import OpenAICompatibleProvider
from . import roadmap as roadmap_store
from .capabilities import suggest_location
from .safety import SafetyError, file_hash, safe_relative
from .tui import GeneratorTUI, MENU, TUILogHandler
from .workflow import diff, stage_edits

ACTION_LABELS = {
    "inspect": "Ver arquivos e estrutura", "index": "Mapear módulos e funções", "plan": "Transformar requisito em plano",
    "generate": "Preparar mudanças do plano", "validate": "Verificar alteração preparada", "diff": "Revisar diferenças",
    "apply": "Aplicar alteração revisada", "status": "Ver estado do workspace", "quit": "Sair",
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
    BINDINGS = [Binding("q", "quit", "Sair"), Binding("1", "show_tab('roadmap')", "Trabalho"),
                Binding("2", "show_tab('workspace')", "Workspace"), Binding("3", "show_tab('diff')", "Diff"),
                Binding("4", "show_tab('logs')", "Logs"), Binding("5", "show_tab('chat')", "Chat")]

    def __init__(self, controller: GeneratorTUI):
        t = controller.theme
        self.CSS = f"""
        Screen {{ background: #101820; color: {_terminal_color(t.text)}; }}
        Header {{ background: #101820; color: {_terminal_color(t.header)}; }}
        Footer {{ background: #101820; }}
        #body {{ height: 1fr; padding: 0 1; }}
        #actions {{ width: 28%; min-width: 24; border: round {_terminal_color(t.accent)}; padding: 0 1; }}
        #main {{ width: 1fr; border: round {_terminal_color(t.accent)}; }}
        #roadmap-pane {{ height: 1fr; }}
        #work-items {{ height: 1fr; border: round {_terminal_color(t.panel)}; }}
        #roadmap-detail {{ height: 1fr; border: round {_terminal_color(t.panel)}; padding: 1; overflow: auto; }}
        #roadmap-fields {{ height: auto; }}
        #roadmap-title {{ width: 1fr; }}
        #roadmap-description, #location-hint {{ width: 1fr; }}
        #action-list {{ height: 1fr; }}
        #action-input {{ margin-top: 1; }}
        #tabs {{ height: 1fr; }}
        #workspace-pane {{ layout: horizontal; }}
        #workspace-tree {{ width: 28%; border: round {_terminal_color(t.panel)}; }}
        #editor-pane {{ width: 1fr; }}
        #workspace-summary {{ height: 5; padding: 0 1; overflow: auto; }}
        #editor-path {{ height: 1; padding: 0 1; color: {_terminal_color(t.muted)}; }}
        #file-editor {{ height: 1fr; border: round {_terminal_color(t.panel)}; }}
        #markdown-preview {{ height: 1fr; border: round {_terminal_color(t.panel)}; }}
        #editor-controls {{ height: auto; padding: 0 1; }}
        #new-file-path {{ width: 1fr; }}
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
        self._editor_path: str | None = None
        self._editor_hash: str | None = None
        self._editor_original = ""
        self._work_items: list[dict] = []
        self._selected_work_item: dict | None = None
        self._location_suggestion: dict | None = None
        self._work_item_changeset: tuple[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="actions"):
                yield Label("AÇÕES")
                yield ListView(*(ListItem(Label(ACTION_LABELS[action]), id=f"action-{action}") for _, action in MENU), id="action-list")
                yield Input(placeholder="Descreva o requisito ou informe um ID", id="action-input")
                yield Button("Executar operação", id="run-action", variant="primary")
            with Vertical(id="main"):
                with TabbedContent(initial="roadmap", id="tabs"):
                    with TabPane("Trabalho", id="roadmap"):
                        with Vertical(id="roadmap-pane"):
                            yield ListView(id="work-items")
                            yield Static("O roadmap está vazio. Adicione um item ou importe o roadmap legado pelo CLI.", id="roadmap-detail", markup=False)
                            with Vertical(id="roadmap-fields"):
                                yield Input(placeholder="O que precisa ser feito?", id="roadmap-title")
                                yield Input(placeholder="Critério de aceite ou contexto (opcional)", id="roadmap-description")
                                with Horizontal():
                                    yield Button("Adicionar trabalho", id="add-work", variant="primary")
                                    yield Input(placeholder="Arquivo existente, se já souber", id="location-hint")
                                    yield Button("Sugerir local", id="suggest-location")
                                    yield Button("Preparar alteração", id="prepare-work")
                    with TabPane("Workspace", id="workspace"):
                        with TabbedContent(initial="editor", id="workspace-views"):
                            with TabPane("Editor", id="editor"):
                                with Horizontal(id="workspace-pane"):
                                    yield DirectoryTree(str(self.controller.root), id="workspace-tree")
                                    with Vertical(id="editor-pane"):
                                        yield Static("Workspace ainda não inspecionado. Selecione Inspecionar workspace para carregar o inventário.", id="workspace-summary")
                                        yield Static("Selecione um arquivo na árvore ou crie um caminho novo.", id="editor-path")
                                        yield TextArea("", id="file-editor")
                                        with Vertical(id="editor-controls"):
                                            with Horizontal():
                                                yield Input(placeholder="Novo caminho relativo, ex.: apps/ledger/src/lib.rs", id="new-file-path")
                                                yield Button("Novo arquivo", id="new-file")
                                            with Horizontal():
                                                yield Button("Editar com Neovim", id="open-neovim")
                                                yield Button("Descartar", id="discard-file")
                                                yield Button("Salvar no workspace", id="save-file", variant="primary")
                            with TabPane("Markdown", id="markdown"):
                                with Vertical():
                                    yield Button("Editar arquivo no Neovim", id="open-neovim-markdown")
                                    yield Button("Atualizar prévia", id="refresh-markdown")
                                    yield MarkdownViewer("Selecione um arquivo **.md** na árvore do Workspace para visualizar a documentação formatada.", id="markdown-preview", show_table_of_contents=True)
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
                yield Static(f"Workspace: {self.controller.root}\nEscolha um trabalho no Roadmap; aplicação exige revisar diff, validar e confirmar APPLY <ID>.", id="status")
        yield Footer()

    async def on_mount(self) -> None:
        logging.getLogger().addHandler(self._log_handler)
        self.set_interval(self.controller.config.refresh_ms / 1000, self._refresh_logs)
        self.query_one("#action-list", ListView).index = 0
        self.query_one("#file-editor", TextArea).show_line_numbers = True
        self.query_one("#file-editor", TextArea).soft_wrap = False
        self.query_one("#diff-log", RichLog).write("Nenhum diff carregado.")
        self.query_one("#chat-log", RichLog).write("Chat do workspace. Mensagens são enviadas ao provedor OpenAI-compatible configurado.\nA conversa não aplica alterações; use o workflow e revise o diff.")
        try:
            roadmap_store.initialize_empty(self.controller.root)
            await self._refresh_roadmap()
        except (OSError, ValueError) as exc:
            self.notify(f"Roadmap indisponível: {exc}", severity="error")

    async def _refresh_roadmap(self, selected_id: str | None = None) -> None:
        self._work_items = roadmap_store.list_records(self.controller.root, "Roadmap")
        listing = self.query_one("#work-items", ListView)
        await listing.clear()
        await listing.extend(ListItem(Label(
            f"{item['id']}  ·  {next((str(value) for key, value in item['data'].items() if key.casefold() in {'deliverable / work item', 'work item', 'title', 'name'}), item['id'])}"
        , markup=False), id=f"work-{index}") for index, item in enumerate(self._work_items))
        if not self._work_items:
            self._selected_work_item = None
            self.query_one("#roadmap-detail", Static).update("Nenhum item ainda. Descreva uma tarefa abaixo para começar, ou importe o workbook legado com `konstructor roadmap import`.")
            return
        index = next((i for i, item in enumerate(self._work_items) if item["id"] == selected_id), 0)
        listing.index = index
        self._select_work_item(index)

    def _select_work_item(self, index: int) -> None:
        if not 0 <= index < len(self._work_items):
            return
        self._selected_work_item = self._work_items[index]
        self._location_suggestion = None
        item = self._selected_work_item
        self._render_work_item()

    def _render_work_item(self) -> None:
        if not self._selected_work_item:
            return
        item = self._selected_work_item
        lines = [f"{item['id']}  ·  {item['sheet']}"]
        lines.extend(f"{key}: {value}" for key, value in item["data"].items() if value not in (None, ""))
        if item.get("current"):
            lines.extend(["", f"Estado atual: {item['current'].get('status') or 'sem atualização'}", f"Evidência registrada: {item['current'].get('evidence') or 'nenhuma'}", f"Nota: {item['current'].get('note') or 'nenhuma'}"])
        if item.get("completion"):
            lines.extend(["", f"Conclusão: {item['completion']['state']} ({item['completion']['criteria_passed']}/{item['completion']['criteria_total']} critérios)"])
            lines.extend(f"{criterion['id']} · {criterion['state']} · {criterion['text']}" for criterion in item["completion"]["criteria"])
            lines.extend(f"{evidence['evidence_id']} · {evidence['kind']} · {evidence['verification']} · {evidence['reference']}" for evidence in item["completion"]["available_evidence"])
        if self._location_suggestion:
            result = self._location_suggestion
            lines.extend(["", f"Sugestão: {result['module_path']}", f"Arquivo: {result['target_file']}", f"Confiança: {result['confidence']}", result["reason"], f"Perguntas: {'; '.join(result['questions']) or 'nenhuma'}"])
        self.query_one("#roadmap-detail", Static).update("\n".join(lines))

    async def _add_work_item(self) -> None:
        title = self.query_one("#roadmap-title", Input).value.strip()
        description = self.query_one("#roadmap-description", Input).value.strip()
        try:
            item = roadmap_store.create_work_item(self.controller.root, title, description)
            self.query_one("#roadmap-title", Input).value = ""
            self.query_one("#roadmap-description", Input).value = ""
            await self._refresh_roadmap(item["id"])
            self.controller.log(f"Trabalho {item['id']} adicionado ao roadmap.")
        except (OSError, ValueError) as exc:
            self.notify(f"Não foi possível adicionar o trabalho: {exc}", severity="error")

    def _suggest_selected_location(self) -> None:
        if not self._selected_work_item:
            self.notify("Selecione um item do roadmap primeiro.", severity="warning")
            return
        item = self._selected_work_item
        hint = self.query_one("#location-hint", Input).value.strip()
        self.query_one("#suggest-location", Button).disabled = True
        self.run_worker(lambda: self._suggest_location_worker(item, hint), thread=True, exclusive=True)

    def _suggest_location_worker(self, item: dict, hint: str) -> None:
        try:
            result = suggest_location(self.controller.root, item, OpenAICompatibleProvider(), hint)
            error = None
        except Exception as exc:
            result, error = None, str(exc)
        self.call_from_thread(self._show_location_suggestion, item["id"], result, error)

    def _show_location_suggestion(self, item_id: str, result: dict | None, error: str | None) -> None:
        self.query_one("#suggest-location", Button).disabled = False
        if not self._selected_work_item or self._selected_work_item["id"] != item_id:
            return
        if error:
            self.notify(f"Não foi possível sugerir um local: {error}", severity="error")
            return
        self._location_suggestion = result
        self._render_work_item()

    def _prepare_work_item(self) -> None:
        if not self._selected_work_item or not self._location_suggestion:
            self.notify("Selecione um trabalho e peça uma sugestão de local antes de preparar a alteração.", severity="warning")
            return
        item, suggestion = self._selected_work_item, self._location_suggestion
        self.query_one("#prepare-work", Button).disabled = True
        self.run_worker(lambda: self._prepare_work_worker(item, suggestion), thread=True, exclusive=True)

    def _prepare_work_worker(self, item: dict, suggestion: dict) -> None:
        title = next((str(value) for key, value in item["data"].items() if key.casefold() in {"deliverable / work item", "work item", "title", "name"}), item["id"])
        criteria = next((str(value) for key, value in item["data"].items() if key.casefold() in {"definition of done / acceptance criteria", "acceptance criteria", "description"}), "")
        request = f"Roadmap {item['id']}: {title}\nCritério/contexto: {criteria}\nLocal recomendado: {suggestion['module_path']} — {suggestion['reason']}"
        try:
            changeset = stage_edits(self.controller.root, request, [suggestion["target_file"]], OpenAICompatibleProvider())
            text = diff(self.controller.root, changeset.id)
            error = None
        except Exception as exc:
            changeset, text, error = None, "", str(exc)
        self.call_from_thread(self._show_prepared_work, item["id"], changeset, text, error)

    def _show_prepared_work(self, item_id: str, changeset, text: str, error: str | None) -> None:
        self.query_one("#prepare-work", Button).disabled = False
        if error:
            self.notify(f"Não foi possível preparar a alteração: {error}", severity="error")
            return
        self.controller.changeset_id = changeset.id
        self._work_item_changeset = (item_id, changeset.id)
        self.controller.diff_lines = text.splitlines() or ["Diff vazio."]
        pane = self.query_one("#diff-log", RichLog)
        pane.clear()
        pane.write(text or "Diff vazio.")
        self.query_one("#tabs", TabbedContent).active = "diff"
        self.query_one("#status", Static).update(f"Alteração para {item_id} preparada em staging ({changeset.id}). Revise o diff e use Validar changeset antes de aplicar.")
        self.controller.log(f"Changeset {changeset.id} preparado para o trabalho {item_id}; nada aplicado.")

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
                tabs = ("roadmap", "workspace", "diff", "logs", "chat")
                current = self.query_one(TabbedContent).active
                self.query_one(TabbedContent).active = tabs[(tabs.index(current) + 1) % len(tabs)]
            elif action in {"workspace_view", "diff_view", "logs_view", "split_view"}:
                self.query_one(TabbedContent).active = {"workspace_view": "workspace", "diff_view": "diff", "logs_view": "logs", "split_view": "chat"}[action]

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "action-list":
            action = (event.item.id or "").removeprefix("action-")
            self._dispatch(action)
        elif event.list_view.id == "work-items" and event.list_view.index is not None:
            self._select_work_item(event.list_view.index)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-action":
            item = self.query_one("#action-list", ListView).highlighted_child
            if item:
                self._dispatch((item.id or "").removeprefix("action-"))
        elif event.button.id == "send-chat":
            self._send_chat()
        elif event.button.id == "create-plan":
            self._create_plan()
        elif event.button.id == "new-file":
            self._new_file()
        elif event.button.id in {"open-neovim", "open-neovim-markdown"}:
            await self._open_in_neovim()
        elif event.button.id == "refresh-markdown":
            await self._refresh_markdown_preview()
        elif event.button.id == "save-file":
            self._save_file()
        elif event.button.id == "discard-file":
            self._discard_file()
        elif event.button.id == "add-work":
            await self._add_work_item()
        elif event.button.id == "suggest-location":
            self._suggest_selected_location()
        elif event.button.id == "prepare-work":
            self._prepare_work_item()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat-input":
            self._send_chat()
        elif event.input.id == "new-file-path":
            self._new_file()
        elif event.input.id == "roadmap-title":
            self.run_worker(self._add_work_item, thread=False)
        elif event.input.id == "location-hint":
            self._suggest_selected_location()
        elif event.input.id == "roadmap-description":
            self.run_worker(self._add_work_item, thread=False)
        elif event.input.id == "action-input":
            item = self.query_one("#action-list", ListView).highlighted_child
            if item:
                self._dispatch((item.id or "").removeprefix("action-"))

    async def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        try:
            relative = event.path.relative_to(self.controller.root).as_posix()
            target = safe_relative(self.controller.root, relative)
            if self._editor_path and self.query_one("#file-editor", TextArea).text != self._editor_original and relative != self._editor_path:
                self.notify("Salve ou descarte as alterações abertas antes de trocar de arquivo.", severity="warning")
                return
            if target.stat().st_size > 2_000_000:
                raise ValueError("arquivo maior que 2 MB; use Neovim ou outro editor")
            content = target.read_text(encoding="utf-8")
            self._editor_path = relative
            self._editor_hash = file_hash(target)
            self._editor_original = content
            self.query_one("#file-editor", TextArea).text = content
            self.query_one("#editor-path", Static).update(relative)
            if target.suffix.lower() == ".md":
                await self._refresh_markdown_preview(content)
                self.query_one("#workspace-views", TabbedContent).active = "markdown"
            else:
                self.query_one("#workspace-views", TabbedContent).active = "editor"
        except (OSError, UnicodeError, SafetyError, ValueError) as exc:
            self.notify(f"Não foi possível abrir o arquivo: {exc}", severity="error")

    async def _refresh_markdown_preview(self, content: str | None = None) -> None:
        if not self._editor_path or not self._editor_path.lower().endswith(".md"):
            return
        viewer = self.query_one("#markdown-preview", MarkdownViewer)
        markdown = content if content is not None else self.query_one("#file-editor", TextArea).text
        viewer.show_table_of_contents = False
        try:
            await viewer.document.update(markdown)
        finally:
            viewer.show_table_of_contents = True

    async def _open_in_neovim(self) -> None:
        if not self._editor_path:
            self.notify("Selecione ou crie um arquivo antes de abrir o Neovim.", severity="warning")
            return
        editor = shutil.which("nvim")
        if not editor:
            self.notify("Neovim não encontrado no PATH.", severity="error")
            return
        if self.query_one("#file-editor", TextArea).text != self._editor_original:
            self.notify("Salve ou descarte as alterações da TUI antes de abrir o Neovim.", severity="warning")
            return
        try:
            target = safe_relative(self.controller.root, self._editor_path)
            if target.exists() and not target.is_file():
                raise SafetyError("o caminho selecionado não é um arquivo")
            target.parent.mkdir(parents=True, exist_ok=True)
            target = safe_relative(self.controller.root, self._editor_path)
            with self.suspend():
                result = subprocess.run([editor, str(target)], check=False)
            if result.returncode:
                raise RuntimeError(f"Neovim encerrou com código {result.returncode}")
            target = safe_relative(self.controller.root, self._editor_path)
            if target.is_file():
                content = target.read_text(encoding="utf-8")
                self._editor_hash = file_hash(target)
                self._editor_original = content
                self.query_one("#file-editor", TextArea).text = content
                if target.suffix.lower() == ".md":
                    await self._refresh_markdown_preview(content)
                self.query_one("#workspace-tree", DirectoryTree).reload()
                self.controller.log(f"Arquivo editado com Neovim: {self._editor_path}")
        except (OSError, UnicodeError, SafetyError, RuntimeError, ValueError) as exc:
            self.notify(f"Não foi possível abrir no Neovim: {exc}", severity="error")

    def _new_file(self) -> None:
        if self._editor_path and self.query_one("#file-editor", TextArea).text != self._editor_original:
            self.notify("Salve ou descarte as alterações abertas antes de criar outro arquivo.", severity="warning")
            return
        relative = self.query_one("#new-file-path", Input).value.strip()
        if not relative:
            self.notify("Informe um caminho relativo ao workspace.", severity="warning")
            return
        try:
            target = safe_relative(self.controller.root, relative)
            if target.exists():
                raise FileExistsError(relative)
            self._editor_path = Path(relative).as_posix()
            self._editor_hash = None
            self._editor_original = ""
            self.query_one("#file-editor", TextArea).text = ""
            self.query_one("#editor-path", Static).update(f"NOVO · {self._editor_path} · ainda não salvo")
            self.query_one("#new-file-path", Input).value = ""
            self.query_one("#file-editor", TextArea).focus()
        except (OSError, SafetyError, ValueError) as exc:
            self.notify(f"Não foi possível criar o arquivo: {exc}", severity="error")

    def _save_file(self) -> None:
        if not self._editor_path:
            self.notify("Selecione ou crie um arquivo antes de salvar.", severity="warning")
            return
        temporary = None
        try:
            target = safe_relative(self.controller.root, self._editor_path)
            if self._editor_hash is None:
                if target.exists():
                    raise FileExistsError(f"arquivo criado por outro processo: {self._editor_path}")
            elif file_hash(target) != self._editor_hash:
                raise RuntimeError("arquivo mudou desde a abertura; reabra para evitar sobrescrever edição externa")
            target.parent.mkdir(parents=True, exist_ok=True)
            target = safe_relative(self.controller.root, self._editor_path)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=target.parent,
                                             prefix=f".{target.name}.", suffix=".konstructor-tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(self.query_one("#file-editor", TextArea).text)
            if self._editor_hash is not None:
                if file_hash(target) != self._editor_hash:
                    raise RuntimeError("arquivo mudou durante o salvamento; edição não aplicada")
                os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
            elif target.exists():
                raise FileExistsError(f"arquivo criado por outro processo: {self._editor_path}")
            safe_relative(self.controller.root, self._editor_path)
            os.replace(temporary, target)
            temporary = None
            self._editor_hash = file_hash(target)
            self._editor_original = target.read_text(encoding="utf-8")
            self.query_one("#file-editor", TextArea).text = self._editor_original
            self.query_one("#editor-path", Static).update(self._editor_path)
            self.query_one("#workspace-tree", DirectoryTree).reload()
            self.controller.log(f"Arquivo salvo manualmente: {self._editor_path}")
            self.notify(f"Salvo: {self._editor_path}")
        except (OSError, UnicodeError, SafetyError, ValueError, RuntimeError) as exc:
            self.notify(f"Não foi possível salvar: {exc}", severity="error")
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def _discard_file(self) -> None:
        if not self._editor_path:
            return
        if self._editor_hash is None:
            self._editor_path = None
            self._editor_hash = None
            self._editor_original = ""
            self.query_one("#file-editor", TextArea).text = ""
            self.query_one("#editor-path", Static).update("Edição descartada; nenhum arquivo carregado.")
            return
        try:
            target = safe_relative(self.controller.root, self._editor_path)
            content = target.read_text(encoding="utf-8")
            self.query_one("#editor-path", Static).update(self._editor_path)
            self._editor_hash = file_hash(target)
            self._editor_original = content
            self.query_one("#file-editor", TextArea).text = content
            self.notify("Edição local descartada; arquivo recarregado.")
        except (OSError, UnicodeError, SafetyError, ValueError) as exc:
            self.notify(f"Não foi possível recarregar: {exc}", severity="error")

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
            self.query_one("#tabs", TabbedContent).active = "workspace"
        elif action in {"plan", "generate", "validate", "apply"}:
            self.query_one(TabbedContent).active = "diff"
        if action == "plan" and result:
            self.query_one("#action-input", Input).value = ""
        elif action == "generate" and result:
            self.query_one("#action-input", Input).value = result["changeset_id"]
        elif action == "apply" and result and self._work_item_changeset and self._work_item_changeset[1] == self.controller.changeset_id:
            item_id = self._work_item_changeset[0]
            evidence = roadmap_store.add_evidence(self.controller.root, item_id, "changeset", self.controller.changeset_id, "Changeset aplicado pela TUI.")
            roadmap_store.update_record(self.controller.root, item_id, "Aguardando aceite", evidence["evidence_id"], "Changeset aplicado; revise cada critério de aceite.")
            self._work_item_changeset = None
            self.run_worker(lambda: self._refresh_roadmap(item_id), thread=False)

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
