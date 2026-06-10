"""
semantic.py

Word-level bidirectional anchor map + review storage for the PDF diff viewer.

Design (reviews target UNCHANGED content only):
  Only `equal` words exist on BOTH old and new sides, so they are the only
  tokens that can carry a comment that maps to old PDF, new PDF, and both md
  lines at once. A review = a CHUNK of equal words captured by a drag.

semantic_map shipped in viewer_data.json:
  {
    "unit": "word",
    "equal_words": [
      {"old_word_id": 41, "new_word_id": 43, "segment_id": "seg-12",
       "old_line": 88, "new_line": 90, "text": "Revenue"},
      ...
    ]
  }
old_word_id / new_word_id are indices into old_words / new_words (== their idx),
so the client can resolve bbox/page from each document run word payload.

Inputs (already produced by app.py):
  segments : diff_doc["segments"] after suppress_layout_moves
  mapped   : map_result_segments_to_pdf_indices(segments, old_words, new_words)
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _seg_num(seg_id):
    m = re.search(r"(\d+)$", str(seg_id or ""))
    return int(m.group(1)) if m else 0


def _bbox_overlap(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def build_semantic_map(segments, mapped, old_words, new_words):
    mapped_by_seg = {m["seg_idx"]: m for m in mapped}
    equal_words = []
    for seg_idx, seg in enumerate(segments):
        if seg.get("suppressed") or seg.get("type") != "equal":
            continue
        m = mapped_by_seg.get(seg_idx)
        if not m:
            continue
        owid, nwid = m.get("old_pdf_idx"), m.get("new_pdf_idx")
        if owid is None or nwid is None:
            continue
        equal_words.append(
            {
                "old_word_id": owid,
                "new_word_id": nwid,
                "segment_id": seg.get("segment_id"),
                "old_line": seg.get("old_line"),
                "new_line": seg.get("new_line"),
                "text": seg.get("text", ""),
            }
        )
    return {"unit": "word", "equal_words": equal_words}


def _equal_index(semantic_map):
    eq = (semantic_map or {}).get("equal_words", [])
    by_old = {e["old_word_id"]: e for e in eq}
    by_new = {e["new_word_id"]: e for e in eq}
    return eq, by_old, by_new


def resolve_word_ids(semantic_map, side, word_ids):
    _, by_old, by_new = _equal_index(semantic_map)
    src = by_new if side == "new" else by_old
    seen, chunk = set(), []
    for w in (word_ids or []):
        e = src.get(w)
        if not e:
            continue
        key = (e["old_word_id"], e["new_word_id"])
        if key in seen:
            continue
        seen.add(key)
        chunk.append(e)
    return chunk


def chunk_summary(chunk):
    old_ids = sorted({e["old_word_id"] for e in chunk})
    new_ids = sorted({e["new_word_id"] for e in chunk})
    return {
        "old_word_ids": old_ids,
        "new_word_ids": new_ids,
        "segment_ids": sorted({e["segment_id"] for e in chunk if e["segment_id"]}, key=_seg_num),
        "old_lines": sorted({e["old_line"] for e in chunk if e["old_line"] is not None}),
        "new_lines": sorted({e["new_line"] for e in chunk if e["new_line"] is not None}),
        "text": " ".join(e["text"] for e in chunk),
    }


def _anchor_id(summary):
    basis = ",".join(map(str, summary["new_word_ids"] or summary["old_word_ids"]))
    if not basis:
        return None
    return "a-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:8]


def attach_index_old_side(index_items, semantic_map, old_words, new_words):
    _, _, by_new = _equal_index(semantic_map)
    for item in index_items:
        item["old_page"] = None
        item["old_bbox"] = None
        page, bbox = item.get("page"), item.get("bbox")
        if page is None or not bbox:
            continue
        match = None
        for w in new_words:
            if w.get("page") == page and _bbox_overlap(w.get("bbox", [0, 0, 0, 0]), bbox):
                match = by_new.get(w["idx"])
                if match:
                    break
        if match is not None and 0 <= match["old_word_id"] < len(old_words):
            ow = old_words[match["old_word_id"]]
            item["old_page"] = ow["page"]
            item["old_bbox"] = ow["bbox"]
    return index_items


def _now():
    return datetime.now(timezone.utc).isoformat()


def _reviews_path(run_dir):
    return Path(run_dir) / "reviews.json"


def load_reviews(run_dir):
    p = _reviews_path(run_dir)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_reviews(run_dir, reviews):
    _reviews_path(run_dir).write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")


def add_review(run_dir, semantic_map, data):
    side = data.get("side")
    word_ids = data.get("word_ids", [])
    chunk = resolve_word_ids(semantic_map, side, word_ids)
    s = chunk_summary(chunk)
    if not s["old_word_ids"] and not s["new_word_ids"]:
        return {"error": "no_unchanged_word_selected"}

    review = {
        "review_id": "r-" + uuid.uuid4().hex[:8],
        "anchor_id": _anchor_id(s),
        "side": side,
        "old_word_ids": s["old_word_ids"],
        "new_word_ids": s["new_word_ids"],
        "segment_ids": s["segment_ids"],
        "change_ids": [],
        "old_lines": s["old_lines"],
        "new_lines": s["new_lines"],
        "text": s["text"],
        "selection": {"page": data.get("page"), "rect": data.get("rect"), "word_ids": word_ids},
        "comment": data.get("comment", ""),
        "status": data.get("status", "open"),
        "created_at": _now(),
        "updated_at": _now(),
    }
    reviews = load_reviews(run_dir)
    reviews.append(review)
    save_reviews(run_dir, reviews)
    return review


def update_review(run_dir, review_id, patch):
    reviews = load_reviews(run_dir)
    updated = None
    for r in reviews:
        if r.get("review_id") == review_id:
            for k in ("comment", "status"):
                if k in patch:
                    r[k] = patch[k]
            r["updated_at"] = _now()
            updated = r
            break
    if updated:
        save_reviews(run_dir, reviews)
    return updated


def delete_review(run_dir, review_id):
    reviews = load_reviews(run_dir)
    kept = [r for r in reviews if r.get("review_id") != review_id]
    changed = len(kept) != len(reviews)
    if changed:
        save_reviews(run_dir, kept)
    return changed


def _read_md_lines(path):
    p = Path(path)
    if not p.exists():
        return []
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


def _lines_block(lines, line_nos, context=2):
    if not line_nos:
        return {"lines": [], "text": "", "context": []}
    targets = set(line_nos)
    lo = max(1, min(line_nos) - context)
    hi = min(len(lines), max(line_nos) + context)
    ctx = [{"line": i, "text": lines[i - 1], "target": i in targets} for i in range(lo, hi + 1)]
    text = " ".join(lines[n - 1] for n in line_nos if 1 <= n <= len(lines))
    return {"lines": sorted(targets), "text": text, "context": ctx}


def build_ai_bundle(run_dir, review_id, context_lines=2):
    run_dir = Path(run_dir)
    review = next((r for r in load_reviews(run_dir) if r.get("review_id") == review_id), None)
    if review is None:
        return None
    viewer = json.loads((run_dir / "viewer_data.json").read_text(encoding="utf-8"))
    old_md = _read_md_lines(run_dir / "old.md")
    new_md = _read_md_lines(run_dir / "new.md")
    return {
        "job_id": viewer.get("job_id"),
        "review": {
            "review_id": review["review_id"],
            "comment": review.get("comment", ""),
            "status": review.get("status"),
            "side": review.get("side"),
        },
        "anchor": {
            "anchor_id": review.get("anchor_id"),
            "selected_text": review.get("text", ""),
            "old": _lines_block(old_md, review.get("old_lines", []), context_lines),
            "new": _lines_block(new_md, review.get("new_lines", []), context_lines),
        },
        "documents": {
            "old_filename": viewer.get("old_filename"),
            "new_filename": viewer.get("new_filename"),
        },
    }
