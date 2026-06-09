import json
import logging
import re
import subprocess
import sys
import threading
import uuid
import semantic
from pathlib import Path
from difflib import SequenceMatcher

import fitz
import flask.cli
from flask import Flask, Response, jsonify, request, send_file

BASE_DIR = Path(__file__).resolve().parent
RUNS_DIR = BASE_DIR / "runs"
RUNS_DIR.mkdir(exist_ok=True)

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
    - 변경없는 단어 위에 하이라이트 (open=노란색 / resolved=회색)
    - 각 리뷰의 첫 단어 옆에 sticky note (코멘트 내용 포함)
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

        comment = (rv.get("comment") or "").strip() or "(코멘트 없음)"
        status  = rv.get("status", "open")
        color   = [1.0, 0.84, 0.0] if status != "resolved" else [0.75, 0.75, 0.75]
        title   = f"Review #{idx}  [{status}]"

        # 페이지별 단어 묶기
        by_page: dict = {}
        for wid in word_ids:
            if 0 <= wid < len(words):
                w = words[wid]
                by_page.setdefault(w["page"], []).append(w)

        first = True
        for page_no in sorted(by_page):
            page_words = by_page[page_no]
            page = doc[page_no - 1]

            # 하이라이트 (단어 전체를 한 annotation으로)
            rects = [fitz.Rect(w["bbox"]) for w in page_words]
            hl = page.add_highlight_annot(rects)
            hl.set_colors(stroke=color)
            hl.set_info(title=title, content=comment)
            hl.update()

            # sticky note는 첫 페이지의 첫 단어 오른쪽에만
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
        return jsonify({"error": "저장된 리뷰가 없습니다"}), 400
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
