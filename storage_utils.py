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


def workspaces_root(documents_dir: Path):
    return documents_dir / "workspaces"


def legacy_document_dir(documents_dir: Path, doc_id):
    return documents_dir / doc_id


def resolve_document_dir(documents_dir: Path, doc_id):
    preferred = workspaces_root(documents_dir) / doc_id
    legacy = legacy_document_dir(documents_dir, doc_id)
    if preferred.exists():
        return preferred
    if legacy.exists():
        return legacy
    return preferred


def iter_workspace_dirs(documents_dir: Path):
    """
    Yield workspace directories from the new layout first, then legacy roots.
    """
    seen = set()
    root = workspaces_root(documents_dir)
    if root.exists():
        for candidate in root.iterdir():
            if not candidate.is_dir():
                continue
            seen.add(candidate.name)
            yield candidate
    if documents_dir.exists():
        for candidate in documents_dir.iterdir():
            if not candidate.is_dir():
                continue
            if candidate.name == "workspaces":
                continue
            if candidate.name in seen:
                continue
            yield candidate


def migrate_legacy_workspaces(documents_dir: Path):
    """
    Move legacy `documents/<doc_id>/...` entries into `documents/workspaces/<doc_id>/...`.
    """
    if not documents_dir.exists():
        return
    root = workspaces_root(documents_dir)
    root.mkdir(parents=True, exist_ok=True)
    for candidate in documents_dir.iterdir():
        if not candidate.is_dir():
            continue
        if candidate.name == "workspaces":
            continue
        if candidate.name.startswith(".tmp_overwrite_"):
            continue
        looks_like_workspace = (
            (candidate / "meta.json").exists()
            or (candidate / "file_manager" / "meta.json").exists()
            or (candidate / "runs").exists()
            or (candidate / "snapshots").exists()
        )
        if not looks_like_workspace:
            continue
        target = root / candidate.name
        if target.exists():
            continue
        candidate.rename(target)


def document_dir(documents_dir: Path, doc_id):
    return resolve_document_dir(documents_dir, doc_id)


def document_run_dir(documents_dir: Path, doc_id, run_id):
    return document_dir(documents_dir, doc_id) / "runs" / run_id


def document_snapshots_dir(documents_dir: Path, doc_id):
    return document_dir(documents_dir, doc_id) / "snapshots"


def document_snapshot_dir(documents_dir: Path, doc_id, snapshot_id):
    return document_snapshots_dir(documents_dir, doc_id) / snapshot_id


def document_meta_path(documents_dir: Path, doc_id):
    return document_dir(documents_dir, doc_id) / "file_manager" / "meta.json"


def document_reviews_path(documents_dir: Path, doc_id):
    return document_dir(documents_dir, doc_id) / "file_manager" / "reviews.json"

