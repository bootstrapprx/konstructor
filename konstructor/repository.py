from __future__ import annotations
import re, subprocess, tomllib
from pathlib import Path
from .models import Finding
from .safety import ensure_root, redact

MEMBERS_RE = re.compile(r"members\s*=\s*\[(.*?)\]", re.S)
SYMBOL_RE = re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?(?:fn|struct|enum|trait|const|type|mod)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
SKIP = {".git", "target", "node_modules", ".env", ".venv"}

class KodooRepositoryAdapter:
    def __init__(self, root: str | Path): self.root = ensure_root(root)
    def git(self, *args: str) -> str:
        p = subprocess.run(["git", "-C", str(self.root), *args], text=True, capture_output=True)
        return p.stdout.strip() if p.returncode == 0 else ""
    def members(self) -> list[str]:
        manifest = self.root / "Cargo.toml"
        if not manifest.exists(): return []
        match = MEMBERS_RE.search(manifest.read_text(encoding="utf-8"))
        return re.findall(r'"([^"\\]+)"', match.group(1)) if match else []
    def inspect(self) -> dict:
        members = self.members()
        template = self.root / "modules/_template"
        standard = self.root / "scripts/standard.py"
        return {
            "root": str(self.root),
            "git": {"branch": self.git("branch", "--show-current"), "commit": self.git("rev-parse", "HEAD"), "status": self.git("status", "--short")},
            "workspace_members": members,
            "template": {"path": "modules/_template", "present": template.is_dir(), "files": sorted(str(p.relative_to(self.root)) for p in template.rglob("*") if p.is_file())} if template.is_dir() else {"path": "modules/_template", "present": False},
            "standard_script": {"path": "scripts/standard.py", "present": standard.is_file()},
        }
    def index(self) -> dict:
        findings: list[Finding] = []
        for member in self.members():
            path = self.root / member
            manifest = path / "Cargo.toml"
            if not manifest.exists():
                findings.append(Finding("member", member, "manifest ausente", "unknown")); continue
            text = manifest.read_text(encoding="utf-8", errors="replace")
            package = re.search(r'^\s*name\s*=\s*"([^"]+)"', text, re.M)
            findings.append(Finding("module", member, f"package={package.group(1) if package else 'unknown'}", "verified" if package else "unknown"))
            for source in sorted(path.rglob("*.rs")):
                if any(part in SKIP for part in source.parts): continue
                content = source.read_text(encoding="utf-8", errors="replace")
                for match in SYMBOL_RE.finditer(content):
                    findings.append(Finding("symbol", str(source.relative_to(self.root)), match.group(1), "verified", content[:match.start()].count("\n") + 1))
        return {"root": str(self.root), "source_of_truth": "workspace files", "findings": [f.__dict__ for f in sorted(findings, key=lambda f: (f.path, f.line or 0, f.detail))], "inspect": self.inspect()}


def source_excerpt(root: Path, relative: str, max_chars: int = 4000) -> str:
    path = root / relative
    if path.suffix in {".env", ".pem", ".key"} or "secret" in path.name.lower(): return "[REDACTED]"
    return redact(path.read_text(encoding="utf-8", errors="replace")[:max_chars])
