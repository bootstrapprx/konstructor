# Konstructor MCP server design

## Intent

Let Codex act as the reasoning and code-generation model while Konstructor supplies local, repository-aware tools. Konstructor must not call its configured LLM provider for MCP tasks. The same interface must expose roadmap management and safe app/module editing.

## Goals

- Implement a local MCP server over newline-delimited JSON-RPC on stdio, using only the Python standard library.
- Expose all current non-interactive Konstructor capabilities that are useful to Codex: repository inspection/indexing, plan and app proposal workflows, staged edits, diff, validation, roadmap dashboard/list/show/update/import/export, and changeset application after independently verified approval.
- Bind the server to one workspace selected in its process environment (`KONSTRUCTOR_WORKSPACE`); clients cannot select arbitrary roots per request.
- Keep target files untouched until a staged changeset passes validation and an explicit human approval is recorded locally.
- Return structured tool results and actionable errors; write protocol messages only to stdout and diagnostics only to stderr.

## Non-goals

- No HTTP MCP transport, remote access, TUI/web control, shell execution, package publication, or arbitrary command tool.
- No LLM provider calls from MCP. Codex supplies proposed file contents to staging tools.
- No claim that unavailable or blocked validation succeeded.

## Tool surface

- `konstructor_workspace_inspect`, `konstructor_repository_index`, `konstructor_plan_create`.
- `konstructor_app_proposal` for supported Kodoo module and Dioxus app proposals.
- `konstructor_changeset_stage` accepts an explicit list of `{path, action, content, reason}`; `add` requires a missing path, `modify` requires an existing regular file. All paths are workspace-relative and confined to the configured root.
- `konstructor_changeset_diff`, `konstructor_changeset_validate`, and `konstructor_changeset_apply`.
- `konstructor_roadmap_dashboard`, `konstructor_roadmap_list`, `konstructor_roadmap_get`, `konstructor_roadmap_update`, `konstructor_roadmap_import`, and `konstructor_roadmap_export`.
- Tools that can mutate local state are marked accordingly in descriptions. Applying target changes additionally requires a one-time approval token issued by the interactive CLI for that exact validated changeset.

## Approval and integrity

The CLI command `konstructor approve <workspace> <changeset-id>` prints the reviewed diff and requires the operator to type `APPLY <changeset-id>`. It then records a one-time token bound to workspace, changeset ID, validated artifact hashes, base hashes, and expiration. The MCP apply tool consumes that token exactly once, rechecks all bindings and hashes, and delegates to the existing safe apply path. A client-supplied boolean is never authorization. Roadmap metadata updates remain auditable SQLite state changes and do not write into the target repository.

## Protocol

Support `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`. Use JSON-RPC 2.0 request IDs as supplied; suppress responses to notifications; reject malformed JSON and unknown methods/tools with protocol errors. Each tool result contains JSON text and `structuredContent`. Cap one input line at 2 MiB and return bounded errors without secrets.

## Configuration and operation

Launch with `KONSTRUCTOR_WORKSPACE=/absolute/path/to/repo konstructor mcp`. The process is local and inherits normal filesystem permissions. The workbook is imported with the existing roadmap import operation; MCP reports a clear error until import has occurred. Configure the command as an MCP stdio server in Codex.

## Acceptance

- Protocol tests cover initialize, tool discovery, valid/invalid calls, notifications, malformed/oversized lines, and stdio round trips.
- Tool tests cover root confinement, file collision, staged hash changes, stale target changes, blocked validation, approval binding and one-time consumption.
- Roadmap tools read/update/import/export only the configured workspace's Konstructor state.
- No MCP operation invokes the OpenAI-compatible provider or shell commands.
