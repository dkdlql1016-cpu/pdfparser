import re

from flask import request

_HEX12_RE = re.compile(r"^[0-9a-f]{12}$")
_SNAPSHOT_ID_RE = re.compile(r"^snap-[0-9a-f]{10}$")


def is_valid_hex_id(value: str) -> bool:
    return bool(_HEX12_RE.match(str(value or "")))


def is_valid_snapshot_id(value: str) -> bool:
    return bool(_SNAPSHOT_ID_RE.match(str(value or "")))


def has_invalid_hex_id(*values: str) -> bool:
    return any(not is_valid_hex_id(value) for value in values)


def ids_payload(workspace_id: str):
    return {"workspace_id": str(workspace_id or "")}


def parse_optional_positive_int(name: str):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None, None
    try:
        parsed = int(raw)
    except ValueError:
        return None, f"{name} must be an integer"
    if parsed < 1:
        return None, f"{name} must be >= 1"
    return parsed, None


def chars_page_filters_from_query():
    page, page_err = parse_optional_positive_int("page")
    if page_err:
        return None, page_err
    page_start, start_err = parse_optional_positive_int("page_start")
    if start_err:
        return None, start_err
    page_end, end_err = parse_optional_positive_int("page_end")
    if end_err:
        return None, end_err
    if page is not None and (page_start is not None or page_end is not None):
        return None, "page cannot be combined with page_start/page_end"
    if page_start is not None and page_end is not None and page_start > page_end:
        return None, "page_start must be <= page_end"
    return {"page": page, "page_start": page_start, "page_end": page_end}, None
