import re


def assessment_reviews_for_run(
    doc_id,
    run_id,
    *,
    load_document_meta_fn,
    run_meta_for_fn,
    load_document_reviews_fn,
    assessment_anchor_for_previous_side_fn,
    review_id=None,
):
    meta = load_document_meta_fn(doc_id) or {}
    run_meta = run_meta_for_fn(meta, run_id) or {}
    prev_run_id = run_meta.get("previous_run_id")
    if not prev_run_id:
        return []
    reviews = []
    for review in load_document_reviews_fn(doc_id):
        if review_id and review.get("review_id") != review_id:
            continue
        if review.get("status", "open") not in ("open", "partial", "unclear"):
            continue
        prev_anchor, anchor_run_id = assessment_anchor_for_previous_side_fn(review, prev_run_id, run_id)
        if prev_anchor:
            reviews.append((review, prev_anchor, anchor_run_id))
    return reviews


def map_old_word_ids_to_current(old_word_ids, semantic_map):
    by_old = {
        item.get("old_word_id"): item
        for item in (semantic_map or {}).get("equal_words", [])
        if item.get("old_word_id") is not None and item.get("new_word_id") is not None
    }
    mapped = [by_old[wid].get("new_word_id") for wid in old_word_ids if wid in by_old]
    return [wid for wid in mapped if wid is not None]


def map_old_word_ids_to_current_md_anchor(old_word_ids, semantic_map, new_words, *, current_word_ids_from_md_equal_anchor_fn):
    return current_word_ids_from_md_equal_anchor_fn(old_word_ids, semantic_map, new_words)


def segment_numbers_for_old_word_ids(old_word_ids, semantic_map):
    wanted = set(old_word_ids or [])
    nums = []
    for item in (semantic_map or {}).get("equal_words", []):
        if item.get("old_word_id") in wanted:
            seg_id = item.get("segment_id")
            m = re.search(r"(\d+)$", str(seg_id or ""))
            if m:
                nums.append(int(m.group(1)))
    return nums


def related_diff_changes_for_review(viewer_data, prev_anchor, semantic_map, *, trim_for_prompt_fn, limit=8):
    changes = (viewer_data or {}).get("changes", []) or []
    old_ids = list(prev_anchor.get("word_ids") or prev_anchor.get("old_word_ids") or prev_anchor.get("new_word_ids") or [])
    seg_nums = segment_numbers_for_old_word_ids(old_ids, semantic_map)
    anchor_page = prev_anchor.get("page")
    scored = []
    for change in changes:
        seg_start = change.get("seg_start")
        seg_end = change.get("seg_end")
        seg_score = 999999
        if seg_nums and seg_start is not None and seg_end is not None:
            seg_score = min(0 if seg_start <= n <= seg_end else min(abs(n - seg_start), abs(n - seg_end)) for n in seg_nums)
        page_score = 0 if anchor_page and change.get("old_page") == anchor_page else 50
        if not seg_nums and page_score:
            continue
        scored.append((seg_score + page_score, change))
    scored.sort(key=lambda x: (x[0], x[1].get("id", 0)))
    return [
        {
            "change_id": change.get("id"),
            "previous_page": change.get("old_page"),
            "current_page": change.get("new_page"),
            "removed_text": trim_for_prompt_fn(change.get("old_text", ""), 1800),
            "added_text": trim_for_prompt_fn(change.get("new_text", ""), 1800),
        }
        for _score, change in scored[:limit]
        if change.get("old_text") or change.get("new_text")
    ]


def assessment_group_key(entry):
    pack = entry.get("context_pack", {})
    section = pack.get("recognized_report_section") or {}
    prev_section_id = section.get("previous_section_id")
    if prev_section_id:
        return f"previous_section:{prev_section_id}"
    current_section_id = section.get("current_section_id")
    if current_section_id:
        return f"current_section:{current_section_id}"
    page = (pack.get("selected_previous_anchor") or {}).get("page")
    if page:
        return f"previous_page:{page}"
    return f"review:{entry.get('item', {}).get('review_id')}"


def group_assessment_entries_by_report_section(entries):
    grouped = {}
    for entry in entries:
        key = assessment_group_key(entry)
        grouped.setdefault(key, []).append(entry)
    return list(grouped.items())


def change_by_id(viewer_data, change_id):
    wanted = int(change_id)
    for change in (viewer_data or {}).get("changes", []) or []:
        if int(change.get("id", -1)) == wanted:
            return change
    return None


def section_id_for_change_anchor(run_dir, section_map, change, side, *, infer_section_id_for_anchor_fn, section_entries_fn):
    anchor = (change or {}).get("old_anchor" if side == "old" else "new_anchor") or {}
    inferred = infer_section_id_for_anchor_fn(section_map, anchor, run_dir / ("prev_report.md" if side == "old" else "report.md"))
    if inferred:
        return inferred
    page = (change or {}).get("old_page" if side == "old" else "new_page")
    if not page:
        return None
    for section in section_entries_fn(section_map):
        start = section.get("page_start")
        end = section.get("page_end") or start
        if start and end and int(start) <= int(page) <= int(end):
            return section.get("section_id")
    return None


def reviews_for_sections(
    doc_id,
    run_id,
    *,
    document_reviews_for_run_fn,
    review_threads_for_prompt_from_projection_fn,
    old_section_id=None,
    current_section_id=None,
):
    out = []
    seen = set()
    for review in document_reviews_for_run_fn(doc_id, run_id):
        rid = review.get("review_id")
        if not rid or rid in seen:
            continue
        side = "old" if (review.get("old_word_ids") or []) else "new"
        sec = review.get("section_id")
        if side == "old" and old_section_id and sec != old_section_id:
            continue
        if side == "new" and current_section_id and sec != current_section_id:
            continue
        if side == "old" and not old_section_id:
            continue
        if side == "new" and not current_section_id:
            continue
        seen.add(rid)
        out.append(
            {
                "review_id": rid,
                "side": side,
                "status": review.get("status", "open"),
                "section_id": sec,
                "text": review.get("text", ""),
                "review_thread": review_threads_for_prompt_from_projection_fn(review),
            }
        )
    return out[:30]


def build_assessment_context_pack(
    doc_id,
    run_id,
    run_dir,
    review,
    prev_anchor,
    prev_section_map,
    current_section_map,
    viewer_data,
    semantic_map,
    *,
    read_json_fn,
    map_anchor_to_current_md_anchor_fn,
    infer_section_id_for_anchor_fn,
    first_word_page_fn,
    words_bbox_fn,
    text_for_word_ids_fn,
    review_thread_for_prompt_fn,
    reviews_for_sections_fn,
    related_diff_changes_for_review_fn,
):
    prev_md = run_dir / "prev_report.md"
    current_md = run_dir / "report.md"
    current_words = read_json_fn(run_dir / "words.json", []) or []
    old_ids = list(prev_anchor.get("word_ids") or prev_anchor.get("old_word_ids") or prev_anchor.get("new_word_ids") or [])
    current_ids = map_anchor_to_current_md_anchor_fn(prev_anchor, semantic_map, current_words)

    previous_section_id = infer_section_id_for_anchor_fn(prev_section_map, prev_anchor, prev_md)
    current_anchor = {
        "word_ids": current_ids,
        "page": first_word_page_fn(current_words, current_ids),
        "bbox": words_bbox_fn(current_words, current_ids) if current_ids else None,
        "text": text_for_word_ids_fn(current_words, current_ids) if current_ids else "",
    }
    current_section_id = infer_section_id_for_anchor_fn(current_section_map, current_anchor, current_md)

    return {
        "review_id": review.get("review_id"),
        "review_status": review.get("status", "open"),
        "review_thread": review_thread_for_prompt_fn(review),
        "recognized_report_section": {
            "previous_section_id": previous_section_id,
            "current_section_id": current_section_id,
        },
        "selected_previous_anchor": {
            "page": prev_anchor.get("page"),
            "bbox": prev_anchor.get("bbox"),
            "text": prev_anchor.get("text") or review.get("text", ""),
            "word_ids_count": len(old_ids),
        },
        "matched_current_anchor": {
            "page": current_anchor.get("page"),
            "bbox": current_anchor.get("bbox"),
            "text": current_anchor.get("text"),
            "word_ids_count": len(current_ids),
        },
        "matching_section_reviews": reviews_for_sections_fn(doc_id, run_id, old_section_id=previous_section_id, current_section_id=current_section_id),
        "matching_diff_add_delete": related_diff_changes_for_review_fn(viewer_data, prev_anchor, semantic_map),
    }


def build_change_assessment_context_pack(
    doc_id,
    run_id,
    run_dir,
    change,
    prev_section_map,
    current_section_map,
    *,
    section_id_for_change_anchor_fn,
    reviews_for_sections_fn,
    trim_for_prompt_fn,
):
    old_section_id = section_id_for_change_anchor_fn(run_dir, prev_section_map, change, "old")
    current_section_id = section_id_for_change_anchor_fn(run_dir, current_section_map, change, "new")
    return {
        "change_id": change.get("id"),
        "change_bundle": {
            "removed_text": trim_for_prompt_fn(change.get("old_text", ""), 3500),
            "added_text": trim_for_prompt_fn(change.get("new_text", ""), 3500),
            "old_page": change.get("old_page"),
            "new_page": change.get("new_page"),
            "removed_count": len(change.get("old_highlight_ids") or []),
            "added_count": len(change.get("new_highlight_ids") or []),
        },
        "recognized_report_section": {
            "previous_section_id": old_section_id,
            "current_section_id": current_section_id,
        },
        "matching_section_reviews": reviews_for_sections_fn(doc_id, run_id, old_section_id=old_section_id, current_section_id=current_section_id),
    }


def nearest_words_for_anchor(new_words, page, bbox, limit=8):
    if not (new_words and page and bbox):
        return []
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    scored = []
    for word in new_words:
        if word.get("page") != page:
            continue
        wx0, wy0, wx1, wy1 = word.get("bbox", [0, 0, 0, 0])
        wcx = (wx0 + wx1) / 2
        wcy = (wy0 + wy1) / 2
        dist = ((wcx - cx) ** 2 + (wcy - cy) ** 2) ** 0.5
        scored.append((dist, word.get("idx")))
    scored.sort(key=lambda x: x[0])
    return [wid for _dist, wid in scored if isinstance(wid, int)][:limit]


def new_word_ids_for_change(run_dir, change, *, read_json_fn, word_ids_in_page_bbox_fn, nearest_words_for_anchor_fn):
    new_words = read_json_fn(run_dir / "words.json", []) or []
    if not new_words:
        return []
    ids = []
    by_id = {h.get("id"): h for h in (read_json_fn(run_dir / "highlights_new.json", []) or [])}
    if not by_id:
        viewer_data = read_json_fn(run_dir / "viewer_data.json", {}) or {}
        by_id = {h.get("id"): h for h in (viewer_data.get("highlights_new") or []) if h.get("id")}
    for hid in change.get("new_highlight_ids") or []:
        h = by_id.get(hid) or {}
        page = h.get("page")
        bbox = h.get("bbox")
        if page and bbox:
            ids.extend(word_ids_in_page_bbox_fn(new_words, page, bbox, pad=6))
    if ids:
        return sorted({i for i in ids if isinstance(i, int)})
    new_anchor = change.get("new_anchor") or {}
    anchor_ids = word_ids_in_page_bbox_fn(
        new_words,
        new_anchor.get("page") or change.get("new_page"),
        new_anchor.get("bbox"),
        pad=12,
    )
    if anchor_ids:
        return sorted({i for i in anchor_ids if isinstance(i, int)})
    old_anchor = change.get("old_anchor") or {}
    nearest = nearest_words_for_anchor_fn(
        new_words,
        change.get("new_page") or old_anchor.get("page") or 1,
        old_anchor.get("bbox") or [0, 0, 1, 1],
        limit=8,
    )
    return sorted({i for i in nearest if isinstance(i, int)})


def build_change_groups_for_run(
    doc_id,
    run_id,
    *,
    load_document_meta_fn,
    run_meta_for_fn,
    document_run_dir_fn,
    read_json_fn,
    build_change_assessment_context_pack_fn,
    change_ids=None,
):
    meta = load_document_meta_fn(doc_id) or {}
    run_meta = run_meta_for_fn(meta, run_id) or {}
    if not run_meta:
        return []
    if not run_meta.get("previous_run_id"):
        return []
    run_dir = document_run_dir_fn(doc_id, run_id)
    prev_section_map = read_json_fn(run_dir / "prev_section_map.json", []) or []
    current_section_map = read_json_fn(run_dir / "section_map.json", []) or []
    viewer_data = read_json_fn(run_dir / "viewer_data.json", {}) or {}
    changes = viewer_data.get("changes", []) or []
    if change_ids is not None:
        wanted = {int(cid) for cid in (change_ids or [])}
        changes = [c for c in changes if int(c.get("id", -1)) in wanted]
    grouped = {}
    for change in changes:
        pack = build_change_assessment_context_pack_fn(doc_id, run_id, run_dir, change, prev_section_map, current_section_map)
        old_sid = (pack.get("recognized_report_section") or {}).get("previous_section_id")
        cur_sid = (pack.get("recognized_report_section") or {}).get("current_section_id")
        if old_sid:
            gkey = f"old:{old_sid}"
        elif cur_sid:
            gkey = f"new:{cur_sid}"
        else:
            gkey = f"page:{change.get('old_page') or '-'}:{change.get('new_page') or '-'}"
        grouped.setdefault(gkey, []).append(int(change.get("id")))
    return [{"group_key": key, "change_ids": ids} for key, ids in grouped.items()]
