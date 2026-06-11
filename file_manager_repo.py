import shutil
import uuid
from pathlib import Path

import run_layout
from storage_utils import (
    document_run_dir,
    ensure_canonical_meta_path,
    iter_workspace_dirs,
    resolve_workspace_meta_path,
    workspace_file_dir,
    workspace_files_dir,
    workspace_stored_pdf_path,
    workspaces_root,
)


def _meta_path(workspace_dir: Path) -> Path | None:
    return resolve_workspace_meta_path(workspace_dir)


def public_report_filename(meta, run_meta, *, normalize_document_title_fn):
    filename = str((run_meta or {}).get("filename") or "").strip()
    if filename.lower() in ("report.pdf", "prev_report.pdf", "old.pdf", "new.pdf", "source.pdf", ""):
        title = normalize_document_title_fn((run_meta or {}).get("title") or (meta or {}).get("title") or "")
        if title:
            return title if title.lower().endswith(".pdf") else f"{title}.pdf"
        return "report.pdf"
    return filename


def workspace_files(meta):
    return list(meta.get("files") or [])


def analysis_runs(meta):
    return list(meta.get("runs") or [])


def file_entry_title(entry, *, normalize_document_title_fn):
    title = normalize_document_title_fn((entry or {}).get("title") or "")
    if title:
        return title
    filename = str((entry or {}).get("filename") or "").strip()
    if filename:
        stem = Path(filename).stem
        if stem:
            return normalize_document_title_fn(stem)
    return ""


def find_file_in_meta(meta, title, *, normalize_document_title_fn):
    wanted = normalize_document_title_fn(title).casefold()
    if not wanted:
        return None
    for entry in reversed(workspace_files(meta)):
        if file_entry_title(entry, normalize_document_title_fn=normalize_document_title_fn).casefold() == wanted:
            return entry
    return None


def find_file_by_id(meta, file_id):
    for entry in workspace_files(meta):
        if entry.get("file_id") == file_id:
            return entry
    return None


def first_pdf_in_file_dir(file_dir: Path):
    if not file_dir.exists():
        return None
    pdfs = sorted(
        (path for path in file_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.name.lower(),
    )
    return pdfs[0] if pdfs else None


def workspace_saved_pdf_count(documents_dir: Path, doc_id: str) -> int:
    files_dir = workspace_files_dir(documents_dir, doc_id)
    if not files_dir.exists():
        return 0
    count = 0
    for file_dir in files_dir.iterdir():
        if file_dir.is_dir() and first_pdf_in_file_dir(file_dir):
            count += 1
    return count


def prune_ephemeral_workspaces(documents_dir: Path, *, read_json_fn):
    """Remove temporary analysis-only workspaces that were never saved to file_manager."""
    if not documents_dir.exists():
        return
    for candidate in iter_workspace_dirs(documents_dir):
        ensure_canonical_meta_path(candidate)
        meta_path = _meta_path(candidate)
        if not meta_path:
            continue
        meta = read_json_fn(meta_path, None)
        if not meta or meta.get("is_saved") is not False:
            continue
        if workspace_files(meta) or workspace_saved_pdf_count(documents_dir, candidate.name):
            continue
        shutil.rmtree(candidate, ignore_errors=True)


def resolve_workspace_file_pdf(documents_dir: Path, doc_id, file_id, entry=None):
    if entry:
        named = workspace_stored_pdf_path(documents_dir, doc_id, file_id, entry.get("filename"))
        if named.exists():
            return named
    file_dir = workspace_file_dir(documents_dir, doc_id, file_id)
    legacy_source = file_dir / "source.pdf"
    if legacy_source.exists():
        return legacy_source
    # The canonical PDF lives in the file dir. Prefer it over any ephemeral run-dir copy:
    # a run can share the file_id (opening a saved file uses run_id == file_id), and that
    # run's source.pdf must not shadow the real stored file when meta filenames drift.
    actual_pdf = first_pdf_in_file_dir(file_dir)
    if actual_pdf:
        return actual_pdf
    legacy_path = run_layout.source_pdf(document_run_dir(documents_dir, doc_id, file_id), "current")
    if legacy_path.exists():
        return legacy_path
    return workspace_stored_pdf_path(
        documents_dir,
        doc_id,
        file_id,
        (entry or {}).get("filename"),
    )


def migrate_named_file_storage(meta, documents_dir: Path, doc_id):
    dirty = False
    for entry in meta.get("files") or []:
        file_id = entry.get("file_id")
        filename = str(entry.get("filename") or "").strip()
        if not file_id or not filename:
            continue
        file_dir = workspace_file_dir(documents_dir, doc_id, file_id)
        target = workspace_stored_pdf_path(documents_dir, doc_id, file_id, filename)
        source = file_dir / "source.pdf"
        if source.exists() and source != target:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                source.unlink(missing_ok=True)
            else:
                shutil.move(str(source), str(target))
            dirty = True
    for entry in meta.get("files") or []:
        filename = str(entry.get("filename") or "").strip()
        if not filename:
            continue
        stem = Path(filename).stem
        if not stem:
            continue
        current = str(entry.get("title") or "").strip()
        if current != stem:
            entry["title"] = stem
            dirty = True
    return dirty


def migrate_workspace_files(meta, documents_dir: Path, doc_id):
    workspace_dir = workspaces_root(documents_dir) / doc_id
    meta.setdefault("files", [])
    known_ids = {entry.get("file_id") for entry in meta["files"] if entry.get("file_id")}
    dirty = False

    legacy_runs = [r for r in (meta.get("runs") or []) if r.get("kind") == "file"]
    for run in legacy_runs:
        file_id = run.get("run_id")
        if not file_id:
            continue
        legacy_pdf = run_layout.source_pdf(document_run_dir(documents_dir, doc_id, file_id), "current")
        save_filename = run.get("filename") or "report.pdf"
        target_pdf = workspace_stored_pdf_path(documents_dir, doc_id, file_id, save_filename)
        if file_id not in known_ids:
            meta["files"].append({
                "file_id": file_id,
                "title": run.get("title") or Path(save_filename).stem,
                "filename": save_filename,
                "status": run.get("status") or "ready",
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at") or run.get("saved_at") or run.get("created_at"),
                "saved_at": run.get("saved_at"),
            })
            known_ids.add(file_id)
            dirty = True
        if legacy_pdf.exists():
            target_pdf.parent.mkdir(parents=True, exist_ok=True)
            if not target_pdf.exists():
                shutil.copy2(legacy_pdf, target_pdf)
            legacy_dir = document_run_dir(documents_dir, doc_id, file_id)
            if legacy_dir.exists():
                shutil.rmtree(legacy_dir, ignore_errors=True)
            dirty = True

    if migrate_named_file_storage(meta, documents_dir, doc_id):
        dirty = True

    if legacy_runs:
        meta["runs"] = [r for r in (meta.get("runs") or []) if r.get("kind") != "file"]
        dirty = True

    return dirty


def list_documents_for_manager(documents_dir: Path, *, read_json_fn, normalize_document_title_fn):
    raw_items = []
    if not documents_dir.exists():
        return raw_items
    for candidate in iter_workspace_dirs(documents_dir):
        ensure_canonical_meta_path(candidate)
        meta_path = _meta_path(candidate)
        if not meta_path:
            continue
        meta = read_json_fn(meta_path, None)
        if not meta:
            continue
        doc_id = meta.get("workspace_id") or candidate.name
        saved_flag = meta.get("is_saved")
        if saved_flag is False:
            continue
        runs = analysis_runs(meta)
        files = workspace_files(meta)
        saved_count = max(len(files), workspace_saved_pdf_count(documents_dir, doc_id))
        if files:
            latest = files[-1]
            latest_id = latest.get("file_id")
            public_filename = str(latest.get("filename") or "").strip() or public_report_filename(
                meta,
                latest,
                normalize_document_title_fn=normalize_document_title_fn,
            )
        else:
            latest = runs[-1] if runs else {}
            latest_id = latest.get("run_id") or latest.get("file_id")
            public_filename = public_report_filename(
                meta,
                latest,
                normalize_document_title_fn=normalize_document_title_fn,
            )
        raw_items.append({
            "workspace_id": doc_id,
            "title": meta.get("title") or (latest.get("title") or latest.get("filename") or "Workspace"),
            "name": meta.get("title") or public_filename,
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
            "file_count": saved_count,
            "run_count": len(runs),
            "latest_run_id": latest_id,
            "latest_filename": public_filename,
            "latest_status": latest.get("status") or "unknown",
            "_explicit_saved": bool(saved_flag is True),
            "_seed": bool(meta.get("seed_key")),
        })
    dedup = {}
    for item in raw_items:
        key = (
            str(item.get("title") or "").strip().lower(),
            str(item.get("latest_filename") or "").strip().lower(),
        )
        prev = dedup.get(key)
        if not prev:
            dedup[key] = item
            continue
        prev_rank = (
            1 if prev.get("_explicit_saved") else 0,
            1 if prev.get("_seed") else 0,
            prev.get("updated_at") or prev.get("created_at") or "",
        )
        cur_rank = (
            1 if item.get("_explicit_saved") else 0,
            1 if item.get("_seed") else 0,
            item.get("updated_at") or item.get("created_at") or "",
        )
        if cur_rank > prev_rank:
            dedup[key] = item
    items = []
    for item in dedup.values():
        item.pop("_explicit_saved", None)
        item.pop("_seed", None)
        items.append(item)
    items.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
    return items


# Backward-compatible aliases
file_run_title = file_entry_title
find_file_run_in_meta = find_file_in_meta
find_file_run_by_id = find_file_by_id


def list_workspace_files(doc_id, documents_dir: Path, *, read_json_fn, normalize_document_title_fn):
    workspace_dir = workspaces_root(documents_dir) / doc_id
    ensure_canonical_meta_path(workspace_dir)
    meta_path = _meta_path(workspace_dir)
    if not meta_path:
        return []
    meta = read_json_fn(meta_path, None)
    if not meta:
        return []
    files = []
    for entry in workspace_files(meta):
        file_id = entry.get("file_id")
        if not file_id:
            continue
        meta_filename = public_report_filename(
            meta,
            entry,
            normalize_document_title_fn=normalize_document_title_fn,
        ) or entry.get("filename") or "report.pdf"
        pdf_path = resolve_workspace_file_pdf(documents_dir, doc_id, file_id, entry=entry)
        file_exists = pdf_path.exists()
        display_filename = pdf_path.name if file_exists else meta_filename
        display_title = (
            normalize_document_title_fn(Path(display_filename).stem)
            if file_exists and display_filename != meta_filename
            else file_entry_title(entry, normalize_document_title_fn=normalize_document_title_fn)
        )
        files.append({
            "file_id": file_id,
            "run_id": file_id,
            "title": display_title or Path(display_filename).stem or "File",
            "filename": display_filename,
            "created_at": entry.get("created_at"),
            "updated_at": entry.get("updated_at") or entry.get("saved_at") or entry.get("created_at"),
            "status": "missing" if not file_exists else (entry.get("status") or "ready"),
            "file_exists": file_exists,
        })
    known_ids = {item["file_id"] for item in files}
    files_root = workspace_files_dir(documents_dir, doc_id)
    if files_root.exists():
        for file_dir in sorted(files_root.iterdir(), key=lambda path: path.name):
            if not file_dir.is_dir():
                continue
            file_id = file_dir.name
            if file_id in known_ids:
                continue
            pdf_path = first_pdf_in_file_dir(file_dir)
            if not pdf_path:
                continue
            display_filename = pdf_path.name
            display_title = normalize_document_title_fn(Path(display_filename).stem)
            files.append({
                "file_id": file_id,
                "run_id": file_id,
                "title": display_title or "File",
                "filename": display_filename,
                "created_at": None,
                "updated_at": None,
                "status": "ready",
                "file_exists": True,
            })
            known_ids.add(file_id)
    files.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return files


def workspace_file_title_exists(doc_id, title, documents_dir: Path, *, read_json_fn, normalize_document_title_fn):
    workspace_dir = workspaces_root(documents_dir) / doc_id
    ensure_canonical_meta_path(workspace_dir)
    meta_path = _meta_path(workspace_dir)
    if not meta_path:
        return False
    meta = read_json_fn(meta_path, None)
    if not meta:
        return False
    return find_file_in_meta(meta, title, normalize_document_title_fn=normalize_document_title_fn) is not None


def is_saved_or_seed_meta(meta):
    if not meta:
        return False
    saved_flag = meta.get("is_saved")
    is_saved = True if saved_flag is None else bool(saved_flag)
    return is_saved or bool(meta.get("seed_key"))


def find_workspace_id_by_title(title: str, documents_dir: Path, *, read_json_fn, normalize_document_title_fn, exclude_workspace_id: str = None):
    wanted = normalize_document_title_fn(title).casefold()
    if not wanted or not documents_dir.exists():
        return None
    for candidate in iter_workspace_dirs(documents_dir):
        ensure_canonical_meta_path(candidate)
        meta_path = _meta_path(candidate)
        if not meta_path:
            continue
        meta = read_json_fn(meta_path, None)
        if not is_saved_or_seed_meta(meta):
            continue
        workspace_id = str(meta.get("workspace_id") or candidate.name)
        if exclude_workspace_id and workspace_id == exclude_workspace_id:
            continue
        current_title = normalize_document_title_fn(meta.get("title") or "").casefold()
        if current_title and current_title == wanted:
            return workspace_id
    return None


def workspace_title_exists(title: str, documents_dir: Path, *, read_json_fn, normalize_document_title_fn, exclude_workspace_id: str = None):
    return find_workspace_id_by_title(
        title,
        documents_dir,
        read_json_fn=read_json_fn,
        normalize_document_title_fn=normalize_document_title_fn,
        exclude_workspace_id=exclude_workspace_id,
    ) is not None


def ensure_default_workspace(
    documents_dir: Path,
    *,
    read_json_fn,
    write_json_fn,
    normalize_document_title_fn,
    utc_now_fn,
    default_title="Workspace",
    preferred_workspace_id=None,
):
    title = normalize_document_title_fn(default_title)
    if preferred_workspace_id:
        # Pin the app to a fixed workspace: return it if it already exists on disk,
        # otherwise create it with that exact id so boot always lands in the same place.
        workspace_dir = workspaces_root(documents_dir) / preferred_workspace_id
        ensure_canonical_meta_path(workspace_dir)
        meta_path = _meta_path(workspace_dir)
        if meta_path:
            meta = read_json_fn(meta_path, None)
            if meta:
                canonical_id = str(meta.get("workspace_id") or preferred_workspace_id)
                if meta.get("is_saved") is False:
                    meta["is_saved"] = True
                    meta["workspace_id"] = canonical_id
                    write_json_fn(workspace_dir / "file_manager" / "meta.json", meta)
                return {
                    "workspace_id": canonical_id,
                    "title": meta.get("title") or title,
                    "created": False,
                }
        now = utc_now_fn()
        meta = {
            "workspace_id": preferred_workspace_id,
            "title": title,
            "is_saved": True,
            "created_at": now,
            "updated_at": now,
            "runs": [],
            "files": [],
            "snapshots": [],
        }
        write_json_fn(workspace_dir / "file_manager" / "meta.json", meta)
        return {"workspace_id": preferred_workspace_id, "title": title, "created": True}
    if documents_dir.exists():
        for candidate in iter_workspace_dirs(documents_dir):
            ensure_canonical_meta_path(candidate)
            meta_path = _meta_path(candidate)
            if not meta_path:
                continue
            meta = read_json_fn(meta_path, None)
            if not meta:
                continue
            if meta.get("is_saved") is False:
                continue
            current_title = normalize_document_title_fn(meta.get("title") or "")
            if current_title.casefold() == title.casefold():
                return {
                    "workspace_id": meta.get("workspace_id") or candidate.name,
                    "title": meta.get("title") or title,
                    "created": False,
                }
    items = list_documents_for_manager(
        documents_dir,
        read_json_fn=read_json_fn,
        normalize_document_title_fn=normalize_document_title_fn,
    )
    if items:
        top = items[0]
        return {
            "workspace_id": top["workspace_id"],
            "title": top.get("title") or title,
            "created": False,
        }
    doc_id = uuid.uuid4().hex[:12]
    now = utc_now_fn()
    meta = {
        "workspace_id": doc_id,
        "title": title,
        "is_saved": True,
        "created_at": now,
        "updated_at": now,
        "runs": [],
        "files": [],
        "snapshots": [],
    }
    meta_path = workspaces_root(documents_dir) / doc_id / "file_manager" / "meta.json"
    write_json_fn(meta_path, meta)
    return {"workspace_id": doc_id, "title": title, "created": True}
