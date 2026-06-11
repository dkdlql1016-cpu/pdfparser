import tempfile
import unittest
from pathlib import Path

from flask import Flask

from document_pipeline_service import estimated_char_count, filter_words_for_chars
from routes.document_routes import create_document_blueprint
from routes.snapshot_routes import create_snapshot_blueprint


def _dummy_chars(words):
    chars = []
    for word in words:
        for ch in str(word.get("text", "")):
            chars.append({"char": ch, "page": word.get("page")})
    return chars


class CharsGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.doc_id = "abcdef123456"
        self.run_id = "123456abcdef"
        self.snapshot_id = "snap-abcdef1234"
        self.words = [
            {"text": "AB", "page": 1},
            {"text": "CD", "page": 2},
            {"text": "E", "page": 3},
        ]

    def tearDown(self):
        self._tmp.cleanup()

    def _read_json(self, path, default=None):
        if Path(path).exists():
            return list(self.words)
        return default

    def _build_app(self, *, max_chars):
        app = Flask(__name__)
        doc_run_dir = self.tmp_dir / self.doc_id / "runs" / self.run_id
        doc_run_dir.mkdir(parents=True, exist_ok=True)
        (doc_run_dir / "words.json").write_text("[]", encoding="utf-8")
        snap_dir = self.tmp_dir / self.doc_id / "snapshots" / self.snapshot_id
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "new.words").write_text("[]", encoding="utf-8")

        app.register_blueprint(
            create_document_blueprint(
                deps={
                    "get_uploaded_pdf_fn": lambda: None,
                    "document_run_dir_fn": lambda _doc_id, _run_id: doc_run_dir,
                    "utc_now_fn": lambda: "2026-01-01T00:00:00+00:00",
                    "save_document_meta_fn": lambda *_args, **_kwargs: None,
                    "process_single_document_run_fn": lambda *_args, **_kwargs: {},
                    "update_run_meta_fn": lambda *_args, **_kwargs: None,
                    "load_document_meta_fn": lambda _doc_id: None,
                    "load_document_reviews_fn": lambda _doc_id: [],
                    "save_document_reviews_fn": lambda *_args, **_kwargs: None,
                    "copy_if_exists_fn": lambda *_args, **_kwargs: None,
                    "read_json_fn": self._read_json,
                    "build_chars_from_words_fn": _dummy_chars,
                    "filter_words_for_chars_fn": filter_words_for_chars,
                    "estimated_char_count_fn": estimated_char_count,
                    "chars_max_words": 100,
                    "chars_max_estimated_count": max_chars,
                    "anchor_for_selection_fn": lambda *_args, **_kwargs: None,
                    "canonical_review_from_anchor_fn": lambda *_args, **_kwargs: {},
                    "document_reviews_for_run_fn": lambda *_args, **_kwargs: [],
                    "process_document_diff_run_fn": lambda *_args, **_kwargs: {},
                    "render_pdf_page_fn": lambda *_args, **_kwargs: b"",
                    "create_document_level_review_fn": lambda *_args, **_kwargs: {},
                    "export_annotated_pdf_fn": lambda *_args, **_kwargs: b"",
                    "run_meta_for_fn": lambda *_args, **_kwargs: None,
                    "overwrite_saved_document_fn": lambda *_args, **_kwargs: ({}, 200),
                    "normalize_document_title_fn": lambda title: title,
                    "document_title_exists_fn": lambda *_args, **_kwargs: False,
                    "save_run_side_file_fn": lambda *_args, **_kwargs: ({}, 200),
                    "document_dir_fn": lambda _doc_id: self.tmp_dir / self.doc_id,
                    "write_json_fn": lambda *_args, **_kwargs: None,
                }
            )
        )
        app.register_blueprint(
            create_snapshot_blueprint(
                deps={
                    "load_document_meta_fn": lambda _doc_id: None,
                    "snapshot_limit": 10,
                    "create_run_snapshot_fn": lambda *_args, **_kwargs: ({}, 200),
                    "document_snapshot_dir_fn": lambda _doc_id, _snapshot_id: snap_dir,
                    "read_json_fn": self._read_json,
                    "build_chars_from_words_fn": _dummy_chars,
                    "filter_words_for_chars_fn": filter_words_for_chars,
                    "estimated_char_count_fn": estimated_char_count,
                    "chars_max_words": 100,
                    "chars_max_estimated_count": max_chars,
                    "snapshot_side_file_fn": lambda side, kind: f"{side}.{kind}",
                    "render_pdf_page_fn": lambda *_args, **_kwargs: b"",
                    "export_annotated_pdf_fn": lambda *_args, **_kwargs: b"",
                }
            )
        )
        return app

    def test_document_chars_page_filter_works(self):
        app = self._build_app(max_chars=100)
        client = app.test_client()
        response = client.get(f"/api/documents/{self.doc_id}/runs/{self.run_id}/chars/new?page=2")
        self.assertEqual(response.status_code, 200)
        chars = response.get_json()
        self.assertEqual(len(chars), 2)
        self.assertEqual({entry["page"] for entry in chars}, {2})

    def test_document_chars_invalid_page_range_returns_400(self):
        app = self._build_app(max_chars=100)
        client = app.test_client()
        response = client.get(
            f"/api/documents/{self.doc_id}/runs/{self.run_id}/chars/new?page_start=3&page_end=1"
        )
        self.assertEqual(response.status_code, 400)

    def test_document_chars_large_payload_returns_413(self):
        app = self._build_app(max_chars=4)
        client = app.test_client()
        response = client.get(f"/api/documents/{self.doc_id}/runs/{self.run_id}/chars/new")
        self.assertEqual(response.status_code, 413)

    def test_snapshot_chars_large_payload_returns_413(self):
        app = self._build_app(max_chars=4)
        client = app.test_client()
        response = client.get(f"/api/documents/{self.doc_id}/snapshots/{self.snapshot_id}/chars/new")
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
