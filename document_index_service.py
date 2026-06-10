import re
from difflib import SequenceMatcher
from pathlib import Path

from section_context_utils import read_md_lines, strip_section_markers
from text_utils import norm_token

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
    return [min(w["bbox"][0] for w in words), min(w["bbox"][1] for w in words), max(w["bbox"][2] for w in words), max(w["bbox"][3] for w in words)]


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
    s = re.sub(r"^\s*[-*]\s*", "", s)
    s = s.replace("**", " ").replace("__", " ").replace("_", " ").replace("`", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_title_for_index(title):
    title = clean_structural_text(title)
    title = re.sub(r"\s+", " ", title).strip()
    title = re.sub(r"[,.;:]+$", "", title).strip()
    return title


FS_SECTION_PATTERNS = [
    (re.compile(r"financial\s+position", re.I), "fs_position"),
    (re.compile(r"(?:comprehensive\s+)?income|profit\s+or\s+loss", re.I), "fs_income"),
    (re.compile(r"changes\s+in\s+equity", re.I), "fs_equity"),
    (re.compile(r"cash\s+flows?", re.I), "fs_cashflow"),
]


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
        found.append({"section_id": section_id, "item": item, "start_idx": start_idx})

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
            visual_lines.append({"page": page, "block": -1, "line": idx, "cy": line["cy"], "words": items, "text": txt, "bbox": bbox})
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
        if norms[i : i + n] == needle_tokens:
            matched = words[i : i + n]
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
        line_title = normalize_title_for_index(line_text(words[note_pos + 1 :]))
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
                items.append({"type": "fs", "label": fs, "title": fs, "page": ln["page"], "bbox": ln["bbox"], "anchor_text": ln["text"], "source": "pdf_visual_line"})
    return items


def build_new_pdf_index(new_md_path: Path, new_words, page_count):
    visual_lines = group_pdf_visual_lines(new_words)
    notes_start_page = detect_notes_start_page(new_md_path, visual_lines)
    items = []
    cover_line = first_line_on_page(visual_lines, 1)
    if page_count >= 1:
        items.append({"type": "cover", "label": "Cover", "title": "Cover", "page": 1, "bbox": cover_line["bbox"] if cover_line else [0, 0, 1, 1], "anchor_text": cover_line["text"] if cover_line else "Cover"})
    contents_line = first_line_on_page(visual_lines, 2)
    if page_count >= 2:
        items.append({"type": "contents", "label": "Contents", "title": "Contents", "page": 2, "bbox": contents_line["bbox"] if contents_line else [0, 0, 1, 1], "anchor_text": contents_line["text"] if contents_line else "Contents"})
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
        items.append(
            {
                "type": "note",
                "note_no": note_no,
                "label": f"Note {note_no}. {title}",
                "title": title,
                "page": anchor["page"],
                "bbox": anchor["bbox"],
                "anchor_text": anchor["anchor_text"],
                "source": cand.get("source", "md_first"),
            }
        )
    items.sort(key=lambda x: (x.get("page", 9999), x.get("bbox", [0, 0, 0, 0])[1], x.get("bbox", [0, 0, 0, 0])[0]))
    return items
