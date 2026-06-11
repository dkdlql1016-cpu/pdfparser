import shutil
import uuid
from pathlib import Path

import run_layout


def overwrite_saved_document(
    source_doc_id,
    source_run_id,
    target_doc_id,
    *,
    documents_dir: Path,
    document_dir_fn,
    load_document_meta_fn,
    read_json_fn,
    utc_now_fn,
    write_json_fn,
):
    source_dir = document_dir_fn(source_doc_id)
    target_dir = document_dir_fn(target_doc_id)
    if not source_dir.exists():
        return {"error": "source document not found"}, 404
    target_meta = load_document_meta_fn(target_doc_id)
    if not target_meta:
        return {"error": "target document not found"}, 404
    tmp_dir = documents_dir / f".tmp_overwrite_{target_doc_id}_{uuid.uuid4().hex[:8]}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    shutil.copytree(source_dir, tmp_dir)
    copied_meta = read_json_fn(tmp_dir / "meta.json", {}) or {}
    copied_meta["doc_id"] = target_doc_id
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
    write_json_fn(tmp_dir / "meta.json", copied_meta)
    reviews = read_json_fn(tmp_dir / "reviews.json", []) or []
    for review in reviews:
        review["doc_id"] = target_doc_id
    write_json_fn(tmp_dir / "reviews.json", reviews)
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
    tmp_dir.rename(target_dir)
    latest_run_id = (copied_meta.get("runs") or [{}])[-1].get("run_id")
    return {
        "saved": True,
        "overwritten": True,
        "doc_id": target_doc_id,
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
    doc_id,
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
        "doc_id": doc_id,
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


def reviews_for_single_file_side(
    source_doc_id,
    source_run_id,
    side,
    target_doc_id,
    file_run_id,
    *,
    document_run_dir_fn,
    document_reviews_for_run_fn,
    anchor_for_selection_fn,
    canonical_review_from_anchor_fn,
):
    """Copy the reviews shown on one side of a run into a saved single-file document."""
    side_norm = "old" if side in ("old", "prev", "previous") else "new"
    source_run_dir = document_run_dir_fn(source_doc_id, source_run_id)
    out = []
    seen_source_ids = set()
    for proj in document_reviews_for_run_fn(source_doc_id, source_run_id):
        ids = list((proj.get("old_word_ids") if side_norm == "old" else proj.get("new_word_ids")) or [])
        if not ids:
            continue
        source_review_id = proj.get("review_id")
        if source_review_id in seen_source_ids:
            continue
        anchor = anchor_for_selection_fn(source_run_dir, file_run_id, side_norm, ids, (proj.get("selection") or {}).get("rect"))
        if not anchor:
            continue
        seen_source_ids.add(source_review_id)
        anchor["side"] = "new"
        anchor["old_word_ids"] = []
        anchor["new_word_ids"] = list(anchor.get("word_ids") or ids)
        out.append(
            canonical_review_from_anchor_fn(
                target_doc_id,
                file_run_id,
                anchor,
                status=proj.get("status", "open"),
                comments=proj.get("comments"),
                text=proj.get("text", ""),
                source_review_id=source_review_id,
                source_side=side_norm,
                created_at=proj.get("created_at"),
            )
        )
    return out


def write_single_file_document(
    doc_id,
    pdf_path: Path,
    filename: str,
    title: str,
    *,
    document_dir_fn,
    document_run_dir_fn,
    reviews_for_single_file_side_fn,
    utc_now_fn,
    normalize_document_title_fn,
    save_document_meta_fn,
    save_document_reviews_fn,
    source_doc_id=None,
    source_run_id=None,
    source_side="new",
    replace=False,
):
    doc_path = document_dir_fn(doc_id)
    run_id = uuid.uuid4().hex[:12]
    reviews = (
        reviews_for_single_file_side_fn(source_doc_id, source_run_id, source_side, doc_id, run_id)
        if source_doc_id and source_run_id
        else []
    )
    if replace and doc_path.exists():
        shutil.rmtree(doc_path, ignore_errors=True)
    run_dir = document_run_dir_fn(doc_id, run_id)
    run_layout.current_dir(run_dir).mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdf_path, run_layout.source_pdf(run_dir, "current"))
    now = utc_now_fn()
    meta = {
        "doc_id": doc_id,
        "title": normalize_document_title_fn(title)[:120] or Path(filename).stem or "Workspace",
        "is_saved": True,
        "created_at": now,
        "updated_at": now,
        "source_doc_id": source_doc_id,
        "source_run_id": source_run_id,
        "runs": [
            {
                "run_id": run_id,
                "kind": "file",
                "status": "ready",
                "created_at": now,
                "filename": filename or "report.pdf",
                "has_diff": False,
                "has_ai_assessment": False,
            }
        ],
    }
    save_document_meta_fn(meta)
    if reviews:
        save_document_reviews_fn(doc_id, reviews)
    return meta, run_id


def save_run_side_file(
    doc_id,
    run_id,
    side,
    *,
    document_run_dir_fn,
    run_side_pdf_info_fn,
    find_document_id_by_title_fn,
    load_document_meta_fn,
    public_report_filename_fn,
    write_single_file_document_fn,
    normalize_document_title_fn,
    document_title_exists_fn,
    target_doc_id=None,
    title=None,
    overwrite_existing=False,
):
    run_dir = document_run_dir_fn(doc_id, run_id)
    pdf_path, filename = run_side_pdf_info_fn(doc_id, run_id, run_dir, side)
    if not pdf_path or not pdf_path.exists():
        return {"error": "report file not found for side"}, 404

    # Save (without an explicit target) onto an existing name overwrites that document.
    if not target_doc_id and overwrite_existing and title:
        target_doc_id = find_document_id_by_title_fn(title)

    if target_doc_id:
        target_meta = load_document_meta_fn(target_doc_id)
        if not target_meta:
            return {"error": "target document not found"}, 404
        target_runs = target_meta.get("runs", []) or []
        target_latest = target_runs[-1] if target_runs else {}
        save_title = target_meta.get("title") or title or Path(filename).stem
        save_filename = public_report_filename_fn(target_meta, target_latest) or filename
        meta, new_run_id = write_single_file_document_fn(
            target_doc_id,
            pdf_path,
            save_filename,
            save_title,
            source_doc_id=doc_id,
            source_run_id=run_id,
            source_side=side,
            replace=True,
        )
        return {
            "saved": True,
            "overwritten": True,
            "doc_id": target_doc_id,
            "run_id": new_run_id,
            "title": meta.get("title"),
        }, 200

    save_title = normalize_document_title_fn(title or Path(filename).stem)
    if document_title_exists_fn(save_title):
        return {"error": "duplicate title"}, 409
    save_filename = save_title if save_title.lower().endswith(".pdf") else f"{save_title}.pdf"
    new_doc_id = uuid.uuid4().hex[:12]
    meta, new_run_id = write_single_file_document_fn(
        new_doc_id,
        pdf_path,
        save_filename,
        save_title,
        source_doc_id=doc_id,
        source_run_id=run_id,
        source_side=side,
    )
    return {
        "saved": True,
        "created": True,
        "doc_id": new_doc_id,
        "run_id": new_run_id,
        "title": meta.get("title"),
    }, 201
