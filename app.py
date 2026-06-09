import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
import semantic
from datetime import datetime, timezone
from pathlib import Path
from difflib import SequenceMatcher

import fitz
import flask.cli
from flask import Flask, Response, jsonify, request, send_file

BASE_DIR = Path(__file__).resolve().parent
RUNS_DIR = BASE_DIR / "runs"
DOCUMENTS_DIR = BASE_DIR / "documents"
RUNS_DIR.mkdir(exist_ok=True)
DOCUMENTS_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
JOBS = {}


# =========================================================
# Token normalization
# =========================================================

def norm_token(value):
    s = str(value or "")
    s = (
        s.replace("\u2019", "'")
         .replace("\u2018", "'")
         .replace("\u201c", '"')
         .replace("\u201d", '"')
         .replace("\u2013", "-")
         .replace("\u2014", "-")
         .replace("\u2212", "-")
         .replace("\\-", "-")
    )
    s = s.strip().lower()
    s = re.sub(r"^[*_#`\[\]\(\)\{\}<>|]+", "", s)
    s = re.sub(r"[*_#`\[\]\(\)\{\}<>|]+$", "", s).strip()
    s = re.sub(r"^[,.;:]+|[,.;:]+$", "", s)
    if re.fullmatch(r"page_?\d+", s):
        return ""
    if re.search(r"\d", s):
        neg = s.startswith("(") and s.endswith(")")
        n = re.sub(r"[^0-9a-z%.-]", "", s).replace(",", "")
        if neg and not n.startswith("-"):
            n = "-" + n
        return n
    return re.sub(r"[^a-z0-9%.-]", "", s)


def keep_token(value):
    n = norm_token(value)
    return bool(n) and not re.fullmatch(r"-{2,}", n)


# =========================================================
# PDF extraction / rendering
# =========================================================

def extract_pdf_words(pdf_path: Path):
    doc = fitz.open(str(pdf_path))
    words = []
    page_sizes = []
    for page_no, page in enumerate(doc, start=1):
        page_sizes.append({"width": float(page.rect.width), "height": float(page.rect.height)})
        raw_words = sorted(page.get_text("words"), key=lambda w: (w[5], w[6], w[7], w[1], w[0]))
        for w in raw_words:
            x0, y0, x1, y1, text, block, line, word_no = w[:8]
            if not keep_token(text):
                continue
            words.append({
                "idx": len(words),
                "text": text,
                "norm": norm_token(text),
                "page": page_no,
                "bbox": [float(x0), float(y0), float(x1), float(y1)],
                "block": int(block),
                "line": int(line),
                "word_no": int(word_no),
            })
    return words, page_sizes


def render_pdf_page(pdf_path: Path, page_no: int, zoom: float):
    doc = fitz.open(str(pdf_path))
    pix = doc[page_no - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return pix.tobytes("png")


# =========================================================
# OpenDataLoader / diff extraction
# =========================================================

def run_opendataloader_to_markdown(pdf_path: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        import opendataloader_pdf
    except Exception as e:
        raise RuntimeError("opendataloader_pdf import failed. Cause: " + str(e))
    kwargs = dict(
        input_path=[str(pdf_path)],
        output_dir=str(output_dir),
        format="markdown",
        table_method="default",
        reading_order="xycut",
        markdown_page_separator="--- %page-number% ---",
    )
    try:
        opendataloader_pdf.convert(**kwargs, include_header_footer=False)
    except TypeError:
        opendataloader_pdf.convert(**kwargs)
    candidates = []
    for pattern in ("*.md", "*.markdown", "**/*.md", "**/*.markdown"):
        candidates.extend(output_dir.glob(pattern))
    candidates = sorted(set(candidates), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError(f"OpenDataLoader ran but markdown file was not found: {output_dir}")
    return candidates[0]


def run_diff_extract(old_md: Path, new_md: Path, result_json: Path):
    cmd = [
        sys.executable,
        str(BASE_DIR / "diff_extract.py"),
        str(old_md),
        str(new_md),
        "-o", str(result_json),
        "--unit", "word",
        "--engine", "auto",
        "--diff-algorithm", "histogram",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not result_json.exists():
        raise RuntimeError(proc.stderr or proc.stdout or "diff_extract.py failed")
    return json.loads(result_json.read_text(encoding="utf-8"))


# =========================================================
# Result segment to PDF word alignment
# =========================================================

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


# =========================================================
# Layout/read-order move suppression
# =========================================================

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
    candidate_pairs = []
    for di in deletes:
        dn = move_norm_token(segs[di].get("text", ""))
        if not dn or is_weak_move_token(dn):
            continue
        candidates = [ai for ai in adds_by_norm.get(dn, []) if abs(ai - di) <= window]
        if not candidates:
            continue
        dctx = context_signature(segs, di, 50)
        scored = []
        for ai in candidates:
            ctx_score = weighted_jaccard(dctx, context_signature(segs, ai, 50))
            anchor_score = near_equal_anchor_score(segs, di, ai, 14)
            section_score = weighted_jaccard(context_signature(segs, di, 180), context_signature(segs, ai, 180))
            dist = abs(ai - di)
            dist_score = 1.0 if dist <= 40 else 0.75 if dist <= 100 else 0.45
            token_strength = min(1.0, max(len(dn) / 8.0, 0.35))
            score = 0.38 + ctx_score * 0.30 + anchor_score * 0.20 + section_score * 0.07 + dist_score * 0.05 + token_strength * 0.03
            scored.append({"score": score, "add_idx": ai, "context_score": ctx_score,
                           "anchor_score": anchor_score, "section_score": section_score, "distance": dist})
        scored.sort(key=lambda x: x["score"], reverse=True)
        best = scored[0]
        second = scored[1]["score"] if len(scored) > 1 else -1.0
        min_score = 0.72 if len(dn) <= 4 else 0.66
        if best["score"] >= min_score and (best["score"] - second) >= 0.08:
            candidate_pairs.append({"score": best["score"], "delete_idx": di, "add_idx": best["add_idx"],
                                    "context_score": best["context_score"], "anchor_score": best["anchor_score"],
                                    "section_score": best["section_score"], "distance": best["distance"]})
    candidate_pairs.sort(key=lambda x: x["score"], reverse=True)
    used_d, used_a = set(), set()
    strong_indices = set()
    suppressed_moves = []
    move_no = 0
    for pair in candidate_pairs:
        di, ai = pair["delete_idx"], pair["add_idx"]
        if di in used_d or ai in used_a:
            continue
        used_d.add(di); used_a.add(ai)
        move_no += 1
        pair_id = f"lm{move_no}"
        for idx in (di, ai):
            segs[idx]["suppressed"] = True
            segs[idx]["suppress_reason"] = "layout_move"
            segs[idx]["move_pair_id"] = pair_id
            segs[idx]["move_score"] = round(pair["score"], 4)
        strong_indices.update([di, ai])
        suppressed_moves.append({"move_pair_id": pair_id, "text": segs[di].get("text"),
                                  "old_line": segs[di].get("old_line"), "new_line": segs[ai].get("new_line"),
                                  "score": round(pair["score"], 4), "context_score": round(pair["context_score"], 4),
                                  "anchor_score": round(pair["anchor_score"], 4),
                                  "section_score": round(pair["section_score"], 4),
                                  "segment_distance": pair["distance"]})
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


# =========================================================
# Index extraction - MD first, PDF anchor second
# =========================================================

NOTE_HEADING_TOKEN_RE = re.compile(r"^(\d{1,2})\.?$")
NOTE_HEADING_LINE_RE = re.compile(r"^\s*(\d{1,2})\.\s+(.+?)\s*$")
PAGE_MARKER_RE = re.compile(
    r"^\s*(?:\\?---\s*(\d+)\s*---|<page_(\d+)>|</page_(\d+)>|-+\s*(\d+)\s*-+)\s*$",
    re.I,
)
FS_PATTERNS = [
    "Statements of Financial Position",
    "Statements of Income",
    "Statements of Changes in Equity",
    "Statements of Cash Flows",
]


def line_bbox(words):
    return [min(w["bbox"][0] for w in words), min(w["bbox"][1] for w in words),
            max(w["bbox"][2] for w in words), max(w["bbox"][3] for w in words)]


def line_text(words):
    return " ".join(str(w.get("text", "")) for w in words).strip()


def is_amount_like_token(text):
    n = norm_token(text)
    if not n:
        return False
    if n in {"-", "w"}:
        return True
    return bool(re.search(r"\d", n))


def numeric_density_from_text(text):
    toks = [t for t in str(text or "").split() if t]
    if not toks:
        return 0.0
    return sum(1 for t in toks if is_amount_like_token(t)) / len(toks)


def clean_structural_text(s):
    s = str(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ")
    s = re.sub(r"!\[\]\[[^\]]+\]", " ", s)
    s = re.sub(r"^\s*#{1,8}\s*", "", s)
    s = re.sub(r"^\s*[-•]\s*", "", s)
    s = s.replace("**", " ").replace("__", " ").replace("_", " ").replace("`", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_title_for_index(title):
    title = clean_structural_text(title)
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"[,.;:]+$", "", title).strip()
    return title


SECTION_MARKER_RE = re.compile(r"^\s*<!--\s*SECTION_(?:START|END):[^>]+-->\s*$", re.I)
FS_SECTION_PATTERNS = [
    (re.compile(r"financial\s+position", re.I), "fs_position"),
    (re.compile(r"(?:comprehensive\s+)?income|profit\s+or\s+loss", re.I), "fs_income"),
    (re.compile(r"changes\s+in\s+equity", re.I), "fs_equity"),
    (re.compile(r"cash\s+flows?", re.I), "fs_cashflow"),
]


def read_md_lines(md_path: Path):
    return Path(md_path).read_text(encoding="utf-8", errors="replace").splitlines()


def strip_section_markers(lines):
    return [line for line in lines if not SECTION_MARKER_RE.match(str(line or "").strip())]


def slug_for_section_id(value):
    slug = re.sub(r"[^a-z0-9]+", "_", clean_structural_text(value).lower()).strip("_")
    return slug or "unknown"


def fs_section_id(label):
    label = clean_structural_text(label)
    for pattern, section_id in FS_SECTION_PATTERNS:
        if pattern.search(label):
            return section_id
    return f"fs_{slug_for_section_id(label)}"


def section_id_for_index_item(item):
    typ = item.get("type")
    if typ == "note" and item.get("note_no"):
        return f"note_{int(item['note_no'])}"
    if typ == "fs":
        return fs_section_id(item.get("label") or item.get("title") or "")
    return None


def md_line_pages(lines):
    pages = []
    current_page = 1
    for line in lines:
        pm = PAGE_MARKER_RE.match(str(line or "").strip())
        if pm:
            current_page = int(next(g for g in pm.groups() if g))
        pages.append(current_page)
    return pages


def title_match_score(text, title):
    text = normalize_title_for_index(text).lower()
    title = normalize_title_for_index(title).lower()
    if not text or not title:
        return 0.0
    if text == title:
        return 1.0
    if title in text or text in title:
        return 0.92
    text_tokens = [norm_token(t) for t in text.split() if norm_token(t)]
    title_tokens = [norm_token(t) for t in title.split() if norm_token(t)]
    if not text_tokens or not title_tokens:
        return 0.0
    shared = len(set(text_tokens) & set(title_tokens))
    overlap = shared / max(1, min(len(text_tokens), len(title_tokens)))
    ratio = SequenceMatcher(None, " ".join(text_tokens), " ".join(title_tokens)).ratio()
    return max(overlap, ratio)


def note_heading_candidate(lines, idx, note_no, title):
    cleaned = normalize_title_for_index(lines[idx])
    m = re.match(r"^(?:note\s+)?(\d{1,2})\.?\s+(.+?)\s*$", cleaned, re.I)
    if m and int(m.group(1)) == int(note_no):
        return idx, title_match_score(m.group(2), title)

    m = re.match(r"^(?:note\s+)?(\d{1,2})\.?\s*$", cleaned, re.I)
    if m and int(m.group(1)) == int(note_no):
        for j in range(idx + 1, min(len(lines), idx + 4)):
            candidate = normalize_title_for_index(lines[j])
            if candidate:
                return idx, title_match_score(candidate, title)
    return None


def find_section_heading_line(lines, line_pages, item):
    section_type = item.get("type")
    title = item.get("title") or item.get("label") or ""
    preferred_page = item.get("page_hint") or item.get("page")
    best = None

    for idx, raw in enumerate(lines):
        if PAGE_MARKER_RE.match(str(raw or "").strip()):
            continue
        if section_type == "note":
            hit = note_heading_candidate(lines, idx, item.get("note_no"), title)
            if not hit:
                continue
            line_idx, match_score = hit
        elif section_type == "fs":
            match_score = title_match_score(raw, title)
            if match_score < 0.72:
                continue
            line_idx = idx
        else:
            continue

        page = line_pages[line_idx] if line_idx < len(line_pages) else None
        page_penalty = abs(page - preferred_page) * 0.08 if page and preferred_page else 0
        score = match_score - page_penalty
        if preferred_page and page and abs(page - preferred_page) > 2:
            score -= 0.35
        if best is None or score > best[0]:
            best = (score, line_idx)

    if best and best[0] >= 0.55:
        return best[1]
    return None


def inject_section_markers(md_path: Path, index_items):
    lines = strip_section_markers(read_md_lines(md_path))
    line_pages = md_line_pages(lines)
    found = []
    seen_ids = set()

    for item in index_items:
        section_id = section_id_for_index_item(item)
        if not section_id or section_id in seen_ids:
            continue
        start_idx = find_section_heading_line(lines, line_pages, item)
        if start_idx is None:
            continue
        seen_ids.add(section_id)
        found.append({
            "section_id": section_id,
            "item": item,
            "start_idx": start_idx,
        })

    found.sort(key=lambda x: x["start_idx"])
    if not found:
        return {"sections": {}}

    for i, section in enumerate(found):
        next_start = found[i + 1]["start_idx"] if i + 1 < len(found) else len(lines)
        end_idx = max(section["start_idx"], next_start - 1)
        while end_idx > section["start_idx"]:
            trailing = str(lines[end_idx] or "").strip()
            if trailing and not PAGE_MARKER_RE.match(trailing):
                break
            end_idx -= 1
        section["end_idx"] = end_idx

    starts = {s["start_idx"]: s for s in found}
    ends = {s["end_idx"]: s for s in found}
    out = []
    sections = {}

    for idx, line in enumerate(lines):
        if idx in starts:
            section = starts[idx]
            out.append(f"<!-- SECTION_START:{section['section_id']} -->")
            item = section["item"]
            sections[section["section_id"]] = {
                "title": item.get("title") or item.get("label") or "",
                "type": item.get("type"),
                "note_no": item.get("note_no"),
                "start_line": len(out) + 1,
                "end_line": None,
                "page_start": line_pages[idx] if idx < len(line_pages) else item.get("page"),
                "page_end": None,
            }

        out.append(line)

        if idx in ends:
            section = ends[idx]
            meta = sections[section["section_id"]]
            meta["end_line"] = len(out)
            meta["page_end"] = line_pages[idx] if idx < len(line_pages) else meta["page_start"]
            out.append(f"<!-- SECTION_END:{section['section_id']} -->")

    md_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"sections": sections}


def group_pdf_visual_lines(words, y_tolerance=3.2):
    by_page = {}
    for w in words:
        by_page.setdefault(w.get("page"), []).append(w)
    visual_lines = []
    for page, page_words in sorted(by_page.items(), key=lambda x: x[0] or 0):
        prepared = []
        for w in page_words:
            x0, y0, x1, y1 = w["bbox"]
            ww = dict(w)
            ww["_cy"] = (y0 + y1) / 2
            ww["_h"] = max(1.0, y1 - y0)
            prepared.append(ww)
        prepared.sort(key=lambda w: (w["_cy"], w["bbox"][0]))
        lines = []
        for w in prepared:
            placed = False
            tol = max(y_tolerance, w.get("_h", 1.0) * 0.38)
            for line in lines:
                if abs(line["cy"] - w["_cy"]) <= max(tol, line.get("tol", y_tolerance)):
                    line["words"].append(w)
                    line["cy"] = sum(x["_cy"] for x in line["words"]) / len(line["words"])
                    line["tol"] = max(line.get("tol", y_tolerance), tol)
                    placed = True
                    break
            if not placed:
                lines.append({"page": page, "cy": w["_cy"], "tol": tol, "words": [w]})
        for idx, line in enumerate(lines):
            items = sorted(line["words"], key=lambda x: (x["bbox"][0], x.get("word_no", 0)))
            txt = line_text(items)
            if not txt:
                continue
            bbox = line_bbox(items)
            visual_lines.append({"page": page, "block": -1, "line": idx, "cy": line["cy"],
                                  "words": items, "text": txt, "bbox": bbox})
    visual_lines.sort(key=lambda x: (x["page"], x["cy"], x["bbox"][0]))
    return visual_lines


def first_line_on_page(visual_lines, page_no):
    for ln in visual_lines:
        if ln.get("page") == page_no:
            return ln
    return None


def detect_notes_start_page(new_md_path: Path, visual_lines):
    for ln in visual_lines:
        txt = re.sub(r"\s+", " ", ln.get("text", "")).strip().lower()
        if "notes to the financial statements" in txt:
            return ln.get("page", 1)
    if new_md_path and Path(new_md_path).exists():
        current_page = 1
        for raw in Path(new_md_path).read_text(encoding="utf-8", errors="replace").splitlines():
            raw_s = str(raw or "").strip()
            pm = PAGE_MARKER_RE.match(raw_s)
            if pm:
                current_page = int(next(g for g in pm.groups() if g))
                continue
            if "notes to the financial statements" in clean_structural_text(raw_s).lower():
                return current_page
    return 3


def search_sequence(words, needle_tokens):
    if not words or not needle_tokens:
        return None
    norms = [w.get("norm") or norm_token(w.get("text", "")) for w in words]
    n = len(needle_tokens)
    for i in range(0, len(norms) - n + 1):
        if norms[i:i+n] == needle_tokens:
            matched = words[i:i+n]
            return {"page": matched[0]["page"], "bbox": line_bbox(matched), "anchor_text": line_text(matched)}
    return None


def find_note_anchor_in_words(note_no, title, new_words, preferred_page=None, notes_start_page=None):
    title_tokens = [norm_token(x) for x in str(title or "").split()]
    title_tokens = [x for x in title_tokens if x]
    if not title_tokens:
        fallback_page = preferred_page or notes_start_page or 1
        return {"page": fallback_page, "bbox": [0, 0, 1, 1], "anchor_text": ""}
    note_variants = [norm_token(f"{note_no}."), norm_token(str(note_no))]
    note_variants = [x for x in note_variants if x]
    def page_words(pages):
        page_set = {p for p in pages if p and p >= 1}
        return [w for w in new_words if w.get("page") in page_set]
    search_sets = []
    if preferred_page:
        near = page_words([preferred_page - 1, preferred_page, preferred_page + 1])
        if near:
            search_sets.append(("preferred", near))
    if notes_start_page:
        notes_words = [w for w in new_words if w.get("page", 1) >= notes_start_page]
        if notes_words:
            search_sets.append(("notes", notes_words))
    if not search_sets:
        search_sets.append(("all", new_words))
    for _, words in search_sets:
        for title_len in range(min(10, len(title_tokens)), 0, -1):
            for note_tok in note_variants:
                hit = search_sequence(words, [note_tok] + title_tokens[:title_len])
                if hit:
                    return hit
    if preferred_page:
        near = page_words([preferred_page - 1, preferred_page, preferred_page + 1])
        for title_len in range(min(10, len(title_tokens)), 0, -1):
            hit = search_sequence(near, title_tokens[:title_len])
            if hit:
                return hit
    fallback_page = preferred_page or notes_start_page or 1
    same_page = [w for w in new_words if w.get("page") == fallback_page]
    if same_page:
        w = same_page[0]
        return {"page": fallback_page, "bbox": w.get("bbox", [0, 0, 1, 1]), "anchor_text": w.get("text", "")}
    return {"page": fallback_page, "bbox": [0, 0, 1, 1], "anchor_text": ""}


def refine_title_with_pdf_line(note_no, md_title, visual_lines, preferred_page=None):
    md_title = normalize_title_for_index(md_title)
    if not md_title:
        return md_title
    candidates = visual_lines
    if preferred_page:
        candidates = [ln for ln in visual_lines if abs((ln.get("page") or 0) - preferred_page) <= 1] or visual_lines
    md_norm = [norm_token(t) for t in md_title.split() if norm_token(t)]
    best_title, best_score = None, -1
    for ln in candidates:
        words = ln.get("words", [])
        if len(words) < 2:
            continue
        note_pos = None
        for pos, w in enumerate(words[:4]):
            m = NOTE_HEADING_TOKEN_RE.match(str(w.get("text", "")).strip())
            if m and int(m.group(1)) == note_no:
                note_pos = pos
                break
        if note_pos is None:
            continue
        line_title = normalize_title_for_index(line_text(words[note_pos + 1:]))
        line_norm = [norm_token(t) for t in line_title.split() if norm_token(t)]
        if not line_norm:
            continue
        prefix_len = min(len(line_norm), len(md_norm))
        if md_norm[:prefix_len] == line_norm[:prefix_len] and prefix_len > best_score:
            best_score = prefix_len
            best_title = line_title
    return best_title or md_title


def extract_note_headings_from_md(new_md_path: Path, notes_start_page):
    if not new_md_path or not Path(new_md_path).exists():
        return []
    lines = Path(new_md_path).read_text(encoding="utf-8", errors="replace").splitlines()
    candidates = []
    current_page = 1
    pending_note_no = None
    pending_page = None
    for raw in lines:
        raw_s = str(raw or "").strip()
        pm = PAGE_MARKER_RE.match(raw_s)
        if pm:
            current_page = int(next(g for g in pm.groups() if g))
            continue
        if current_page < notes_start_page:
            continue
        cleaned = clean_structural_text(raw_s)
        if not cleaned:
            continue
        m = NOTE_HEADING_LINE_RE.match(cleaned)
        if m:
            note_no = int(m.group(1))
            title = normalize_title_for_index(m.group(2))
            if 1 <= note_no <= 99 and title and len(title.split()) <= 30 and numeric_density_from_text(title) <= 0.35:
                candidates.append({"note_no": note_no, "title": title, "page_hint": current_page, "source": "md_direct"})
                pending_note_no = None
                pending_page = None
                continue
        m_num = NOTE_HEADING_TOKEN_RE.match(cleaned)
        if m_num:
            note_no = int(m_num.group(1))
            if 1 <= note_no <= 99:
                pending_note_no = note_no
                pending_page = current_page
                continue
        if pending_note_no is not None:
            title = normalize_title_for_index(cleaned)
            if title and len(title.split()) <= 30 and numeric_density_from_text(title) <= 0.35:
                candidates.append({"note_no": pending_note_no, "title": title, "page_hint": pending_page, "source": "md_split"})
            pending_note_no = None
            pending_page = None
    return candidates


def extract_fs_from_pdf_visual_lines(visual_lines):
    items, seen = [], set()
    for ln in visual_lines:
        if ln.get("page", 1) <= 2:
            continue
        txt = re.sub(r"\s+", " ", ln.get("text", "")).strip()
        for fs in FS_PATTERNS:
            if txt == fs and fs not in seen:
                seen.add(fs)
                items.append({"type": "fs", "label": fs, "title": fs, "page": ln["page"],
                               "bbox": ln["bbox"], "anchor_text": ln["text"], "source": "pdf_visual_line"})
    return items


def build_new_pdf_index(new_md_path: Path, new_words, page_count):
    visual_lines = group_pdf_visual_lines(new_words)
    notes_start_page = detect_notes_start_page(new_md_path, visual_lines)
    items = []
    cover_line = first_line_on_page(visual_lines, 1)
    if page_count >= 1:
        items.append({"type": "cover", "label": "Cover", "title": "Cover", "page": 1,
                      "bbox": cover_line["bbox"] if cover_line else [0, 0, 1, 1],
                      "anchor_text": cover_line["text"] if cover_line else "Cover"})
    contents_line = first_line_on_page(visual_lines, 2)
    if page_count >= 2:
        items.append({"type": "contents", "label": "Contents", "title": "Contents", "page": 2,
                      "bbox": contents_line["bbox"] if contents_line else [0, 0, 1, 1],
                      "anchor_text": contents_line["text"] if contents_line else "Contents"})
    items.extend(extract_fs_from_pdf_visual_lines(visual_lines))
    seen_notes = set()
    for cand in extract_note_headings_from_md(new_md_path, notes_start_page):
        note_no = cand["note_no"]
        if note_no in seen_notes:
            continue
        title = refine_title_with_pdf_line(note_no, cand["title"], visual_lines, cand.get("page_hint"))
        if not title:
            continue
        anchor = find_note_anchor_in_words(note_no, title, new_words, cand.get("page_hint"), notes_start_page)
        seen_notes.add(note_no)
        items.append({"type": "note", "note_no": note_no, "label": f"Note {note_no}. {title}",
                      "title": title, "page": anchor["page"], "bbox": anchor["bbox"],
                      "anchor_text": anchor["anchor_text"], "source": cand.get("source", "md_first")})
    items.sort(key=lambda x: (x.get("page", 9999), x.get("bbox", [0, 0, 0, 0])[1], x.get("bbox", [0, 0, 0, 0])[0]))
    return items


# =========================================================
# Highlight / change generation
# =========================================================

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
                c[f"{side}_anchor"] = {"page": w["page"], "bbox": w["bbox"],
                                        "text": w.get("text", ""), "seg_idx": anchor.get("seg_idx")}
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
            current = {"id": change_id, "old_tokens": [], "new_tokens": [],
                       "old_highlight_ids": [], "new_highlight_ids": [],
                       "old_page": None, "new_page": None,
                       "seg_start": item["seg_idx"], "seg_end": item["seg_idx"]}
        current["seg_end"] = item["seg_idx"]
        if typ == "delete":
            current["old_tokens"].append(item.get("text", ""))
            if item["old_pdf_idx"] is not None:
                w = old_words[item["old_pdf_idx"]]
                hid = f"old-{len(old_highlights) + 1}"
                old_highlights.append({"id": hid, "change_id": change_id, "type": "delete",
                                        "text": w["text"], "page": w["page"], "bbox": w["bbox"]})
                current["old_highlight_ids"].append(hid)
                current["old_page"] = current["old_page"] or w["page"]
        elif typ == "add":
            current["new_tokens"].append(item.get("text", ""))
            if item["new_pdf_idx"] is not None:
                w = new_words[item["new_pdf_idx"]]
                hid = f"new-{len(new_highlights) + 1}"
                new_highlights.append({"id": hid, "change_id": change_id, "type": "add",
                                        "text": w["text"], "page": w["page"], "bbox": w["bbox"]})
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
        groups.setdefault((h["page"], h["type"]), []).append(hh)
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
                merged.append({
                    "id": "__".join(chunk["ids"]),
                    "change_id": chunk["change_ids"][0],
                    "change_ids": sorted(set(chunk["change_ids"])),
                    "type": chunk["type"],
                    "page": chunk["page"],
                    "bbox": [max(0, chunk["x0"] - pad_x), chunk["y0"] + pad_y,
                              chunk["x1"] + pad_x, max(chunk["y0"] + pad_y + 1, chunk["y1"] - pad_y)],
                    "text": " ".join(chunk["texts"]),
                })
                chunk = None

            for h in line["items"]:
                x0, y0, x1, y1 = h["bbox"]
                if chunk is None:
                    chunk = {"ids": [h["id"]], "texts": [h.get("text", "")], "change_ids": [h["change_id"]],
                             "type": h["type"], "page": h["page"], "x0": x0, "y0": y0, "x1": x1, "y1": y1}
                    continue
                gap = x0 - chunk["x1"]
                if gap <= max_gap:
                    chunk["ids"].append(h["id"]); chunk["texts"].append(h.get("text", ""))
                    chunk["change_ids"].append(h["change_id"])
                    chunk["x0"] = min(chunk["x0"], x0); chunk["y0"] = min(chunk["y0"], y0)
                    chunk["x1"] = max(chunk["x1"], x1); chunk["y1"] = max(chunk["y1"], y1)
                else:
                    flush()
                    chunk = {"ids": [h["id"]], "texts": [h.get("text", "")], "change_ids": [h["change_id"]],
                             "type": h["type"], "page": h["page"], "x0": x0, "y0": y0, "x1": x1, "y1": y1}
            flush()
    return merged


def alignment_report(segments, old_words, new_words):
    old_stream, new_stream = build_side_stream(segments, "old"), build_side_stream(segments, "new")
    old_map = align_stream_to_pdf_words(old_stream, old_words)
    new_map = align_stream_to_pdf_words(new_stream, new_words)
    return {
        "old_md_tokens": len(old_stream), "new_md_tokens": len(new_stream),
        "old_pdf_words": len(old_words), "new_pdf_words": len(new_words),
        "old_aligned": len(old_map), "new_aligned": len(new_map),
        "old_alignment_rate": round(len(old_map) / max(1, len(old_stream)), 4),
        "new_alignment_rate": round(len(new_map) / max(1, len(new_stream)), 4),
    }


# =========================================================
# PDF annotation export
# =========================================================

def export_annotated_pdf(run_dir: Path, side: str) -> bytes:
    """
    Embed reviews.json into the PDF as standard annotations.
    - Highlight reviewed words (open=yellow / cleared=gray)
    - Add a sticky note near the first word of each review.
    Returns raw PDF bytes.
    """
    import semantic as _sem

    reviews   = _sem.load_reviews(run_dir)
    words_path = run_dir / f"{side}_words.json"
    pdf_path   = run_dir / f"{side}.pdf"

    if not pdf_path.exists():
        raise FileNotFoundError(f"{side}.pdf not found")

    words = json.loads(words_path.read_text(encoding="utf-8")) if words_path.exists() else []
    doc   = fitz.open(str(pdf_path))

    for idx, rv in enumerate(reviews, start=1):
        word_ids = rv.get(f"{side}_word_ids") or []
        if not word_ids:
            continue

        comment = (rv.get("comment") or "").strip() or "(No comment)"
        status  = rv.get("status", "open")
        color   = [0.75, 0.75, 0.75] if status in ("cleared", "resolved") else [1.0, 0.84, 0.0]
        title   = f"Review #{idx}  [{status}]"

        # Group selected words by page.
        by_page: dict = {}
        for wid in word_ids:
            if 0 <= wid < len(words):
                w = words[wid]
                by_page.setdefault(w["page"], []).append(w)

        first = True
        for page_no in sorted(by_page):
            page_words = by_page[page_no]
            page = doc[page_no - 1]

            # Highlight all selected words as one annotation.
            rects = [fitz.Rect(w["bbox"]) for w in page_words]
            hl = page.add_highlight_annot(rects)
            hl.set_colors(stroke=color)
            hl.set_info(title=title, content=comment)
            hl.update()

            # Place a sticky note next to the first word only.
            if first:
                anchor = page_words[0]["bbox"]          # [x0, y0, x1, y1]
                pt = fitz.Point(anchor[2] + 4, anchor[1])
                note = page.add_text_annot(pt, comment, icon="Note")
                note.set_info(title=title, content=comment)
                note.update()
                first = False

    return doc.tobytes(garbage=4, deflate=True)


# =========================================================
# Job pipeline
# =========================================================

def process_job(job_id, old_pdf, new_pdf):
    job = JOBS[job_id]
    run_dir = RUNS_DIR / job_id

    try:
        job["status"] = "extracting_pdf_words"
        old_words, old_page_sizes = extract_pdf_words(old_pdf)
        new_words, new_page_sizes = extract_pdf_words(new_pdf)
        (run_dir / "old_words.json").write_text(json.dumps(old_words, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "new_words.json").write_text(json.dumps(new_words, ensure_ascii=False, indent=2), encoding="utf-8")

        job["status"] = "running_opendataloader"
        old_md_src = run_opendataloader_to_markdown(old_pdf, run_dir / "opendataloader_old")
        new_md_src = run_opendataloader_to_markdown(new_pdf, run_dir / "opendataloader_new")
        old_md, new_md = run_dir / "old.md", run_dir / "new.md"
        old_md.write_text(old_md_src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
        new_md.write_text(new_md_src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

        job["status"] = "running_diff_extract"
        result_path = run_dir / "result.json"
        diff_doc = run_diff_extract(old_md, new_md, result_path)

        diff_doc["segments"], suppressed_moves = suppress_layout_moves(diff_doc.get("segments", []))
        diff_doc["suppressed_moves"] = suppressed_moves
        diff_doc["summary"] = {
            "equal":     sum(1 for s in diff_doc["segments"] if s.get("type") == "equal"  and not s.get("suppressed")),
            "deleted":   sum(1 for s in diff_doc["segments"] if s.get("type") == "delete" and not s.get("suppressed")),
            "added":     sum(1 for s in diff_doc["segments"] if s.get("type") == "add"    and not s.get("suppressed")),
            "suppressed":sum(1 for s in diff_doc["segments"] if s.get("suppressed")),
        }
        result_path.write_text(json.dumps(diff_doc, ensure_ascii=False, indent=2), encoding="utf-8")

        job["status"] = "mapping_result_json_to_pdf"
        old_highlights, new_highlights, changes = make_highlights_and_changes(diff_doc["segments"], old_words, new_words)
        old_highlights = merge_highlight_rects_server(old_highlights)
        new_highlights = merge_highlight_rects_server(new_highlights)
        report = alignment_report(diff_doc["segments"], old_words, new_words)
        index_items = build_new_pdf_index(new_md, new_words, len(new_page_sizes))
        section_map = inject_section_markers(new_md, index_items)
        (run_dir / "section_map.json").write_text(json.dumps(section_map, ensure_ascii=False, indent=2), encoding="utf-8")

        # ---- semantic_map: word-level equal anchor for reviews + index dual-scroll ----
        mapped = map_result_segments_to_pdf_indices(diff_doc["segments"], old_words, new_words)
        semantic_map = semantic.build_semantic_map(diff_doc["segments"], mapped, old_words, new_words)
        semantic.attach_index_old_side(index_items, semantic_map, old_words, new_words)

        viewer_data = {
            "job_id": job_id,
            "app_version": "index-md-first-v7",
            "summary": diff_doc.get("summary", {}),
            "engine": diff_doc.get("engine"),
            "algorithm": diff_doc.get("algorithm"),
            "old_page_count": len(old_page_sizes),
            "new_page_count": len(new_page_sizes),
            "old_page_sizes": old_page_sizes,
            "new_page_sizes": new_page_sizes,
            "highlights_old": old_highlights,
            "highlights_new": new_highlights,
            "changes": changes,
            "alignment_report": report,
            "suppressed_moves": diff_doc.get("suppressed_moves", []),
            "index_items": index_items,
            "section_map": section_map,
            "old_filename": old_pdf.name,
            "new_filename": new_pdf.name,
            "semantic_map": semantic_map,
        }
        (run_dir / "viewer_data.json").write_text(json.dumps(viewer_data, ensure_ascii=False, indent=2), encoding="utf-8")
        job["status"] = "done"
        job["result"] = viewer_data

    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)


# =========================================================
# Document / run pipeline (Phase 2)
# =========================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def document_dir(doc_id):
    return DOCUMENTS_DIR / doc_id


def document_run_dir(doc_id, run_id):
    return document_dir(doc_id) / "runs" / run_id


def document_meta_path(doc_id):
    return document_dir(doc_id) / "meta.json"


def load_document_meta(doc_id):
    return read_json(document_meta_path(doc_id))


def save_document_meta(meta):
    meta["updated_at"] = utc_now()
    write_json(document_meta_path(meta["doc_id"]), meta)


def get_uploaded_pdf(field_names=("pdf", "report_pdf", "file")):
    for name in field_names:
        if name in request.files:
            return request.files[name]
    return None


def copy_if_exists(src: Path, dst: Path):
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def write_unmarked_md_copy(src: Path, dst: Path):
    dst.write_text("\n".join(strip_section_markers(read_md_lines(src))) + "\n", encoding="utf-8")
    return dst


def sync_report_compat_files(run_dir: Path):
    copy_if_exists(run_dir / "report.pdf", run_dir / "new.pdf")
    copy_if_exists(run_dir / "report.md", run_dir / "new.md")
    copy_if_exists(run_dir / "words.json", run_dir / "new_words.json")
    copy_if_exists(run_dir / "chars.json", run_dir / "new_chars.json")
    copy_if_exists(run_dir / "prev_report.pdf", run_dir / "old.pdf")
    copy_if_exists(run_dir / "prev_report.md", run_dir / "old.md")
    copy_if_exists(run_dir / "prev_words.json", run_dir / "old_words.json")
    copy_if_exists(run_dir / "prev_chars.json", run_dir / "old_chars.json")


def build_chars_from_words(words):
    chars = []
    for w in words or []:
        text = str(w.get("text", ""))
        if not text:
            continue
        x0, y0, x1, y1 = w.get("bbox", [0, 0, 0, 0])
        width = max(0.1, x1 - x0)
        step = width / max(1, len(text))
        for i, ch in enumerate(text):
            chars.append({
                "idx": len(chars),
                "char": ch,
                "word_id": w.get("idx"),
                "char_index": i,
                "page": w.get("page"),
                "block": w.get("block"),
                "line": w.get("line"),
                "word_no": w.get("word_no"),
                "bbox": [x0 + step * i, y0, x0 + step * (i + 1), y1],
                "order": len(chars),
            })
    return chars


def process_single_document_run(run_dir: Path, pdf_path: Path, *, doc_id=None, run_id=None, filename=None):
    words, page_sizes = extract_pdf_words(pdf_path)
    chars = build_chars_from_words(words)
    write_json(run_dir / "words.json", words)
    write_json(run_dir / "new_words.json", words)
    write_json(run_dir / "chars.json", chars)
    write_json(run_dir / "new_chars.json", chars)

    md_src = run_opendataloader_to_markdown(pdf_path, run_dir / "opendataloader_report")
    report_md = run_dir / "report.md"
    report_md.write_text(md_src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

    index_items = build_new_pdf_index(report_md, words, len(page_sizes))
    section_map = inject_section_markers(report_md, index_items)
    write_json(run_dir / "section_map.json", section_map)
    sync_report_compat_files(run_dir)

    viewer_data = {
        "doc_id": doc_id,
        "run_id": run_id,
        "mode": "single",
        "app_version": "documents-phase2-v1",
        "summary": {},
        "engine": None,
        "algorithm": None,
        "old_page_count": 0,
        "new_page_count": len(page_sizes),
        "page_count": len(page_sizes),
        "old_page_sizes": [],
        "new_page_sizes": page_sizes,
        "page_sizes": page_sizes,
        "highlights_old": [],
        "highlights_new": [],
        "changes": [],
        "alignment_report": None,
        "suppressed_moves": [],
        "index_items": index_items,
        "section_map": section_map,
        "old_filename": None,
        "new_filename": filename or pdf_path.name,
        "filename": filename or pdf_path.name,
        "semantic_map": {"unit": "word", "equal_words": []},
    }
    write_json(run_dir / "viewer_data.json", viewer_data)
    return viewer_data


def process_document_diff_run(run_dir: Path, *, doc_id=None, run_id=None):
    prev_pdf = run_dir / "prev_report.pdf"
    report_pdf = run_dir / "report.pdf"
    prev_md = run_dir / "prev_report.md"
    report_md = run_dir / "report.md"
    if not prev_pdf.exists() or not report_pdf.exists():
        raise FileNotFoundError("prev_report.pdf and report.pdf are required before diff")
    if not prev_md.exists() or not report_md.exists():
        raise FileNotFoundError("prev_report.md and report.md are required before diff")

    prev_words = read_json(run_dir / "prev_words.json", [])
    report_words = read_json(run_dir / "words.json", [])
    prev_page_sizes = fitz.open(str(prev_pdf))
    report_page_sizes = fitz.open(str(report_pdf))
    old_page_sizes = [{"width": float(p.rect.width), "height": float(p.rect.height)} for p in prev_page_sizes]
    new_page_sizes = [{"width": float(p.rect.width), "height": float(p.rect.height)} for p in report_page_sizes]

    result_path = run_dir / "result.json"
    diff_prev_md = write_unmarked_md_copy(prev_md, run_dir / "prev_report.diff.md")
    diff_report_md = write_unmarked_md_copy(report_md, run_dir / "report.diff.md")
    diff_doc = run_diff_extract(diff_prev_md, diff_report_md, result_path)
    diff_doc["segments"], suppressed_moves = suppress_layout_moves(diff_doc.get("segments", []))
    diff_doc["suppressed_moves"] = suppressed_moves
    diff_doc["summary"] = {
        "equal": sum(1 for s in diff_doc["segments"] if s.get("type") == "equal" and not s.get("suppressed")),
        "deleted": sum(1 for s in diff_doc["segments"] if s.get("type") == "delete" and not s.get("suppressed")),
        "added": sum(1 for s in diff_doc["segments"] if s.get("type") == "add" and not s.get("suppressed")),
        "suppressed": sum(1 for s in diff_doc["segments"] if s.get("suppressed")),
    }
    write_json(result_path, diff_doc)

    old_highlights, new_highlights, changes = make_highlights_and_changes(diff_doc["segments"], prev_words, report_words)
    old_highlights = merge_highlight_rects_server(old_highlights)
    new_highlights = merge_highlight_rects_server(new_highlights)
    report = alignment_report(diff_doc["segments"], prev_words, report_words)
    index_items = build_new_pdf_index(report_md, report_words, len(new_page_sizes))
    section_map = inject_section_markers(report_md, index_items)
    write_json(run_dir / "section_map.json", section_map)

    mapped = map_result_segments_to_pdf_indices(diff_doc["segments"], prev_words, report_words)
    semantic_map = semantic.build_semantic_map(diff_doc["segments"], mapped, prev_words, report_words)
    semantic.attach_index_old_side(index_items, semantic_map, prev_words, report_words)
    sync_report_compat_files(run_dir)
    projected_reviews = document_reviews_for_run(doc_id, run_id) if doc_id else []

    viewer_data = {
        "doc_id": doc_id,
        "run_id": run_id,
        "mode": "diff",
        "app_version": "documents-phase2-v1",
        "summary": diff_doc.get("summary", {}),
        "engine": diff_doc.get("engine"),
        "algorithm": diff_doc.get("algorithm"),
        "old_page_count": len(old_page_sizes),
        "new_page_count": len(new_page_sizes),
        "old_page_sizes": old_page_sizes,
        "new_page_sizes": new_page_sizes,
        "highlights_old": old_highlights,
        "highlights_new": new_highlights,
        "changes": changes,
        "alignment_report": report,
        "suppressed_moves": diff_doc.get("suppressed_moves", []),
        "index_items": index_items,
        "section_map": section_map,
        "old_filename": prev_pdf.name,
        "new_filename": report_pdf.name,
        "semantic_map": semantic_map,
        "carried_review_count": len(projected_reviews),
    }
    write_json(run_dir / "viewer_data.json", viewer_data)
    return viewer_data


def document_semantic_map(doc_id, run_id):
    p = document_run_dir(doc_id, run_id) / "viewer_data.json"
    return (read_json(p, {}) or {}).get("semantic_map", {})


def document_reviews_path(doc_id):
    return document_dir(doc_id) / "reviews.json"


def load_document_reviews(doc_id):
    return read_json(document_reviews_path(doc_id), []) or []


def save_document_reviews(doc_id, reviews):
    write_json(document_reviews_path(doc_id), reviews)


def words_bbox(words, word_ids):
    selected = [words[i] for i in word_ids if isinstance(i, int) and 0 <= i < len(words)]
    if not selected:
        return [0, 0, 1, 1]
    return line_bbox(selected)


def first_word_page(words, word_ids):
    for wid in word_ids:
        if isinstance(wid, int) and 0 <= wid < len(words):
            return words[wid].get("page")
    return None


def text_for_word_ids(words, word_ids):
    return " ".join(str(words[i].get("text", "")) for i in word_ids if isinstance(i, int) and 0 <= i < len(words))


def anchor_for_selection(run_dir: Path, run_id, side, word_ids, rect=None):
    words_path = run_dir / ("prev_words.json" if side in ("old", "prev", "previous") else "words.json")
    words = read_json(words_path, []) or []
    ids = sorted({int(w) for w in word_ids if isinstance(w, int) or str(w).isdigit()})
    ids = [i for i in ids if 0 <= i < len(words)]
    if not ids:
        return None
    return {
        "run_id": run_id,
        "side": "old" if side in ("old", "prev", "previous") else "new",
        "word_ids": ids,
        "old_word_ids": ids if side in ("old", "prev", "previous") else [],
        "new_word_ids": ids if side not in ("old", "prev", "previous") else [],
        "page": first_word_page(words, ids),
        "bbox": words_bbox(words, ids),
        "rect": rect,
        "text": text_for_word_ids(words, ids),
        "floating": False,
    }


def review_projection_for_anchor(review, anchor_run_id, display_side=None):
    anchor = (review.get("anchors") or {}).get(anchor_run_id)
    if not anchor:
        return None
    side = display_side or anchor.get("side", "new")
    word_ids = anchor.get("word_ids", [])
    projected = {
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
        "text": anchor.get("text") or review.get("text", ""),
        "selection": {"page": anchor.get("page"), "rect": anchor.get("rect"), "word_ids": anchor.get("word_ids", [])},
        "comment": "\n\n".join(c.get("text", "") for c in review.get("comments", []) if c.get("text")),
        "comments": review.get("comments", []),
        "status": review.get("status", "open"),
        "created_at": review.get("created_at"),
        "updated_at": review.get("updated_at"),
        "floating": anchor.get("floating", False),
    }
    return projected


def document_reviews_for_run(doc_id, run_id):
    meta = load_document_meta(doc_id) or {}
    run_meta = next((r for r in meta.get("runs", []) if r.get("run_id") == run_id), {})
    prev_run_id = run_meta.get("previous_run_id")
    projected = []
    for review in load_document_reviews(doc_id):
        if prev_run_id and prev_run_id != run_id:
            prev_anchor = (review.get("anchors") or {}).get(prev_run_id)
            if prev_anchor and prev_anchor.get("side") != "old":
                prev_projection = review_projection_for_anchor(review, prev_run_id, "old")
                if prev_projection:
                    projected.append(prev_projection)
        current_projection = review_projection_for_anchor(review, run_id)
        if current_projection:
            projected.append(current_projection)
    return projected


def create_document_level_review(doc_id, run_id, run_dir: Path, data):
    anchor = anchor_for_selection(run_dir, run_id, data.get("side", "new"), data.get("word_ids", []), data.get("rect"))
    if not anchor:
        return {"error": "no_word_selected"}
    now = utc_now()
    comment_text = data.get("comment", "")
    review = {
        "review_id": "r-" + uuid.uuid4().hex[:8],
        "anchor_id": "a-" + uuid.uuid4().hex[:8],
        "doc_id": doc_id,
        "status": data.get("status", "open"),
        "created_run_id": run_id,
        "is_floating": False,
        "text": anchor.get("text", ""),
        "comments": ([{"comment_id": "c-" + uuid.uuid4().hex[:8], "author": data.get("author", "user"), "text": comment_text, "created_at": now}] if comment_text else []),
        "anchors": {run_id: anchor},
        "created_at": now,
        "updated_at": now,
    }
    reviews = load_document_reviews(doc_id)
    reviews.append(review)
    save_document_reviews(doc_id, reviews)
    return review_projection_for_anchor(review, run_id)


def remap_document_reviews_to_run(doc_id, prev_run_id, run_id, run_dir: Path, semantic_map):
    if not prev_run_id:
        return []
    reviews = load_document_reviews(doc_id)
    if not reviews:
        return []
    old_words = read_json(run_dir / "prev_words.json", []) or []
    now = utc_now()
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
            "page": first_word_page(old_words, old_ids),
            "bbox": words_bbox(old_words, old_ids),
            "text": prev_anchor.get("text", review.get("text", "")),
            "floating": False,
            "previous_run_id": prev_run_id,
        }
        anchors[run_id] = anchor
        review["updated_at"] = now
        changed = True
    if changed:
        save_document_reviews(doc_id, reviews)
    return document_reviews_for_run(doc_id, run_id)


def add_single_document_review(run_dir: Path, data):
    side = data.get("side")
    if side not in ("new", "report", "current"):
        return {"error": "single_run_reviews_only_support_current_report"}
    word_ids = sorted({int(w) for w in data.get("word_ids", []) if isinstance(w, int) or str(w).isdigit()})
    words = read_json(run_dir / "words.json", []) or []
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
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    reviews = semantic.load_reviews(run_dir)
    reviews.append(review)
    semantic.save_reviews(run_dir, reviews)
    return review


def carry_forward_previous_reviews(run_dir: Path, semantic_map):
    prev_reviews = read_json(run_dir / "prev_reviews.json", []) or []
    if not prev_reviews or semantic.load_reviews(run_dir):
        return semantic.load_reviews(run_dir)

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
        rv["updated_at"] = utc_now()
        carried.append(rv)

    semantic.save_reviews(run_dir, carried)
    return carried


def update_run_meta(meta, run_id, **patch):
    for run in meta.get("runs", []):
        if run.get("run_id") == run_id:
            run.update(patch)
            return run
    return None


AI_ASSESSMENT_MODEL = os.environ.get("AI_ASSESSMENT_MODEL", "claude-3-5-sonnet-latest")
AI_CONTEXT_CHARS = 9000


def ai_assessment_path(doc_id, run_id):
    return document_run_dir(doc_id, run_id) / "ai_assessment.json"


def load_ai_assessment(doc_id, run_id):
    return read_json(ai_assessment_path(doc_id, run_id), {"doc_id": doc_id, "run_id": run_id, "items": []}) or {"doc_id": doc_id, "run_id": run_id, "items": []}


def save_ai_assessment(doc_id, run_id, assessment):
    assessment["doc_id"] = doc_id
    assessment["run_id"] = run_id
    assessment["updated_at"] = utc_now()
    write_json(ai_assessment_path(doc_id, run_id), assessment)


def run_meta_for(meta, run_id):
    return next((r for r in meta.get("runs", []) if r.get("run_id") == run_id), None)


def anchor_section_id(anchor):
    return anchor.get("section_id") or anchor.get("section") or None


def norm_space(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def section_context_from_markdown(md_path: Path, section_map, section_id=None, fallback_text=""):
    if not md_path.exists():
        return ""
    lines = read_md_lines(md_path)
    chosen = None
    if section_id:
        chosen = next((s for s in section_map or [] if s.get("section_id") == section_id), None)
    if chosen:
        start = max(0, int(chosen.get("start_line", 1)) - 1)
        end = min(len(lines), int(chosen.get("end_line", len(lines))))
        text = "\n".join(strip_section_markers(lines[start:end])).strip()
    else:
        raw = "\n".join(strip_section_markers(lines))
        needle = norm_space(fallback_text)
        idx = norm_space(raw).find(needle[:80]) if needle else -1
        if idx >= 0:
            start = max(0, idx - AI_CONTEXT_CHARS // 3)
            text = raw[start:start + AI_CONTEXT_CHARS].strip()
        else:
            text = raw[:AI_CONTEXT_CHARS].strip()
    return text[:AI_CONTEXT_CHARS]


def review_anchor_for_run(review, run_id):
    return (review.get("anchors") or {}).get(run_id)


def assessment_reviews_for_run(doc_id, run_id, review_id=None):
    meta = load_document_meta(doc_id) or {}
    run_meta = run_meta_for(meta, run_id) or {}
    prev_run_id = run_meta.get("previous_run_id")
    if not prev_run_id:
        return []
    reviews = []
    for review in load_document_reviews(doc_id):
        if review_id and review.get("review_id") != review_id:
            continue
        if review.get("status", "open") not in ("open", "partial", "unclear"):
            continue
        prev_anchor = review_anchor_for_run(review, prev_run_id)
        if prev_anchor:
            reviews.append((review, prev_anchor, prev_run_id))
    return reviews


def build_assessment_prompt(review, prev_anchor, old_context, new_context):
    comments = "\n".join(f"- {c.get('text', '')}" for c in review.get("comments", []) if c.get("text"))
    return (
        "You are reviewing whether a report update resolved a prior review comment.\n"
        "Use the submit_verdict tool exactly once.\n"
        "verdict must be one of: cleared, partial, unclear, not_cleared.\n\n"
        f"Review text:\n{prev_anchor.get('text') or review.get('text', '')}\n\n"
        f"Reviewer comments:\n{comments or '(No comment)'}\n\n"
        f"Previous report context:\n{old_context}\n\n"
        f"Current report context:\n{new_context}\n"
    )


def normalize_assessment_payload(data):
    verdict = data.get("verdict")
    if verdict not in ("cleared", "partial", "unclear", "not_cleared"):
        raise ValueError("invalid verdict")
    return {
        "verdict": verdict,
        "confidence": data.get("confidence"),
        "reasoning": str(data.get("reasoning", ""))[:2000],
        "evidence_old": str(data.get("evidence_old", ""))[:1200],
        "evidence_new": str(data.get("evidence_new", ""))[:1200],
    }


def call_ai_assessment(prompt):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    try:
        from anthropic import Anthropic
    except Exception as e:
        raise RuntimeError("anthropic package is not installed") from e
    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=AI_ASSESSMENT_MODEL,
        max_tokens=800,
        temperature=0,
        tools=[{
            "name": "submit_verdict",
            "description": "Submit the structured assessment verdict.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": ["cleared", "partial", "unclear", "not_cleared"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reasoning": {"type": "string"},
                    "evidence_old": {"type": "string"},
                    "evidence_new": {"type": "string"},
                },
                "required": ["verdict", "confidence", "reasoning", "evidence_old", "evidence_new"],
                "additionalProperties": False,
            },
        }],
        tool_choice={"type": "tool", "name": "submit_verdict"},
        messages=[{"role": "user", "content": prompt}],
    )
    for block in msg.content:
        if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == "submit_verdict":
            return normalize_assessment_payload(block.input)
    raise ValueError("submit_verdict tool was not called")


def run_ai_assessment(doc_id, run_id, review_id=None):
    meta = load_document_meta(doc_id)
    if not meta:
        return None, ("document not found", 404)
    run_meta = run_meta_for(meta, run_id)
    if not run_meta:
        return None, ("run not found", 404)
    prev_run_id = run_meta.get("previous_run_id")
    if not prev_run_id:
        return None, ("AI assessment requires a diff run", 400)
    run_dir = document_run_dir(doc_id, run_id)
    prev_section_map = read_json(run_dir / "prev_section_map.json", []) or []
    current_section_map = read_json(run_dir / "section_map.json", []) or []
    targets = assessment_reviews_for_run(doc_id, run_id, review_id=review_id)
    if review_id and not targets:
        return None, ("review is not assessable for previous run", 404)
    if targets and not os.environ.get("ANTHROPIC_API_KEY"):
        return None, ("ANTHROPIC_API_KEY is not set", 400)
    previous = load_ai_assessment(doc_id, run_id)
    replacing = {review.get("review_id") for review, _, _ in targets}
    items = [] if not review_id else [item for item in previous.get("items", []) if item.get("review_id") not in replacing]
    started_at = utc_now()
    for review, prev_anchor, anchor_run_id in targets:
        section_id = anchor_section_id(prev_anchor)
        old_context = section_context_from_markdown(run_dir / "prev_report.md", prev_section_map, section_id, prev_anchor.get("text", ""))
        new_context = section_context_from_markdown(run_dir / "report.md", current_section_map, section_id, prev_anchor.get("text", ""))
        item = {
            "review_id": review.get("review_id"),
            "anchor_run_id": anchor_run_id,
            "section_id": section_id,
            "status": "done",
            "assessed_at": utc_now(),
        }
        try:
            item.update(call_ai_assessment(build_assessment_prompt(review, prev_anchor, old_context, new_context)))
        except Exception as e:
            item.update({"status": "error", "verdict": "unclear", "confidence": None, "reasoning": str(e), "evidence_old": "", "evidence_new": ""})
        items.append(item)
    assessment = {
        "status": "done",
        "model": AI_ASSESSMENT_MODEL,
        "started_at": started_at,
        "completed_at": utc_now(),
        "items": items,
    }
    save_ai_assessment(doc_id, run_id, assessment)
    update_run_meta(meta, run_id, has_ai_assessment=True)
    save_document_meta(meta)
    return assessment, None


# =========================================================
# Routes
# =========================================================

def _run_dir(job_id):
    return RUNS_DIR / job_id


def _semantic_map(job_id):
    job = JOBS.get(job_id)
    if job and job.get("status") == "done":
        return job["result"].get("semantic_map", {})
    p = _run_dir(job_id) / "viewer_data.json"
    return json.loads(p.read_text(encoding="utf-8")).get("semantic_map", {}) if p.exists() else {}


@app.route("/")
def index():
    index_path = BASE_DIR / "static" / "index.html"
    if index_path.exists():
        return send_file(index_path)
    return Response("static/index.html not found", status=404, mimetype="text/plain")


@app.route("/api/documents", methods=["POST"])
def create_document():
    uploaded = get_uploaded_pdf()
    if uploaded is None:
        return jsonify({"error": "pdf file is required; use form field pdf, report_pdf, or file"}), 400

    doc_id = uuid.uuid4().hex[:12]
    run_id = uuid.uuid4().hex[:12]
    run_dir = document_run_dir(doc_id, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    report_pdf = run_dir / "report.pdf"
    uploaded.save(report_pdf)

    title = request.form.get("title") or Path(uploaded.filename or "report.pdf").stem
    created_at = utc_now()
    meta = {
        "doc_id": doc_id,
        "title": title,
        "created_at": created_at,
        "updated_at": created_at,
        "runs": [{
            "run_id": run_id,
            "kind": "initial",
            "status": "processing",
            "created_at": created_at,
            "filename": uploaded.filename or "report.pdf",
            "has_diff": False,
            "has_ai_assessment": False,
        }],
    }
    save_document_meta(meta)

    try:
        viewer_data = process_single_document_run(
            run_dir,
            report_pdf,
            doc_id=doc_id,
            run_id=run_id,
            filename=uploaded.filename or "report.pdf",
        )
    except Exception as e:
        update_run_meta(meta, run_id, status="error", error=str(e))
        save_document_meta(meta)
        return jsonify({"error": str(e), "doc_id": doc_id, "run_id": run_id}), 500

    update_run_meta(meta, run_id, status="ready")
    save_document_meta(meta)
    return jsonify({"doc_id": doc_id, "run_id": run_id, "meta": meta, "result": viewer_data}), 201


@app.route("/api/documents/<doc_id>")
def get_document(doc_id):
    meta = load_document_meta(doc_id)
    if not meta:
        return jsonify({"error": "document not found"}), 404
    return jsonify(meta)


@app.route("/api/documents/<doc_id>/reviews", methods=["GET"])
def list_document_level_reviews(doc_id):
    if not load_document_meta(doc_id):
        return jsonify({"error": "document not found"}), 404
    return jsonify(load_document_reviews(doc_id))


@app.route("/api/documents/<doc_id>/reviews/<review_id>", methods=["PATCH"])
def patch_document_level_review(doc_id, review_id):
    patch = request.get_json(force=True) or {}
    reviews = load_document_reviews(doc_id)
    updated = None
    for review in reviews:
        if review.get("review_id") == review_id:
            for key in ("status", "human_decision", "is_floating"):
                if key in patch:
                    review[key] = patch[key]
            review["updated_at"] = utc_now()
            updated = review
            break
    if not updated:
        return jsonify({"error": "not found"}), 404
    save_document_reviews(doc_id, reviews)
    return jsonify(updated)


@app.route("/api/documents/<doc_id>/reviews/<review_id>", methods=["DELETE"])
def delete_document_level_review(doc_id, review_id):
    reviews = load_document_reviews(doc_id)
    kept = [r for r in reviews if r.get("review_id") != review_id]
    if len(kept) == len(reviews):
        return jsonify({"error": "not found"}), 404
    save_document_reviews(doc_id, kept)
    return jsonify({"deleted": True})


@app.route("/api/documents/<doc_id>/reviews/<review_id>/comments", methods=["POST"])
def add_document_level_comment(doc_id, review_id):
    data = request.get_json(force=True) or {}
    reviews = load_document_reviews(doc_id)
    updated = None
    for review in reviews:
        if review.get("review_id") == review_id:
            review.setdefault("comments", []).append({
                "comment_id": "c-" + uuid.uuid4().hex[:8],
                "author": data.get("author", "user"),
                "text": data.get("text") or data.get("comment") or "",
                "created_at": utc_now(),
            })
            review["updated_at"] = utc_now()
            updated = review
            break
    if not updated:
        return jsonify({"error": "not found"}), 404
    save_document_reviews(doc_id, reviews)
    return jsonify(updated)


@app.route("/api/documents/<doc_id>/runs", methods=["POST"])
def create_document_run(doc_id):
    meta = load_document_meta(doc_id)
    if not meta:
        return jsonify({"error": "document not found"}), 404
    if not meta.get("runs"):
        return jsonify({"error": "document has no previous run"}), 400

    uploaded = get_uploaded_pdf()
    if uploaded is None:
        return jsonify({"error": "pdf file is required; use form field pdf, report_pdf, or file"}), 400

    prev_run_id = meta["runs"][-1]["run_id"]
    prev_dir = document_run_dir(doc_id, prev_run_id)
    if not (prev_dir / "report.pdf").exists():
        return jsonify({"error": f"previous run {prev_run_id} is missing report.pdf"}), 400

    run_id = uuid.uuid4().hex[:12]
    run_dir = document_run_dir(doc_id, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    copy_if_exists(prev_dir / "report.pdf", run_dir / "prev_report.pdf")
    copy_if_exists(prev_dir / "report.md", run_dir / "prev_report.md")
    copy_if_exists(prev_dir / "words.json", run_dir / "prev_words.json")
    copy_if_exists(prev_dir / "chars.json", run_dir / "prev_chars.json")
    copy_if_exists(prev_dir / "section_map.json", run_dir / "prev_section_map.json")
    copy_if_exists(prev_dir / "reviews.json", run_dir / "prev_reviews.json")

    report_pdf = run_dir / "report.pdf"
    uploaded.save(report_pdf)
    created_at = utc_now()
    run_meta = {
        "run_id": run_id,
        "kind": "update",
        "status": "processing",
        "created_at": created_at,
        "filename": uploaded.filename or "report.pdf",
        "previous_run_id": prev_run_id,
        "has_diff": False,
        "has_ai_assessment": False,
    }
    meta.setdefault("runs", []).append(run_meta)
    save_document_meta(meta)

    try:
        viewer_data = process_single_document_run(
            run_dir,
            report_pdf,
            doc_id=doc_id,
            run_id=run_id,
            filename=uploaded.filename or "report.pdf",
        )
        sync_report_compat_files(run_dir)
    except Exception as e:
        update_run_meta(meta, run_id, status="error", error=str(e))
        save_document_meta(meta)
        return jsonify({"error": str(e), "doc_id": doc_id, "run_id": run_id}), 500

    update_run_meta(meta, run_id, status="ready")
    save_document_meta(meta)
    return jsonify({"doc_id": doc_id, "run_id": run_id, "previous_run_id": prev_run_id, "result": viewer_data}), 201


@app.route("/api/documents/<doc_id>/runs/<run_id>/diff", methods=["POST"])
def diff_document_run(doc_id, run_id):
    meta = load_document_meta(doc_id)
    if not meta:
        return jsonify({"error": "document not found"}), 404
    run_dir = document_run_dir(doc_id, run_id)
    if not run_dir.exists():
        return jsonify({"error": "run not found"}), 404

    try:
        viewer_data = process_document_diff_run(run_dir, doc_id=doc_id, run_id=run_id)
    except Exception as e:
        update_run_meta(meta, run_id, status="error", error=str(e))
        save_document_meta(meta)
        return jsonify({"error": str(e), "doc_id": doc_id, "run_id": run_id}), 500

    update_run_meta(meta, run_id, status="ready", has_diff=True)
    save_document_meta(meta)
    return jsonify({"doc_id": doc_id, "run_id": run_id, "result": viewer_data})


@app.route("/api/documents/<doc_id>/runs/<run_id>")
def get_document_run(doc_id, run_id):
    run_dir = document_run_dir(doc_id, run_id)
    viewer_path = run_dir / "viewer_data.json"
    if not viewer_path.exists():
        return jsonify({"error": "run data not found"}), 404
    return jsonify(read_json(viewer_path, {}))


@app.route("/api/documents/<doc_id>/runs/<run_id>/page/<side>/<int:page_no>")
def document_page_image(doc_id, run_id, side, page_no):
    zoom = float(request.args.get("zoom", "1.6"))
    if side in ("new", "report", "current"):
        pdf_name = "report.pdf"
    elif side in ("old", "prev", "previous"):
        pdf_name = "prev_report.pdf"
    else:
        return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
    pdf_path = document_run_dir(doc_id, run_id) / pdf_name
    if not pdf_path.exists():
        return jsonify({"error": "pdf not found"}), 404
    return Response(render_pdf_page(pdf_path, page_no, zoom), mimetype="image/png")


@app.route("/api/documents/<doc_id>/runs/<run_id>/words/<side>")
def document_words(doc_id, run_id, side):
    if side in ("new", "report", "current"):
        name = "words.json"
    elif side in ("old", "prev", "previous"):
        name = "prev_words.json"
    else:
        return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
    p = document_run_dir(doc_id, run_id) / name
    if not p.exists():
        return jsonify({"error": "not found"}), 404
    return send_file(p, mimetype="application/json")


@app.route("/api/documents/<doc_id>/runs/<run_id>/chars/<side>")
def document_chars(doc_id, run_id, side):
    if side in ("new", "report", "current"):
        name = "chars.json"
    elif side in ("old", "prev", "previous"):
        name = "prev_chars.json"
    else:
        return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
    p = document_run_dir(doc_id, run_id) / name
    if not p.exists():
        return jsonify({"error": "not found"}), 404
    return send_file(p, mimetype="application/json")


@app.route("/api/documents/<doc_id>/runs/<run_id>/reviews", methods=["GET"])
def list_document_reviews(doc_id, run_id):
    reviews = document_reviews_for_run(doc_id, run_id)
    semantic.save_reviews(document_run_dir(doc_id, run_id), reviews)
    return jsonify(reviews)


@app.route("/api/documents/<doc_id>/runs/<run_id>/reviews", methods=["POST"])
def create_document_review(doc_id, run_id):
    run_dir = document_run_dir(doc_id, run_id)
    if not run_dir.exists():
        return jsonify({"error": "run not found"}), 404
    data = request.get_json(force=True) or {}
    rev = create_document_level_review(doc_id, run_id, run_dir, data)
    semantic.save_reviews(run_dir, document_reviews_for_run(doc_id, run_id))
    return jsonify(rev), (400 if rev.get("error") else 201)


@app.route("/api/documents/<doc_id>/runs/<run_id>/reviews/<review_id>", methods=["PATCH"])
def patch_document_review(doc_id, run_id, review_id):
    response = patch_document_level_review(doc_id, review_id)
    semantic.save_reviews(document_run_dir(doc_id, run_id), document_reviews_for_run(doc_id, run_id))
    return response


@app.route("/api/documents/<doc_id>/runs/<run_id>/reviews/<review_id>", methods=["DELETE"])
def remove_document_review(doc_id, run_id, review_id):
    response = delete_document_level_review(doc_id, review_id)
    semantic.save_reviews(document_run_dir(doc_id, run_id), document_reviews_for_run(doc_id, run_id))
    return response


@app.route("/api/documents/<doc_id>/runs/<run_id>/reviews/<review_id>/comments", methods=["POST"])
def add_document_review_comment(doc_id, run_id, review_id):
    response = add_document_level_comment(doc_id, review_id)
    semantic.save_reviews(document_run_dir(doc_id, run_id), document_reviews_for_run(doc_id, run_id))
    return response


@app.route("/api/documents/<doc_id>/runs/<run_id>/export/<side>")
def export_document_pdf(doc_id, run_id, side):
    if side in ("new", "report", "current"):
        compat_side = "new"
    elif side in ("old", "prev", "previous"):
        compat_side = "old"
    else:
        return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
    run_dir = document_run_dir(doc_id, run_id)
    reviews = document_reviews_for_run(doc_id, run_id)
    semantic.save_reviews(run_dir, reviews)
    if not reviews:
        return jsonify({"error": "No saved reviews"}), 400
    try:
        pdf_bytes = export_annotated_pdf(run_dir, compat_side)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    filename = f"reviewed_{side}_{run_id[:6]}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/api/documents/<doc_id>/workspace")
def document_workspace(doc_id):
    meta = load_document_meta(doc_id)
    if not meta:
        return jsonify({"error": "document not found"}), 404
    runs = []
    for run in reversed(meta.get("runs", [])[-5:]):
        run_dir = document_run_dir(doc_id, run["run_id"])
        reviews = document_reviews_for_run(doc_id, run["run_id"])
        runs.append({
            **run,
            "review_count": len(reviews),
            "open_reviews": sum(1 for r in reviews if r.get("status", "open") == "open"),
            "closed_reviews": sum(1 for r in reviews if r.get("status") in ("closed", "cleared", "resolved")),
            "has_diff": bool((run_dir / "result.json").exists()),
            "has_ai_assessment": bool((run_dir / "ai_assessment.json").exists()),
        })
    return jsonify({"doc_id": doc_id, "title": meta.get("title"), "runs": runs})


@app.route("/api/documents/<doc_id>/runs/<run_id>/assess", methods=["POST"])
def assess_document_run(doc_id, run_id):
    assessment, error = run_ai_assessment(doc_id, run_id)
    if error:
        message, status = error
        return jsonify({"error": message}), status
    return jsonify(assessment)


@app.route("/api/documents/<doc_id>/runs/<run_id>/assess", methods=["GET"])
def get_document_assessment(doc_id, run_id):
    meta = load_document_meta(doc_id)
    if not meta:
        return jsonify({"error": "document not found"}), 404
    if not run_meta_for(meta, run_id):
        return jsonify({"error": "run not found"}), 404
    return jsonify(load_ai_assessment(doc_id, run_id))


@app.route("/api/documents/<doc_id>/runs/<run_id>/assess/<review_id>", methods=["PATCH"])
def patch_document_assessment(doc_id, run_id, review_id):
    patch = request.get_json(force=True) or {}
    assessment = load_ai_assessment(doc_id, run_id)
    updated = None
    for item in assessment.get("items", []):
        if item.get("review_id") == review_id:
            for key in ("human_decision", "human_note"):
                if key in patch:
                    item[key] = patch[key]
            item["human_updated_at"] = utc_now()
            updated = item
            break
    if not updated:
        return jsonify({"error": "assessment item not found"}), 404
    save_ai_assessment(doc_id, run_id, assessment)
    return jsonify(updated)


@app.route("/api/documents/<doc_id>/runs/<run_id>/assess/<review_id>", methods=["POST"])
def assess_single_document_review(doc_id, run_id, review_id):
    assessment, error = run_ai_assessment(doc_id, run_id, review_id=review_id)
    if error:
        message, status = error
        return jsonify({"error": message}), status
    return jsonify(assessment)


@app.route("/api/documents/<doc_id>/runs/<run_id>/migrate", methods=["POST"])
def migrate_document_reviews(doc_id, run_id):
    return jsonify({"error": "review migration is planned for Phase 6"}), 501


@app.route("/api/jobs", methods=["POST"])
def create_job():
    if "old_pdf" not in request.files or "new_pdf" not in request.files:
        return jsonify({"error": "old_pdf and new_pdf are required"}), 400
    job_id = uuid.uuid4().hex[:12]
    run_dir = RUNS_DIR / job_id
    run_dir.mkdir(parents=True, exist_ok=True)
    old_pdf, new_pdf = run_dir / "old.pdf", run_dir / "new.pdf"
    request.files["old_pdf"].save(old_pdf)
    request.files["new_pdf"].save(new_pdf)
    JOBS[job_id] = {"status": "queued", "job_id": job_id}
    threading.Thread(target=process_job, args=(job_id, old_pdf, new_pdf), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/<job_id>")
def get_job(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "job not found", "job_id": job_id, "from_disk": False}), 404
    payload = {k: v for k, v in job.items() if k != "result"}
    payload["job_id"] = job_id
    payload["from_disk"] = False
    if job.get("status") == "done":
        payload["result"] = job["result"]
    return jsonify(payload)


@app.route("/api/jobs/<job_id>/page/<side>/<int:page_no>")
def page_image(job_id, side, page_no):
    zoom = float(request.args.get("zoom", "1.6"))
    if side not in ("old", "new"):
        return jsonify({"error": "side must be old or new"}), 400
    pdf_path = RUNS_DIR / job_id / ("old.pdf" if side == "old" else "new.pdf")
    if not pdf_path.exists():
        return jsonify({"error": "pdf not found"}), 404
    return Response(render_pdf_page(pdf_path, page_no, zoom), mimetype="image/png")


@app.route("/api/jobs/<job_id>/words/<side>")
def words(job_id, side):
    if side not in ("old", "new"):
        return jsonify({"error": "side must be old or new"}), 400
    p = _run_dir(job_id) / f"{side}_words.json"
    if not p.exists():
        return jsonify({"error": "not found"}), 404
    return send_file(p, mimetype="application/json")


@app.route("/api/jobs/<job_id>/reviews", methods=["GET"])
def list_reviews(job_id):
    return jsonify(semantic.load_reviews(_run_dir(job_id)))


@app.route("/api/jobs/<job_id>/reviews", methods=["POST"])
def create_review(job_id):
    rev = semantic.add_review(_run_dir(job_id), _semantic_map(job_id), request.get_json(force=True) or {})
    return jsonify(rev), (400 if rev.get("error") else 201)


@app.route("/api/jobs/<job_id>/reviews/<review_id>", methods=["PATCH"])
def patch_review(job_id, review_id):
    r = semantic.update_review(_run_dir(job_id), review_id, request.get_json(force=True) or {})
    return (jsonify(r), 200) if r else (jsonify({"error": "not found"}), 404)


@app.route("/api/jobs/<job_id>/reviews/<review_id>", methods=["DELETE"])
def remove_review(job_id, review_id):
    ok = semantic.delete_review(_run_dir(job_id), review_id)
    return (jsonify({"deleted": True}), 200) if ok else (jsonify({"error": "not found"}), 404)


@app.route("/api/jobs/<job_id>/export/<side>")
def export_pdf(job_id, side):
    """Download the old or new PDF with all reviews embedded as PDF annotations."""
    if side not in ("old", "new"):
        return jsonify({"error": "side must be old or new"}), 400
    run_dir = _run_dir(job_id)
    reviews = semantic.load_reviews(run_dir)
    if not reviews:
        return jsonify({"error": "No saved reviews"}), 400
    try:
        pdf_bytes = export_annotated_pdf(run_dir, side)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    filename = f"reviewed_{side}_{job_id[:6]}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.route("/api/jobs/<job_id>/reviews/<review_id>/bundle")
def review_bundle(job_id, review_id):
    b = semantic.build_ai_bundle(_run_dir(job_id), review_id)
    return (jsonify(b), 200) if b else (jsonify({"error": "not found"}), 404)


@app.route("/api/jobs/<job_id>/resolve", methods=["POST"])
def resolve(job_id):
    b = request.get_json(force=True) or {}
    chunk = semantic.resolve_word_ids(_semantic_map(job_id), b.get("side"), b.get("word_ids", []))
    return jsonify(semantic.chunk_summary(chunk))


@app.route("/api/jobs/<job_id>/download/<name>")
def download(job_id, name):
    allowed = {"result.json", "viewer_data.json", "old.md", "new.md", "old_words.json", "new_words.json"}
    if name not in allowed:
        return jsonify({"error": "not allowed"}), 400
    file_path = RUNS_DIR / job_id / name
    if not file_path.exists():
        return jsonify({"error": "file not found"}), 404
    return send_file(file_path, as_attachment=True)


def suppress_flask_console_noise():
    flask.cli.show_server_banner = lambda *args, **kwargs: None
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(logging.ERROR)
    werkzeug_logger.disabled = True


if __name__ == "__main__":
    suppress_flask_console_noise()
    print("PDF Diff Viewer running: http://127.0.0.1:8000", flush=True)
    app.run(host="127.0.0.1", port=8000, debug=True, use_reloader=False)
