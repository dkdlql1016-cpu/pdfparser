import re
import shutil

from flask import Blueprint, current_app, jsonify, request

_HEX12_RE = re.compile(r"^[0-9a-f]{12}$")


def _is_valid_hex_id(value: str) -> bool:
    return bool(_HEX12_RE.match(value or ""))


def _ids_payload(workspace_id: str):
    value = str(workspace_id or "")
    return {"workspace_id": value}


def create_file_manager_blueprint(*, deps):
    list_documents_for_manager_fn = deps["list_documents_for_manager_fn"]
    ensure_default_workspace_fn = deps["ensure_default_workspace_fn"]
    load_document_meta_fn = deps["load_document_meta_fn"]
    normalize_document_title_fn = deps["normalize_document_title_fn"]
    document_title_exists_fn = deps["document_title_exists_fn"]
    save_document_meta_fn = deps["save_document_meta_fn"]
    document_dir_fn = deps["document_dir_fn"]
    utc_now_fn = deps.get("utc_now_fn")
    bp = Blueprint("file_manager", __name__)

    @bp.route("/api/file-manager")
    def file_manager_index():
        return jsonify({"items": list_documents_for_manager_fn()})

    @bp.route("/api/workspace/default", methods=["GET"])
    def workspace_default():
        return jsonify(ensure_default_workspace_fn())

    @bp.route("/api/file-manager/<doc_id>", methods=["PATCH"])
    def file_manager_rename(doc_id):
        if not _is_valid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        data = request.get_json(silent=True) or {}
        title = str(data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        title = normalize_document_title_fn(title[:120])
        if document_title_exists_fn(title, exclude_workspace_id=doc_id):
            return jsonify({"error": "duplicate title"}), 409
        meta["title"] = title
        if utc_now_fn:
            meta["updated_at"] = utc_now_fn()
        save_document_meta_fn(meta)
        return jsonify({"renamed": True, **_ids_payload(doc_id), "title": meta.get("title")})

    @bp.route("/api/file-manager/<doc_id>", methods=["DELETE"])
    def file_manager_delete(doc_id):
        if not _is_valid_hex_id(doc_id):
            return jsonify({"error": "invalid id"}), 400
        path = document_dir_fn(doc_id)
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return jsonify({"error": "document not found"}), 404
        except PermissionError:
            return jsonify({"error": "document is locked or access is denied"}), 423
        except OSError as e:
            current_app.logger.exception("file-manager delete failed: workspace_id=%s path=%s error=%s", doc_id, path, e)
            return jsonify({"error": "delete failed due to filesystem error"}), 500
        return jsonify({"deleted": True, **_ids_payload(doc_id)})

    return bp
