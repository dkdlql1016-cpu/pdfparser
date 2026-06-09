#!/usr/bin/env python3
"""
diff_extract.py

Word/line level diff between two (markdown) text files, emitting a JSON
document that describes equal / delete / add segments with source line numbers.

Pipeline position:
    PDF -> OpenDataLoader markdown -> diff_extract.py -> result.json
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher

# Markdown heading prefix, e.g. "### " in "### Revenue"
HEADER_RE = re.compile(r'#{1,6}[ \t]+')
# A standalone heading-marker token, e.g. "#", "##", ... "######"
MARKER_RE = re.compile(r'#{1,6}$')
# A page-separator line, e.g. "--- 30 ---"
_PAGE_MARKER_LINE_RE = re.compile(r"^\s*-{2,}\s*\d{1,4}\s*-{2,}\s*$")


def read_lines(path):
    with open(path, encoding='utf-8') as f:
        return f.read().splitlines()


def normalize_line(line):
    """Strip a leading markdown heading marker so '## Revenue' == 'Revenue'."""
    return HEADER_RE.sub('', line, count=1).strip()


def is_marker_token(token):
    """True for standalone heading-marker tokens ('#' .. '######')."""
    return bool(MARKER_RE.match(token))


def tokens_from_path(path, normalize=True, unit="word"):
    """
    Return a list of (key, display_text, line_number) tuples.

    key          : value used for diff comparison (normalized when requested)
    display_text : original text, preserved for output
    line_number  : 1-based source line number
    """
    raw = read_lines(path)

    if unit == "line":
        out = []
        for i, t in enumerate(raw):
            if _PAGE_MARKER_LINE_RE.match(t.strip()):
                continue
            key = normalize_line(t) if normalize else t
            out.append((key, t, i + 1))
        return out

    out = []
    for i, line in enumerate(raw, start=1):
        if _PAGE_MARKER_LINE_RE.match(line.strip()):
            continue
        for w in line.split():
            if normalize and is_marker_token(w):
                continue
            out.append((w, w, i))
    return out


def remap(raw, old_tokens, new_tokens):
    """Turn index-based opcodes into segments carrying text + line numbers."""
    out = []
    for s in raw:
        typ = s['type']
        if typ == 'equal':
            ot = old_tokens[s['old_idx'] - 1]
            nt = new_tokens[s['new_idx'] - 1]
            out.append({'type': 'equal', 'text': ot[1], 'old_line': ot[2], 'new_line': nt[2]})
        elif typ == 'delete':
            ot = old_tokens[s['old_idx'] - 1]
            out.append({'type': 'delete', 'text': ot[1], 'old_line': ot[2], 'new_line': None})
        else:  # add
            nt = new_tokens[s['new_idx'] - 1]
            out.append({'type': 'add', 'text': nt[1], 'old_line': None, 'new_line': nt[2]})
    return out


def remove_page_marker_segments(segments):
    """
    Remove page marker diffs at the result.json segment stage.

    Patterns to remove:
      equal --- / delete 30 / add 31 / equal ---
      equal -   / delete 30 / add 31 / equal -
      delete --- 30 --- / add --- 31 ---
      standalone delete/add numbers attached to surrounding dash tokens
    """
    def txt(i):
        if i < 0 or i >= len(segments):
            return ""
        return str(segments[i].get("text", "")).strip()

    def typ(i):
        if i < 0 or i >= len(segments):
            return ""
        return segments[i].get("type")

    def is_dash_token(x):
        return bool(re.fullmatch(r"\\?-{1,}", str(x or "").strip()))

    def is_page_num_token(x):
        return bool(re.fullmatch(r"\d{1,4}", str(x or "").strip()))

    remove = set()

    for i, s in enumerate(segments):
        t = str(s.get("text", "")).strip()
        st = s.get("type")

        # Treat delete/add numbers surrounded by dash tokens as page marker numbers.
        # Example: equal --- / delete 30 / add 31 / equal ---
        if st in ("delete", "add") and is_page_num_token(t):
            left_dash = is_dash_token(txt(i - 1))
            right_dash = is_dash_token(txt(i + 1)) or is_dash_token(txt(i + 2))

            # Paired delete/add numbers at the same location.
            pair_num = (
                (typ(i - 1) in ("delete", "add") and is_page_num_token(txt(i - 1)))
                or
                (typ(i + 1) in ("delete", "add") and is_page_num_token(txt(i + 1)))
            )

            if left_dash and (right_dash or pair_num):
                remove.add(i)

                # Remove nearby dash tokens when they are part of the page marker.
                if is_dash_token(txt(i - 1)):
                    remove.add(i - 1)
                if is_dash_token(txt(i + 1)):
                    remove.add(i + 1)
                if is_dash_token(txt(i + 2)):
                    remove.add(i + 2)

        # Remove dash tokens near page marker numbers.
        if is_dash_token(t):
            near_page_num = any(
                typ(j) in ("delete", "add", "equal") and is_page_num_token(txt(j))
                for j in (i - 2, i - 1, i + 1, i + 2)
                if 0 <= j < len(segments)
            )
            if near_page_num:
                remove.add(i)

    return [s for i, s in enumerate(segments) if i not in remove]


def diff_with_difflib(old_tokens, new_tokens):
    sm = SequenceMatcher(None,
                         [x[0] for x in old_tokens],
                         [x[0] for x in new_tokens],
                         autojunk=False)
    raw = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                raw.append({'type': 'equal', 'old_idx': i1 + k + 1, 'new_idx': j1 + k + 1})
        else:
            if tag in ('delete', 'replace'):
                for i in range(i1, i2):
                    raw.append({'type': 'delete', 'old_idx': i + 1, 'new_idx': None})
            if tag in ('insert', 'replace'):
                for j in range(j1, j2):
                    raw.append({'type': 'add', 'old_idx': None, 'new_idx': j + 1})
    return remap(raw, old_tokens, new_tokens)


def write_tmp(lines):
    f = tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.txt', delete=False)
    f.write('\n'.join(lines) + ('\n' if lines else ''))
    f.close()
    return f.name


def parse_git_unified(text):
    raw = []
    old_idx = new_idx = None
    in_body = False
    for line in text.splitlines():
        if line.startswith('@@'):
            m = re.search(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if m:
                old_idx = int(m.group(1))
                new_idx = int(m.group(2))
                in_body = True
            continue
        if not in_body or not line:
            continue
        tag = line[0]
        if tag == ' ':
            raw.append({'type': 'equal', 'old_idx': old_idx, 'new_idx': new_idx})
            old_idx += 1
            new_idx += 1
        elif tag == '-':
            if line.startswith('--- '):
                continue
            raw.append({'type': 'delete', 'old_idx': old_idx, 'new_idx': None})
            old_idx += 1
        elif tag == '+':
            if line.startswith('+++ '):
                continue
            raw.append({'type': 'add', 'old_idx': None, 'new_idx': new_idx})
            new_idx += 1
    return raw


def diff_with_git(old_tokens, new_tokens, algorithm):
    old_tmp = write_tmp([x[0] for x in old_tokens])
    new_tmp = write_tmp([x[0] for x in new_tokens])
    try:
        cmd = [
            'git', 'diff', '--no-index', '--no-color',
            f'--diff-algorithm={algorithm}', '-U1000000000',
            old_tmp, new_tmp,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding='utf-8', errors='replace')
        if proc.returncode not in (0, 1):
            raise RuntimeError(proc.stderr or 'git diff failed')

        if proc.stdout.strip():
            raw = parse_git_unified(proc.stdout)
        else:
            # Identical inputs: git emits nothing, so build all-equal manually.
            raw = [{'type': 'equal', 'old_idx': i + 1, 'new_idx': i + 1}
                   for i in range(min(len(old_tokens), len(new_tokens)))]
        return remap(raw, old_tokens, new_tokens)
    finally:
        for p in (old_tmp, new_tmp):
            try:
                os.unlink(p)
            except OSError:
                pass


def build_doc(segments, engine, algorithm, normalize, unit):
    summary = {'equal': 0, 'deleted': 0, 'added': 0}
    for s in segments:
        if s['type'] == 'equal':
            summary['equal'] += 1
        elif s['type'] == 'delete':
            summary['deleted'] += 1
        else:
            summary['added'] += 1
    return {
        'engine': engine,
        'algorithm': algorithm,
        'unit': unit,
        'normalized_headers': normalize,
        'summary': summary,
        'segments': segments,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('old')
    ap.add_argument('new')
    ap.add_argument('-o', '--output')
    ap.add_argument('--unit', choices=['word', 'line'], default='word')
    ap.add_argument('--engine', choices=['auto', 'git', 'difflib'], default='auto')
    ap.add_argument('--diff-algorithm',
                    choices=['histogram', 'myers', 'patience', 'minimal'],
                    default='histogram')
    ap.add_argument('--no-normalize', action='store_true')
    args = ap.parse_args()

    normalize = not args.no_normalize
    old_tokens = tokens_from_path(args.old, normalize, args.unit)
    new_tokens = tokens_from_path(args.new, normalize, args.unit)

    if args.engine in ('auto', 'git') and shutil.which('git'):
        try:
            segments = diff_with_git(old_tokens, new_tokens, args.diff_algorithm)
            engine = 'git'
        except Exception:
            if args.engine == 'git':
                raise
            segments = diff_with_difflib(old_tokens, new_tokens)
            engine = 'difflib'
    else:
        segments = diff_with_difflib(old_tokens, new_tokens)
        engine = 'difflib'

    segments = remove_page_marker_segments(segments)

    doc = build_doc(segments, engine, args.diff_algorithm, normalize, args.unit)
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(text)
    else:
        print(text)


if __name__ == '__main__':
    main()
