import shutil
import uuid


def snapshot_file_names():
    return [
        "viewer_data.json",
        "result.json",
        "ai_assessment.json",
        "change_ai_assessment.json",
        "reviews.json",
        "report.pdf",
        "report.md",
        "words.json",
        "chars.json",
        "section_map.json",
        "prev_report.pdf",
        "prev_report.md",
        "prev_words.json",
        "prev_chars.json",
        "prev_section_map.json",
        "new.pdf",
        "new.md",
        "new_words.json",
        "new_chars.json",
        "old.pdf",
        "old.md",
        "old_words.json",
        "old_chars.json",
    ]


def snapshot_counts(reviews):
    return {
        "review_count": len(reviews or []),
        "open_reviews": sum(1 for r in reviews or [] if r.get("status", "open") == "open"),
        "closed_reviews": sum(1 for r in reviews or [] if r.get("status") in ("closed", "cleared", "resolved")),
    }


def prune_document_snapshots(doc_id, meta, *, snapshot_limit, document_snapshot_dir_fn):
    snapshots = sorted(meta.get("snapshots", []), key=lambda s: s.get("created_at", ""))
    while len(snapshots) > snapshot_limit:
        old = snapshots.pop(0)
        shutil.rmtree(document_snapshot_dir_fn(doc_id, old.get("snapshot_id", "")), ignore_errors=True)
    meta["snapshots"] = snapshots


def create_run_snapshot(
    doc_id,
    run_id,
    *,
    label="",
    load_document_meta_fn,
    run_meta_for_fn,
    document_run_dir_fn,
    read_json_fn,
    document_snapshot_dir_fn,
    document_reviews_for_run_fn,
    semantic_module,
    snapshot_file_names_fn,
    copy_if_exists_fn,
    write_json_fn,
    utc_now_fn,
    snapshot_counts_fn,
    prune_document_snapshots_fn,
    save_document_meta_fn,
):
    meta = load_document_meta_fn(doc_id)
    if not meta:
        return {"error": "document not found"}, 404
    run_meta = run_meta_for_fn(meta, run_id)
    if not run_meta:
        return {"error": "run not found"}, 404
    run_dir = document_run_dir_fn(doc_id, run_id)
    viewer_data = read_json_fn(run_dir / "viewer_data.json", None)
    if not viewer_data:
        return {"error": "run data not found"}, 404

    snapshot_id = "snap-" + uuid.uuid4().hex[:10]
    snap_dir = document_snapshot_dir_fn(doc_id, snapshot_id)
    snap_dir.mkdir(parents=True, exist_ok=True)
    reviews = document_reviews_for_run_fn(doc_id, run_id)
    semantic_module.save_reviews(run_dir, reviews)
    for name in snapshot_file_names_fn():
        copy_if_exists_fn(run_dir / name, snap_dir / name)
    write_json_fn(snap_dir / "reviews.json", reviews)
    if not (snap_dir / "viewer_data.json").exists():
        write_json_fn(snap_dir / "viewer_data.json", viewer_data)

    created_at = utc_now_fn()
    snapshot_meta = {
        "snapshot_id": snapshot_id,
        "doc_id": doc_id,
        "run_id": run_id,
        "created_at": created_at,
        "filename": run_meta.get("filename"),
        "label": str(label or "").strip()[:120],
        "mode": viewer_data.get("mode"),
        "has_diff": bool((snap_dir / "result.json").exists()),
        "has_ai_assessment": bool((snap_dir / "ai_assessment.json").exists()),
        **snapshot_counts_fn(reviews),
    }
    write_json_fn(snap_dir / "snapshot.json", snapshot_meta)
    meta.setdefault("snapshots", []).append(snapshot_meta)
    prune_document_snapshots_fn(doc_id, meta)
    save_document_meta_fn(meta)
    return snapshot_meta, 201


def snapshot_meta_for(doc_id, snapshot_id, *, load_document_meta_fn):
    meta = load_document_meta_fn(doc_id) or {}
    return next((s for s in meta.get("snapshots", []) if s.get("snapshot_id") == snapshot_id), None)


def snapshot_side_file(side, kind):
    if kind == "pdf":
        return "report.pdf" if side in ("new", "report", "current") else "prev_report.pdf"
    if kind == "words":
        return "words.json" if side in ("new", "report", "current") else "prev_words.json"
    if kind == "chars":
        return "chars.json" if side in ("new", "report", "current") else "prev_chars.json"
    return None
