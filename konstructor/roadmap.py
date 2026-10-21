from __future__ import annotations

import hashlib
import json
import re
import sqlite3
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
    """)
    return db


def _headers(rows: list[dict], row_num: int) -> dict[int, str]:
    row = next((r for r in rows if r["row"] == row_num), None)
    if not row: return {}
    return {col: str(value).strip() for col, value in row["cells"].items() if str(value).strip()}


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
    return _store_import(root, str(seed), hashlib.sha256(raw).hexdigest(), records, schemas, updates)


def _store_import(root: Path, source: str, digest: str, sheets: dict[str, list[dict]], schemas: dict[str, dict] | None = None, updates: list[dict] | None = None) -> dict:
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
            if updates:
                known_ids = {row["id"] for rows in sheets.values() for row in rows}
                for item in updates:
                    if item["record_id"] in known_ids:
                        db.execute("INSERT INTO updates(import_id,record_id,status,evidence,note,updated_at) VALUES(?,?,?,?,?,?)",
                                   (import_id, item["record_id"], item.get("status"), item.get("evidence"), item.get("note"), item.get("updated_at") or datetime.now(timezone.utc).isoformat()))
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
            if update: value["current"] = update
            current_status = (update["status"] if update and update["status"] else next((v for k,v in data.items() if k.lower() in {"current status", "status", "implementation status", "estado"}), ""))
            current_priority = next((v for k,v in data.items() if k.lower() == "priority"), "")
            if status and str(current_status).casefold() != status.casefold(): continue
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


def dashboard(root: str | Path) -> dict:
    roadmap = list_records(root, "Roadmap")
    decisions = list_records(root, "Decisões pendentes")
    gates = list_records(root, "Gates de aceite")
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
    return {"source": imported["source"], "source_sha256": imported["sha256"], "records_by_sheet":catalog,"roadmap_items":len(roadmap),"roadmap_by_status":count(roadmap,"initial status"),"p0_open":sum(1 for row in roadmap if str(row["data"].get("Priority", "")).upper()=="P0" and (row.get("current",{}).get("status") or row["data"].get("Current Status", row["data"].get("Initial Status",""))) not in {"Concluído","Concluída","Verified","Não se aplica"}),"decisions":len(decisions),"open_decisions":sum(1 for x in decisions if (x.get("current",{}).get("status") or x["data"].get("Status", x["data"].get("Estado","Aberta"))) not in {"Decidida","Concluído","Closed"}),"acceptance_gates":len(gates),"gates_by_status":count(gates,"status")}


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
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({"version":2,"title":"Konstructor roadmap backup","source":dict(imported),"sheets":sheets,"updates":updates},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        return destination
    finally: db.close()
