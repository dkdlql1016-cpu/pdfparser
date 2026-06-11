import json
import os
import tempfile
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
    content = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.replace(tmp, path)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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

