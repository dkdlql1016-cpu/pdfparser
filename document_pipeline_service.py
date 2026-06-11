from pathlib import Path

import fitz


def copy_if_exists(src: Path, dst: Path):
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy2(src, dst)


def write_unmarked_md_copy(src: Path, dst: Path, *, strip_section_markers_fn, read_md_lines_fn):
    dst.write_text("\n".join(strip_section_markers_fn(read_md_lines_fn(src))) + "\n", encoding="utf-8")
    return dst


def build_chars_from_words(words):
    chars = []
    for word in words or []:
        text = str(word.get("text", ""))
        if not text:
            continue
        x0, y0, x1, y1 = word.get("bbox", [0, 0, 0, 0])
        width = max(0.1, x1 - x0)
        step = width / max(1, len(text))
        for i, ch in enumerate(text):
            chars.append({
                "idx": len(chars),
                "char": ch,
                "word_id": word.get("idx"),
                "char_index": i,
                "page": word.get("page"),
                "block": word.get("block"),
                "line": word.get("line"),
                "word_no": word.get("word_no"),
                "bbox": [x0 + step * i, y0, x0 + step * (i + 1), y1],
                "order": len(chars),
            })
    return chars


def process_single_document_run(
    run_dir: Path,
    pdf_path: Path,
    *,
    extract_pdf_words_fn,
    write_json_fn,
    run_opendataloader_to_markdown_fn,
    build_new_pdf_index_fn,
    inject_section_markers_fn,
    doc_id=None,
    run_id=None,
    filename=None,
):
    words, page_sizes = extract_pdf_words_fn(pdf_path)
    write_json_fn(run_dir / "words.json", words)

    md_src = run_opendataloader_to_markdown_fn(pdf_path, run_dir / "opendataloader_report")
    report_md = run_dir / "report.md"
    report_md.write_text(md_src.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

    index_items = build_new_pdf_index_fn(report_md, words, len(page_sizes))
    section_map = inject_section_markers_fn(report_md, index_items)
    write_json_fn(run_dir / "section_map.json", section_map)

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
    write_json_fn(run_dir / "viewer_data.json", viewer_data)
    return viewer_data


def process_document_diff_run(
    run_dir: Path,
    *,
    read_json_fn,
    write_json_fn,
    write_unmarked_md_copy_fn,
    run_diff_extract_fn,
    suppress_layout_moves_fn,
    make_highlights_and_changes_fn,
    merge_highlight_rects_server_fn,
    alignment_report_fn,
    build_new_pdf_index_fn,
    inject_section_markers_fn,
    map_result_segments_to_pdf_indices_fn,
    document_reviews_for_run_fn,
    semantic_module,
    doc_id=None,
    run_id=None,
):
    prev_pdf = run_dir / "prev_report.pdf"
    report_pdf = run_dir / "report.pdf"
    prev_md = run_dir / "prev_report.md"
    report_md = run_dir / "report.md"
    if not prev_pdf.exists() or not report_pdf.exists():
        raise FileNotFoundError("prev_report.pdf and report.pdf are required before diff")
    if not prev_md.exists() or not report_md.exists():
        raise FileNotFoundError("prev_report.md and report.md are required before diff")

    prev_words = read_json_fn(run_dir / "prev_words.json", [])
    report_words = read_json_fn(run_dir / "words.json", [])
    with fitz.open(str(prev_pdf)) as prev_page_sizes:
        old_page_sizes = [{"width": float(p.rect.width), "height": float(p.rect.height)} for p in prev_page_sizes]
    with fitz.open(str(report_pdf)) as report_page_sizes:
        new_page_sizes = [{"width": float(p.rect.width), "height": float(p.rect.height)} for p in report_page_sizes]

    result_path = run_dir / "result.json"
    diff_prev_md = write_unmarked_md_copy_fn(prev_md, run_dir / "prev_report.diff.md")
    diff_report_md = write_unmarked_md_copy_fn(report_md, run_dir / "report.diff.md")
    diff_doc = run_diff_extract_fn(diff_prev_md, diff_report_md, result_path)
    diff_doc["segments"], suppressed_moves = suppress_layout_moves_fn(diff_doc.get("segments", []))
    diff_doc["suppressed_moves"] = suppressed_moves
    diff_doc["summary"] = {
        "equal": sum(1 for s in diff_doc["segments"] if s.get("type") == "equal" and not s.get("suppressed")),
        "deleted": sum(1 for s in diff_doc["segments"] if s.get("type") == "delete" and not s.get("suppressed")),
        "added": sum(1 for s in diff_doc["segments"] if s.get("type") == "add" and not s.get("suppressed")),
        "suppressed": sum(1 for s in diff_doc["segments"] if s.get("suppressed")),
    }
    write_json_fn(result_path, diff_doc)

    old_highlights, new_highlights, changes = make_highlights_and_changes_fn(diff_doc["segments"], prev_words, report_words)
    old_highlights = merge_highlight_rects_server_fn(old_highlights)
    new_highlights = merge_highlight_rects_server_fn(new_highlights)
    report = alignment_report_fn(diff_doc["segments"], prev_words, report_words)
    index_items = build_new_pdf_index_fn(report_md, report_words, len(new_page_sizes))
    section_map = inject_section_markers_fn(report_md, index_items)
    write_json_fn(run_dir / "section_map.json", section_map)

    mapped = map_result_segments_to_pdf_indices_fn(diff_doc["segments"], prev_words, report_words)
    semantic_map = semantic_module.build_semantic_map(diff_doc["segments"], mapped, prev_words, report_words)
    semantic_module.attach_index_old_side(index_items, semantic_map, prev_words, report_words)
    projected_reviews = document_reviews_for_run_fn(doc_id, run_id) if doc_id else []

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
    write_json_fn(run_dir / "viewer_data.json", viewer_data)
    return viewer_data
