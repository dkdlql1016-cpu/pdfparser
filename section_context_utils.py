import re
from pathlib import Path

from assessment_normalizers import anchor_section_id
from text_utils import norm_space

SECTION_MARKER_RE = re.compile(r"^\s*<!--\s*SECTION_(?:START|END):[^>]+-->\s*$", re.I)


def read_md_lines(md_path: Path):
    return Path(md_path).read_text(encoding="utf-8", errors="replace").splitlines()


def strip_section_markers(lines):
    return [line for line in lines if not SECTION_MARKER_RE.match(str(line or "").strip())]


def section_entries(section_map):
    raw = (section_map or {}).get("sections", section_map or {})
    if isinstance(raw, dict):
        return [{"section_id": sid, **(meta or {})} for sid, meta in raw.items()]
    return raw if isinstance(raw, list) else []


def section_context_from_markdown(md_path: Path, section_map, section_id=None, fallback_text="", context_chars=9000):
    if not md_path.exists():
        return ""
    lines = read_md_lines(md_path)
    chosen = None
    if section_id:
        chosen = next((s for s in section_entries(section_map) if s.get("section_id") == section_id), None)
    if chosen:
        start = max(0, int(chosen.get("start_line", 1)) - 1)
        end = min(len(lines), int(chosen.get("end_line", len(lines))))
        text = "\n".join(strip_section_markers(lines[start:end])).strip()
    else:
        raw = "\n".join(strip_section_markers(lines))
        needle = norm_space(fallback_text)
        idx = norm_space(raw).find(needle[:80]) if needle else -1
        if idx >= 0:
            start = max(0, idx - context_chars // 3)
            text = raw[start:start + context_chars].strip()
        else:
            text = raw[:context_chars].strip()
    return text[:context_chars]


def available_assessment_sections(prev_section_map, current_section_map):
    rows = []
    for file_key, section_map in (("previous", prev_section_map), ("current", current_section_map)):
        for section in section_entries(section_map):
            rows.append({
                "file": file_key,
                "section_id": section.get("section_id"),
                "title": section.get("title", ""),
                "type": section.get("type", ""),
                "note_no": section.get("note_no"),
                "page_start": section.get("page_start"),
                "page_end": section.get("page_end"),
            })
    return rows


def infer_section_id_for_anchor(section_map, anchor, md_path=None, context_chars=9000):
    explicit = anchor_section_id(anchor)
    if explicit:
        return explicit
    page = anchor.get("page")
    text = norm_space(anchor.get("text", ""))
    candidates = []
    for section in section_entries(section_map):
        start = section.get("page_start")
        end = section.get("page_end") or start
        if page and start and end and int(start) <= int(page) <= int(end):
            candidates.append(section)
    if not candidates:
        return None
    if text and md_path and md_path.exists():
        needle = text[:120].lower()
        for section in candidates:
            body = norm_space(
                section_context_from_markdown(
                    md_path,
                    section_map,
                    section.get("section_id"),
                    context_chars=context_chars,
                )
            ).lower()
            if needle and needle in body:
                return section.get("section_id")
    return candidates[0].get("section_id")


def search_markdown_context(md_path: Path, query, limit=4):
    if not md_path.exists() or not query:
        return []
    lines = strip_section_markers(read_md_lines(md_path))
    query_norm = norm_space(query).lower()
    hits = []
    for idx, line in enumerate(lines):
        if query_norm and query_norm in norm_space(line).lower():
            start = max(0, idx - 8)
            end = min(len(lines), idx + 9)
            hits.append({
                "line": idx + 1,
                "context": "\n".join(lines[start:end])[:5000],
            })
            if len(hits) >= limit:
                break
    return hits


def keyword_search_markdown(
    md_path: Path,
    section_map,
    keyword,
    *,
    case_sensitive=False,
    whole_word=True,
    max_hits=200,
):
    if not md_path.exists() or not keyword:
        return {
            "keyword": str(keyword or ""),
            "total_count": 0,
            "truncated": False,
            "matches": [],
            "section_counts": {},
        }
    lines = strip_section_markers(read_md_lines(md_path))
    max_hits = max(1, min(int(max_hits or 200), 1000))
    escaped = re.escape(str(keyword))
    if whole_word:
        pattern = rf"(?<!\w){escaped}(?!\w)"
    else:
        pattern = escaped
    flags = 0 if case_sensitive else re.IGNORECASE
    regex = re.compile(pattern, flags)

    matches = []
    total_count = 0
    section_counts = {}
    sections = section_entries(section_map)

    for idx, line in enumerate(lines, start=1):
        line_text = str(line or "")
        for m in regex.finditer(line_text):
            total_count += 1
            section_id = None
            for section in sections:
                start = int(section.get("start_line") or 1)
                end = int(section.get("end_line") or len(lines))
                if start <= idx <= end:
                    section_id = section.get("section_id")
                    break
            if section_id:
                section_counts[section_id] = section_counts.get(section_id, 0) + 1
            if len(matches) < max_hits:
                matches.append(
                    {
                        "line": idx,
                        "column_start": m.start() + 1,
                        "column_end": m.end(),
                        "match_text": m.group(0),
                        "section_id": section_id,
                        "context": line_text[:500],
                    }
                )

    return {
        "keyword": str(keyword),
        "total_count": total_count,
        "truncated": total_count > len(matches),
        "matches": matches,
        "section_counts": section_counts,
    }

