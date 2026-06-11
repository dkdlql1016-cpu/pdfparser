import re
from difflib import SequenceMatcher

from text_utils import keep_token, norm_token


def build_side_stream(segments, side):
    out = []
    for seg_idx, seg in enumerate(segments):
        if seg.get("suppressed"):
            continue
        typ = seg.get("type")
        text = seg.get("text", "")
        if not keep_token(text):
            continue
        if typ == "equal" or (side == "old" and typ == "delete") or (side == "new" and typ == "add"):
            out.append({
                "stream_idx": len(out),
                "seg_idx": seg_idx,
                "type": typ,
                "text": text,
                "norm": norm_token(text),
            })
    return out


def align_stream_to_pdf_words(stream, pdf_words):
    md_norms = [x["norm"] for x in stream]
    pdf_norms = [w["norm"] for w in pdf_words]
    matcher = SequenceMatcher(None, md_norms, pdf_norms, autojunk=False)
    mapping = {}
    used_pdf = set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                mapping[i1 + k] = j1 + k
                used_pdf.add(j1 + k)
        else:
            center = j1
            for i in range(i1, i2):
                token = md_norms[i]
                if not token:
                    continue
                found = None
                for lo, hi in [(max(0, center - 80), min(len(pdf_norms), center + 140)), (0, len(pdf_norms))]:
                    for j in range(lo, hi):
                        if j not in used_pdf and pdf_norms[j] == token:
                            found = j
                            break
                    if found is not None:
                        break
                if found is not None:
                    mapping[i] = found
                    used_pdf.add(found)
                    center = found
    return mapping


def map_result_segments_to_pdf_indices(segments, old_words, new_words):
    old_stream = build_side_stream(segments, "old")
    new_stream = build_side_stream(segments, "new")
    old_alignment = align_stream_to_pdf_words(old_stream, old_words)
    new_alignment = align_stream_to_pdf_words(new_stream, new_words)
    old_seg_to_pdf = {x["seg_idx"]: old_alignment.get(x["stream_idx"]) for x in old_stream}
    new_seg_to_pdf = {x["seg_idx"]: new_alignment.get(x["stream_idx"]) for x in new_stream}
    mapped = []
    for seg_idx, seg in enumerate(segments):
        if seg.get("suppressed"):
            continue
        text = seg.get("text", "")
        if not keep_token(text):
            continue
        mapped.append({
            "seg_idx": seg_idx,
            "type": seg.get("type"),
            "text": text,
            "old_pdf_idx": old_seg_to_pdf.get(seg_idx),
            "new_pdf_idx": new_seg_to_pdf.get(seg_idx),
        })
    return mapped


def move_norm_token(value):
    return norm_token(value)


def is_weak_move_token(n):
    return n in {"", "-", "w", "and", "of", "the", "in", "to", "for", "as", "a", "an"}


def context_signature(segments, idx, radius=50):
    bag = []
    lo = max(0, idx - radius)
    hi = min(len(segments), idx + radius + 1)
    for j in range(lo, hi):
        if j == idx:
            continue
        s = segments[j]
        if s.get("type") != "equal":
            continue
        n = move_norm_token(s.get("text", ""))
        if n and not is_weak_move_token(n):
            bag.append(n)
    return bag


def weighted_jaccard(a, b):
    if not a or not b:
        return 0.0
    ca, cb = {}, {}
    for x in a:
        ca[x] = ca.get(x, 0) + 1
    for x in b:
        cb[x] = cb.get(x, 0) + 1
    keys = set(ca) | set(cb)
    inter = sum(min(ca.get(k, 0), cb.get(k, 0)) for k in keys)
    union = sum(max(ca.get(k, 0), cb.get(k, 0)) for k in keys)
    return inter / union if union else 0.0


def near_equal_anchor_score(segments, di, ai, radius=14):
    def anchors(idx, direction):
        out = []
        if direction == "left":
            rng = range(idx - 1, max(-1, idx - radius - 1), -1)
        else:
            rng = range(idx + 1, min(len(segments), idx + radius + 1))
        for j in rng:
            s = segments[j]
            if s.get("type") == "equal":
                n = move_norm_token(s.get("text", ""))
                if n and not is_weak_move_token(n):
                    out.append(n)
            if len(out) >= 8:
                break
        return set(out)

    dl, dr = anchors(di, "left"), anchors(di, "right")
    al, ar = anchors(ai, "left"), anchors(ai, "right")
    return max(len(x & y) / max(1, len(x | y)) for x, y in ((dl, al), (dr, ar), (dl, ar), (dr, al)))


def suppress_layout_moves(segments, window=220):
    segs = [dict(s) for s in segments]
    deletes = [i for i, s in enumerate(segs) if s.get("type") == "delete" and not s.get("suppressed")]
    adds = [i for i, s in enumerate(segs) if s.get("type") == "add" and not s.get("suppressed")]
    adds_by_norm = {}
    for ai in adds:
        n = move_norm_token(segs[ai].get("text", ""))
        if n:
            adds_by_norm.setdefault(n, []).append(ai)
    _ctx_cache = {}

    def _ctx(idx, radius):
        key = (idx, radius)
        if key not in _ctx_cache:
            _ctx_cache[key] = context_signature(segs, idx, radius)
        return _ctx_cache[key]

    candidate_pairs = []
    for di in deletes:
        dn = move_norm_token(segs[di].get("text", ""))
        if not dn or is_weak_move_token(dn):
            continue
        candidates = [ai for ai in adds_by_norm.get(dn, []) if abs(ai - di) <= window]
        if not candidates:
            continue
        dctx = _ctx(di, 50)
        scored = []
        for ai in candidates:
            ctx_score = weighted_jaccard(dctx, _ctx(ai, 50))
            anchor_score = near_equal_anchor_score(segs, di, ai, 14)
            section_score = weighted_jaccard(_ctx(di, 180), _ctx(ai, 180))
            dist = abs(ai - di)
            dist_score = 1.0 if dist <= 40 else 0.75 if dist <= 100 else 0.45
            token_strength = min(1.0, max(len(dn) / 8.0, 0.35))
            score = 0.38 + ctx_score * 0.30 + anchor_score * 0.20 + section_score * 0.07 + dist_score * 0.05 + token_strength * 0.03
            scored.append(
                {
                    "score": score,
                    "add_idx": ai,
                    "context_score": ctx_score,
                    "anchor_score": anchor_score,
                    "section_score": section_score,
                    "distance": dist,
                }
            )
        scored.sort(key=lambda x: x["score"], reverse=True)
        best = scored[0]
        second = scored[1]["score"] if len(scored) > 1 else -1.0
        min_score = 0.72 if len(dn) <= 4 else 0.66
        if best["score"] >= min_score and (best["score"] - second) >= 0.08:
            candidate_pairs.append(
                {
                    "score": best["score"],
                    "delete_idx": di,
                    "add_idx": best["add_idx"],
                    "context_score": best["context_score"],
                    "anchor_score": best["anchor_score"],
                    "section_score": best["section_score"],
                    "distance": best["distance"],
                }
            )
    candidate_pairs.sort(key=lambda x: x["score"], reverse=True)
    used_d, used_a = set(), set()
    strong_indices = set()
    suppressed_moves = []
    move_no = 0
    for pair in candidate_pairs:
        di, ai = pair["delete_idx"], pair["add_idx"]
        if di in used_d or ai in used_a:
            continue
        used_d.add(di)
        used_a.add(ai)
        move_no += 1
        pair_id = f"lm{move_no}"
        for idx in (di, ai):
            segs[idx]["suppressed"] = True
            segs[idx]["suppress_reason"] = "layout_move"
            segs[idx]["move_pair_id"] = pair_id
            segs[idx]["move_score"] = round(pair["score"], 4)
        strong_indices.update([di, ai])
        suppressed_moves.append(
            {
                "move_pair_id": pair_id,
                "text": segs[di].get("text"),
                "old_line": segs[di].get("old_line"),
                "new_line": segs[ai].get("new_line"),
                "score": round(pair["score"], 4),
                "context_score": round(pair["context_score"], 4),
                "anchor_score": round(pair["anchor_score"], 4),
                "section_score": round(pair["section_score"], 4),
                "segment_distance": pair["distance"],
            }
        )
    for idx in list(strong_indices):
        pair_id = segs[idx].get("move_pair_id")
        counterpart = next((k for k, s in enumerate(segs) if k != idx and s.get("move_pair_id") == pair_id), None)
        if counterpart is None:
            continue
        for j in (idx - 1, idx + 1):
            if not (0 <= j < len(segs)):
                continue
            if segs[j].get("type") not in ("delete", "add") or segs[j].get("suppressed"):
                continue
            weak_norm = move_norm_token(segs[j].get("text", ""))
            if not is_weak_move_token(weak_norm):
                continue
            for jj in (counterpart - 1, counterpart + 1):
                if 0 <= jj < len(segs) and segs[jj].get("type") in ("delete", "add") and move_norm_token(segs[jj].get("text", "")) == weak_norm:
                    for x in (j, jj):
                        segs[x]["suppressed"] = True
                        segs[x]["suppress_reason"] = "layout_move_adjacent_weak_token"
                        segs[x]["move_pair_id"] = pair_id
                    break
    return segs, suppressed_moves


def attach_nearest_equal_anchors(changes, mapped, old_words, new_words):
    def nearest_equal(seg_start, seg_end, side):
        idx_key = "old_pdf_idx" if side == "old" else "new_pdf_idx"
        best = None
        best_score = None
        for m in mapped:
            if m.get("type") != "equal":
                continue
            pdf_idx = m.get(idx_key)
            if pdf_idx is None:
                continue
            sidx = m.get("seg_idx", 0)
            dist = 0 if seg_start <= sidx <= seg_end else min(abs(sidx - seg_start), abs(sidx - seg_end))
            score = (dist, 0 if sidx <= seg_start else 1)
            if best_score is None or score < best_score:
                best_score = score
                best = m
        return best

    for c in changes:
        seg_start, seg_end = c.get("seg_start"), c.get("seg_end")
        if seg_start is None or seg_end is None:
            continue
        for side, words in (("old", old_words), ("new", new_words)):
            anchor = nearest_equal(seg_start, seg_end, side)
            idx_key = f"{side}_pdf_idx"
            if anchor and anchor.get(idx_key) is not None:
                w = words[anchor[idx_key]]
                c[f"{side}_anchor"] = {"page": w["page"], "bbox": w["bbox"], "text": w.get("text", ""), "seg_idx": anchor.get("seg_idx")}
                if c.get(f"{side}_page") is None:
                    c[f"{side}_page"] = w["page"]
    return changes


def make_highlights_and_changes(segments, old_words, new_words):
    mapped = map_result_segments_to_pdf_indices(segments, old_words, new_words)
    old_highlights, new_highlights, changes = [], [], []
    current, change_id = None, 0

    def flush():
        nonlocal current
        if current and (current["old_highlight_ids"] or current["new_highlight_ids"] or current["old_tokens"] or current["new_tokens"]):
            current["old_text"] = " ".join(current["old_tokens"])
            current["new_text"] = " ".join(current["new_tokens"])
            changes.append(current)
        current = None

    for item in mapped:
        typ = item["type"]
        if typ == "equal":
            flush()
            continue
        if current is None:
            change_id += 1
            current = {
                "id": change_id,
                "old_tokens": [],
                "new_tokens": [],
                "old_highlight_ids": [],
                "new_highlight_ids": [],
                "old_page": None,
                "new_page": None,
                "seg_start": item["seg_idx"],
                "seg_end": item["seg_idx"],
            }
        current["seg_end"] = item["seg_idx"]
        if typ == "delete":
            current["old_tokens"].append(item.get("text", ""))
            if item["old_pdf_idx"] is not None:
                w = old_words[item["old_pdf_idx"]]
                hid = f"old-{len(old_highlights) + 1}"
                old_highlights.append({"id": hid, "change_id": change_id, "type": "delete", "text": w["text"], "page": w["page"], "bbox": w["bbox"]})
                current["old_highlight_ids"].append(hid)
                current["old_page"] = current["old_page"] or w["page"]
        elif typ == "add":
            current["new_tokens"].append(item.get("text", ""))
            if item["new_pdf_idx"] is not None:
                w = new_words[item["new_pdf_idx"]]
                hid = f"new-{len(new_highlights) + 1}"
                new_highlights.append({"id": hid, "change_id": change_id, "type": "add", "text": w["text"], "page": w["page"], "bbox": w["bbox"]})
                current["new_highlight_ids"].append(hid)
                current["new_page"] = current["new_page"] or w["page"]
    flush()
    changes = attach_nearest_equal_anchors(changes, mapped, old_words, new_words)
    return old_highlights, new_highlights, changes


def merge_highlight_rects_server(highlights, y_tolerance=5.5, max_gap=42, pad_x=0.8, pad_y=0.9):
    groups = {}
    for h in highlights:
        x0, y0, x1, y1 = h["bbox"]
        hh = dict(h)
        hh["_cy"] = (y0 + y1) / 2
        groups.setdefault((h["page"], h["type"], h["change_id"]), []).append(hh)
    merged = []
    for items in groups.values():
        items.sort(key=lambda h: (h["_cy"], h["bbox"][0]))
        lines = []
        for h in items:
            placed = False
            for line in lines:
                if abs(line["cy"] - h["_cy"]) <= y_tolerance:
                    line["items"].append(h)
                    line["cy"] = sum(x["_cy"] for x in line["items"]) / len(line["items"])
                    placed = True
                    break
            if not placed:
                lines.append({"cy": h["_cy"], "items": [h]})
        for line in lines:
            line["items"].sort(key=lambda h: h["bbox"][0])
            chunk = None

            def flush():
                nonlocal chunk
                if not chunk:
                    return
                change_id = chunk["change_ids"][0]
                merged.append(
                    {
                        "id": "__".join(chunk["ids"]),
                        "change_id": change_id,
                        "change_ids": [change_id],
                        "type": chunk["type"],
                        "page": chunk["page"],
                        "bbox": [
                            max(0, chunk["x0"] - pad_x),
                            chunk["y0"] + pad_y,
                            chunk["x1"] + pad_x,
                            max(chunk["y0"] + pad_y + 1, chunk["y1"] - pad_y),
                        ],
                        "text": " ".join(chunk["texts"]),
                    }
                )
                chunk = None

            for h in line["items"]:
                x0, y0, x1, y1 = h["bbox"]
                if chunk is None:
                    chunk = {
                        "ids": [h["id"]],
                        "texts": [h.get("text", "")],
                        "change_ids": [h["change_id"]],
                        "type": h["type"],
                        "page": h["page"],
                        "x0": x0,
                        "y0": y0,
                        "x1": x1,
                        "y1": y1,
                    }
                    continue
                gap = x0 - chunk["x1"]
                if gap <= max_gap:
                    chunk["ids"].append(h["id"])
                    chunk["texts"].append(h.get("text", ""))
                    chunk["change_ids"].append(h["change_id"])
                    chunk["x0"] = min(chunk["x0"], x0)
                    chunk["y0"] = min(chunk["y0"], y0)
                    chunk["x1"] = max(chunk["x1"], x1)
                    chunk["y1"] = max(chunk["y1"], y1)
                else:
                    flush()
                    chunk = {
                        "ids": [h["id"]],
                        "texts": [h.get("text", "")],
                        "change_ids": [h["change_id"]],
                        "type": h["type"],
                        "page": h["page"],
                        "x0": x0,
                        "y0": y0,
                        "x1": x1,
                        "y1": y1,
                    }
            flush()
    return merged


def alignment_report(segments, old_words, new_words):
    old_stream, new_stream = build_side_stream(segments, "old"), build_side_stream(segments, "new")
    old_map = align_stream_to_pdf_words(old_stream, old_words)
    new_map = align_stream_to_pdf_words(new_stream, new_words)
    return {
        "old_md_tokens": len(old_stream),
        "new_md_tokens": len(new_stream),
        "old_pdf_words": len(old_words),
        "new_pdf_words": len(new_words),
        "old_aligned": len(old_map),
        "new_aligned": len(new_map),
        "old_alignment_rate": round(len(old_map) / max(1, len(old_stream)), 4),
        "new_alignment_rate": round(len(new_map) / max(1, len(new_stream)), 4),
    }
