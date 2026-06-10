import uuid


def run_ai_assessment_service(
    doc_id,
    run_id,
    *,
    review_id=None,
    review_ids=None,
    skip_existing=True,
    model_name,
    load_document_meta_fn,
    run_meta_for_fn,
    document_run_dir_fn,
    read_json_fn,
    available_assessment_sections_fn,
    assessment_reviews_for_run_fn,
    required_ai_api_key_name_fn,
    required_ai_api_key_fn,
    load_ai_assessment_fn,
    utc_now_fn,
    build_assessment_context_pack_fn,
    anchor_section_id_fn,
    call_ai_assessment_fn,
    build_assessment_prompt_fn,
    group_assessment_entries_by_report_section_fn,
    call_ai_assessment_batch_fn,
    save_ai_assessment_fn,
    update_run_meta_fn,
    save_document_meta_fn,
):
    meta = load_document_meta_fn(doc_id)
    if not meta:
        return None, ("document not found", 404)
    run_meta = run_meta_for_fn(meta, run_id)
    if not run_meta:
        return None, ("run not found", 404)
    prev_run_id = run_meta.get("previous_run_id")
    if not prev_run_id:
        return None, ("AI assessment requires a diff run", 400)
    run_dir = document_run_dir_fn(doc_id, run_id)
    prev_section_map = read_json_fn(run_dir / "prev_section_map.json", []) or []
    current_section_map = read_json_fn(run_dir / "section_map.json", []) or []
    viewer_data = read_json_fn(run_dir / "viewer_data.json", {}) or {}
    semantic_map = viewer_data.get("semantic_map", {}) or {}
    tool_context = {
        "previous": (run_dir / "prev_report.md", prev_section_map),
        "current": (run_dir / "report.md", current_section_map),
    }
    available_sections = available_assessment_sections_fn(prev_section_map, current_section_map)
    targets = assessment_reviews_for_run_fn(doc_id, run_id, review_id=review_id)
    if review_ids is not None:
        allowed = {str(rid).strip() for rid in (review_ids or []) if str(rid).strip()}
        targets = [
            (review, prev_anchor, anchor_run_id)
            for review, prev_anchor, anchor_run_id in targets
            if str(review.get("review_id", "")).strip() in allowed
        ]
    if review_id and not targets:
        return None, ("review is not assessable for previous run", 404)
    required_key_name = required_ai_api_key_name_fn(model_name)
    if targets and not required_ai_api_key_fn(model_name):
        return None, (f"{required_key_name} is not set", 400)
    previous = load_ai_assessment_fn(doc_id, run_id)
    replacing = {review.get("review_id") for review, _, _ in targets}
    previous_items = previous.get("items", []) or []
    previous_done = {
        item.get("review_id")
        for item in previous_items
        if item.get("review_id") and item.get("status") == "done" and item.get("verdict")
    }
    skipped_existing_count = 0
    if review_id:
        items = [item for item in previous_items if item.get("review_id") not in replacing]
    else:
        if skip_existing:
            before_skip = len(targets)
            targets = [(review, prev_anchor, anchor_run_id) for review, prev_anchor, anchor_run_id in targets if review.get("review_id") not in previous_done]
            skipped_existing_count = before_skip - len(targets)
        items = list(previous_items)
    started_at = utc_now_fn()
    prepared = []
    for review, prev_anchor, anchor_run_id in targets:
        context_pack = build_assessment_context_pack_fn(doc_id, run_id, run_dir, review, prev_anchor, prev_section_map, current_section_map, viewer_data, semantic_map)
        item = {
            "review_id": review.get("review_id"),
            "anchor_run_id": anchor_run_id,
            "section_id": (context_pack.get("recognized_report_section") or {}).get("previous_section_id") or anchor_section_id_fn(prev_anchor),
            "status": "done",
            "assessed_at": utc_now_fn(),
            "input_summary": {
                "previous_section_id": (context_pack.get("recognized_report_section") or {}).get("previous_section_id"),
                "current_section_id": (context_pack.get("recognized_report_section") or {}).get("current_section_id"),
                "diff_count": len(context_pack.get("matching_diff_add_delete", [])),
                "comment_count": len(context_pack.get("review_thread", [])),
                "related_review_count": len(context_pack.get("matching_section_reviews", [])),
            },
        }
        prepared.append({"review": review, "prev_anchor": prev_anchor, "item": item, "context_pack": context_pack})

    if review_id:
        for entry in prepared:
            try:
                entry["item"].update(call_ai_assessment_fn(build_assessment_prompt_fn(entry["review"], entry["prev_anchor"], available_sections, entry["context_pack"]), tool_context))
            except Exception as e:
                entry["item"].update({"status": "error", "verdict": "unclear", "confidence": None, "reasoning": str(e), "evidence_old": "", "evidence_new": ""})
            items.append(entry["item"])
    else:
        for group_index, (group_key, batch) in enumerate(group_assessment_entries_by_report_section_fn(prepared), start=1):
            try:
                verdicts = call_ai_assessment_batch_fn([entry["context_pack"] for entry in batch], available_sections, tool_context)
                for entry in batch:
                    rid = entry["item"].get("review_id")
                    if rid in verdicts:
                        entry["item"].update(verdicts[rid])
                    else:
                        entry["item"].update({"status": "error", "verdict": "unclear", "confidence": None, "reasoning": "AI did not return a verdict for this review_id", "evidence_old": "", "evidence_new": ""})
                    entry["item"]["batch_index"] = group_index
                    entry["item"]["batch_key"] = group_key
                    items.append(entry["item"])
            except Exception as e:
                for entry in batch:
                    entry["item"].update({"status": "error", "verdict": "unclear", "confidence": None, "reasoning": str(e), "evidence_old": "", "evidence_new": "", "batch_index": group_index, "batch_key": group_key})
                    items.append(entry["item"])
    assessment = {
        "status": "done",
        "model": model_name,
        "started_at": started_at,
        "completed_at": utc_now_fn(),
        "skipped_existing_count": skipped_existing_count,
        "items": items,
    }
    save_ai_assessment_fn(doc_id, run_id, assessment)
    update_run_meta_fn(meta, run_id, has_ai_assessment=True)
    save_document_meta_fn(meta)
    return assessment, None


def run_change_ai_assessment_service(
    doc_id,
    run_id,
    *,
    change_id=None,
    change_ids=None,
    model_name,
    load_document_meta_fn,
    run_meta_for_fn,
    document_run_dir_fn,
    read_json_fn,
    required_ai_api_key_name_fn,
    required_ai_api_key_fn,
    load_change_ai_assessment_fn,
    utc_now_fn,
    available_assessment_sections_fn,
    build_change_assessment_context_pack_fn,
    call_ai_change_assessment_fn,
    build_change_assessment_prompt_fn,
    enforce_change_verdict_policy_fn,
    build_change_groups_for_run_fn,
    call_ai_change_assessment_batch_fn,
    harmonize_change_items_fn,
    save_change_ai_assessment_fn,
    update_run_meta_fn,
    save_document_meta_fn,
):
    meta = load_document_meta_fn(doc_id)
    if not meta:
        return None, ("document not found", 404)
    run_meta = run_meta_for_fn(meta, run_id)
    if not run_meta:
        return None, ("run not found", 404)
    prev_run_id = run_meta.get("previous_run_id")
    if not prev_run_id:
        return None, ("AI assessment requires a diff run", 400)
    run_dir = document_run_dir_fn(doc_id, run_id)
    prev_section_map = read_json_fn(run_dir / "prev_section_map.json", []) or []
    current_section_map = read_json_fn(run_dir / "section_map.json", []) or []
    viewer_data = read_json_fn(run_dir / "viewer_data.json", {}) or {}
    tool_context = {
        "previous": (run_dir / "prev_report.md", prev_section_map),
        "current": (run_dir / "report.md", current_section_map),
    }
    changes = viewer_data.get("changes", []) or []
    if change_id is not None:
        only = int(change_id)
        changes = [c for c in changes if int(c.get("id", -1)) == only]
        if not changes:
            return None, ("change not found", 404)
    if change_ids is not None:
        wanted = {int(cid) for cid in (change_ids or [])}
        changes = [c for c in changes if int(c.get("id", -1)) in wanted]
    required_key_name = required_ai_api_key_name_fn(model_name)
    if changes and not required_ai_api_key_fn(model_name):
        return None, (f"{required_key_name} is not set", 400)
    previous = load_change_ai_assessment_fn(doc_id, run_id)
    replacing = {int(c.get("id")) for c in changes if c.get("id") is not None}
    previous_items = previous.get("items", []) or []
    previous_by_id = {int(item.get("change_id")): item for item in previous_items if item.get("change_id") is not None}
    kept = [item for item in previous_items if int(item.get("change_id", -1)) not in replacing]
    started_at = utc_now_fn()
    available_sections = available_assessment_sections_fn(prev_section_map, current_section_map)
    prepared = []
    for change in changes:
        pack = build_change_assessment_context_pack_fn(doc_id, run_id, run_dir, change, prev_section_map, current_section_map)
        item = {
            "change_id": int(change.get("id")),
            "status": "done",
            "assessed_at": utc_now_fn(),
            "old_section_id": (pack.get("recognized_report_section") or {}).get("previous_section_id"),
            "current_section_id": (pack.get("recognized_report_section") or {}).get("current_section_id"),
            "old_text": change.get("old_text", ""),
            "new_text": change.get("new_text", ""),
        }
        prev_item = previous_by_id.get(item["change_id"]) or {}
        if prev_item.get("forwarded_review_id"):
            item["forwarded_review_id"] = prev_item.get("forwarded_review_id")
            item["forwarded_at"] = prev_item.get("forwarded_at")
        prepared.append({"change": change, "item": item, "context_pack": pack})
    if change_id is not None:
        for entry in prepared:
            try:
                entry["item"].update(call_ai_change_assessment_fn(
                    build_change_assessment_prompt_fn(entry["context_pack"], available_sections),
                    tool_context,
                ))
                enforce_change_verdict_policy_fn(entry["item"])
            except Exception as e:
                entry["item"].update({
                    "status": "error",
                    "verdict": "low",
                    "confidence": None,
                    "reasoning": str(e),
                    "recommended_comment": "",
                    "evidence_old": "",
                    "evidence_new": "",
                })
            kept.append(entry["item"])
    else:
        grouped_meta = build_change_groups_for_run_fn(
            doc_id,
            run_id,
            change_ids=[entry["item"].get("change_id") for entry in prepared],
        )
        grouped = {str(group.get("group_key")): [] for group in grouped_meta}
        by_id = {entry["item"].get("change_id"): entry for entry in prepared}
        for group in grouped_meta:
            gkey = str(group.get("group_key"))
            for cid in group.get("change_ids") or []:
                if cid in by_id:
                    grouped[gkey].append(by_id[cid])
        for group_key, batch in grouped.items():
            if not batch:
                continue
            try:
                verdicts = call_ai_change_assessment_batch_fn(
                    [entry["context_pack"] for entry in batch],
                    available_sections,
                    tool_context,
                )
                for entry in batch:
                    cid = entry["item"].get("change_id")
                    if cid in verdicts:
                        entry["item"].update(verdicts[cid])
                        enforce_change_verdict_policy_fn(entry["item"])
                    else:
                        entry["item"].update({
                            "status": "error",
                            "verdict": "low",
                            "confidence": None,
                            "reasoning": "AI did not return a verdict for this change_id",
                            "recommended_comment": "",
                            "evidence_old": "",
                            "evidence_new": "",
                        })
                    entry["item"]["batch_key"] = group_key
                    kept.append(entry["item"])
            except Exception as e:
                for entry in batch:
                    entry["item"].update({
                        "status": "error",
                        "verdict": "low",
                        "confidence": None,
                        "reasoning": str(e),
                        "recommended_comment": "",
                        "evidence_old": "",
                        "evidence_new": "",
                        "batch_key": group_key,
                    })
                    kept.append(entry["item"])
    harmonize_change_items_fn(kept)
    assessment = {
        "status": "done",
        "model": model_name,
        "started_at": started_at,
        "completed_at": utc_now_fn(),
        "items": kept,
    }
    save_change_ai_assessment_fn(doc_id, run_id, assessment)
    update_run_meta_fn(meta, run_id, has_change_ai_assessment=True)
    save_document_meta_fn(meta)
    return assessment, None


def forward_change_assessment_to_review_service(
    doc_id,
    run_id,
    change_id,
    *,
    load_document_meta_fn,
    run_meta_for_fn,
    document_run_dir_fn,
    read_json_fn,
    change_by_id_fn,
    load_change_ai_assessment_fn,
    document_reviews_for_run_fn,
    new_word_ids_for_change_fn,
    anchor_for_selection_fn,
    utc_now_fn,
    load_document_reviews_fn,
    save_document_reviews_fn,
    semantic_save_reviews_fn,
    review_projection_for_anchor_fn,
    save_change_ai_assessment_fn,
):
    meta = load_document_meta_fn(doc_id)
    if not meta:
        return {"error": "document not found"}, 404
    if not run_meta_for_fn(meta, run_id):
        return {"error": "run not found"}, 404
    run_dir = document_run_dir_fn(doc_id, run_id)
    viewer_data = read_json_fn(run_dir / "viewer_data.json", {}) or {}
    change = change_by_id_fn(viewer_data, change_id)
    if not change:
        return {"error": "change not found"}, 404
    assessment = load_change_ai_assessment_fn(doc_id, run_id)
    item = next((x for x in (assessment.get("items") or []) if int(x.get("change_id", -1)) == int(change_id)), None)
    if not item:
        return {"error": "change assessment not found"}, 404
    comment_text = str(item.get("recommended_comment") or "").strip()
    if not comment_text:
        return {"error": "no recommended review comment to forward"}, 400
    if item.get("forwarded_review_id"):
        projected = next((r for r in document_reviews_for_run_fn(doc_id, run_id) if r.get("review_id") == item.get("forwarded_review_id")), None)
        return {"already_forwarded": True, "review": projected, "change_id": int(change_id)}, 200
    word_ids = new_word_ids_for_change_fn(run_dir, change)
    if not word_ids:
        return {"error": "could not anchor forwarded review"}, 400
    anchor = anchor_for_selection_fn(run_dir, run_id, "new", word_ids, (change.get("new_anchor") or {}).get("bbox"))
    if not anchor:
        return {"error": "could not anchor forwarded review"}, 400
    now = utc_now_fn()
    review = {
        "review_id": "r-" + uuid.uuid4().hex[:8],
        "anchor_id": "a-" + uuid.uuid4().hex[:8],
        "doc_id": doc_id,
        "status": "open",
        "created_run_id": run_id,
        "is_floating": False,
        "text": anchor.get("text", ""),
        "comments": [{
            "comment_id": "c-" + uuid.uuid4().hex[:8],
            "author": "ai",
            "text": comment_text,
            "created_at": now,
        }],
        "anchors": {run_id: anchor},
        "source_change_id": int(change_id),
        "source_change_assessment_at": item.get("assessed_at"),
        "created_at": now,
        "updated_at": now,
    }
    reviews = load_document_reviews_fn(doc_id)
    reviews.append(review)
    save_document_reviews_fn(doc_id, reviews)
    semantic_save_reviews_fn(run_dir, document_reviews_for_run_fn(doc_id, run_id))
    item["forwarded_review_id"] = review["review_id"]
    item["forwarded_at"] = now
    save_change_ai_assessment_fn(doc_id, run_id, assessment)
    return {"forwarded": True, "change_id": int(change_id), "review": review_projection_for_anchor_fn(review, run_id)}, 200

