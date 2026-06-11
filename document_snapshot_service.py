import shutil
import uuid

import run_layout


def snapshot_artifacts(run_dir):
    return run_layout.durable_artifacts(run_dir)


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
    snapshot_artifacts_fn,
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
    viewer_data = read_json_fn(run_layout.viewer_path(run_dir), None)
    if not viewer_data:
        return {"error": "run data not found"}, 404

    snapshot_id = "snap-" + uuid.uuid4().hex[:10]
    snap_dir = document_snapshot_dir_fn(doc_id, snapshot_id)
    snap_dir.mkdir(parents=True, exist_ok=True)
    reviews = document_reviews_for_run_fn(doc_id, run_id)
    semantic_module.save_reviews(run_dir, reviews)
    for src in snapshot_artifacts_fn(run_dir):
        dst = snap_dir / src.relative_to(run_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    write_json_fn(run_layout.run_reviews_path(snap_dir), reviews)
    if not run_layout.viewer_path(snap_dir).exists():
        write_json_fn(run_layout.viewer_path(snap_dir), viewer_data)

    created_at = utc_now_fn()
    snapshot_meta = {
        "snapshot_id": snapshot_id,
        "workspace_id": doc_id,
        "run_id": run_id,
        "created_at": created_at,
        "filename": run_meta.get("filename"),
        "label": str(label or "").strip()[:120],
        "mode": viewer_data.get("mode"),
        "has_diff": bool(run_layout.diff_segments_path(snap_dir).exists()),
        "has_ai_assessment": bool(run_layout.review_assessment_path(snap_dir).exists()),
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
    try:
        if kind == "pdf":
            return run_layout.source_pdf(".", side)
        if kind == "words":
            return run_layout.words_path(".", side)
        if kind == "chars":
            return run_layout.words_path(".", side)
        return None
    except ValueError:
        return None
