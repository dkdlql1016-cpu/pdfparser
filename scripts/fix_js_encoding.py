"""Repair UTF-8 corruption in extracted static JS files."""
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"

REPLACEMENTS = [
    ("rail.textContent='??;", "rail.textContent='\u2630';"),
    ("formatDate(s,'??)", "formatDate(s,'\u2014')"),
    ("<span class=\"delText\">??'", "<span class=\"delText\">\u2212 '"),
    ("del</span>??{s.del", "del</span>\u2212${s.del"),
    ("textContent='??;", "textContent='\u25bc';"),
    ("(r.modified||'??)", "(r.modified||'\u2014')"),
    ("open?'??:'??", "open?'\u25b2':'\u25bc'"),
    ("\uc918\uc744", "\u00b7"),
]


def fix_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in REPLACEMENTS:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8", newline="\n")
        return True
    return False


def main() -> None:
    changed = []
    for name in ("viewer-app.js", "dashboard-app.js"):
        path = STATIC / name
        if not path.exists():
            print(f"missing: {path}")
            continue
        if fix_file(path):
            changed.append(name)
            print(f"fixed: {name}")
        else:
            print(f"unchanged: {name}")


if __name__ == "__main__":
    main()
