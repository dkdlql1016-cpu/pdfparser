import shutil

from flask import Blueprint, jsonify, request


def create_file_manager_blueprint(*, deps):
    ensure_default_file_manager_documents_fn = deps["ensure_default_file_manager_documents_fn"]
    list_documents_for_manager_fn = deps["list_documents_for_manager_fn"]
    load_document_meta_fn = deps["load_document_meta_fn"]
    normalize_document_title_fn = deps["normalize_document_title_fn"]
    document_title_exists_fn = deps["document_title_exists_fn"]
    save_document_meta_fn = deps["save_document_meta_fn"]
    document_dir_fn = deps["document_dir_fn"]
    bp = Blueprint("file_manager", __name__)

    @bp.route("/api/file-manager")
    def file_manager_index():
        ensure_default_file_manager_documents_fn()
        return jsonify({"items": list_documents_for_manager_fn()})

    @bp.route("/api/file-manager/<doc_id>", methods=["PATCH"])
    def file_manager_rename(doc_id):
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        data = request.get_json(silent=True) or {}
        title = str(data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        title = normalize_document_title_fn(title[:120])
        if document_title_exists_fn(title, exclude_doc_id=doc_id):
            return jsonify({"error": "duplicate title"}), 409
        meta["title"] = title
        save_document_meta_fn(meta)
        return jsonify({"renamed": True, "doc_id": doc_id, "title": meta.get("title")})

    @bp.route("/api/file-manager/<doc_id>", methods=["DELETE"])
    def file_manager_delete(doc_id):
        path = document_dir_fn(doc_id)
        if not path.exists():
            return jsonify({"error": "document not found"}), 404
        shutil.rmtree(path, ignore_errors=True)
        return jsonify({"deleted": True, "doc_id": doc_id})

    return bp
