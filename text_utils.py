import re


def norm_token(value):
    s = str(value or "")
    s = (
        s.replace("\u2019", "'")
         .replace("\u2018", "'")
         .replace("\u201c", '"')
         .replace("\u201d", '"')
         .replace("\u2013", "-")
         .replace("\u2014", "-")
         .replace("\u2212", "-")
         .replace("\\-", "-")
    )
    s = s.strip().lower()
    s = re.sub(r"^[*_#`\[\]\(\)\{\}<>|]+", "", s)
    s = re.sub(r"[*_#`\[\]\(\)\{\}<>|]+$", "", s).strip()
    s = re.sub(r"^[,.;:]+|[,.;:]+$", "", s)
    if re.fullmatch(r"page_?\d+", s):
        return ""
    if re.search(r"\d", s):
        neg = s.startswith("(") and s.endswith(")")
        n = re.sub(r"[^0-9a-z%.-]", "", s).replace(",", "")
        if neg and not n.startswith("-"):
            n = "-" + n
        return n
    return re.sub(r"[^a-z0-9%.-]", "", s)


def keep_token(value):
    n = norm_token(value)
    return bool(n) and not re.fullmatch(r"-{2,}", n)


def normalize_document_title(title: str) -> str:
    return re.sub(r"\s+", " ", str(title or "").strip())


def norm_space(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def trim_for_prompt(value, limit):
    text = str(value or "")
    return text if len(text) <= limit else text[:limit - 20] + "\n...[truncated]"

