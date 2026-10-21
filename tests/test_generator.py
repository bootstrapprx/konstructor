import os
import logging
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from konstructor.repository import KodooRepositoryAdapter
from konstructor.planner import make_plan, write_plan
from konstructor.workflow import generate, validate, diff, apply, stage_edits
from konstructor.safety import SafetyError, safe_relative
from konstructor.themes import Theme, load_theme
from konstructor.tui import COMMANDS, GeneratorTUI, LiveLogBuffer, TUILogHandler, TABS
from konstructor.config import load_tui_config
from konstructor.web_terminal import WebTerminalApp, WebTerminalServer, make_server
from konstructor.wizard import create_module_proposal
from konstructor.wizard import create_app_proposal, load_proposal, validate_proposal, apply_app_proposal
from konstructor.provider import OpenAICompatibleProvider
from konstructor.provider import _validate_schema
from konstructor.roadmap import import_workbook, list_records, show_record, update_record, dashboard, export_json

class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.repo = Path(self.tmp.name)
        os.environ["KONSTRUCTOR_HOME"] = str(self.repo / "state")
        (self.repo / "Cargo.toml").write_text('[workspace]\nmembers = ["modules/people"]\n', encoding="utf-8")
        (self.repo / "modules/people/src").mkdir(parents=True)
        (self.repo / "modules/people/Cargo.toml").write_text('[package]\nname = "people"\n', encoding="utf-8")
        (self.repo / "modules/people/src/lib.rs").write_text('pub struct Person;\npub fn create_person() {}\n', encoding="utf-8")
        (self.repo / "modules/_template").mkdir(parents=True, exist_ok=True); (self.repo / "scripts").mkdir()
        (self.repo / "scripts/standard.py").write_text('# standard', encoding="utf-8")
    def tearDown(self): self.tmp.cleanup()
    def test_inspect_and_index_have_source_evidence(self):
        adapter = KodooRepositoryAdapter(self.repo); inspection = adapter.inspect(); catalog = adapter.index()
        self.assertEqual(inspection["workspace_members"], ["modules/people"])
        symbols = [x["detail"] for x in catalog["findings"] if x["kind"] == "symbol"]
        self.assertIn("Person", symbols); self.assertIn("create_person", symbols)
    def test_plan_and_generate_never_modify_target(self):
        before = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob("*") if p.is_file() and p.relative_to(self.repo).parts[:1] != ("state",)}
        plan = make_plan(self.repo, "criar módulo de pessoas", KodooRepositoryAdapter(self.repo).index()); write_plan(self.repo, plan); cs = generate(self.repo, plan.id)
        after = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob("*") if p.is_file() and p.relative_to(self.repo).parts[:1] != ("state",)}
        self.assertEqual(before, after); self.assertTrue(Path(cs.staging).is_dir())
        self.assertIn("modules/criar-modulo-de-pessoas/src/domain.rs", [x["path"] for x in cs.files])
    def test_diff_validate_and_apply_require_approval(self):
        plan = make_plan(self.repo, "nova capacidade", KodooRepositoryAdapter(self.repo).index()); write_plan(self.repo, plan); cs = generate(self.repo, plan.id)
        self.assertIn("+++", diff(self.repo, cs.id))
        with self.assertRaisesRegex(RuntimeError, "validação aprovada"):
            apply(self.repo, cs.id, yes=True)
        result = validate(self.repo, cs.id)
        self.assertTrue(any(x["name"] == "path-policy" and x["status"] == "passed" for x in result["checks"]))
        with self.assertRaises(PermissionError): apply(self.repo, cs.id)
        changed = apply(self.repo, cs.id, yes=True); self.assertTrue(changed and (self.repo / changed[0]).exists())

    def test_staging_tamper_and_add_collision_block_apply(self):
        plan = make_plan(self.repo, "integridade", KodooRepositoryAdapter(self.repo).index()); write_plan(self.repo, plan); cs = generate(self.repo, plan.id)
        validate(self.repo, cs.id)
        staged = Path(cs.staging) / cs.files[0]["path"]
        staged.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "adulterados"):
            apply(self.repo, cs.id, yes=True)
        cs = generate(self.repo, plan.id); validate(self.repo, cs.id)
        target = self.repo / cs.files[0]["path"]; target.parent.mkdir(parents=True, exist_ok=True); target.write_text("existing", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "conflito"):
            apply(self.repo, cs.id, yes=True)
    def test_conflict_blocks_apply(self):
        plan = make_plan(self.repo, "conflito", KodooRepositoryAdapter(self.repo).index()); write_plan(self.repo, plan); cs = generate(self.repo, plan.id)
        first = self.repo / cs.files[0]["path"]; first.parent.mkdir(parents=True, exist_ok=True); first.write_text("human edit", encoding="utf-8")
        validate(self.repo, cs.id)
        with self.assertRaisesRegex(RuntimeError, "alvo mudou"): apply(self.repo, cs.id, yes=True)
    def test_path_traversal_is_rejected(self):
        with self.assertRaises(SafetyError): safe_relative(self.repo, "../outside.txt")

    def test_tui_theme_can_be_loaded_from_custom_json(self):
        theme_path = self.repo / "theme.json"
        theme_path.write_text('{"name":"qa","header":"magenta","selected":"yellow"}', encoding="utf-8")
        theme = load_theme(theme_path)
        self.assertEqual(theme.name, "qa")
        self.assertEqual(theme.header, "magenta")
        self.assertEqual(theme.selected, "yellow")
        self.assertEqual(theme.diff_add, Theme().diff_add)

    def test_tui_tabs_and_keyboard_navigation_are_deterministic(self):
        app = GeneratorTUI(self.repo)
        self.assertEqual(app.view, "split")
        self.assertEqual(app.cycle_view(), "workspace")
        self.assertEqual(app.cycle_view(), "diff")
        self.assertEqual(app.cycle_view(), "logs")
        self.assertEqual(app.select_view("split"), "split")
        self.assertEqual(app.move_selection(1), 1)
        self.assertEqual(app.move_selection(-1), 0)
        with self.assertRaises(ValueError): app.select_view("unknown")
        self.assertTrue({"inspect", "index", "plan", "generate", "validate", "diff", "apply", "status"} <= COMMANDS)
        self.assertEqual(TABS, ("workspace", "diff", "logs", "split"))

    def test_live_log_buffer_and_logging_handler_are_thread_safe(self):
        buffer = LiveLogBuffer(limit=2)
        buffer.append("first"); buffer.append("second"); buffer.append("third")
        self.assertEqual(buffer.snapshot(), ["second", "third"])
        handler = TUILogHandler(buffer); handler.setFormatter(logging.Formatter("%(levelname)s:%(message)s"))
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
        handler.emit(record)
        self.assertEqual(buffer.snapshot()[-1], "INFO:hello")

    def test_custom_shortcuts_are_used_by_terminal_and_web_input(self):
        config_path = self.repo / "tui.json"
        config_path.write_text(json.dumps({"shortcuts": {"quit": "x", "logs_view": "l", "next_view": "v"}}), encoding="utf-8")
        config = load_tui_config(config_path)
        app = GeneratorTUI(self.repo, config=config_path)
        self.assertEqual(config.key_for("quit"), "x")
        self.assertTrue(app.shortcut_matches("x", "quit"))
        self.assertFalse(app.shortcut_matches("q", "quit"))
        app.handle_web_key("l")
        self.assertEqual(app.view, "logs")

    def test_web_terminal_serves_state_and_commands(self):
        app = WebTerminalApp(self.repo)
        try:
            server = WebTerminalServer(("127.0.0.1", 0), app)
        except PermissionError as exc:
            self.skipTest(f"sandbox bloqueia sockets locais: {exc}")
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/state") as response:
                state = json.loads(response.read())
            self.assertEqual(state["workspace"], str(self.repo))
            self.assertIn("logs", state); self.assertEqual(state["view"], "split")
            request = urllib.request.Request(base + "/action", data=json.dumps({"action": "inspect"}).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request) as response:
                result = json.loads(response.read())
            self.assertTrue(result["ok"]); self.assertIn("workspace_members", result["result"])
            request = urllib.request.Request(base + "/input", data=b'{"key":"3"}', headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request) as response: json.loads(response.read())
            self.assertEqual(app.controller.view, "logs")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_remote_web_bind_is_rejected(self):
        with self.assertRaisesRegex(PermissionError, "loopback"):
            make_server("0.0.0.0", 8765, self.repo)

    def test_manual_module_wizard_proposes_without_writing_target(self):
        (self.repo / "scripts").mkdir(exist_ok=True)
        (self.repo / "scripts/standard.py").write_text("# scaffold", encoding="utf-8")
        (self.repo / "modules/_template").mkdir(parents=True, exist_ok=True)
        result = create_module_proposal(self.repo, "inventory", "Gerenciar estoque", "yes")
        self.assertEqual(result["path"], "modules/inventory")
        self.assertFalse(result["writes_target"])
        self.assertFalse((self.repo / "modules/inventory").exists())
        loaded = load_proposal(self.repo, result["proposal_id"])
        self.assertEqual(loaded["name"], "inventory")

    def test_new_app_wizard_is_honest_about_no_scaffold_and_does_not_write(self):
        (self.repo / "apps").mkdir(exist_ok=True)
        (self.repo / "Cargo.toml").write_text('[workspace]\nmembers = []\n', encoding="utf-8")
        proposal = create_app_proposal(self.repo, "reporting", "Relatórios de operação")
        self.assertEqual(proposal["status"], "proposed")
        self.assertFalse((self.repo / proposal["path"]).exists())

    def test_app_proposal_review_validate_and_approved_apply(self):
        (self.repo / "apps").mkdir(exist_ok=True)
        (self.repo / "Cargo.toml").write_text('[workspace]\nmembers = [\n]\n[workspace.package]\nversion = "0.1.0"\nedition = "2024"\n[workspace.dependencies]\ndioxus = { version = "0.7", default-features = false }\n', encoding="utf-8")
        proposal = create_app_proposal(self.repo, "reporting", "Relatórios de operação")
        self.assertTrue(validate_proposal(proposal)["passed"])
        with self.assertRaises(PermissionError): apply_app_proposal(proposal, False)
        changed = apply_app_proposal(proposal, True)
        self.assertIn("Cargo.toml", changed)
        self.assertTrue((self.repo / "apps/reporting/src/lib.rs").is_file())
        self.assertIn('"apps/reporting"', (self.repo / "Cargo.toml").read_text())

    def test_saved_app_proposal_remains_valid_after_reload(self):
        (self.repo / "apps").mkdir(exist_ok=True)
        (self.repo / "Cargo.toml").write_text('[workspace]\nmembers = []\n', encoding="utf-8")
        proposal = create_app_proposal(self.repo, "reporting", "Relatórios")
        loaded = load_proposal(self.repo, proposal["proposal_id"])
        self.assertTrue(validate_proposal(loaded)["passed"])

    def test_app_proposal_rejects_artifacts_outside_its_app(self):
        (self.repo / "apps").mkdir(exist_ok=True)
        (self.repo / "Cargo.toml").write_text('[workspace]\nmembers = []\n', encoding="utf-8")
        proposal = create_app_proposal(self.repo, "reporting", "Relatórios")
        proposal["artifacts"]["backend/Cargo.toml"] = "[package]"
        checks = validate_proposal(proposal)
        self.assertFalse(checks["artifacts_safe"])
        self.assertFalse(checks["passed"])

    def test_app_proposal_rejects_symlink_escape(self):
        (self.repo / "apps").symlink_to(self.repo.parent, target_is_directory=True)
        (self.repo / "Cargo.toml").write_text('[workspace]\nmembers = []\n', encoding="utf-8")
        proposal = create_app_proposal(self.repo, "reporting", "Relatórios")
        checks = validate_proposal(proposal)
        self.assertFalse(checks["artifacts_safe"])
        self.assertFalse(checks["passed"])

    def test_generated_app_documents_unwired_product_surfaces(self):
        (self.repo / "apps").mkdir(exist_ok=True)
        (self.repo / "Cargo.toml").write_text('[workspace]\nmembers = []\n', encoding="utf-8")
        proposal = create_app_proposal(self.repo, "reporting", "Relatórios")
        readme = proposal["artifacts"]["apps/reporting/README.md"]
        self.assertIn("dx serve", readme)
        self.assertIn("Direct", readme)
        self.assertIn("navegação", readme)

    def test_validation_uses_configured_temp_directory(self):
        import konstructor.workflow as workflow
        from unittest.mock import patch
        temp_parent = self.repo / "validation-tmp"
        temp_parent.mkdir()
        plan = make_plan(self.repo, "temporary validation", KodooRepositoryAdapter(self.repo).index())
        write_plan(self.repo, plan)
        cs = generate(self.repo, plan.id)
        calls = []
        real_tempdir = workflow.tempfile.TemporaryDirectory
        def record_tempdir(*args, **kwargs):
            calls.append(kwargs.get("dir"))
            return real_tempdir(*args, **kwargs)
        previous = os.environ.get("KONSTRUCTOR_TMPDIR")
        os.environ["KONSTRUCTOR_TMPDIR"] = str(temp_parent)
        try:
            with patch.object(workflow.tempfile, "TemporaryDirectory", side_effect=record_tempdir):
                workflow.validate(self.repo, cs.id)
        finally:
            if previous is None: os.environ.pop("KONSTRUCTOR_TMPDIR", None)
            else: os.environ["KONSTRUCTOR_TMPDIR"] = previous
        self.assertEqual(calls, [str(temp_parent)])

    def test_provider_structured_schema_validation(self):
        schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False}
        _validate_schema({"title": "ok"}, schema)
        with self.assertRaisesRegex(RuntimeError, "missing required"):
            _validate_schema({}, schema)

    def test_existing_file_edit_is_staged_and_applied_after_validation(self):
        source = self.repo / "modules/people/src/lib.rs"
        class Provider:
            def complete_structured(self, request, schema):
                return {"files": [{"path": "modules/people/src/lib.rs", "content": "pub struct Person { pub name: String }\n"}]}
        cs = stage_edits(self.repo, "Add a name field", ["modules/people/src/lib.rs"], Provider())
        self.assertEqual(source.read_text(), "pub struct Person;\npub fn create_person() {}\n")
        self.assertIn("+pub struct Person", diff(self.repo, cs.id))
        checked = validate(self.repo, cs.id)
        if checked["validation_passed"]:
            apply(self.repo, cs.id, yes=True)
            self.assertIn("pub struct Person", source.read_text())
        else:
            self.assertTrue(any(c["status"] in {"blocked", "failed"} for c in checked["checks"]))
            with self.assertRaisesRegex(RuntimeError, "validação aprovada"):
                apply(self.repo, cs.id, yes=True)

    def test_edit_provider_cannot_add_unselected_paths(self):
        class Provider:
            def complete_structured(self, request, schema):
                return {"files": [{"path": "../outside", "content": "bad"}]}
        with self.assertRaisesRegex(SafetyError, "não selecionado"):
            stage_edits(self.repo, "bad edit", ["modules/people/src/lib.rs"], Provider())

    def test_edit_changeset_rejects_target_change_after_validation(self):
        source = self.repo / "modules/people/src/lib.rs"
        class Provider:
            def complete_structured(self, request, schema):
                return {"files": [{"path": "modules/people/src/lib.rs", "content": "pub struct Person { pub name: String }\n"}]}
        cs = stage_edits(self.repo, "add name", ["modules/people/src/lib.rs"], Provider())
        result = validate(self.repo, cs.id)
        if not result["validation_passed"]: self.skipTest("isolated Cargo formatting unavailable for fixture")
        source.write_text("changed after validation", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "alvo mudou"):
            apply(self.repo, cs.id, yes=True)

    def test_provider_posts_openai_compatible_request(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import json as json_module
        received = {}
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received["body"] = json_module.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body = json_module.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self, *args): pass
        try:
            server = HTTPServer(("127.0.0.1", 0), Handler)
        except PermissionError as exc:
            self.skipTest(f"sandbox bloqueia sockets locais: {exc}")
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            provider = OpenAICompatibleProvider(f"http://127.0.0.1:{server.server_port}/v1", "test-model", "secret")
            self.assertEqual(provider.complete_structured("hello", {"type": "object"}), {"ok": True})
            self.assertEqual(received["body"]["model"], "test-model")
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)

    def test_roadmap_import_preserves_records_and_tracks_updates(self):
        from zipfile import ZipFile
        book = self.repo / "roadmap.xlsx"
        with ZipFile(book, "w") as z:
            z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Roadmap" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="5"><c r="A5" t="inlineStr"><is><t>ID</t></is></c><c r="F5" t="inlineStr"><is><t>Priority</t></is></c><c r="M5" t="inlineStr"><is><t>Current Status</t></is></c></row><row r="6"><c r="A6" t="inlineStr"><is><t>RM-001</t></is></c><c r="F6" t="inlineStr"><is><t>P0</t></is></c><c r="M6" t="inlineStr"><is><t>A validar</t></is></c></row></sheetData></worksheet>')
        imported = import_workbook(self.repo, book)
        self.assertEqual(imported["sheets"], 1)
        self.assertEqual(len(list_records(self.repo, "Roadmap")), 1)
        self.assertEqual(show_record(self.repo, "RM-001")["data"]["Current Status"], "A validar")
        updated = update_record(self.repo, "RM-001", "Em andamento", "tests/roadmap.sql")
        self.assertEqual(updated["current"]["status"], "Em andamento")
        update_record(self.repo, "RM-001", None, None, "audit note")
        self.assertEqual(show_record(self.repo, "RM-001")["current"]["evidence"], "tests/roadmap.sql")
        import_workbook(self.repo, book)
        self.assertEqual(show_record(self.repo, "RM-001")["current"]["status"], "Em andamento")
        self.assertEqual(dashboard(self.repo)["p0_open"], 1)
        backup = export_json(self.repo, self.repo / "backup.json")
        self.assertTrue(backup.is_file())

    def test_tui_commands_execute_through_the_same_workflow(self):
        app = GeneratorTUI(self.repo)
        inspected = app.perform("inspect")
        self.assertEqual(inspected["workspace_members"], ["modules/people"])
        indexed = app.perform("index")
        self.assertTrue(indexed["findings"])
        planned = app.perform("plan", request="criar módulo de auditoria")
        generated = app.perform("generate", plan_id=planned["plan_id"])
        self.assertTrue(generated["changeset_id"])
        validated = app.perform("validate", changeset_id=generated["changeset_id"])
        self.assertTrue(validated["checks"])
        reviewed = app.perform("diff", changeset_id=generated["changeset_id"])
        self.assertIn("diff", reviewed)
        with self.assertRaises(PermissionError):
            app.perform("apply", changeset_id=generated["changeset_id"])

if __name__ == "__main__": unittest.main()
