from flask import Blueprint, jsonify, request


def create_assessment_blueprint(*, deps):
    run_ai_assessment_fn = deps["run_ai_assessment_fn"]
    run_change_ai_assessment_fn = deps["run_change_ai_assessment_fn"]
    load_document_meta_fn = deps["load_document_meta_fn"]
    run_meta_for_fn = deps["run_meta_for_fn"]
    load_change_ai_assessment_fn = deps["load_change_ai_assessment_fn"]
    build_change_groups_for_run_fn = deps["build_change_groups_for_run_fn"]
    forward_change_assessment_to_review_fn = deps["forward_change_assessment_to_review_fn"]
    load_ai_assessment_fn = deps["load_ai_assessment_fn"]
    utc_now_fn = deps["utc_now_fn"]
    save_ai_assessment_fn = deps["save_ai_assessment_fn"]
    migrate_previous_review_to_current_fn = deps["migrate_previous_review_to_current_fn"]
    bp = Blueprint("assessment", __name__)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/assess", methods=["POST"])
    def assess_document_run(doc_id, run_id):
        data = request.get_json(silent=True) or {}
        review_ids = data.get("review_ids")
        if review_ids is not None and not isinstance(review_ids, list):
            return jsonify({"error": "review_ids must be a list"}), 400
        skip_existing = bool(data.get("skip_existing", True))
        assessment, error = run_ai_assessment_fn(doc_id, run_id, review_ids=review_ids, skip_existing=skip_existing)
        if error:
            message, status = error
            return jsonify({"error": message}), status
        return jsonify(assessment)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/change-assess", methods=["POST"])
    def assess_document_changes(doc_id, run_id):
        data = request.get_json(silent=True) or {}
        change_ids = data.get("change_ids")
        if change_ids is not None and not isinstance(change_ids, list):
            return jsonify({"error": "change_ids must be a list"}), 400
        assessment, error = run_change_ai_assessment_fn(doc_id, run_id, change_ids=change_ids)
        if error:
            message, status = error
            return jsonify({"error": message}), status
        return jsonify(assessment)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/change-assess", methods=["GET"])
    def get_document_change_assessment(doc_id, run_id):
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        if not run_meta_for_fn(meta, run_id):
            return jsonify({"error": "run not found"}), 404
        return jsonify(load_change_ai_assessment_fn(doc_id, run_id))

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/change-assess/groups", methods=["POST"])
    def get_document_change_assessment_groups(doc_id, run_id):
        data = request.get_json(silent=True) or {}
        change_ids = data.get("change_ids")
        if change_ids is not None and not isinstance(change_ids, list):
            return jsonify({"error": "change_ids must be a list"}), 400
        groups = build_change_groups_for_run_fn(doc_id, run_id, change_ids=change_ids)
        return jsonify({"groups": groups})

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/change-assess/<int:change_id>", methods=["POST"])
    def assess_single_document_change(doc_id, run_id, change_id):
        assessment, error = run_change_ai_assessment_fn(doc_id, run_id, change_id=change_id)
        if error:
            message, status = error
            return jsonify({"error": message}), status
        return jsonify(assessment)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/change-assess/<int:change_id>/forward", methods=["POST"])
    def forward_document_change_assessment(doc_id, run_id, change_id):
        payload, status = forward_change_assessment_to_review_fn(doc_id, run_id, change_id)
        return jsonify(payload), status

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/assess", methods=["GET"])
    def get_document_assessment(doc_id, run_id):
        meta = load_document_meta_fn(doc_id)
        if not meta:
            return jsonify({"error": "document not found"}), 404
        if not run_meta_for_fn(meta, run_id):
            return jsonify({"error": "run not found"}), 404
        return jsonify(load_ai_assessment_fn(doc_id, run_id))

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/assess/<review_id>", methods=["PATCH"])
    def patch_document_assessment(doc_id, run_id, review_id):
        patch = request.get_json(force=True) or {}
        assessment = load_ai_assessment_fn(doc_id, run_id)
        updated = None
        for item in assessment.get("items", []):
            if item.get("review_id") == review_id:
                for key in ("human_decision", "human_note"):
                    if key in patch:
                        item[key] = patch[key]
                item["human_updated_at"] = utc_now_fn()
                updated = item
                break
        if not updated:
            return jsonify({"error": "assessment item not found"}), 404
        save_ai_assessment_fn(doc_id, run_id, assessment)
        return jsonify(updated)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/assess/<review_id>", methods=["POST"])
    def assess_single_document_review(doc_id, run_id, review_id):
        assessment, error = run_ai_assessment_fn(doc_id, run_id, review_id=review_id)
        if error:
            message, status = error
            return jsonify({"error": message}), status
        return jsonify(assessment)

    @bp.route("/api/documents/<doc_id>/runs/<run_id>/migrate", methods=["POST"])
    def migrate_document_reviews(doc_id, run_id):
        data = request.get_json(force=True) or {}
        review_id = data.get("review_id")
        if not review_id:
            return jsonify({"error": "review_id is required"}), 400
        payload, status = migrate_previous_review_to_current_fn(doc_id, run_id, review_id)
        return jsonify(payload), status

    return bp
