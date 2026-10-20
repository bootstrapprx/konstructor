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
from konstructor.workflow import generate, validate, diff, apply
from konstructor.safety import SafetyError, safe_relative
from konstructor.themes import Theme, load_theme
from konstructor.tui import COMMANDS, GeneratorTUI, LiveLogBuffer, TUILogHandler, TABS
from konstructor.config import load_tui_config
from konstructor.web_terminal import WebTerminalApp, WebTerminalServer, make_server
from konstructor.wizard import create_module_proposal
from konstructor.wizard import create_app_proposal, load_proposal
from konstructor.provider import OpenAICompatibleProvider
from konstructor.provider import _validate_schema

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
        (self.repo / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
        proposal = create_app_proposal(self.repo, "reporting", "Relatórios de operação")
        self.assertEqual(proposal["status"], "proposal-only")
        self.assertFalse((self.repo / proposal["path"]).exists())

    def test_provider_structured_schema_validation(self):
        schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False}
        _validate_schema({"title": "ok"}, schema)
        with self.assertRaisesRegex(RuntimeError, "missing required"):
            _validate_schema({}, schema)

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
