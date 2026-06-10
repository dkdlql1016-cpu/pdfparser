import re
import uuid
from pathlib import Path


def equal_refs_for_selection(semantic_map, side, word_ids):
    wanted = {int(w) for w in word_ids if isinstance(w, int) or str(w).isdigit()}
    if not wanted:
        return []
    key = "old_word_id" if side in ("old", "prev", "previous") else "new_word_id"
    refs = [e for e in (semantic_map or {}).get("equal_words", []) if e.get(key) in wanted]
    refs.sort(key=lambda e: (e.get("old_line") or 0, e.get("old_word_id") or 0, e.get("new_word_id") or 0))
    return refs


def compact_equal_ref(entry):
    return {
        "old_word_id": entry.get("old_word_id"),
        "new_word_id": entry.get("new_word_id"),
        "old_line": entry.get("old_line"),
        "new_line": entry.get("new_line"),
        "segment_id": entry.get("segment_id"),
        "text": entry.get("text", ""),
    }


def build_anchor_md_metadata(run_dir: Path, side, anchor, *, semantic_map_for_run_dir_fn, section_context_for_side_fn, infer_section_id_for_anchor_fn, semantic_map=None):
    semantic_map = semantic_map if semantic_map is not None else semantic_map_for_run_dir_fn(run_dir)
    refs = equal_refs_for_selection(semantic_map, side, anchor.get("word_ids", []))
    md_path, section_map = section_context_for_side_fn(run_dir, side)
    section_id = infer_section_id_for_anchor_fn(section_map, anchor, md_path)
    old_lines = sorted({r.get("old_line") for r in refs if r.get("old_line") is not None})
    new_lines = sorted({r.get("new_line") for r in refs if r.get("new_line") is not None})
    return {
        "source": "selection_equal" if refs else "section_only",
        "section_id": section_id,
        "old_lines": old_lines,
        "new_lines": new_lines,
        "segment_ids": sorted(
            {r.get("segment_id") for r in refs if r.get("segment_id")},
            key=lambda x: int(re.search(r"(\d+)$", str(x)).group(1)) if re.search(r"(\d+)$", str(x)) else 0,
        ),
        "equal_refs": [compact_equal_ref(r) for r in refs[:120]],
    }


def apply_md_metadata_to_anchor(anchor, md_meta):
    if not md_meta:
        return anchor
    anchor["md_anchor"] = md_meta
    anchor["section_id"] = md_meta.get("section_id")
    anchor["old_lines"] = md_meta.get("old_lines", [])
    anchor["new_lines"] = md_meta.get("new_lines", [])
    anchor["segment_ids"] = md_meta.get("segment_ids", [])
    return anchor


def anchor_for_selection(
    run_dir: Path,
    run_id,
    side,
    word_ids,
    *,
    read_json_fn,
    first_word_page_fn,
    words_bbox_fn,
    text_for_word_ids_fn,
    build_anchor_md_metadata_fn,
    apply_md_metadata_to_anchor_fn,
    rect=None,
):
    words_path = run_dir / ("prev_words.json" if side in ("old", "prev", "previous") else "words.json")
    words = read_json_fn(words_path, []) or []
    ids = sorted({int(w) for w in word_ids if isinstance(w, int) or str(w).isdigit()})
    ids = [i for i in ids if 0 <= i < len(words)]
    if not ids:
        return None
    anchor = {
        "run_id": run_id,
        "side": "old" if side in ("old", "prev", "previous") else "new",
        "word_ids": ids,
        "old_word_ids": ids if side in ("old", "prev", "previous") else [],
        "new_word_ids": ids if side not in ("old", "prev", "previous") else [],
        "page": first_word_page_fn(words, ids),
        "bbox": words_bbox_fn(words, ids),
        "rect": rect,
        "text": text_for_word_ids_fn(words, ids),
        "floating": False,
    }
    return apply_md_metadata_to_anchor_fn(anchor, build_anchor_md_metadata_fn(run_dir, side, anchor))


def review_projection_for_anchor(review, anchor_run_id, display_side=None):
    anchor = (review.get("anchors") or {}).get(anchor_run_id)
    if not anchor:
        return None
    side = display_side or anchor.get("side", "new")
    word_ids = anchor.get("word_ids", [])
    return {
        "review_id": review.get("review_id"),
        "anchor_id": review.get("anchor_id"),
        "side": side,
        "anchor_run_id": anchor_run_id,
        "old_word_ids": word_ids if side == "old" else [],
        "new_word_ids": word_ids if side != "old" else [],
        "segment_ids": anchor.get("segment_ids", []),
        "change_ids": anchor.get("change_ids", []),
        "old_lines": anchor.get("old_lines", []),
        "new_lines": anchor.get("new_lines", []),
        "section_id": anchor.get("section_id"),
        "md_anchor": anchor.get("md_anchor", {}),
        "text": anchor.get("text") or review.get("text", ""),
        "selection": {"page": anchor.get("page"), "rect": anchor.get("rect"), "word_ids": anchor.get("word_ids", [])},
        "comment": "\n\n".join(c.get("text", "") for c in review.get("comments", []) if c.get("text")),
        "comments": review.get("comments", []),
        "status": review.get("status", "open"),
        "created_at": review.get("created_at"),
        "updated_at": review.get("updated_at"),
        "floating": anchor.get("floating", False),
    }


def run_side_anchor_key(run_id, side):
    return f"{run_id}:{side}"


def copied_comments_for_forward(review, *, utc_now_fn):
    copied = []
    for comment in review.get("comments", []) or []:
        text = str(comment.get("text", "")).strip()
        if not text:
            continue
        copied.append(
            {
                "comment_id": "c-" + uuid.uuid4().hex[:8],
                "author": comment.get("author", "user"),
                "text": text,
                "created_at": comment.get("created_at") or utc_now_fn(),
            }
        )
    if not copied and review.get("comment"):
        copied.append(
            {
                "comment_id": "c-" + uuid.uuid4().hex[:8],
                "author": "user",
                "text": str(review.get("comment", "")),
                "created_at": review.get("created_at") or utc_now_fn(),
            }
        )
    return copied


def assessment_anchor_for_previous_side(review, prev_run_id, run_id):
    anchors = review.get("anchors") or {}
    run_old_key = run_side_anchor_key(run_id, "old")
    candidates = [
        (prev_run_id, anchors.get(prev_run_id)),
        (run_old_key, anchors.get(run_old_key)),
        (run_id, anchors.get(run_id)),
    ]
    for anchor_run_id, anchor in candidates:
        if not anchor:
            continue
        if anchor_run_id == run_id and anchor.get("side") != "old":
            continue
        return anchor, anchor_run_id
    return None, None


def document_reviews_for_run(doc_id, run_id, *, load_document_meta_fn, load_document_reviews_fn):
    meta = load_document_meta_fn(doc_id) or {}
    run_meta = next((r for r in meta.get("runs", []) if r.get("run_id") == run_id), {})
    prev_run_id = run_meta.get("previous_run_id")
    projected = []
    for review in load_document_reviews_fn(doc_id):
        explicit_old_projection = review_projection_for_anchor(review, run_side_anchor_key(run_id, "old"), "old")
        if explicit_old_projection:
            projected.append(explicit_old_projection)
        elif prev_run_id and prev_run_id != run_id:
            prev_anchor = (review.get("anchors") or {}).get(prev_run_id)
            if prev_anchor and prev_anchor.get("side") != "old":
                prev_projection = review_projection_for_anchor(review, prev_run_id, "old")
                if prev_projection:
                    projected.append(prev_projection)
        current_projection = review_projection_for_anchor(review, run_id)
        if current_projection:
            projected.append(current_projection)
    return projected


def md_equal_entries_for_anchor(old_ids, semantic_map, max_nearby_distance=120):
    old_ids = sorted({int(w) for w in old_ids if isinstance(w, int) or str(w).isdigit()})
    if not old_ids:
        return []
    eq = [e for e in (semantic_map or {}).get("equal_words", []) if e.get("old_word_id") is not None and e.get("new_word_id") is not None]
    exact = [e for e in eq if e.get("old_word_id") in set(old_ids)]
    if exact:
        return sorted(exact, key=lambda e: e.get("old_word_id", 0))

    lo, hi = min(old_ids), max(old_ids)

    def distance(entry):
        oid = entry.get("old_word_id", 0)
        if lo <= oid <= hi:
            return 0
        return min(abs(oid - lo), abs(oid - hi))

    nearby = sorted(eq, key=lambda e: (distance(e), e.get("old_word_id", 0)))
    if not nearby or distance(nearby[0]) > max_nearby_distance:
        return []
    best = nearby[0]
    best_old_line = best.get("old_line")
    if best_old_line is not None:
        same_line = [e for e in eq if e.get("old_line") == best_old_line]
        if same_line:
            return sorted(same_line, key=lambda e: e.get("old_word_id", 0))
    return [best]


def current_word_ids_from_md_equal_anchor(old_ids, semantic_map, new_words):
    entries = md_equal_entries_for_anchor(old_ids, semantic_map)
    if not entries:
        return []
    new_lines = {e.get("new_line") for e in entries if e.get("new_line") is not None}
    eq = (semantic_map or {}).get("equal_words", [])
    if new_lines:
        ids = [e.get("new_word_id") for e in eq if e.get("new_line") in new_lines and e.get("new_word_id") is not None]
    else:
        ids = [e.get("new_word_id") for e in entries if e.get("new_word_id") is not None]
    seen, out = set(), []
    for wid in ids:
        if isinstance(wid, int) and 0 <= wid < len(new_words) and wid not in seen:
            seen.add(wid)
            out.append(wid)
    return out


def current_word_ids_from_anchor_md(prev_anchor, semantic_map, new_words):
    md_anchor = prev_anchor.get("md_anchor") or {}
    refs = md_anchor.get("equal_refs") or []
    new_lines = {r.get("new_line") for r in refs if r.get("new_line") is not None}
    if not new_lines:
        old_lines = set(md_anchor.get("old_lines") or prev_anchor.get("old_lines") or [])
        if old_lines:
            new_lines = {e.get("new_line") for e in (semantic_map or {}).get("equal_words", []) if e.get("old_line") in old_lines and e.get("new_line") is not None}
    ids = []
    if new_lines:
        ids = [e.get("new_word_id") for e in (semantic_map or {}).get("equal_words", []) if e.get("new_line") in new_lines and e.get("new_word_id") is not None]
    elif refs:
        ids = [r.get("new_word_id") for r in refs if r.get("new_word_id") is not None]
    seen, out = set(), []
    for wid in ids:
        if isinstance(wid, int) and 0 <= wid < len(new_words) and wid not in seen:
            seen.add(wid)
            out.append(wid)
    return out


def map_anchor_to_current_md_anchor(prev_anchor, semantic_map, new_words):
    ids = current_word_ids_from_anchor_md(prev_anchor, semantic_map, new_words)
    if ids:
        return ids
    old_word_ids = prev_anchor.get("word_ids") or prev_anchor.get("old_word_ids") or prev_anchor.get("new_word_ids") or []
    return current_word_ids_from_md_equal_anchor(old_word_ids, semantic_map, new_words)


def find_word_sequence_by_text(words, text, *, norm_token_fn):
    tokens = [norm_token_fn(t) for t in str(text or "").split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return []
    norms = [w.get("norm") or norm_token_fn(w.get("text", "")) for w in words]
    max_len = min(len(tokens), 80)
    for length in range(max_len, 0, -1):
        needle = tokens[:length]
        for start in range(0, max(0, len(norms) - length + 1)):
            if norms[start : start + length] == needle:
                return [words[i].get("idx", i) for i in range(start, start + length)]
    return []


def word_ids_in_page_bbox(words, page, bbox, pad=18):
    if not page or not bbox:
        return []
    x0, y0, x1, y1 = bbox
    box = [x0 - pad, y0 - pad, x1 + pad, y1 + pad]
    ids = []
    for word in words or []:
        if word.get("page") != page:
            continue
        wx0, wy0, wx1, wy1 = word.get("bbox", [0, 0, 0, 0])
        if not (wx1 < box[0] or box[2] < wx0 or wy1 < box[1] or box[3] < wy0):
            wid = word.get("idx")
            if isinstance(wid, int):
                ids.append(wid)
    return ids


def migrate_previous_review_to_current(
    doc_id,
    run_id,
    review_id,
    *,
    load_document_meta_fn,
    update_run_meta_fn,
    document_run_dir_fn,
    load_document_reviews_fn,
    read_json_fn,
    document_semantic_map_fn,
    map_anchor_to_current_md_anchor_fn,
    find_word_sequence_by_text_fn,
    fallback_current_word_ids_from_diff_fn,
    first_word_page_fn,
    words_bbox_fn,
    text_for_word_ids_fn,
    utc_now_fn,
    copied_comments_for_forward_fn,
    save_document_reviews_fn,
    document_reviews_for_run_fn,
    semantic_module,
):
    meta = load_document_meta_fn(doc_id)
    if not meta:
        return {"error": "document not found"}, 404
    run_meta = update_run_meta_fn(meta, run_id)
    if not run_meta:
        return {"error": "run not found"}, 404
    prev_run_id = run_meta.get("previous_run_id")
    if not prev_run_id:
        return {"error": "migration requires a diff run"}, 400
    run_dir = document_run_dir_fn(doc_id, run_id)
    if not run_dir.exists():
        return {"error": "run not found"}, 404

    reviews = load_document_reviews_fn(doc_id)
    source_review = next((r for r in reviews if r.get("review_id") == review_id), None)
    if not source_review:
        return {"error": "review not found"}, 404
    existing_copy = next(
        (
            r
            for r in reviews
            if r.get("copied_from_review_id") == review_id
            and r.get("created_run_id") == run_id
            and ((r.get("anchors") or {}).get(run_id) or {}).get("side") == "new"
        ),
        None,
    )
    if existing_copy:
        return {"review": review_projection_for_anchor(existing_copy, run_id), "already_current": True}, 200
    anchors = source_review.setdefault("anchors", {})

    source_anchor_run_id = prev_run_id
    prev_anchor = anchors.get(prev_run_id)
    if not prev_anchor and anchors.get(run_id, {}).get("side") == "old":
        source_anchor_run_id = run_id
        prev_anchor = anchors.get(run_id)
    if not prev_anchor:
        return {"error": "previous anchor not found"}, 404

    new_words = read_json_fn(run_dir / "words.json", []) or []
    new_ids = map_anchor_to_current_md_anchor_fn(prev_anchor, document_semantic_map_fn(doc_id, run_id), new_words)
    if not new_ids:
        new_ids = find_word_sequence_by_text_fn(new_words, prev_anchor.get("text") or source_review.get("text", ""))
    if not new_ids:
        new_ids = fallback_current_word_ids_from_diff_fn(run_dir, prev_anchor, new_words)
    anchor = {
        "run_id": run_id,
        "side": "new",
        "word_ids": new_ids,
        "old_word_ids": [],
        "new_word_ids": new_ids,
        "page": first_word_page_fn(new_words, new_ids) if new_ids else None,
        "bbox": words_bbox_fn(new_words, new_ids) if new_ids else None,
        "text": text_for_word_ids_fn(new_words, new_ids) if new_ids else "",
        "floating": not bool(new_ids),
        "migrated_from_run_id": source_anchor_run_id,
        "migrated_at": utc_now_fn(),
    }
    now = utc_now_fn()
    copied_review = {
        "review_id": "r-" + uuid.uuid4().hex[:8],
        "anchor_id": "a-" + uuid.uuid4().hex[:8],
        "doc_id": doc_id,
        "status": source_review.get("status", "open"),
        "created_run_id": run_id,
        "is_floating": anchor.get("floating", False),
        "text": anchor.get("text") or source_review.get("text", ""),
        "comments": copied_comments_for_forward_fn(source_review),
        "anchors": {run_id: anchor},
        "copied_from_review_id": source_review.get("review_id"),
        "copied_from_anchor_run_id": source_anchor_run_id,
        "copied_at": now,
        "created_at": now,
        "updated_at": now,
    }
    reviews.append(copied_review)
    save_document_reviews_fn(doc_id, reviews)
    semantic_module.save_reviews(run_dir, document_reviews_for_run_fn(doc_id, run_id))
    return {"review": review_projection_for_anchor(copied_review, run_id), "already_current": False}, 200
