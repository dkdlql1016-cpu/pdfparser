import json


def review_thread_for_prompt(review):
    comments = []
    for idx, comment in enumerate(review.get("comments", []), start=1):
        text = str(comment.get("text", "")).strip()
        if not text:
            continue
        comments.append({
            "index": idx,
            "author": comment.get("author", "user"),
            "created_at": comment.get("created_at", ""),
            "text": text,
        })
    if not comments and review.get("comment"):
        comments.append({"index": 1, "author": "user", "created_at": review.get("created_at", ""), "text": review.get("comment", "")})
    return comments


def review_threads_for_prompt_from_projection(review):
    return review_thread_for_prompt(review)


def build_assessment_prompt(review, prev_anchor, available_sections, context_pack):
    suggested_section_id = (context_pack.get("recognized_report_section") or {}).get("previous_section_id")
    return (
        "Assess whether this prior review requirement is reflected in the current report.\n"
        "Return cleared, partial, not_cleared, or unclear (only if the review itself is too vague to judge).\n\n"
        "Input package:\n"
        f"{json.dumps(context_pack, ensure_ascii=False, indent=2)[:50000]}\n\n"
        f"Suggested section_id:\n{suggested_section_id or '(unknown)'}\n\n"
        "Available markdown files for exceptional extra lookup only: previous, current.\n"
        f"Available sections:\n{json.dumps(available_sections, ensure_ascii=False)[:12000]}\n"
    )


def build_batch_assessment_prompt(context_packs, available_sections):
    return (
        "Assess whether each prior review requirement is reflected in the current report. "
        "These reviews are grouped because they belong to the same inferred report section or nearby fallback page.\n"
        "Return exactly one verdict per review_id: cleared, partial, not_cleared, "
        "or unclear only when the review itself is too vague to judge.\n"
        "Call submit_verdicts with one item per review_id.\n\n"
        "Review bundles:\n"
        f"{json.dumps(context_packs, ensure_ascii=False, indent=2)}\n\n"
        "Available markdown files for exceptional extra lookup only: previous, current.\n"
        f"Available sections:\n{json.dumps(available_sections, ensure_ascii=False)[:12000]}\n"
    )


def build_change_assessment_prompt(context_pack, available_sections):
    return (
        "Assess whether this change introduces additional risk in the updated report.\n"
        "If there is meaningful risk, provide a practical recommended review comment.\n"
        "If there is no meaningful risk, still provide short reasoning.\n\n"
        "Input package:\n"
        f"{json.dumps(context_pack, ensure_ascii=False, indent=2)}\n\n"
        "Available markdown files for exceptional extra lookup only: previous, current.\n"
        f"Available sections:\n{json.dumps(available_sections, ensure_ascii=False)[:12000]}\n"
    )


def build_change_batch_assessment_prompt(context_packs, available_sections):
    return (
        "Assess each change bundle and decide if it introduces additional risk in the updated report.\n"
        "Return one result per change_id using submit_change_reviews.\n\n"
        "Change bundles:\n"
        f"{json.dumps(context_packs, ensure_ascii=False, indent=2)}\n\n"
        "Available markdown files for exceptional extra lookup only: previous, current.\n"
        f"Available sections:\n{json.dumps(available_sections, ensure_ascii=False)[:12000]}\n"
    )

