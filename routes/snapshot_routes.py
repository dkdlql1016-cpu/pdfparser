import re
import time

from flask import Blueprint, Response, current_app, jsonify, request, send_file

_HEX12_RE = re.compile(r'^[0-9a-f]{12}$')
_SNAPSHOT_ID_RE = re.compile(r'^snap-[0-9a-f]{10}$')


def _is_valid_hex_id(value: str) -> bool:
    return bool(_HEX12_RE.match(value))


def _is_valid_snapshot_id(value: str) -> bool:
    return bool(_SNAPSHOT_ID_RE.match(value))


def _parse_optional_positive_int(name: str):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None, None
    try:
        parsed = int(raw)
    except ValueError:
        return None, f"{name} must be an integer"
    if parsed < 1:
        return None, f"{name} must be >= 1"
    return parsed, None


def _chars_page_filters_from_query():
    page, page_err = _parse_optional_positive_int("page")
    if page_err:
        return None, page_err
    page_start, start_err = _parse_optional_positive_int("page_start")
    if start_err:
        return None, start_err
    page_end, end_err = _parse_optional_positive_int("page_end")
    if end_err:
        return None, end_err
    if page is not None and (page_start is not None or page_end is not None):
        return None, "page cannot be combined with page_start/page_end"
    if page_start is not None and page_end is not None and page_start > page_end:
        return None, "page_start must be <= page_end"
    return {"page": page, "page_start": page_start, "page_end": page_end}, None


def create_snapshot_blueprint(*, deps):
    load_document_meta_fn = deps["load_document_meta_fn"]
    snapshot_limit = deps["snapshot_limit"]
    create_run_snapshot_fn = deps["create_run_snapshot_fn"]
    document_snapshot_dir_fn = deps["document_snapshot_dir_fn"]
    read_json_fn = deps["read_json_fn"]
    build_chars_from_words_fn = deps["build_chars_from_words_fn"]
    filter_words_for_chars_fn = deps.get("filter_words_for_chars_fn", lambda words, **_kwargs: list(words or []))
    estimated_char_count_fn = deps.get("estimated_char_count_fn", lambda words: sum(len(str(w.get("text", ""))) for w in (words or [])))
    chars_max_words = int(deps.get("chars_max_words", 50000))
    chars_max_estimated_count = int(deps.get("chars_max_estimated_count", 250000))
    snapshot_side_file_fn = deps["snapshot_side_file_fn"]
    render_pdf_page_fn = deps["render_pdf_page_fn"]
    export_annotated_pdf_fn = deps["export_annotated_pdf_fn"]
    bp = Blueprint("snapshots", __name__)

    @bp.route("/api/documents/<doc_id>/snapshots")
    def list_document_snapshots(doc_id):
        if not _is_valid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        snapshots = sorted(meta.get("snapshots", []), key=lambda s: s.get("created_at", ""), reverse=True)
        return jsonify({"doc_id": doc_id, "snapshots": snapshots[:snapshot_limit]})

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/snapshots", methods=["POST"])
    def save_document_run_snapshot(doc_id, run_id):
        if not _is_valid_hex_id(doc_id) or not _is_valid_hex_id(run_id):
            return jsonify({"error": "invalid id"}), 400
        data = request.get_json(silent=True) or {}
        payload, status = create_run_snapshot_fn(doc_id, run_id, label=data.get("label", ""))
        return jsonify(payload), status

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>")
    def get_document_snapshot(doc_id, snapshot_id):
        if not _is_valid_hex_id(doc_id) or not _is_valid_snapshot_id(snapshot_id):
            return jsonify({"error": "invalid id"}), 400
        snap_dir = document_snapshot_dir_fn(doc_id, snapshot_id)
        if not snap_dir.exists():
            return jsonify({"error": "snapshot not found"}), 404
        viewer_data = read_json_fn(snap_dir / "viewer_data.json", None)
        if not viewer_data:
            return jsonify({"error": "snapshot data not found"}), 404
        return jsonify({"snapshot": read_json_fn(snap_dir / "snapshot.json", {}), "result": viewer_data})

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>/page/<side>/<int:page_no>")
    def snapshot_page_image(doc_id, snapshot_id, side, page_no):
        if not _is_valid_hex_id(doc_id) or not _is_valid_snapshot_id(snapshot_id):
            return jsonify({"error": "invalid id"}), 400
        zoom = float(request.args.get("zoom", "1.6"))
        name = snapshot_side_file_fn(side, "pdf")
        if not name:
            return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
        pdf_path = document_snapshot_dir_fn(doc_id, snapshot_id) / name
        if not pdf_path.exists():
            return jsonify({"error": "pdf not found"}), 404
        return Response(render_pdf_page_fn(pdf_path, page_no, zoom), mimetype="image/png")

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>/words/<side>")
    def snapshot_words(doc_id, snapshot_id, side):
        if not _is_valid_hex_id(doc_id) or not _is_valid_snapshot_id(snapshot_id):
            return jsonify({"error": "invalid id"}), 400
        name = snapshot_side_file_fn(side, "words")
        if not name:
            return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
        path = document_snapshot_dir_fn(doc_id, snapshot_id) / name
        if not path.exists():
            return jsonify({"error": "not found"}), 404
        return send_file(path, mimetype="application/json")

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>/chars/<side>")
    def snapshot_chars(doc_id, snapshot_id, side):
        if not _is_valid_hex_id(doc_id) or not _is_valid_snapshot_id(snapshot_id):
            return jsonify({"error": "invalid id"}), 400
        words_name = snapshot_side_file_fn(side, "words")
        if not words_name:
            return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
        words_path = document_snapshot_dir_fn(doc_id, snapshot_id) / words_name
        if not words_path.exists():
            return jsonify({"error": "not found"}), 404
        page_filters, filter_error = _chars_page_filters_from_query()
        if filter_error:
            return jsonify({"error": filter_error}), 400
        words = read_json_fn(words_path, []) or []
        scoped_words = filter_words_for_chars_fn(
            words,
            page=page_filters["page"],
            page_start=page_filters["page_start"],
            page_end=page_filters["page_end"],
        )
        if len(scoped_words) > chars_max_words:
            current_app.logger.warning(
                "snapshot chars request rejected (word limit): doc_id=%s snapshot_id=%s side=%s words=%s max_words=%s page=%s page_start=%s page_end=%s",
                doc_id,
                snapshot_id,
                side,
                len(scoped_words),
                chars_max_words,
                page_filters["page"],
                page_filters["page_start"],
                page_filters["page_end"],
            )
            return jsonify(
                {
                    "error": "chars payload too large",
                    "reason": "word_limit_exceeded",
                    "max_words": chars_max_words,
                    "word_count": len(scoped_words),
                }
            ), 413
        estimated_chars = estimated_char_count_fn(scoped_words)
        if estimated_chars > chars_max_estimated_count:
            current_app.logger.warning(
                "snapshot chars request rejected (char estimate): doc_id=%s snapshot_id=%s side=%s estimated_chars=%s max_chars=%s page=%s page_start=%s page_end=%s",
                doc_id,
                snapshot_id,
                side,
                estimated_chars,
                chars_max_estimated_count,
                page_filters["page"],
                page_filters["page_start"],
                page_filters["page_end"],
            )
            return jsonify(
                {
                    "error": "chars payload too large",
                    "reason": "char_limit_exceeded",
                    "max_estimated_chars": chars_max_estimated_count,
                    "estimated_chars": estimated_chars,
                }
            ), 413
        started = time.perf_counter()
        chars = build_chars_from_words_fn(scoped_words)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        current_app.logger.info(
            "snapshot chars built: doc_id=%s snapshot_id=%s side=%s words=%s chars=%s elapsed_ms=%s page=%s page_start=%s page_end=%s",
            doc_id,
            snapshot_id,
            side,
            len(scoped_words),
            len(chars),
            elapsed_ms,
            page_filters["page"],
            page_filters["page_start"],
            page_filters["page_end"],
        )
        return jsonify(chars)

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>/reviews")
    def snapshot_reviews(doc_id, snapshot_id):
        if not _is_valid_hex_id(doc_id) or not _is_valid_snapshot_id(snapshot_id):
            return jsonify({"error": "invalid id"}), 400
        return jsonify(read_json_fn(document_snapshot_dir_fn(doc_id, snapshot_id) / "reviews.json", []) or [])

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>/assess")
    def snapshot_assessment(doc_id, snapshot_id):
        if not _is_valid_hex_id(doc_id) or not _is_valid_snapshot_id(snapshot_id):
            return jsonify({"error": "invalid id"}), 400
        return jsonify(read_json_fn(document_snapshot_dir_fn(doc_id, snapshot_id) / "ai_assessment.json", {"items": []}) or {"items": []})

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>/export/<side>")
    def export_snapshot_pdf(doc_id, snapshot_id, side):
        if not _is_valid_hex_id(doc_id) or not _is_valid_snapshot_id(snapshot_id):
            return jsonify({"error": "invalid id"}), 400
        compat_side = "new" if side in ("new", "report", "current") else "old"
        snap_dir = document_snapshot_dir_fn(doc_id, snapshot_id)
        if not snap_dir.exists():
            return jsonify({"error": "snapshot not found"}), 404
        try:
            pdf_bytes = export_annotated_pdf_fn(snap_dir, compat_side)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        filename = f"snapshot_{side}_{snapshot_id}.pdf"
        return Response(pdf_bytes, mimetype="application/pdf", headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'})

    return bp
