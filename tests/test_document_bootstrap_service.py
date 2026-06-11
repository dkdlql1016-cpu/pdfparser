import tempfile
import unittest
from pathlib import Path

from document_bootstrap_service import ensure_default_file_manager_documents
from storage_utils import read_json, write_json


class DocumentBootstrapServiceTests(unittest.TestCase):
    def test_seeds_only_one_default_document_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            documents_dir = root / "documents"
            input_dir.mkdir()
            documents_dir.mkdir()
            (input_dir / "Report_v1.pdf").write_bytes(b"%PDF-1.4")
            (input_dir / "Report_v2.pdf").write_bytes(b"%PDF-1.4")

            calls = []
            ensure_default_file_manager_documents(
                already_seeded=False,
                input_dir=input_dir,
                documents_dir=documents_dir,
                default_file_manager_inputs=("Report_v1.pdf", "Report_v2.pdf"),
                read_json_fn=read_json,
                upsert_seed_document_from_pdf_fn=lambda src, seed_key, existing_doc_id=None: calls.append(
                    (src.name, seed_key, existing_doc_id)
                ),
            )
            self.assertEqual(calls, [("Report_v1.pdf", "Report_v1.pdf", None)])

    def test_creates_seed_when_non_seed_document_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            documents_dir = root / "documents"
            input_dir.mkdir()
            documents_dir.mkdir()
            (input_dir / "Report_v1.pdf").write_bytes(b"%PDF-1.4")

            existing = documents_dir / "workspaces" / "abcdef123456"
            existing.mkdir(parents=True)
            write_json(
                existing / "file_manager" / "meta.json",
                {
                    "workspace_id": "abcdef123456",
                    "title": "Uploaded",
                    "runs": [{"filename": "Uploaded.pdf"}],
                },
            )

            calls = []
            ensure_default_file_manager_documents(
                already_seeded=False,
                input_dir=input_dir,
                documents_dir=documents_dir,
                default_file_manager_inputs=("Report_v1.pdf",),
                read_json_fn=read_json,
                upsert_seed_document_from_pdf_fn=lambda src, seed_key, existing_doc_id=None: calls.append(
                    (src.name, seed_key, existing_doc_id)
                ),
            )
            self.assertEqual(calls, [("Report_v1.pdf", "Report_v1.pdf", None)])

    def test_reuses_existing_seed_document_by_seed_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            documents_dir = root / "documents"
            input_dir.mkdir()
            documents_dir.mkdir()
            (input_dir / "Report_v1.pdf").write_bytes(b"%PDF-1.4")
            (input_dir / "Report_v2.pdf").write_bytes(b"%PDF-1.4")

            existing_seed = documents_dir / "workspaces" / "seedonly12345"
            existing_seed.mkdir(parents=True)
            write_json(
                existing_seed / "file_manager" / "meta.json",
                {
                    "workspace_id": "seedonly12345",
                    "seed_key": "Report_v1.pdf",
                    "runs": [{"filename": "Report_v1.pdf"}],
                },
            )

            calls = []
            ensure_default_file_manager_documents(
                already_seeded=False,
                input_dir=input_dir,
                documents_dir=documents_dir,
                default_file_manager_inputs=("Report_v1.pdf", "Report_v2.pdf"),
                read_json_fn=read_json,
                upsert_seed_document_from_pdf_fn=lambda src, seed_key, existing_doc_id=None: calls.append(
                    (src.name, seed_key, existing_doc_id)
                ),
            )
            self.assertEqual(calls, [("Report_v1.pdf", "Report_v1.pdf", "seedonly12345")])


if __name__ == "__main__":
    unittest.main()
