from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from .safety import ensure_root
from .state import workspace_dir

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships", "p": "http://schemas.openxmlformats.org/package/2006/relationships"}
TABLE_HEADERS = {"Roadmap": 5, "Telas": 4, "Funções": 4, "Arquétipos": 4, "Fases": 4, "Modules": 4, "Constants & Enums": 4, "Metrics & Formulas": 4, "Projections & Dashboards": 4, "Dependencies": 4, "Decisões pendentes": 4, "Gates de aceite": 4, "Auditoria": 4, "Manifesto de Arquivos": 4}


def _column_number(cell: str) -> int:
    value = 0
    for char in re.match(r"[A-Z]+", cell).group(): value = value * 26 + ord(char) - 64
    return value


def read_xlsx(path: str | Path) -> dict[str, list[dict]]:
    """Read workbook cells with stdlib; keep formulas as formula strings when uncached."""
    path = Path(path)
    with zipfile.ZipFile(path) as book:
        strings = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            strings = ["".join(t.text or "" for t in si.findall(".//m:t", NS)) for si in root.findall("m:si", NS)]
        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in rels}
        result = {}
        for sheet in workbook.findall("m:sheets/m:sheet", NS):
            target = targets[sheet.attrib[f"{{{NS['r']}}}id"]]
            member = str(PurePosixPath("xl") / target) if not target.startswith("/") else target.lstrip("/")
            member = str(PurePosixPath(member))
            root = ET.fromstring(book.read(member))
            rows = []
            for row in root.findall(".//m:sheetData/m:row", NS):
                cells = {}
                for cell in row.findall("m:c", NS):
                    value = cell.find("m:v", NS); formula = cell.find("m:f", NS)
                    inline = cell.find("m:is", NS)
                    if cell.attrib.get("t") == "s" and value is not None: content = strings[int(value.text)]
                    elif cell.attrib.get("t") == "inlineStr" and inline is not None: content = "".join(t.text or "" for t in inline.findall(".//m:t", NS))
                    elif value is not None: content = value.text
                    elif formula is not None: content = "=" + (formula.text or "")
                    else: continue
                    cells[_column_number(cell.attrib["r"])] = content
                if cells: rows.append({"row": int(row.attrib["r"]), "cells": cells})
            result[sheet.attrib["name"]] = rows
        return result


def _database(root: str | Path) -> sqlite3.Connection:
    root = ensure_root(root)
    db = sqlite3.connect(workspace_dir(root) / "roadmap.sqlite3")
    db.row_factory = sqlite3.Row
    db.executescript("""
      PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS imports(id INTEGER PRIMARY KEY, source TEXT NOT NULL, sha256 TEXT NOT NULL, imported_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS records(import_id INTEGER NOT NULL REFERENCES imports(id), sheet TEXT NOT NULL, row_num INTEGER NOT NULL, record_id TEXT NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(import_id,sheet,row_num));
      CREATE TABLE IF NOT EXISTS sheet_schemas(import_id INTEGER NOT NULL REFERENCES imports(id), sheet TEXT NOT NULL, schema_json TEXT NOT NULL, PRIMARY KEY(import_id,sheet));
      CREATE INDEX IF NOT EXISTS records_lookup ON records(import_id,record_id);
      CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES imports(id), record_id TEXT NOT NULL, status TEXT, evidence TEXT, note TEXT, updated_at TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS updates_lookup ON updates(import_id,record_id,id);
      CREATE TABLE IF NOT EXISTS work_evidence(import_id INTEGER NOT NULL REFERENCES imports(id), record_id TEXT NOT NULL, evidence_id TEXT NOT NULL, kind TEXT NOT NULL, reference TEXT NOT NULL, summary TEXT NOT NULL, sha256 TEXT, created_at TEXT NOT NULL, PRIMARY KEY(import_id,evidence_id));
      CREATE INDEX IF NOT EXISTS work_evidence_item ON work_evidence(import_id,record_id);
      CREATE TABLE IF NOT EXISTS criterion_reviews(id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL REFERENCES imports(id), record_id TEXT NOT NULL, criterion_id TEXT NOT NULL, decision TEXT NOT NULL, evidence_ids_json TEXT NOT NULL, reviewer TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS criterion_reviews_latest ON criterion_reviews(import_id,record_id,criterion_id,id);
      CREATE TABLE IF NOT EXISTS criteria_overrides(import_id INTEGER NOT NULL REFERENCES imports(id), record_id TEXT NOT NULL, criteria_json TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(import_id,record_id));
    """)
    return db


def _headers(rows: list[dict], row_num: int) -> dict[int, str]:
    row = next((r for r in rows if r["row"] == row_num), None)
    if not row: return {}
    return {col: str(value).strip() for col, value in row["cells"].items() if str(value).strip()}


def _make_criteria(lines: list[str]) -> list[dict]:
    lines = [line.strip().lstrip("-•* ").strip() for line in lines if line.strip()]
    return [{"id": "AC-" + hashlib.sha256(f"{index}:{line}".encode()).hexdigest()[:10], "text": line} for index, line in enumerate(lines, 1)]


def _criteria(data: dict) -> list[dict]:
    raw = next((value for key, value in data.items() if key.casefold() in {"definition of done / acceptance criteria", "acceptance criteria", "criteria", "critério de aceite"}), "")
    return _make_criteria(str(raw or "").splitlines())


def _item_criteria(db: sqlite3.Connection, import_id: int, record_id: str, data: dict) -> list[dict]:
    override = db.execute("SELECT criteria_json FROM criteria_overrides WHERE import_id=? AND record_id=?", (import_id, record_id)).fetchone()
    return _make_criteria(json.loads(override["criteria_json"])) if override else _criteria(data)


def _evidence_status(root: Path, evidence: dict) -> str:
    if evidence["kind"] == "file":
        try:
            from .safety import file_hash, safe_relative
            return "verified" if file_hash(safe_relative(root, evidence["reference"])) == evidence["sha256"] else "stale"
        except (OSError, ValueError):
            return "stale"
    if evidence["kind"] == "changeset":
        if not re.fullmatch(r"[0-9a-f]{12}", evidence["reference"]): return "unverified"
        try:
            from .workflow import load_changeset
            changeset = load_changeset(root, evidence["reference"])
            if Path(changeset["workspace"]).resolve() != root.resolve() or not changeset.get("approved") or not changeset.get("applied") or not changeset.get("validation_passed") or not changeset.get("checks") or any(check.get("status") != "passed" for check in changeset["checks"]):
                return "unverified"
            from .safety import file_hash, safe_relative
            expected = changeset.get("artifact_hashes", {})
            if not expected or changeset.get("validated_artifact_hashes") != expected:
                return "unverified"
            return "verified" if all(file_hash(safe_relative(root, path)) == digest for path, digest in expected.items()) else "stale"
        except (OSError, ValueError, KeyError):
            return "unverified"
    return "attested"


def _completion(db: sqlite3.Connection, root: Path, import_id: int, record_id: str, data: dict) -> dict:
    criteria = _item_criteria(db, import_id, record_id, data)
    output = []
    for criterion in criteria:
        review = db.execute("SELECT * FROM criterion_reviews WHERE import_id=? AND record_id=? AND criterion_id=? ORDER BY id DESC LIMIT 1", (import_id, record_id, criterion["id"])).fetchone()
        evidence = []
        if review:
            for evidence_id in json.loads(review["evidence_ids_json"]):
                row = db.execute("SELECT * FROM work_evidence WHERE import_id=? AND record_id=? AND evidence_id=?", (import_id, record_id, evidence_id)).fetchone()
                if row:
                    item = dict(row)
                    item["verification"] = _evidence_status(root, item)
                    evidence.append(item)
        state = "pending" if not review else review["decision"]
        if review and review["decision"] == "passed":
            state = "passed" if evidence and len(evidence) == len(json.loads(review["evidence_ids_json"])) and all(item["verification"] in {"verified", "attested"} for item in evidence) else "evidence_needed"
        output.append({**criterion, "state": state, "evidence": evidence, "reviewer": review["reviewer"] if review else None, "reviewed_at": review["created_at"] if review else None, "note": review["note"] if review else ""})
    if not output:
        state = "needs_criteria"
    elif all(item["state"] == "passed" for item in output):
        state = "complete"
    elif any(item["state"] == "evidence_needed" for item in output):
        state = "evidence_needed"
    elif all(item["state"] == "pending" for item in output):
        state = "not_started"
    else:
        state = "in_progress"
    evidence_pool = []
    for row in db.execute("SELECT * FROM work_evidence WHERE import_id=? AND record_id=? ORDER BY created_at", (import_id, record_id)):
        item = dict(row)
        item["verification"] = _evidence_status(root, item)
        evidence_pool.append(item)
    return {"state": state, "criteria_total": len(output), "criteria_passed": sum(item["state"] == "passed" for item in output), "criteria": output, "available_evidence": evidence_pool}


def import_workbook(root: str | Path, workbook: str | Path) -> dict:
    root = ensure_root(root); workbook = Path(workbook).expanduser().resolve()
    sheets = read_xlsx(workbook)
    digest = hashlib.sha256(workbook.read_bytes()).hexdigest()
    records, schemas = {}, {}
    for sheet, rows in sheets.items():
        header_row = TABLE_HEADERS.get(sheet)
        headers = _headers(rows, header_row) if header_row else {}
        columns = list(headers.values()) or [f"column_{col}" for col in sorted({col for row in rows for col in row["cells"]})]
        schemas[sheet] = {"kind": "table" if header_row else "reference", "header_row": header_row, "columns": columns}
        id_col = next((col for col, name in headers.items() if name.casefold() in {"id", "function id"}), None)
        records[sheet] = []
        for row in rows:
            if header_row and (row["row"] == header_row or row["row"] < header_row): continue
            values = {headers.get(col, f"column_{col}"): val for col, val in row["cells"].items()}
            if not values: continue
            if id_col is not None:
                raw_id = row["cells"].get(id_col)
                if raw_id is None or str(raw_id).startswith("="): continue
            if header_row and any(str(value).strip().casefold() in {"id", "function id"} for value in values.values()): continue
            rid = next((str(v).strip() for k, v in values.items() if k.lower() in {"id", "function id"} and str(v).strip()), f"{sheet}:{row['row']}")
            records[sheet].append({"row": row["row"], "id": rid, "data": values})
    return _store_import(root, str(workbook), digest, records, schemas)


def import_json(root: str | Path, seed: str | Path) -> dict:
    root = ensure_root(root); seed = Path(seed).expanduser().resolve()
    raw = seed.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("version", 1) not in {1, 2} or not isinstance(payload.get("sheets"), dict):
        raise ValueError("roadmap JSON precisa de version 1 ou 2 e objeto sheets")
    records, schemas = {}, {}
    for sheet, value in payload["sheets"].items():
        if not isinstance(sheet, str):
            raise ValueError("nome de aba inválido no roadmap")
        if payload.get("version", 1) == 2:
            if not isinstance(value, dict) or not isinstance(value.get("records"), list):
                raise ValueError(f"aba inválida no roadmap: {sheet}")
            columns = value.get("columns", [])
            if not isinstance(columns, list) or any(not isinstance(column, str) for column in columns):
                raise ValueError(f"colunas inválidas na aba {sheet}")
            kind = value.get("kind", "table")
            if kind not in {"table", "reference", "guide", "view", "notes"}:
                raise ValueError(f"tipo de aba inválido: {sheet}")
            schemas[sheet] = {"kind": kind, "header_row": value.get("header_row"), "columns": columns}
            rows = value["records"]
        else:
            if not isinstance(value, list):
                raise ValueError("cada aba do roadmap deve ser uma lista de registros")
            rows = value
            schemas[sheet] = {"kind": "table", "header_row": None, "columns": []}
        records[sheet] = []
        row_numbers = set()
        for index, item in enumerate(rows, start=1):
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip() or not isinstance(item.get("data"), dict):
                raise ValueError(f"registro inválido na aba {sheet}, posição {index}")
            if any(not isinstance(key, str) for key in item["data"]):
                raise ValueError(f"colunas devem ter nomes string: {sheet}, posição {index}")
            if payload.get("version", 1) == 1:
                schemas[sheet]["columns"] = list(dict.fromkeys([*schemas[sheet]["columns"], *item["data"]]))
            row_number = item.get("row", index)
            if not isinstance(row_number, int) or isinstance(row_number, bool) or row_number < 1 or row_number in row_numbers:
                raise ValueError(f"número de linha inválido ou duplicado: {sheet}, posição {index}")
            row_numbers.add(row_number)
            records[sheet].append({"row": row_number, "id": item["id"].strip(), "data": item["data"]})
    updates = payload.get("updates", [])
    if not isinstance(updates, list) or any(not isinstance(item, dict) or not isinstance(item.get("record_id"), str) for item in updates):
        raise ValueError("updates inválido no roadmap JSON")
    evidence = payload.get("work_evidence")
    reviews = payload.get("criterion_reviews")
    criteria_overrides = payload.get("criteria_overrides")
    if evidence is not None and (not isinstance(evidence, list) or any(not isinstance(item, dict) or not isinstance(item.get("record_id"), str) or not isinstance(item.get("evidence_id"), str) for item in evidence)):
        raise ValueError("work_evidence inválido no roadmap JSON")
    if reviews is not None and (not isinstance(reviews, list) or any(not isinstance(item, dict) or not isinstance(item.get("record_id"), str) or not isinstance(item.get("criterion_id"), str) for item in reviews)):
        raise ValueError("criterion_reviews inválido no roadmap JSON")
    if criteria_overrides is not None and (not isinstance(criteria_overrides, list) or any(not isinstance(item, dict) or not isinstance(item.get("record_id"), str) or not isinstance(item.get("criteria"), list) or any(not isinstance(value, str) for value in item["criteria"]) for item in criteria_overrides)):
        raise ValueError("criteria_overrides inválido no roadmap JSON")
    return _store_import(root, str(seed), hashlib.sha256(raw).hexdigest(), records, schemas, updates, evidence, reviews, criteria_overrides)


def _store_import(root: Path, source: str, digest: str, sheets: dict[str, list[dict]], schemas: dict[str, dict] | None = None, updates: list[dict] | None = None, evidence: list[dict] | None = None, reviews: list[dict] | None = None, criteria_overrides: list[dict] | None = None) -> dict:
    schemas = schemas or {sheet: {"kind": "table", "header_row": None, "columns": list(dict.fromkeys(key for row in rows for key in row["data"]))} for sheet, rows in sheets.items()}
    db = _database(root)
    try:
        with db:
            previous = db.execute("SELECT id FROM imports WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
            db.execute("UPDATE imports SET active=0")
            cur = db.execute("INSERT INTO imports(source,sha256,imported_at,active) VALUES(?,?,?,1)", (source, digest, datetime.now(timezone.utc).isoformat()))
            import_id = cur.lastrowid
            count = 0
            for sheet, schema in schemas.items():
                db.execute("INSERT INTO sheet_schemas VALUES(?,?,?)", (import_id, sheet, json.dumps(schema, ensure_ascii=False)))
            for sheet, rows in sheets.items():
                for row in rows:
                    db.execute("INSERT INTO records VALUES(?,?,?,?,?)", (import_id, sheet, row["row"], row["id"], json.dumps(row["data"], ensure_ascii=False)))
                    count += 1
            if previous:
                db.execute("""INSERT INTO updates(import_id,record_id,status,evidence,note,updated_at)
                    SELECT ?,u.record_id,u.status,u.evidence,u.note,u.updated_at FROM updates u
                    WHERE u.import_id=? AND EXISTS(SELECT 1 FROM records r WHERE r.import_id=? AND r.record_id=u.record_id)""",
                    (import_id, previous["id"], import_id))
                for row in sheets.get("Roadmap", []):
                    if criteria_overrides is None:
                        db.execute("INSERT OR IGNORE INTO criteria_overrides SELECT ?,record_id,criteria_json,updated_at FROM criteria_overrides WHERE import_id=? AND record_id=?", (import_id, previous["id"], row["id"]))
                    if evidence is None:
                        db.execute("""INSERT OR IGNORE INTO work_evidence
                        SELECT ?,record_id,evidence_id,kind,reference,summary,sha256,created_at
                        FROM work_evidence WHERE import_id=? AND record_id=?""",
                        (import_id, previous["id"], row["id"]))
                    if reviews is None:
                        criterion_ids = {criterion["id"] for criterion in _item_criteria(db, import_id, row["id"], row["data"])}
                        for review in db.execute("SELECT * FROM criterion_reviews WHERE import_id=? AND record_id=? ORDER BY id", (previous["id"], row["id"])):
                            if review["criterion_id"] in criterion_ids:
                                db.execute("""INSERT INTO criterion_reviews(import_id,record_id,criterion_id,decision,evidence_ids_json,reviewer,note,created_at)
                                    VALUES(?,?,?,?,?,?,?,?)""", (import_id, review["record_id"], review["criterion_id"], review["decision"], review["evidence_ids_json"], review["reviewer"], review["note"], review["created_at"]))
            if updates:
                known_ids = {row["id"] for rows in sheets.values() for row in rows}
                for item in updates:
                    if item["record_id"] in known_ids:
                        db.execute("INSERT INTO updates(import_id,record_id,status,evidence,note,updated_at) VALUES(?,?,?,?,?,?)",
                                   (import_id, item["record_id"], item.get("status"), item.get("evidence"), item.get("note"), item.get("updated_at") or datetime.now(timezone.utc).isoformat()))
            criterion_ids = {row["id"]: {criterion["id"] for criterion in _criteria(row["data"])} for row in sheets.get("Roadmap", [])}
            for item in criteria_overrides or []:
                if item["record_id"] in criterion_ids:
                    db.execute("INSERT OR REPLACE INTO criteria_overrides VALUES(?,?,?,?)", (import_id, item["record_id"], json.dumps(item["criteria"], ensure_ascii=False), item.get("updated_at") or datetime.now(timezone.utc).isoformat()))
            criterion_ids = {row["id"]: {criterion["id"] for criterion in _item_criteria(db, import_id, row["id"], row["data"])} for row in sheets.get("Roadmap", [])}
            for item in evidence or []:
                if item["record_id"] in criterion_ids and isinstance(item.get("evidence_id"), str) and item.get("kind") in {"changeset", "file", "command", "manual"} and isinstance(item.get("reference"), str):
                    db.execute("INSERT OR IGNORE INTO work_evidence VALUES(?,?,?,?,?,?,?,?)", (import_id, item["record_id"], item["evidence_id"], item["kind"], item["reference"], str(item.get("summary", "")), item.get("sha256"), item.get("created_at") or datetime.now(timezone.utc).isoformat()))
            for item in reviews or []:
                if item["record_id"] in criterion_ids and item["criterion_id"] in criterion_ids[item["record_id"]] and item.get("decision") in {"passed", "needs_work"}:
                    evidence_ids = item.get("evidence_ids", [])
                    if isinstance(evidence_ids, list) and all(isinstance(value, str) for value in evidence_ids):
                        db.execute("INSERT INTO criterion_reviews(import_id,record_id,criterion_id,decision,evidence_ids_json,reviewer,note,created_at) VALUES(?,?,?,?,?,?,?,?)", (import_id, item["record_id"], item["criterion_id"], item["decision"], json.dumps(evidence_ids), str(item.get("reviewer", "imported")), str(item.get("note", "")), item.get("created_at") or datetime.now(timezone.utc).isoformat()))
        return {"import_id": import_id, "source": source, "sha256": digest, "sheets": len(sheets), "records": count}
    finally: db.close()


def initialize_empty(root: str | Path, template: str | Path | None = None) -> dict:
    root = ensure_root(root)
    template = Path(template) if template else Path(__file__).with_name("roadmap_seed.json")
    db = _database(root)
    try:
        active = db.execute("SELECT id FROM imports WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        db.close()
    if active:
        return {"created": False, "import_id": active["id"], "message": "roadmap existente preservado"}
    result = import_json(root, template)
    result["created"] = True
    return result


def list_sheets(root: str | Path) -> list[dict]:
    db = _database(root)
    try:
        imported = active_import(db)
        schemas = {row["sheet"]: json.loads(row["schema_json"]) for row in db.execute("SELECT sheet,schema_json FROM sheet_schemas WHERE import_id=?", (imported["id"],))}
        counts = {row["sheet"]: row["count"] for row in db.execute("SELECT sheet,COUNT(*) AS count FROM records WHERE import_id=? GROUP BY sheet", (imported["id"],))}
        for sheet, data_json in db.execute("SELECT sheet,data_json FROM records WHERE import_id=? ORDER BY row_num", (imported["id"],)):
            if sheet not in schemas:
                data = json.loads(data_json)
                schemas[sheet] = {"kind": "table", "header_row": None, "columns": list(data)}
        return [{"name": sheet, **schema, "records": counts.get(sheet, 0)} for sheet, schema in schemas.items()]
    finally:
        db.close()


def create_work_item(root: str | Path, title: str, description: str = "", priority: str = "P2") -> dict:
    title = title.strip()
    if not title:
        raise ValueError("descreva o trabalho antes de adicioná-lo")
    db = _database(root)
    try:
        imported = active_import(db)
        schema_row = db.execute("SELECT schema_json FROM sheet_schemas WHERE import_id=? AND sheet='Roadmap'", (imported["id"],)).fetchone()
        if schema_row:
            schema = json.loads(schema_row["schema_json"])
        else:
            first = db.execute("SELECT data_json FROM records WHERE import_id=? AND sheet='Roadmap' ORDER BY row_num LIMIT 1", (imported["id"],)).fetchone()
            schema = {"columns": list(json.loads(first["data_json"])) if first else []}
        if not schema.get("columns"):
            raise ValueError("o esquema do roadmap não contém a aba Roadmap")
        columns = schema.get("columns", [])
        title_column = next((name for name in columns if name.casefold() in {"deliverable / work item", "work item", "title", "name"}), None)
        if not title_column:
            raise ValueError("a aba Roadmap não define uma coluna de item de trabalho")
        existing = {row["record_id"] for row in db.execute("SELECT record_id FROM records WHERE import_id=?", (imported["id"],))}
        sequence = 1
        while f"WORK-{sequence:04d}" in existing:
            sequence += 1
        record_id = f"WORK-{sequence:04d}"
        data = {title_column: title}
        aliases = {
            "id": record_id,
            "definition of done / acceptance criteria": description,
            "priority": priority,
            "initial status": "A planejar",
            "current status": "A planejar",
        }
        for column in columns:
            value = aliases.get(column.casefold())
            if value is not None:
                data[column] = value
        row_num = db.execute("SELECT COALESCE(MAX(row_num),0)+1 FROM records WHERE import_id=? AND sheet='Roadmap'", (imported["id"],)).fetchone()[0]
        with db:
            db.execute("INSERT INTO records VALUES(?,?,?,?,?)", (imported["id"], "Roadmap", row_num, record_id, json.dumps(data, ensure_ascii=False)))
        return {"id": record_id, "sheet": "Roadmap", "row": row_num, "data": data}
    finally:
        db.close()


def active_import(db: sqlite3.Connection) -> sqlite3.Row:
    row = db.execute("SELECT * FROM imports WHERE active=1 ORDER BY id DESC LIMIT 1").fetchone()
    if row is None: raise ValueError("roadmap ainda não importado; rode `konstructor roadmap import`")
    return row


def list_records(root: str | Path, sheet: str | None = None, status: str | None = None, priority: str | None = None) -> list[dict]:
    root = ensure_root(root)
    db = _database(root)
    try:
        imported = active_import(db)
        sql = "SELECT * FROM records WHERE import_id=?"; args: list = [imported["id"]]
        if sheet: sql += " AND sheet=?"; args.append(sheet)
        result = []
        for row in db.execute(sql + " ORDER BY sheet,row_num", args):
            data = json.loads(row["data_json"])
            update_rows = db.execute("SELECT status,evidence,note,updated_at FROM updates WHERE import_id=? AND record_id=? ORDER BY id", (imported["id"], row["record_id"])).fetchall()
            update = None
            if update_rows:
                update = {"status":None,"evidence":None,"note":None,"updated_at":None}
                for entry in update_rows:
                    for field in update:
                        if entry[field] is not None: update[field] = entry[field]
            value = {"id": row["record_id"], "sheet": row["sheet"], "row": row["row_num"], "data": data}
            if row["sheet"] == "Roadmap": value["completion"] = _completion(db, root, imported["id"], row["record_id"], data)
            if update: value["current"] = update
            current_status = (update["status"] if update and update["status"] else next((v for k,v in data.items() if k.lower() in {"current status", "status", "implementation status", "estado"}), ""))
            current_priority = next((v for k,v in data.items() if k.lower() == "priority"), "")
            if status:
                matches = str(current_status).casefold() == status.casefold()
                if row["sheet"] == "Roadmap": matches = matches or value["completion"]["state"].casefold() == status.casefold()
                if not matches: continue
            if priority and str(current_priority).casefold() != priority.casefold(): continue
            result.append(value)
        return result
    finally: db.close()


def show_record(root: str | Path, record_id: str) -> dict:
    matches = [item for item in list_records(root) if item["id"] == record_id]
    if not matches: raise ValueError(f"roadmap item not found: {record_id}")
    if len(matches) > 1: raise ValueError(f"roadmap ID is ambiguous across sheets: {record_id}")
    return matches[0]


def update_record(root: str | Path, record_id: str, status: str | None, evidence: str | None, note: str | None = None) -> dict:
    item = show_record(root, record_id)
    if item["sheet"] == "Roadmap" and status and status.strip().casefold() in {"complete", "completed", "concluído", "concluída", "concluido", "concluida", "verified", "done"}:
        raise ValueError("conclusão é derivada dos critérios revisados e suas evidências; use roadmap criterion-review")
    current = item.get("current", {})
    status = status if status is not None else current.get("status")
    evidence = evidence if evidence is not None else current.get("evidence")
    note = note if note is not None else current.get("note")
    db = _database(root)
    try:
        imported = active_import(db)
        with db: db.execute("INSERT INTO updates(import_id,record_id,status,evidence,note,updated_at) VALUES(?,?,?,?,?,?)", (imported["id"], record_id, status, evidence, note, datetime.now(timezone.utc).isoformat()))
    finally: db.close()
    return show_record(root, record_id)


def set_acceptance_criteria(root: str | Path, record_id: str, criteria: list[str]) -> dict:
    root = ensure_root(root)
    item = next((row for row in list_records(root, "Roadmap") if row["id"] == record_id), None)
    if not item: raise ValueError(f"roadmap item not found: {record_id}")
    if not isinstance(criteria, list) or any(not isinstance(value, str) or not value.strip() for value in criteria):
        raise ValueError("critérios devem ser uma lista de textos não vazios")
    normalized = [value.strip().lstrip("-•* ").strip() for value in criteria]
    if any(not value for value in normalized): raise ValueError("critério vazio")
    db = _database(root)
    try:
        imported = active_import(db)
        with db:
            db.execute("INSERT OR REPLACE INTO criteria_overrides VALUES(?,?,?,?)", (imported["id"], record_id, json.dumps(normalized, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
    finally:
        db.close()
    return show_record(root, record_id)


def add_evidence(root: str | Path, record_id: str, kind: str, reference: str, summary: str = "") -> dict:
    root = ensure_root(root)
    item = next((row for row in list_records(root, "Roadmap") if row["id"] == record_id), None)
    if not item: raise ValueError(f"roadmap item not found: {record_id}")
    if kind not in {"changeset", "file", "command", "manual"}: raise ValueError("evidência deve ser changeset, file, command ou manual")
    reference = reference.strip()
    if not reference: raise ValueError("a referência da evidência não pode ficar vazia")
    digest = None
    if kind == "file":
        from .safety import file_hash, safe_relative
        target = safe_relative(root, reference)
        if not target.is_file(): raise ValueError("o arquivo de evidência não existe")
        reference = target.relative_to(root).as_posix()
        digest = file_hash(target)
    evidence = {"kind": kind, "reference": reference, "summary": summary.strip(), "sha256": digest}
    if kind == "changeset" and _evidence_status(root, evidence) != "verified":
        raise ValueError("changeset precisa estar aplicado, validado e corresponder aos arquivos atuais")
    if kind in {"command", "manual"} and not summary.strip():
        raise ValueError("registre a saída ou descrição da evidência em --summary")
    evidence_id = "EVD-" + uuid.uuid4().hex[:12].upper()
    db = _database(root)
    try:
        imported = active_import(db)
        with db:
            db.execute("INSERT INTO work_evidence VALUES(?,?,?,?,?,?,?,?)", (imported["id"], record_id, evidence_id, kind, reference, summary.strip(), digest, datetime.now(timezone.utc).isoformat()))
    finally:
        db.close()
    return {"evidence_id": evidence_id, **evidence, "verification": _evidence_status(root, evidence)}


def review_criterion(root: str | Path, record_id: str, criterion_id: str, decision: str, evidence_ids: list[str], reviewer: str, note: str = "") -> dict:
    root = ensure_root(root)
    item = next((row for row in list_records(root, "Roadmap") if row["id"] == record_id), None)
    if not item: raise ValueError(f"roadmap item not found: {record_id}")
    criterion = next((row for row in item["completion"]["criteria"] if row["id"] == criterion_id), None)
    if not criterion: raise ValueError(f"critério não encontrado: {criterion_id}")
    if decision not in {"passed", "needs_work"}: raise ValueError("decision deve ser passed ou needs_work")
    reviewer = reviewer.strip()
    if not reviewer: raise ValueError("identifique quem revisou o critério")
    if not isinstance(evidence_ids, list) or any(not isinstance(value, str) for value in evidence_ids) or len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("informe uma lista sem IDs de evidência duplicados")
    if decision == "passed" and not evidence_ids: raise ValueError("um critério só passa com ao menos uma evidência vinculada")
    db = _database(root)
    try:
        imported = active_import(db)
        evidence_rows = []
        for evidence_id in evidence_ids:
            row = db.execute("SELECT * FROM work_evidence WHERE import_id=? AND record_id=? AND evidence_id=?", (imported["id"], record_id, evidence_id)).fetchone()
            if not row: raise ValueError(f"evidência não pertence a este item: {evidence_id}")
            evidence_rows.append(dict(row))
        if decision == "passed" and any(_evidence_status(root, row) not in {"verified", "attested"} for row in evidence_rows):
            raise ValueError("há evidência ausente, desatualizada ou não verificada")
        with db:
            db.execute("INSERT INTO criterion_reviews(import_id,record_id,criterion_id,decision,evidence_ids_json,reviewer,note,created_at) VALUES(?,?,?,?,?,?,?,?)", (imported["id"], record_id, criterion_id, decision, json.dumps(evidence_ids), reviewer, note.strip(), datetime.now(timezone.utc).isoformat()))
    finally:
        db.close()
    return show_record(root, record_id)


def dashboard(root: str | Path) -> dict:
    roadmap = list_records(root, "Roadmap")
    decisions = list_records(root, "Decisões pendentes")
    gates = list_records(root, "Gates de aceite")
    completed = sum(item["completion"]["state"] == "complete" for item in roadmap)
    def count(rows, key):
        counts = {}
        for row in rows:
            current = row.get("current", {}).get("status")
            value = current or next((v for k,v in row["data"].items() if k.lower() in {key, "current status", "status", "estado", "implementation status"}), "Sem estado")
            counts[str(value)] = counts.get(str(value), 0) + 1
        return counts
    db = _database(root)
    try:
        imported = active_import(db)
        catalog = {row["sheet"]: row["n"] for row in db.execute("SELECT sheet,COUNT(*) AS n FROM records WHERE import_id=? GROUP BY sheet ORDER BY sheet", (imported["id"],))}
    finally: db.close()
    completion_counts = {state: sum(row["completion"]["state"] == state for row in roadmap) for state in {row["completion"]["state"] for row in roadmap}}
    return {"source": imported["source"], "source_sha256": imported["sha256"], "records_by_sheet":catalog,"roadmap_items":len(roadmap),"roadmap_completed":completed,"roadmap_completion_rate":completed / len(roadmap) if roadmap else 0.0,"roadmap_by_completion":completion_counts,"roadmap_by_status":count(roadmap,"initial status"),"p0_open":sum(1 for row in roadmap if str(row["data"].get("Priority", "")).upper()=="P0" and row["completion"]["state"] != "complete"),"decisions":len(decisions),"open_decisions":sum(1 for x in decisions if (x.get("current",{}).get("status") or x["data"].get("Status", x["data"].get("Estado","Aberta"))) not in {"Decidida","Concluído","Closed"}),"acceptance_gates":len(gates),"gates_by_status":count(gates,"status")}


def export_json(root: str | Path, destination: str | Path) -> Path:
    root = ensure_root(root); destination = Path(destination).expanduser().resolve()
    db = _database(root)
    try:
        imported = active_import(db)
        sheets = {row["sheet"]: {**json.loads(row["schema_json"]), "records": []}
                  for row in db.execute("SELECT sheet,schema_json FROM sheet_schemas WHERE import_id=? ORDER BY rowid", (imported["id"],))}
        for row in db.execute("SELECT * FROM records WHERE import_id=? ORDER BY sheet,row_num", (imported["id"],)):
            sheets.setdefault(row["sheet"], {"kind":"table", "header_row":None, "columns":[], "records":[]})["records"].append({"row":row["row_num"], "id":row["record_id"], "data":json.loads(row["data_json"])})
        updates = [dict(row) for row in db.execute("SELECT record_id,status,evidence,note,updated_at FROM updates WHERE import_id=? ORDER BY id", (imported["id"],))]
        evidence = [dict(row) for row in db.execute("SELECT record_id,evidence_id,kind,reference,summary,sha256,created_at FROM work_evidence WHERE import_id=? ORDER BY created_at", (imported["id"],))]
        criteria_overrides = [{"record_id": row["record_id"], "criteria": json.loads(row["criteria_json"]), "updated_at": row["updated_at"]} for row in db.execute("SELECT record_id,criteria_json,updated_at FROM criteria_overrides WHERE import_id=? ORDER BY record_id", (imported["id"],))]
        reviews = []
        for row in db.execute("SELECT record_id,criterion_id,decision,evidence_ids_json,reviewer,note,created_at FROM criterion_reviews WHERE import_id=? ORDER BY id", (imported["id"],)):
            reviews.append({**dict(row), "evidence_ids": json.loads(row["evidence_ids_json"])})
            reviews[-1].pop("evidence_ids_json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({"version":2,"title":"Konstructor roadmap backup","source":dict(imported),"sheets":sheets,"updates":updates,"criteria_overrides":criteria_overrides,"work_evidence":evidence,"criterion_reviews":reviews},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        return destination
    finally: db.close()
