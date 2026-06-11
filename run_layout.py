from pathlib import Path

CURRENT_ALIASES = {"new", "report", "current"}
PREVIOUS_ALIASES = {"old", "prev", "previous"}


def side_name(side: str) -> str:
    value = str(side or "").lower()
    if value in CURRENT_ALIASES:
        return "current"
    if value in PREVIOUS_ALIASES:
        return "previous"
    raise ValueError("side must be report/new/current or prev/old/previous")


def is_current_side(side: str) -> bool:
    return side_name(side) == "current"


def side_dir(run_dir: Path, side: str) -> Path:
    return Path(run_dir) / side_name(side)


def current_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "current"


def previous_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "previous"


def source_pdf(run_dir: Path, side: str = "current") -> Path:
    return side_dir(run_dir, side) / "source.pdf"


def source_md(run_dir: Path, side: str = "current") -> Path:
    return side_dir(run_dir, side) / "source.md"


def words_path(run_dir: Path, side: str = "current") -> Path:
    return side_dir(run_dir, side) / "words.json"


def sections_path(run_dir: Path, side: str = "current") -> Path:
    return side_dir(run_dir, side) / "sections.json"


def viewer_path(run_dir: Path) -> Path:
    return Path(run_dir) / "viewer.json"


def diff_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "diff"


def diff_segments_path(run_dir: Path) -> Path:
    return diff_dir(run_dir) / "segments.json"


def ai_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "ai"


def review_assessment_path(run_dir: Path) -> Path:
    return ai_dir(run_dir) / "review_assessment.json"


def change_assessment_path(run_dir: Path) -> Path:
    return ai_dir(run_dir) / "change_assessment.json"


def run_reviews_path(run_dir: Path) -> Path:
    return Path(run_dir) / "reviews.json"


def cache_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "_cache"


def opendataloader_cache_dir(run_dir: Path) -> Path:
    return cache_dir(run_dir) / "opendataloader"


def diff_md_cache_dir(run_dir: Path) -> Path:
    return cache_dir(run_dir) / "diff_md"


def diff_md_path(run_dir: Path, side: str) -> Path:
    return diff_md_cache_dir(run_dir) / f"{side_name(side)}.md"


def durable_artifacts(run_dir: Path):
    root = Path(run_dir)
    if not root.exists():
        return []
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and "_cache" not in path.relative_to(root).parts
    ]
