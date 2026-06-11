import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


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


def iter_workspace_dirs(documents_dir: Path):
    """Yield workspace directories in the canonical layout."""
    root = workspaces_root(documents_dir)
    if not root.exists():
        return
    for candidate in root.iterdir():
        if candidate.is_dir():
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
        try:
            candidate.rename(target)
        except PermissionError:
            # Windows can hold file handles from editors/indexers and block directory rename.
            # Fall back to copy so startup is resilient; source cleanup can be manual later.
            try:
                shutil.copytree(candidate, target, dirs_exist_ok=False)
            except Exception as copy_error:
                logger.warning(
                    "Failed to migrate legacy workspace '%s' to '%s': %s",
                    candidate,
                    target,
                    copy_error,
                )
        except OSError as move_error:
            logger.warning(
                "Failed to migrate legacy workspace '%s' to '%s': %s",
                candidate,
                target,
                move_error,
            )


def document_dir(documents_dir: Path, doc_id):
    return workspaces_root(documents_dir) / doc_id


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

