import tempfile
import unittest
from pathlib import Path

import run_layout


class RunLayoutTests(unittest.TestCase):
    def test_side_aliases(self):
        self.assertEqual(run_layout.side_name("new"), "current")
        self.assertEqual(run_layout.side_name("report"), "current")
        self.assertEqual(run_layout.side_name("old"), "previous")
        self.assertEqual(run_layout.side_name("prev"), "previous")

    def test_paths_are_normalized(self):
        root = Path("run")
        self.assertEqual(run_layout.source_pdf(root, "new"), Path("run/compare/current/source.pdf"))
        self.assertEqual(run_layout.source_md(root, "old"), Path("run/compare/previous/source.md"))
        self.assertEqual(run_layout.words_path(root, "current"), Path("run/compare/current/words.json"))
        self.assertEqual(run_layout.sections_path(root, "previous"), Path("run/compare/previous/sections.json"))
        self.assertEqual(run_layout.viewer_path(root), Path("run/compare/viewer.json"))
        self.assertEqual(run_layout.diff_segments_path(root), Path("run/compare/diff/segments.json"))
        self.assertEqual(run_layout.review_assessment_path(root), Path("run/analysis/review_assessment.json"))
        self.assertEqual(run_layout.change_assessment_path(root), Path("run/analysis/change_assessment.json"))
        self.assertEqual(run_layout.diff_md_path(root, "old"), Path("run/_cache/diff_md/previous.md"))

    def test_invalid_side_raises(self):
        with self.assertRaises(ValueError):
            run_layout.side_name("left")

    def test_durable_artifacts_exclude_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep = root / "compare" / "current" / "words.json"
            drop = root / "_cache" / "diff_md" / "current.md"
            keep.parent.mkdir(parents=True)
            drop.parent.mkdir(parents=True)
            keep.write_text("{}", encoding="utf-8")
            drop.write_text("cache", encoding="utf-8")
            self.assertEqual(run_layout.durable_artifacts(root), [keep])


if __name__ == "__main__":
    unittest.main()
