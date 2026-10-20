from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any
import json

@dataclass
class Finding:
    kind: str
    path: str
    detail: str
    confidence: str = "verified"
    line: int | None = None

@dataclass
class CheckResult:
    name: str
    status: str
    command: str | None = None
    summary: str = ""
    returncode: int | None = None

@dataclass
class Plan:
    id: str
    workspace: str
    request: str
    objective: str
    files: list[dict[str, Any]]
    reused: list[dict[str, Any]]
    dependencies: list[str]
    surfaces: list[str]
    tests: list[str]
    risks: list[str]
    facts: list[str]
    inferences: list[str]
    questions: list[str]

@dataclass
class ChangeSet:
    id: str
    workspace: str
    plan_id: str
    staging: str
    files: list[dict[str, Any]]
    base_hashes: dict[str, str | None]
    checks: list[CheckResult] = field(default_factory=list)
    approved: bool = False
    applied: bool = False

def dump(obj: Any) -> str:
    return json.dumps(asdict(obj), ensure_ascii=False, indent=2, sort_keys=True)
