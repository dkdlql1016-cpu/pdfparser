import json
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def document_dir(documents_dir: Path, doc_id):
    return documents_dir / doc_id


def document_run_dir(documents_dir: Path, doc_id, run_id):
    return document_dir(documents_dir, doc_id) / "runs" / run_id


def document_snapshots_dir(documents_dir: Path, doc_id):
    return document_dir(documents_dir, doc_id) / "snapshots"


def document_snapshot_dir(documents_dir: Path, doc_id, snapshot_id):
    return document_snapshots_dir(documents_dir, doc_id) / snapshot_id


def document_meta_path(documents_dir: Path, doc_id):
    return document_dir(documents_dir, doc_id) / "meta.json"

