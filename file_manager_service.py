import shutil
import uuid
from pathlib import Path

import run_layout


def _workspace_meta_path(workspace_dir: Path) -> Path:
    preferred = workspace_dir / "file_manager" / "meta.json"
    if preferred.exists():
        return preferred
    return workspace_dir / "meta.json"


def _workspace_reviews_path(workspace_dir: Path) -> Path:
    preferred = workspace_dir / "file_manager" / "reviews.json"
    if preferred.exists():
        return preferred
    return workspace_dir / "reviews.json"


def overwrite_saved_document(
    source_workspace_id,
    source_run_id,
    target_workspace_id,
    *,
    documents_dir: Path,
    document_dir_fn,
    load_document_meta_fn,
    read_json_fn,
    utc_now_fn,
    write_json_fn,
):
    source_dir = document_dir_fn(source_workspace_id)
    target_dir = document_dir_fn(target_workspace_id)
    if not source_dir.exists():
        return {"error": "source document not found"}, 404
    target_meta = load_document_meta_fn(target_workspace_id)
    if not target_meta:
        return {"error": "target document not found"}, 404
    tmp_dir = documents_dir / f".tmp_overwrite_{target_workspace_id}_{uuid.uuid4().hex[:8]}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    shutil.copytree(source_dir, tmp_dir)
    copied_meta = read_json_fn(_workspace_meta_path(tmp_dir), {}) or {}
    copied_meta["workspace_id"] = target_workspace_id
    copied_meta["is_saved"] = True
    copied_meta["title"] = target_meta.get("title") or copied_meta.get("title") or "Workspace"
    runs = copied_meta.get("runs", []) or []
    if source_run_id and runs:
        idx = next((i for i, r in enumerate(runs) if r.get("run_id") == source_run_id), -1)
        if idx >= 0:
            selected = runs.pop(idx)
            runs.append(selected)
            copied_meta["runs"] = runs
    copied_meta["updated_at"] = utc_now_fn()
    write_json_fn(tmp_dir / "file_manager" / "meta.json", copied_meta)
    reviews = read_json_fn(_workspace_reviews_path(tmp_dir), []) or []
    for review in reviews:
        review["workspace_id"] = target_workspace_id
    write_json_fn(tmp_dir / "file_manager" / "reviews.json", reviews)
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    tmp_dir.rename(target_dir)
    latest_run_id = (copied_meta.get("runs") or [{}])[-1].get("run_id")
    return {
        "saved": True,
        "overwritten": True,
        "workspace_id": target_workspace_id,
        "run_id": source_run_id or latest_run_id,
        "title": copied_meta.get("title"),
    }, 200


def run_side_pdf_info(
    doc_id,
    run_id,
    run_dir: Path,
    side: str,
    *,
    load_document_meta_fn,
    run_meta_for_fn,
    read_json_fn,
):
    meta = load_document_meta_fn(doc_id) or {}
    run_meta = run_meta_for_fn(meta, run_id) or {}
    viewer_data = read_json_fn(run_layout.viewer_path(run_dir), {}) or {}
    if side in ("old", "prev", "previous"):
        pdf_path = run_layout.source_pdf(run_dir, "previous")
        prev_run_id = run_meta.get("previous_run_id")
        prev_meta = run_meta_for_fn(meta, prev_run_id) if prev_run_id else None
        filename = (prev_meta or {}).get("filename") or viewer_data.get("old_filename") or "previous.pdf"
    elif side in ("new", "report", "current"):
        pdf_path = run_layout.source_pdf(run_dir, "current")
        filename = run_meta.get("filename") or viewer_data.get("new_filename") or viewer_data.get("filename") or "report.pdf"
    else:
        return None, None
    return pdf_path, filename


def canonical_review_from_anchor(
    workspace_id,
    run_id,
    anchor,
    *,
    utc_now_fn,
    status="open",
    comments=None,
    text="",
    source_review_id=None,
    source_side=None,
    created_at=None,
):
    """Build a stored review in the canonical format that document_reviews_for_run can project."""
    now = utc_now_fn()
    return {
        "review_id": "r-" + uuid.uuid4().hex[:8],
        "anchor_id": "a-" + uuid.uuid4().hex[:8],
        "workspace_id": workspace_id,
        "status": status or "open",
        "created_run_id": run_id,
        "is_floating": False,
        "text": anchor.get("text", "") or text,
        "comments": [dict(c) for c in (comments or []) if c.get("text")],
        "anchors": {run_id: anchor},
        "source_review_id": source_review_id,
        "source_side": source_side,
        "created_at": created_at or now,
        "updated_at": now,
    }


def _existing_workspace_pdf(workspace_id, file_id, filename, *, workspace_stored_pdf_fn):
    named = workspace_stored_pdf_fn(workspace_id, file_id, filename)
    if named.exists():
        return named
    source = workspace_stored_pdf_fn(workspace_id, file_id, None)
    if source.exists():
        return source
    file_dir = named.parent
    if file_dir.exists():
        pdfs = sorted(
            (path for path in file_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"),
            key=lambda path: path.name.lower(),
        )
        if pdfs:
            return pdfs[0]
    return named


def _upsert_workspace_file(
    meta,
    *,
    workspace_id,
    pdf_path: Path,
    save_title: str,
    save_filename: str,
    workspace_stored_pdf_fn,
    utc_now_fn,
    existing_entry=None,
):
    now = utc_now_fn()
    meta.setdefault("files", [])
    if existing_entry:
        file_id = existing_entry.get("file_id")
        target_pdf = workspace_stored_pdf_fn(workspace_id, file_id, save_filename)
        target_pdf.parent.mkdir(parents=True, exist_ok=True)
        old_pdf = workspace_stored_pdf_fn(
            workspace_id,
            file_id,
            existing_entry.get("filename"),
        )
        if old_pdf != target_pdf and old_pdf.exists():
            old_pdf.unlink(missing_ok=True)
        (target_pdf.parent / "source.pdf").unlink(missing_ok=True)
        shutil.copy2(pdf_path, target_pdf)
        for entry in meta["files"]:
            if entry.get("file_id") != file_id:
                continue
            entry["filename"] = save_filename
            entry["title"] = save_title
            entry["saved_at"] = now
            entry["updated_at"] = now
            entry["status"] = "ready"
            break
        meta["updated_at"] = now
        meta["is_saved"] = True
        return file_id, True

    file_id = uuid.uuid4().hex[:12]
    target_pdf = workspace_stored_pdf_fn(workspace_id, file_id, save_filename)
    target_pdf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, target_pdf)
    meta["files"].append(
        {
            "file_id": file_id,
            "title": save_title,
            "filename": save_filename,
            "status": "ready",
            "created_at": now,
            "saved_at": now,
            "updated_at": now,
        }
    )
    meta["updated_at"] = now
    meta["is_saved"] = True
    return file_id, False


def save_run_side_file(
    workspace_id,
    run_id,
    side,
    *,
    document_run_dir_fn,
    run_side_pdf_info_fn,
    load_document_meta_fn,
    find_file_in_workspace_fn,
    normalize_document_title_fn,
    workspace_file_title_exists_fn,
    save_document_meta_fn,
    workspace_stored_pdf_fn,
    utc_now_fn,
    title=None,
    overwrite_existing=False,
    target_file_id=None,
):
    meta = load_document_meta_fn(workspace_id)
    if not meta:
        return {"error": "workspace not found"}, 404

    run_dir = document_run_dir_fn(workspace_id, run_id)
    pdf_path, filename = run_side_pdf_info_fn(workspace_id, run_id, run_dir, side)
    if not pdf_path or not pdf_path.exists():
        return {"error": "report file not found for side"}, 404

    explicit_title = bool(title and str(title).strip())
    # A plain Save targets the File Manager file the run is already bound to (resolved by the
    # caller and passed as target_file_id): overwrite it in place, keeping its existing title and
    # filename, so Save never forks a duplicate entry. Save As (explicit title) creates a new file.
    existing_entry = None
    if target_file_id:
        existing_entry = next(
            (e for e in (meta.get("files") or []) if e.get("file_id") == target_file_id), None
        )
    if existing_entry and not explicit_title:
        save_filename = existing_entry.get("filename") or (
            f"{Path(filename).stem}.pdf"
        )
        save_title = normalize_document_title_fn(
            existing_entry.get("title") or Path(save_filename).stem
        )
    else:
        save_title = normalize_document_title_fn(title or Path(filename).stem)
        save_filename = save_title if save_title.lower().endswith(".pdf") else f"{save_title}.pdf"
        save_title = normalize_document_title_fn(Path(save_filename).stem)
    if existing_entry is None and overwrite_existing:
        existing_entry = find_file_in_workspace_fn(meta, save_title)
    if not existing_entry and workspace_file_title_exists_fn(workspace_id, save_title):
        return {"error": "duplicate title"}, 409

    file_id, overwritten = _upsert_workspace_file(
        meta,
        workspace_id=workspace_id,
        pdf_path=pdf_path,
        save_title=save_title,
        save_filename=save_filename,
        workspace_stored_pdf_fn=workspace_stored_pdf_fn,
        utc_now_fn=utc_now_fn,
        existing_entry=existing_entry,
    )
    save_document_meta_fn(meta)
    return {
        "saved": True,
        "overwritten": overwritten,
        "created": not overwritten,
        "workspace_id": workspace_id,
        "file_id": file_id,
        "run_id": file_id,
        "title": save_title,
        "filename": save_filename,
    }, 200 if overwritten else 201


def delete_workspace_file(
    workspace_id,
    file_id,
    *,
    load_document_meta_fn,
    workspace_file_dir_fn,
    save_document_meta_fn,
    find_file_by_id_fn,
    utc_now_fn,
):
    meta = load_document_meta_fn(workspace_id)
    if not meta:
        return {"error": "workspace not found"}, 404
    entry = find_file_by_id_fn(meta, file_id)
    if not entry:
        return {"error": "file not found"}, 404
    file_dir = workspace_file_dir_fn(workspace_id, file_id)
    if file_dir.exists():
        shutil.rmtree(file_dir, ignore_errors=True)
    meta["files"] = [item for item in (meta.get("files") or []) if item.get("file_id") != file_id]
    meta["updated_at"] = utc_now_fn()
    save_document_meta_fn(meta)
    return {"ok": True, "workspace_id": workspace_id, "file_id": file_id, "run_id": file_id}, 200


def rename_workspace_file(
    workspace_id,
    file_id,
    title,
    *,
    load_document_meta_fn,
    save_document_meta_fn,
    find_file_by_id_fn,
    normalize_document_title_fn,
    workspace_file_title_exists_fn,
    workspace_stored_pdf_fn,
    utc_now_fn,
):
    meta = load_document_meta_fn(workspace_id)
    if not meta:
        return {"error": "workspace not found"}, 404
    entry = find_file_by_id_fn(meta, file_id)
    if not entry:
        return {"error": "file not found"}, 404
    next_title = normalize_document_title_fn(title)
    if not next_title:
        return {"error": "title required"}, 400
    save_filename = next_title if next_title.lower().endswith(".pdf") else f"{next_title}.pdf"
    next_title = normalize_document_title_fn(Path(save_filename).stem)
    current_title = normalize_document_title_fn(entry.get("title") or "")
    if not current_title:
        filename = str(entry.get("filename") or "").strip()
        if filename:
            current_title = normalize_document_title_fn(Path(filename).stem)
    if next_title.casefold() != current_title.casefold() and workspace_file_title_exists_fn(
        workspace_id, next_title
    ):
        return {"error": "duplicate title"}, 409
    old_path = _existing_workspace_pdf(
        workspace_id,
        file_id,
        entry.get("filename"),
        workspace_stored_pdf_fn=workspace_stored_pdf_fn,
    )
    new_path = workspace_stored_pdf_fn(workspace_id, file_id, save_filename)
    if old_path.exists() and old_path != new_path:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        if new_path.exists():
            old_path.unlink(missing_ok=True)
        else:
            old_path.rename(new_path)
    (new_path.parent / "source.pdf").unlink(missing_ok=True)
    entry["title"] = next_title
    entry["filename"] = save_filename
    entry["updated_at"] = utc_now_fn()
    meta["updated_at"] = utc_now_fn()
    save_document_meta_fn(meta)
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "file_id": file_id,
        "run_id": file_id,
        "title": next_title,
        "filename": save_filename,
    }, 200


def open_workspace_saved_file(
    workspace_id,
    file_id,
    *,
    document_run_dir_fn,
    load_document_meta_fn,
    find_file_by_id_fn,
    resolve_workspace_file_pdf_fn,
    process_single_document_run_fn,
    read_json_fn,
    document_reviews_for_run_fn,
    semantic_save_reviews_fn,
):
    meta = load_document_meta_fn(workspace_id)
    if not meta:
        return {"error": "workspace not found"}, 404
    entry = find_file_by_id_fn(meta, file_id)
    if not entry:
        return {"error": "file not found"}, 404
    pdf_path = resolve_workspace_file_pdf_fn(workspace_id, file_id, entry=entry)
    if not pdf_path or not pdf_path.exists():
        return {"error": "pdf file missing on disk"}, 404

    run_id = str(file_id)
    run_dir = document_run_dir_fn(workspace_id, run_id)
    viewer_path = run_layout.viewer_path(run_dir)
    filename = str(entry.get("filename") or pdf_path.name)

    if not viewer_path.exists():
        run_layout.current_dir(run_dir).mkdir(parents=True, exist_ok=True)
        target_pdf = run_layout.source_pdf(run_dir, "current")
        shutil.copy2(pdf_path, target_pdf)
        viewer_data = process_single_document_run_fn(
            run_dir,
            target_pdf,
            doc_id=workspace_id,
            run_id=run_id,
            filename=filename,
        )
    else:
        viewer_data = read_json_fn(viewer_path, {}) or {}

    semantic_save_reviews_fn(run_dir, document_reviews_for_run_fn(workspace_id, run_id))
    return {
        "workspace_id": workspace_id,
        "run_id": run_id,
        "file_id": file_id,
        "filename": filename,
        "result": viewer_data,
    }, 200
