import os
import pty
import select
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


class InstallerPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.prefix = self.root / "prefix with space"
        self.env = {**os.environ, "HOME": str(self.root), "SHELL": "/bin/zsh"}
        self.cwd = REPO

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, *args):
        return subprocess.run(
            ["bash", str(REPO / "install.sh"), "--prefix", str(self.prefix), *args],
            cwd=self.cwd, env=self.env, text=True, capture_output=True, check=True,
        )

    def make_kodoo(self):
        root = self.root / "kodoo"
        (root / "apps" / "demo").mkdir(parents=True)
        (root / "modules" / "_template").mkdir(parents=True)
        (root / "scripts").mkdir()
        (root / "Cargo.toml").write_text('[workspace]\nmembers = []\n')
        (root / "scripts" / "standard.py").touch()
        return root

    def test_install_does_not_edit_shell_config_without_opt_in(self):
        result = self.install()
        self.assertFalse((self.root / ".zshrc").exists())
        self.assertIn("--add-to-path", result.stdout)

    def test_path_opt_in_edits_detected_shell_config_once(self):
        self.install("--add-to-path")
        self.install("--add-to-path")
        rc = (self.root / ".zshrc").read_text()
        entry = f"export PATH='{self.prefix}/bin':\"$PATH\""
        self.assertEqual(rc.count(entry), 1)

    def test_path_opt_in_uses_bashrc_for_bash(self):
        self.env["SHELL"] = "/bin/bash"
        self.install("--add-to-path")
        self.assertIn(str(self.prefix / "bin"), (self.root / ".bashrc").read_text())

    def test_workspace_option_accepts_app_path_and_launcher_uses_kodoo_root(self):
        root = self.make_kodoo()
        self.install("--workspace", str(root / "apps" / "demo"))
        installed = self.prefix / "bin" / "konstructor"
        launcher_env = {**self.env, "KONSTRUCTOR_HOME": str(self.root / "state")}
        launcher_env.pop("KONSTRUCTOR_WORKSPACE", None)
        result = subprocess.run(
            [str(installed), "status", "--json"], env=launcher_env,
            text=True, capture_output=True, check=True,
        )
        self.assertEqual(__import__("json").loads(result.stdout)["workspace"], str(root))

    def test_workspace_is_detected_from_current_app_directory(self):
        root = self.make_kodoo()
        self.cwd = root / "apps" / "demo"
        self.install()
        configured = (self.prefix / "share" / "konstructor" / "workspace").read_text().strip()
        self.assertEqual(configured, str(root))

    def test_interactive_prompt_can_select_app_path(self):
        root = self.make_kodoo()
        master, slave = pty.openpty()
        process = subprocess.Popen(
            ["bash", str(REPO / "install.sh"), "--prefix", str(self.prefix)],
            cwd=REPO, env=self.env, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
        )
        os.close(slave)
        output, sent = bytearray(), False
        while process.poll() is None:
            ready, _, _ = select.select([master], [], [], 0.2)
            if not ready:
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            output.extend(chunk)
            if not sent and b"Kodoo ou app existente" in output:
                os.write(master, f"{root / 'apps' / 'demo'}\n".encode())
                sent = True
        os.close(master)
        self.assertEqual(process.wait(), 0, output.decode(errors="replace"))
        self.assertTrue(sent)
        configured = (self.prefix / "share" / "konstructor" / "workspace").read_text().strip()
        self.assertEqual(configured, str(root))

    def test_invalid_explicit_workspace_fails_before_install(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.install("--workspace", str(self.root / "not-kodoo"))
        self.assertFalse(self.prefix.exists())


if __name__ == "__main__":
    unittest.main()
