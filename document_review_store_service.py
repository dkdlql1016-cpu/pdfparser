import copy
import re
from pathlib import Path


def normalize_report_stem(name):
    stem = Path(str(name or "")).stem.casefold()
    # Strip formatting-variant suffixes only. A version token (_v1, _v2, ...) is part of the
    # report's IDENTITY: stripping it collapses report_v1 and report_v2 to the same stem, which
    # makes an update run resolve to the wrong version's file and migrates that file's reviews
    # onto the other report. Keep the version so v1 and v2 resolve to their own files.
    stem = re.sub(r"_(clean|final|draft)$", "", stem, flags=re.IGNORECASE)
    return stem


def normalize_report_name(name):
    return Path(str(name or "")).name.casefold()


def workspace_file_reviews_path(documents_dir: Path, doc_id, file_id):
    return documents_dir / "workspaces" / doc_id / "file_manager" / "files" / file_id / "reviews.json"


def legacy_workspace_reviews_path(documents_dir: Path, doc_id):
    workspace_dir = documents_dir / "workspaces" / doc_id
    preferred = workspace_dir / "file_manager" / "reviews.json"
    if preferred.exists():
        return preferred
    return workspace_dir / "reviews.json"


def ephemeral_run_reviews_path(documents_dir: Path, doc_id, run_id):
    return documents_dir / "workspaces" / doc_id / "runs" / run_id / "analysis" / "reviews_store.json"


def _file_ids(meta):
    return {str(e.get("file_id")) for e in (meta.get("files") or []) if e.get("file_id")}


def resolve_file_id_for_analysis_run(meta, run_id, *, find_file_by_id_fn):
    if find_file_by_id_fn(meta, run_id):
        return str(run_id)
    run_meta = next((r for r in (meta.get("runs") or []) if r.get("run_id") == run_id), None)
    if not run_meta:
        return None
    # Prefer an exact filename match so files that share a normalized stem (e.g. report_clean.pdf
    # vs report_final.pdf) still resolve to the file the run actually came from.
    run_name = normalize_report_name(run_meta.get("filename") or "")
    if run_name:
        for entry in meta.get("files") or []:
            fid = entry.get("file_id")
            if fid and normalize_report_name(entry.get("filename") or "") == run_name:
                return str(fid)
    run_stem = normalize_report_stem(run_meta.get("filename") or "")
    if not run_stem:
        return None
    best = None
    best_score = -1
    for entry in meta.get("files") or []:
        fid = entry.get("file_id")
        if not fid:
            continue
        file_stem = normalize_report_stem(entry.get("filename") or entry.get("title") or "")
        if not file_stem:
            continue
        if run_stem == file_stem:
            return str(fid)
        if run_stem in file_stem or file_stem in run_stem:
            score = min(len(run_stem), len(file_stem))
            if score > best_score:
                best = str(fid)
                best_score = score
    return best


def resolve_file_ids_for_run_session(meta, run_id, *, find_file_by_id_fn):
    if find_file_by_id_fn(meta, run_id):
        return [str(run_id)]
    run_meta = next((r for r in (meta.get("runs") or []) if r.get("run_id") == run_id), None)
    if not run_meta:
        return []
    prev_run_id = run_meta.get("previous_run_id")
    # A plain upload run (single report, no diff) binds to the saved File Manager file the report
    # came from, so the report's OWN reviews load on upload. Version tokens are preserved in the
    # stem match, so v1 resolves to the v1 file and v2 to the v2 file -- each report keeps its own
    # distinct comment set and they are never conflated. (The earlier "v1 and v2 show the same
    # comments" bug was data duplication on disk, since fixed by the save-side filtering; it was not
    # the resolution.) When no saved file matches the uploaded report it is a genuinely new report
    # and resolves to nothing: reviews live in the per-run ephemeral store until the report is saved.
    if not prev_run_id:
        fid = resolve_file_id_for_analysis_run(meta, run_id, find_file_by_id_fn=find_file_by_id_fn)
        return [fid] if fid else []
    file_ids = []
    prev_file = resolve_file_id_for_analysis_run(meta, prev_run_id, find_file_by_id_fn=find_file_by_id_fn)
    if prev_file:
        file_ids.append(prev_file)
    current_file = resolve_file_id_for_analysis_run(meta, run_id, find_file_by_id_fn=find_file_by_id_fn)
    if current_file and current_file not in file_ids:
        file_ids.append(current_file)
    return file_ids


def primary_file_id_for_run_session(meta, run_id, *, find_file_by_id_fn):
    file_ids = resolve_file_ids_for_run_session(meta, run_id, find_file_by_id_fn=find_file_by_id_fn)
    if not file_ids:
        return None
    if find_file_by_id_fn(meta, run_id):
        return str(run_id)
    return file_ids[-1]


def storage_file_id_for_run_session(meta, run_id, side, *, find_file_by_id_fn):
    """Resolve which File Manager file a NEW review authored on `side` should be stored in.

    A comment created on the OLD pane during a diff belongs to the PREVIOUS report's file,
    not the current/primary file. Without this, an old-pane comment is written into the
    update report's reviews and vanishes from the previous report it was actually about.
    For everything else (new side, single opened file, non-diff run) this matches
    primary_file_id_for_run_session.
    """
    if find_file_by_id_fn(meta, run_id):
        return str(run_id)
    file_ids = resolve_file_ids_for_run_session(meta, run_id, find_file_by_id_fn=find_file_by_id_fn)
    if not file_ids:
        return None
    if str(side) == "old" and len(file_ids) >= 2:
        return file_ids[-2]
    return file_ids[-1]


def _review_belongs_to_file(review, file_id, run_to_file):
    anchors = review.get("anchors") or {}
    if file_id in anchors:
        return True
    created = str(review.get("created_run_id") or "")
    if created == file_id:
        return True
    for key in anchors:
        if run_to_file.get(key) == file_id:
            return True
        if key == file_id:
            return True
    if created in run_to_file and run_to_file[created] == file_id:
        return True
    return False


def migrate_legacy_workspace_reviews(
    doc_id,
    *,
    documents_dir: Path,
    load_document_meta_fn,
    find_file_by_id_fn,
    read_json_fn,
    write_json_fn,
):
    meta = load_document_meta_fn(doc_id) or {}
    legacy_path = legacy_workspace_reviews_path(documents_dir, doc_id)
    if not legacy_path.exists():
        return False
    legacy = read_json_fn(legacy_path, []) or []
    if not legacy:
        legacy_path.rename(legacy_path.with_suffix(".json.migrated"))
        return False

    file_ids = _file_ids(meta)
    run_to_file = {}
    for run in meta.get("runs") or []:
        rid = run.get("run_id")
        if not rid or rid in file_ids:
            continue
        mapped = resolve_file_id_for_analysis_run(meta, rid, find_file_by_id_fn=find_file_by_id_fn)
        if mapped:
            run_to_file[str(rid)] = mapped

    buckets = {fid: [] for fid in file_ids}
    unassigned = []
    seen_by_file = {fid: set() for fid in file_ids}
    for review in legacy:
        targets = {fid for fid in file_ids if _review_belongs_to_file(review, fid, run_to_file)}
        if not targets:
            created = str(review.get("created_run_id") or "")
            if created in run_to_file:
                targets.add(run_to_file[created])
            else:
                for key in (review.get("anchors") or {}):
                    if key in run_to_file:
                        targets.add(run_to_file[key])
        if not targets:
            unassigned.append(review)
            continue
        for fid in targets:
            rid = review.get("review_id")
            if rid and rid in seen_by_file[fid]:
                continue
            buckets[fid].append(review)
            if rid:
                seen_by_file[fid].add(rid)

    changed = False
    for fid, reviews in buckets.items():
        path = workspace_file_reviews_path(documents_dir, doc_id, fid)
        existing = read_json_fn(path, []) or [] if path.exists() else []
        existing_ids = {r.get("review_id") for r in existing}
        merged = list(existing)
        for review in reviews:
            if review.get("review_id") in existing_ids:
                continue
            merged.append(review)
            changed = True
        if merged and (not path.exists() or len(merged) != len(existing)):
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_fn(path, merged)
            changed = True

    if unassigned:
        fallback_fid = next(iter(file_ids), None)
        if fallback_fid:
            path = workspace_file_reviews_path(documents_dir, doc_id, fallback_fid)
            existing = read_json_fn(path, []) or [] if path.exists() else []
            existing_ids = {r.get("review_id") for r in existing}
            merged = list(existing)
            for review in unassigned:
                if review.get("review_id") not in existing_ids:
                    merged.append(review)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json_fn(path, merged)
            changed = True

    backup = legacy_path.with_suffix(".json.migrated")
    if not backup.exists():
        legacy_path.rename(backup)
        changed = True
    return changed


def load_file_reviews(documents_dir: Path, doc_id, file_id, *, read_json_fn):
    path = workspace_file_reviews_path(documents_dir, doc_id, file_id)
    if not path.exists():
        return []
    return read_json_fn(path, []) or []


def save_file_reviews(documents_dir: Path, doc_id, file_id, reviews, *, write_json_fn):
    path = workspace_file_reviews_path(documents_dir, doc_id, file_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_fn(path, reviews)


def load_ephemeral_run_reviews(documents_dir: Path, doc_id, run_id, *, read_json_fn):
    path = ephemeral_run_reviews_path(documents_dir, doc_id, run_id)
    if not path.exists():
        return []
    return read_json_fn(path, []) or []


def save_ephemeral_run_reviews(documents_dir: Path, doc_id, run_id, reviews, *, write_json_fn):
    path = ephemeral_run_reviews_path(documents_dir, doc_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_fn(path, reviews)


def load_reviews_for_run(
    doc_id,
    run_id,
    *,
    documents_dir: Path,
    load_document_meta_fn,
    find_file_by_id_fn,
    read_json_fn,
    write_json_fn,
    migrate_legacy_fn,
):
    migrate_legacy_fn(doc_id)
    meta = load_document_meta_fn(doc_id) or {}
    file_ids = resolve_file_ids_for_run_session(meta, run_id, find_file_by_id_fn=find_file_by_id_fn)
    if file_ids:
        merged = {}
        for fid in file_ids:
            for review in load_file_reviews(documents_dir, doc_id, fid, read_json_fn=read_json_fn):
                rid = review.get("review_id")
                if rid:
                    merged[rid] = review
                else:
                    merged[id(review)] = review
        return list(merged.values())
    return load_ephemeral_run_reviews(documents_dir, doc_id, run_id, read_json_fn=read_json_fn)


def save_reviews_for_run(
    doc_id,
    run_id,
    reviews,
    *,
    documents_dir: Path,
    load_document_meta_fn,
    find_file_by_id_fn,
    write_json_fn,
    read_json_fn=None,
):
    meta = load_document_meta_fn(doc_id) or {}
    # Opening a File Manager file directly: run_id IS the file, so every review belongs to it.
    if find_file_by_id_fn(meta, run_id):
        save_file_reviews(documents_dir, doc_id, str(run_id), reviews, write_json_fn=write_json_fn)
        return
    file_ids = resolve_file_ids_for_run_session(meta, run_id, find_file_by_id_fn=find_file_by_id_fn)
    if not file_ids:
        save_ephemeral_run_reviews(documents_dir, doc_id, run_id, reviews, write_json_fn=write_json_fn)
        return
    primary = file_ids[-1]
    other_files = [fid for fid in file_ids if fid != primary]
    if not other_files:
        save_file_reviews(documents_dir, doc_id, primary, reviews, write_json_fn=write_json_fn)
        return
    # A diff session loads a MERGED review set spanning both the base report (e.g. v1, the current
    # report) and the compare report (e.g. v2, the update report). ``primary`` is the compare
    # report. Reviews that belong to the base report must stay bound to the base file and must
    # never be persisted onto the compare report — otherwise a load -> append -> save round-trip
    # (forwarding a comment, migrating a review, ...) silently migrates the current report's
    # reviews onto the update report. Filter base-report reviews out of the primary write and
    # leave the base file(s) on disk untouched.
    run_to_file = {}
    for run in meta.get("runs") or []:
        rid = run.get("run_id")
        if not rid or rid in file_ids:
            continue
        mapped = resolve_file_id_for_analysis_run(meta, rid, find_file_by_id_fn=find_file_by_id_fn)
        if mapped:
            run_to_file[str(rid)] = mapped
    primary_reviews = [
        review
        for review in reviews
        if not any(_review_belongs_to_file(review, fid, run_to_file) for fid in other_files)
    ]
    save_file_reviews(documents_dir, doc_id, primary, primary_reviews, write_json_fn=write_json_fn)


def _session_side_anchor(review, run_id, want_side):
    """Pick the anchor that represents this review on the saved side.

    Returns ``None`` when the review does not belong to ``want_side`` for this run, so a
    new-side save does not drag old-side-only reviews onto the saved file (and vice versa).
    """
    anchors = review.get("anchors") or {}
    run_id = str(run_id)
    # Anchor explicitly keyed by this run: a review reopened on this file (run_id == file_id)
    # or projected onto this run. This is the only key that is safe to match by side alone.
    run_anchor = anchors.get(run_id)
    if run_anchor is not None and (run_anchor.get("side") or "new") == want_side:
        return run_anchor
    if want_side == "old":
        explicit_old = anchors.get(f"{run_id}:old")
        if explicit_old is not None:
            return explicit_old
    # Only reviews AUTHORED in this run session may be carried by side. A previous run's review
    # that merely shares the same side label must not be dragged onto a file saved from a later
    # run — e.g. v1's reviews are side 'new' because v1 was the current report when they were
    # authored, but they must stay bound to v1 and never migrate onto the v2 compare report.
    if str(review.get("created_run_id") or "") == run_id:
        for anchor in anchors.values():
            if (anchor.get("side") or "new") == want_side:
                return anchor
    return None


def attach_run_reviews_to_file(
    doc_id,
    run_id,
    file_id,
    side,
    *,
    documents_dir: Path,
    load_document_meta_fn,
    find_file_by_id_fn,
    read_json_fn,
    write_json_fn,
    migrate_legacy_fn,
):
    """Carry the current run-session's reviews for ``side`` onto a File Manager file.

    Save / Save As only persists the PDF and the ``meta["files"]`` entry. Reviews authored
    during the run live on the run session (the ephemeral run store, or another file_id), so
    without copying them onto the saved ``file_id`` they vanish on the next startup purge of
    ``runs/``. This re-anchors each matching review under the ``file_id`` key so reopening the
    file (where ``run_id == file_id``) projects it. Idempotent: existing file_id anchors and
    already-present reviews are left untouched.
    """
    want_side = "old" if side in ("old", "prev", "previous") else "new"
    # Gather from both sources. By the time Save runs, the service has already added the new
    # file to meta, so load_reviews_for_run may stem-resolve to the (empty) saved file and miss
    # reviews that lived in the ephemeral run store during the session — read that store too.
    session_reviews = []
    seen_ids = set()
    for review in list(
        load_reviews_for_run(
            doc_id,
            run_id,
            documents_dir=documents_dir,
            load_document_meta_fn=load_document_meta_fn,
            find_file_by_id_fn=find_file_by_id_fn,
            read_json_fn=read_json_fn,
            write_json_fn=write_json_fn,
            migrate_legacy_fn=migrate_legacy_fn,
        )
    ) + list(load_ephemeral_run_reviews(documents_dir, doc_id, run_id, read_json_fn=read_json_fn)):
        review_id = review.get("review_id")
        if review_id and review_id in seen_ids:
            continue
        if review_id:
            seen_ids.add(review_id)
        session_reviews.append(review)
    existing = load_file_reviews(documents_dir, doc_id, file_id, read_json_fn=read_json_fn)
    existing_by_id = {r.get("review_id"): r for r in existing if r.get("review_id")}
    changed = False
    for review in session_reviews:
        source_anchor = _session_side_anchor(review, run_id, want_side)
        if source_anchor is None:
            # Reviews loaded from the bound source file are anchored by that file_id, not by this
            # run_id, so the run-keyed lookup misses them. Fall back to the review's home anchor
            # when its side matches, so a Save (As) still carries them onto the saved file.
            anchors = review.get("anchors") or {}
            created = str(review.get("created_run_id") or "")
            home_key = created if created in anchors else next(iter(anchors), None)
            home = anchors.get(home_key) if home_key else None
            if home is not None and (home.get("side") or "new") == want_side:
                source_anchor = home
        if source_anchor is None:
            continue
        review_id = review.get("review_id")
        target = existing_by_id.get(review_id) if review_id else None
        if target is None:
            target = copy.deepcopy(review)
            existing.append(target)
            if review_id:
                existing_by_id[review_id] = target
            changed = True
        target_anchors = target.setdefault("anchors", {})
        if file_id not in target_anchors:
            file_anchor = copy.deepcopy(source_anchor)
            file_anchor["run_id"] = file_id
            file_anchor["side"] = want_side
            target_anchors[file_id] = file_anchor
            changed = True
    if changed:
        save_file_reviews(documents_dir, doc_id, file_id, existing, write_json_fn=write_json_fn)
    return changed


def load_all_workspace_reviews(
    doc_id,
    *,
    documents_dir: Path,
    load_document_meta_fn,
    read_json_fn,
    migrate_legacy_fn,
):
    migrate_legacy_fn(doc_id)
    meta = load_document_meta_fn(doc_id) or {}
    merged = {}
    for entry in meta.get("files") or []:
        fid = entry.get("file_id")
        if not fid:
            continue
        for review in load_file_reviews(documents_dir, doc_id, fid, read_json_fn=read_json_fn):
            rid = review.get("review_id")
            if rid:
                merged[rid] = review
    return list(merged.values())


def find_review_in_run_storage(
    doc_id,
    run_id,
    review_id,
    *,
    documents_dir: Path,
    load_document_meta_fn,
    find_file_by_id_fn,
    read_json_fn,
    write_json_fn,
    migrate_legacy_fn,
):
    for review in load_reviews_for_run(
        doc_id,
        run_id,
        documents_dir=documents_dir,
        load_document_meta_fn=load_document_meta_fn,
        find_file_by_id_fn=find_file_by_id_fn,
        read_json_fn=read_json_fn,
        write_json_fn=write_json_fn,
        migrate_legacy_fn=migrate_legacy_fn,
    ):
        if review.get("review_id") == review_id:
            return review
    return None


def update_review_in_run_storage(
    doc_id,
    run_id,
    review_id,
    mutator,
    *,
    documents_dir: Path,
    load_document_meta_fn,
    find_file_by_id_fn,
    read_json_fn,
    write_json_fn,
    migrate_legacy_fn,
):
    meta = load_document_meta_fn(doc_id) or {}
    # A diff session spans the previous report file AND the current report file. A review may live
    # in either one (e.g. an old-pane comment belongs to the previous report's file), so search
    # every session file and persist the edit back to the file that actually holds the review.
    # Looking only at the primary (current) file silently dropped edits to previous-report reviews.
    file_ids = resolve_file_ids_for_run_session(meta, run_id, find_file_by_id_fn=find_file_by_id_fn) or []
    if not file_ids:
        primary = primary_file_id_for_run_session(meta, run_id, find_file_by_id_fn=find_file_by_id_fn)
        if primary:
            file_ids = [primary]
    if file_ids:
        for file_id in file_ids:
            reviews = load_file_reviews(documents_dir, doc_id, file_id, read_json_fn=read_json_fn)
            for review in reviews:
                if review.get("review_id") == review_id:
                    mutator(review)
                    save_file_reviews(documents_dir, doc_id, file_id, reviews, write_json_fn=write_json_fn)
                    return review
        return None

    reviews = load_ephemeral_run_reviews(documents_dir, doc_id, run_id, read_json_fn=read_json_fn)
    updated = None
    for review in reviews:
        if review.get("review_id") == review_id:
            mutator(review)
            updated = review
            break
    if not updated:
        return None
    save_ephemeral_run_reviews(documents_dir, doc_id, run_id, reviews, write_json_fn=write_json_fn)
    return updated


def append_review_for_run(
    doc_id,
    run_id,
    review,
    *,
    side=None,
    documents_dir: Path,
    load_document_meta_fn,
    find_file_by_id_fn,
    read_json_fn,
    write_json_fn,
):
    meta = load_document_meta_fn(doc_id) or {}
    file_id = storage_file_id_for_run_session(meta, run_id, side, find_file_by_id_fn=find_file_by_id_fn)
    if file_id:
        reviews = load_file_reviews(documents_dir, doc_id, file_id, read_json_fn=read_json_fn)
        reviews.append(review)
        save_file_reviews(documents_dir, doc_id, file_id, reviews, write_json_fn=write_json_fn)
        return
    reviews = load_ephemeral_run_reviews(documents_dir, doc_id, run_id, read_json_fn=read_json_fn)
    reviews.append(review)
    save_ephemeral_run_reviews(documents_dir, doc_id, run_id, reviews, write_json_fn=write_json_fn)


def delete_review_in_run_storage(
    doc_id,
    run_id,
    review_id,
    *,
    documents_dir: Path,
    load_document_meta_fn,
    find_file_by_id_fn,
    read_json_fn,
    write_json_fn,
    migrate_legacy_fn,
):
    meta = load_document_meta_fn(doc_id) or {}
    file_ids = resolve_file_ids_for_run_session(meta, run_id, find_file_by_id_fn=find_file_by_id_fn) or []
    if not file_ids:
        file_id = primary_file_id_for_run_session(meta, run_id, find_file_by_id_fn=find_file_by_id_fn)
        if file_id:
            file_ids = [file_id]
    if not file_ids:
        reviews = load_ephemeral_run_reviews(documents_dir, doc_id, run_id, read_json_fn=read_json_fn)
        kept = [r for r in reviews if r.get("review_id") != review_id]
        if len(kept) == len(reviews):
            return False
        save_ephemeral_run_reviews(documents_dir, doc_id, run_id, kept, write_json_fn=write_json_fn)
        return True

    deleted = False
    for fid in file_ids:
        reviews = load_file_reviews(documents_dir, doc_id, fid, read_json_fn=read_json_fn)
        kept = [r for r in reviews if r.get("review_id") != review_id]
        if len(kept) != len(reviews):
            save_file_reviews(documents_dir, doc_id, fid, kept, write_json_fn=write_json_fn)
            deleted = True
    return deleted
