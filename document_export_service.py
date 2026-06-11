import json
from pathlib import Path

import fitz

import run_layout


def export_annotated_pdf(run_dir: Path, side: str) -> bytes:
    """
    Embed reviews.json into the PDF as standard annotations.
    - Highlight reviewed words (open=yellow / cleared=gray)
    - Add a sticky note near the first word of each review.
    Returns raw PDF bytes.
    """
    import document_semantic as _sem

    reviews = _sem.load_reviews(run_dir)
    words_path = run_layout.words_path(run_dir, side)
    pdf_path = run_layout.source_pdf(run_dir, side)

    if not pdf_path.exists():
        raise FileNotFoundError(f"{run_layout.side_name(side)}/source.pdf not found")

    words = json.loads(words_path.read_text(encoding="utf-8")) if words_path.exists() else []
    with fitz.open(str(pdf_path)) as doc:
        for idx, rv in enumerate(reviews, start=1):
            word_ids = rv.get(f"{side}_word_ids") or []
            if not word_ids:
                continue

            comment = (rv.get("comment") or "").strip() or "(No comment)"
            status = rv.get("status", "open")
            color = [0.75, 0.75, 0.75] if status in ("cleared", "resolved") else [1.0, 0.84, 0.0]
            title = f"Review #{idx}  [{status}]"

            by_page = {}
            for wid in word_ids:
                if 0 <= wid < len(words):
                    w = words[wid]
                    by_page.setdefault(w["page"], []).append(w)

            first = True
            for page_no in sorted(by_page):
                page_words = by_page[page_no]
                page = doc[page_no - 1]

                rects = [fitz.Rect(w["bbox"]) for w in page_words]
                hl = page.add_highlight_annot(rects)
                hl.set_colors(stroke=color)
                hl.update()

                if first:
                    anchor = page_words[0]["bbox"]
                    pt = fitz.Point(anchor[2] + 4, anchor[1])
                    note = page.add_text_annot(pt, comment, icon="Note")
                    note.set_info(title=title, content=comment)
                    note.update()
                    first = False

        return doc.tobytes(garbage=4, deflate=True)
