# Konstructor MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Codex use Konstructor's local roadmap and safe change-management tools as an MCP client, with Codex providing the model and generation.

**Architecture:** A stdio JSON-RPC MCP server is a thin adapter over existing repository, roadmap, workflow, and proposal functions. It is pinned to `KONSTRUCTOR_WORKSPACE`. Target writes require a local one-time approval token bound to a validated changeset.

**Tech Stack:** Python 3.11+ standard library; existing SQLite state, JSON, and filesystem safety functions.

**Spec:** `docs/superpowers/specs/2026-10-20-konstructor-mcp-design.md`

## Global Constraints

- Keep MCP stdio local; do not add HTTP or remote control.
- Do not add a dependency.
- Never invoke the configured LLM provider from MCP.
- Do not trust a client-supplied approval boolean.
- Apply only changesets with passed, current validation and matching staged/base hashes.
- Confine every operation to the process-configured absolute workspace.
- Preserve the spreadsheet source and SQLite import/update history.

## Review Focus

- Client changes workspace path or uses an absolute/traversal path: reject; test per tool.
- Duplicate JSON-RPC IDs, notifications, malformed and oversized lines: protocol behavior stays valid and bounded; protocol tests.
- Validation is blocked or stale: approval cannot be issued or consumed; approval/workflow tests.
- Approval token is replayed or bound to another changeset: reject and consume once; approval tests.
- Provider-shaped content contains secrets or unexpected paths: do not log secrets and reject unselected/out-of-root files; staging tests.

---

### Task 1: Explicit staged artifact API

**Files:**
- Modify: `konstructor/workflow.py`
- Test: `tests/test_generator.py`

**Interfaces:**
- Produces `stage_artifacts(root: Path, request: str, artifacts: list[dict]) -> ChangeSet` for `add` and `modify` only.
- Each artifact has `path`, `action`, `content`, and optional `reason`; reject duplicate paths and unknown actions.

- [ ] Add tests for add, modify, collision, missing modify target, traversal, and symlink escape.
- [ ] Run focused tests and observe failures.
- [ ] Implement staging with exact artifact/base hashes and no target writes.
- [ ] Run focused tests.

### Task 2: One-time human approval records

**Files:**
- Create: `konstructor/approvals.py`
- Modify: `konstructor/cli.py`
- Modify: `konstructor/workflow.py`
- Test: `tests/test_generator.py`

**Interfaces:**
- `approve_changeset(root: Path, changeset_id: str, confirmation: str) -> str` verifies interactive confirmation and passed/current validation, stores a random one-time token bound to hashes and expiry.
- `apply_approved(root: Path, changeset_id: str, token: str) -> list[str]` validates/rechecks, consumes the token once, then calls safe apply.

- [ ] Test wrong confirmation, absent/failed/stale validation, token replay, wrong changeset and altered artifact/base.
- [ ] Run focused tests and observe failures.
- [ ] Implement CLI review-and-type confirmation; never print token to logs.
- [ ] Implement one-time token consumption and apply binding.
- [ ] Run focused tests.

### Task 3: MCP JSON-RPC stdio adapter

**Files:**
- Create: `konstructor/mcp_server.py`
- Modify: `konstructor/cli.py`
- Test: `tests/test_mcp.py`

**Interfaces:**
- `tools() -> list[dict]` returns MCP tool contracts and schemas.
- `handle(message: dict, workspace: Path) -> dict | None` handles protocol and tools.
- `serve(stdin, stdout, stderr, workspace) -> int` processes bounded newline-delimited messages.
- MCP tools cover repository inspection/index/planning, app proposal, explicit staging, diff, validate, apply-with-token, roadmap dashboard/list/get/update/import/export.

- [ ] Test initialize, tool listing, representative tool calls, error paths and notifications.
- [ ] Test malformed JSON, a line over 2 MiB, missing workspace, and no stdout diagnostics.
- [ ] Implement fixed workspace root and schema-validated tool dispatch.
- [ ] Add `konstructor mcp` stdio command; make no provider calls.
- [ ] Run protocol tests and a subprocess pipe round trip.

### Task 4: Documentation and final verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-10-20-konstructor-mcp-design.md`
- Test: `tests/test_mcp.py`, `tests/test_generator.py`

- [ ] Document Codex stdio configuration, environment, available tools and the approval-token flow.
- [ ] Run `python -m unittest discover -s tests -v` and `git diff --check`.
- [ ] Confirm the Kodoo worktree is unchanged and report any sandbox-skipped tests.
