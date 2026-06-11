import json
import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_json_lock_guard = threading.Lock()
_json_locks: dict[str, threading.Lock] = {}


def _json_lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _json_lock_guard:
        lock = _json_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _json_locks[key] = lock
        return lock


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    lock = _json_lock_for(path)
    with lock:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default


def _replace_with_retry(src, dst, *, attempts=12, base_delay=0.05):
    """os.replace that tolerates transient Windows locks on the destination/source.

    On Windows os.replace raises PermissionError (WinError 5 access-denied or 32 sharing-violation)
    when either file is momentarily held open -- an editor previewing the JSON, antivirus scanning
    the just-written tmp file, or an overlapping writer. These locks clear within milliseconds, so
    a brief escalating retry turns what was a hard "save failed" into a successful write. The final
    attempt is outside the retry so a persistent lock still surfaces the real error.
    """
    for i in range(attempts - 1):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(base_delay * (i + 1))
    os.replace(src, dst)


def write_json(path: Path, data):
    lock = _json_lock_for(path)
    with lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, indent=2)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            _replace_with_retry(tmp, path)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
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


def legacy_meta_path(documents_dir: Path, doc_id):
    return document_dir(documents_dir, doc_id) / "meta.json"


def resolve_workspace_meta_path(workspace_dir: Path) -> Path | None:
    canonical = workspace_dir / "file_manager" / "meta.json"
    if canonical.exists():
        return canonical
    legacy = workspace_dir / "meta.json"
    if legacy.exists():
        return legacy
    return None


def ensure_canonical_meta_path(workspace_dir: Path) -> Path | None:
    """Return canonical meta path, migrating legacy root meta.json once if needed."""
    canonical = workspace_dir / "file_manager" / "meta.json"
    if canonical.exists():
        return canonical
    legacy = workspace_dir / "meta.json"
    if not legacy.exists():
        return None
    meta = read_json(legacy, None)
    if not meta:
        return None
    canonical.parent.mkdir(parents=True, exist_ok=True)
    write_json(canonical, meta)
    return canonical


def prune_stale_temp_dirs(documents_dir: Path, *, max_age_hours: float = 24.0):
    if not documents_dir.exists():
        return
    cutoff = time.time() - max_age_hours * 3600
    for candidate in documents_dir.iterdir():
        if not candidate.is_dir() or not candidate.name.startswith(".tmp_overwrite_"):
            continue
        try:
            if candidate.stat().st_mtime < cutoff:
                shutil.rmtree(candidate, ignore_errors=True)
        except OSError:
            pass


def document_reviews_path(documents_dir: Path, doc_id):
    return document_dir(documents_dir, doc_id) / "file_manager" / "reviews.json"


def workspace_files_dir(documents_dir: Path, doc_id):
    return document_dir(documents_dir, doc_id) / "file_manager" / "files"


def workspace_file_dir(documents_dir: Path, doc_id, file_id):
    return workspace_files_dir(documents_dir, doc_id) / file_id


def workspace_stored_pdf_path(documents_dir: Path, doc_id, file_id, filename=None):
    file_dir = workspace_file_dir(documents_dir, doc_id, file_id)
    if filename:
        safe_name = Path(str(filename).strip()).name
        if safe_name:
            return file_dir / safe_name
    return file_dir / "source.pdf"

