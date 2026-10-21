from __future__ import annotations
import hashlib, json, secrets, sqlite3, time
from pathlib import Path
from .state import workspace_dir
from .workflow import apply, load_changeset


def _db(root):
    db = sqlite3.connect(workspace_dir(root) / "approvals.sqlite3")
    db.execute(
        "CREATE TABLE IF NOT EXISTS approvals(token_hash TEXT PRIMARY KEY, changeset TEXT NOT NULL, binding TEXT NOT NULL, expires REAL NOT NULL)"
    )
    return db


def approve_changeset(root: Path, cid: str, confirmation: str) -> str:
    cs = load_changeset(root, cid)
    if confirmation != f"APPLY {cid}":
        raise PermissionError(f"digite APPLY {cid} para confirmar")
    if not cs.get("validation_passed"):
        raise RuntimeError("changeset precisa passar pela validação")
    token = secrets.token_urlsafe(32)
    binding = hashlib.sha256(
        json.dumps(
            {"base": cs["base_hashes"], "artifacts": cs["artifact_hashes"]},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    db = _db(root)
    try:
        with db:
            db.execute(
                "INSERT INTO approvals VALUES(?,?,?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    cid,
                    binding,
                    time.time() + 300,
                ),
            )
    finally:
        db.close()
    return token


def apply_approved(root: Path, cid: str, token: str) -> list[str]:
    cs = load_changeset(root, cid)
    binding = hashlib.sha256(
        json.dumps(
            {"base": cs["base_hashes"], "artifacts": cs["artifact_hashes"]},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    db = _db(root)
    try:
        db.execute("BEGIN IMMEDIATE")
        key = hashlib.sha256(token.encode()).hexdigest()
        row = db.execute(
            "SELECT changeset,binding,expires FROM approvals WHERE token_hash=?", (key,)
        ).fetchone()
        if not row or row[0] != cid or row[1] != binding or row[2] < time.time():
            db.rollback()
            raise PermissionError("token de aprovação inválido, expirado ou usado")
        db.execute("DELETE FROM approvals WHERE token_hash=?", (key,))
        db.commit()
    finally:
        db.close()
    return apply(root, cid, yes=True)
