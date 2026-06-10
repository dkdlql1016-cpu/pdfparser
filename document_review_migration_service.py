import re
import uuid


def remapped_old_side_md_metadata(prev_anchor, semantic_map, *, compact_equal_ref_fn):
    previous_meta = prev_anchor.get("md_anchor") or {}
    carried_old_lines = (
        previous_meta.get("new_lines")
        or prev_anchor.get("new_lines")
        or previous_meta.get("old_lines")
        or prev_anchor.get("old_lines")
        or []
    )
    carried_old_lines = sorted({line for line in carried_old_lines if line is not None})
    refs = [e for e in (semantic_map or {}).get("equal_words", []) if e.get("old_line") in set(carried_old_lines)]
    refs.sort(key=lambda e: (e.get("old_line") or 0, e.get("old_word_id") or 0))
    return {
        "source": "carried_md_equal" if refs else "carried_section",
        "section_id": previous_meta.get("section_id") or prev_anchor.get("section_id"),
        "old_lines": carried_old_lines,
        "new_lines": sorted({r.get("new_line") for r in refs if r.get("new_line") is not None}),
        "segment_ids": sorted(
            {r.get("segment_id") for r in refs if r.get("segment_id")},
            key=lambda x: int(re.search(r"(\d+)$", str(x)).group(1)) if re.search(r"(\d+)$", str(x)) else 0,
        ),
        "equal_refs": [compact_equal_ref_fn(r) for r in refs[:120]],
    }


def remap_document_reviews_to_run(
    doc_id,
    prev_run_id,
    run_id,
    run_dir,
    semantic_map,
    *,
    load_document_reviews_fn,
    read_json_fn,
    utc_now_fn,
    first_word_page_fn,
    words_bbox_fn,
    apply_md_metadata_to_anchor_fn,
    remapped_old_side_md_metadata_fn,
    save_document_reviews_fn,
    document_reviews_for_run_fn,
):
    if not prev_run_id:
        return []
    reviews = load_document_reviews_fn(doc_id)
    if not reviews:
        return []
    old_words = read_json_fn(run_dir / "prev_words.json", []) or []
    now = utc_now_fn()
    changed = False
    for review in reviews:
        anchors = review.setdefault("anchors", {})
        if run_id in anchors:
            continue
        prev_anchor = anchors.get(prev_run_id)
        if not prev_anchor:
            continue
        if prev_anchor.get("side") == "old":
            continue
        old_ids = list(prev_anchor.get("word_ids") or prev_anchor.get("new_word_ids") or [])
        anchor = {
            "run_id": run_id,
            "side": "old",
            "word_ids": old_ids,
            "old_word_ids": old_ids,
            "new_word_ids": [],
            "page": first_word_page_fn(old_words, old_ids),
            "bbox": words_bbox_fn(old_words, old_ids),
            "text": prev_anchor.get("text", review.get("text", "")),
            "floating": False,
            "previous_run_id": prev_run_id,
        }
        apply_md_metadata_to_anchor_fn(anchor, remapped_old_side_md_metadata_fn(prev_anchor, semantic_map))
        anchors[run_id] = anchor
        review["updated_at"] = now
        changed = True
    if changed:
        save_document_reviews_fn(doc_id, reviews)
    return document_reviews_for_run_fn(doc_id, run_id)


def add_single_document_review(run_dir, data, *, read_json_fn, utc_now_fn, semantic_module):
    side = data.get("side")
    if side not in ("new", "report", "current"):
        return {"error": "single_run_reviews_only_support_current_report"}
    word_ids = sorted({int(w) for w in data.get("word_ids", []) if isinstance(w, int) or str(w).isdigit()})
    words = read_json_fn(run_dir / "words.json", []) or []
    selected = [words[i] for i in word_ids if 0 <= i < len(words)]
    if not selected:
        return {"error": "no_word_selected"}
    review = {
        "review_id": "r-" + uuid.uuid4().hex[:8],
        "anchor_id": "a-" + uuid.uuid4().hex[:8],
        "side": "new",
        "old_word_ids": [],
        "new_word_ids": [w["idx"] for w in selected],
        "segment_ids": [],
        "change_ids": [],
        "old_lines": [],
        "new_lines": [],
        "text": " ".join(str(w.get("text", "")) for w in selected),
        "selection": {"page": data.get("page"), "rect": data.get("rect"), "word_ids": word_ids},
        "comment": data.get("comment", ""),
        "status": data.get("status", "open"),
        "created_at": utc_now_fn(),
        "updated_at": utc_now_fn(),
    }
    reviews = semantic_module.load_reviews(run_dir)
    reviews.append(review)
    semantic_module.save_reviews(run_dir, reviews)
    return review


def carry_forward_previous_reviews(run_dir, semantic_map, *, read_json_fn, utc_now_fn, semantic_module):
    prev_reviews = read_json_fn(run_dir / "prev_reviews.json", []) or []
    if not prev_reviews or semantic_module.load_reviews(run_dir):
        return semantic_module.load_reviews(run_dir)

    old_to_new = {
        e.get("old_word_id"): e.get("new_word_id")
        for e in (semantic_map or {}).get("equal_words", [])
        if e.get("old_word_id") is not None and e.get("new_word_id") is not None
    }
    carried = []
    for review in prev_reviews:
        old_ids = list(review.get("new_word_ids") or review.get("old_word_ids") or [])
        new_ids = [old_to_new[wid] for wid in old_ids if wid in old_to_new]
        rv = dict(review)
        rv["old_word_ids"] = old_ids
        rv["new_word_ids"] = new_ids
        rv["side"] = "old" if not new_ids else "new"
        rv["migrated_from"] = review.get("review_id")
        rv["carried_forward"] = True
        rv["updated_at"] = utc_now_fn()
        carried.append(rv)

    semantic_module.save_reviews(run_dir, carried)
    return carried
