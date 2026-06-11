import re

from flask import Blueprint, Response, jsonify, request, send_file

_HEX12_RE = re.compile(r'^[0-9a-f]{12}$')


def _is_valid_hex_id(value: str) -> bool:
    return bool(_HEX12_RE.match(value))


def create_snapshot_blueprint(*, deps):
    load_document_meta_fn = deps["load_document_meta_fn"]
    snapshot_limit = deps["snapshot_limit"]
    create_run_snapshot_fn = deps["create_run_snapshot_fn"]
    document_snapshot_dir_fn = deps["document_snapshot_dir_fn"]
    read_json_fn = deps["read_json_fn"]
    build_chars_from_words_fn = deps["build_chars_from_words_fn"]
    snapshot_side_file_fn = deps["snapshot_side_file_fn"]
    render_pdf_page_fn = deps["render_pdf_page_fn"]
    export_annotated_pdf_fn = deps["export_annotated_pdf_fn"]
    bp = Blueprint("snapshots", __name__)

    @bp.route("/api/documents/<doc_id>/snapshots")
    def list_document_snapshots(doc_id):
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        snapshots = sorted(meta.get("snapshots", []), key=lambda s: s.get("created_at", ""), reverse=True)
        return jsonify({"doc_id": doc_id, "snapshots": snapshots[:snapshot_limit]})

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/snapshots", methods=["POST"])
    def save_document_run_snapshot(doc_id, run_id):
        data = request.get_json(silent=True) or {}
        payload, status = create_run_snapshot_fn(doc_id, run_id, label=data.get("label", ""))
        return jsonify(payload), status

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>")
    def get_document_snapshot(doc_id, snapshot_id):
        snap_dir = document_snapshot_dir_fn(doc_id, snapshot_id)
        if not snap_dir.exists():
            return jsonify({"error": "snapshot not found"}), 404
        viewer_data = read_json_fn(snap_dir / "viewer_data.json", None)
        if not viewer_data:
            return jsonify({"error": "snapshot data not found"}), 404
        return jsonify({"snapshot": read_json_fn(snap_dir / "snapshot.json", {}), "result": viewer_data})

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>/page/<side>/<int:page_no>")
    def snapshot_page_image(doc_id, snapshot_id, side, page_no):
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
        if not _is_valid_hex_id(doc_id) or not _is_valid_hex_id(snapshot_id):
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
        if not _is_valid_hex_id(doc_id) or not _is_valid_hex_id(snapshot_id):
            return jsonify({"error": "invalid id"}), 400
        words_name = snapshot_side_file_fn(side, "words")
        if not words_name:
            return jsonify({"error": "side must be report/new/current or prev/old/previous"}), 400
        words_path = document_snapshot_dir_fn(doc_id, snapshot_id) / words_name
        if not words_path.exists():
            return jsonify({"error": "not found"}), 404
        words = read_json_fn(words_path, [])
        return jsonify(build_chars_from_words_fn(words))

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>/reviews")
    def snapshot_reviews(doc_id, snapshot_id):
        return jsonify(read_json_fn(document_snapshot_dir_fn(doc_id, snapshot_id) / "reviews.json", []) or [])

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>/assess")
    def snapshot_assessment(doc_id, snapshot_id):
        return jsonify(read_json_fn(document_snapshot_dir_fn(doc_id, snapshot_id) / "ai_assessment.json", {"items": []}) or {"items": []})

    @bp.route("/api/documents/<doc_id>/snapshots/<snapshot_id>/export/<side>")
    def export_snapshot_pdf(doc_id, snapshot_id, side):
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
