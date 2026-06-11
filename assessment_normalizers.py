import json
import re


def run_meta_for(meta, run_id):
    return next((r for r in meta.get("runs", []) if r.get("run_id") == run_id), None)


def anchor_section_id(anchor):
    return anchor.get("section_id") or anchor.get("section") or None


def normalize_assessment_payload(data):
    verdict = data.get("verdict")
    if verdict not in ("cleared", "partial", "unclear", "not_cleared"):
        raise ValueError("invalid verdict")
    return {
        "verdict": verdict,
        "confidence": data.get("confidence"),
        "reasoning": str(data.get("reasoning", ""))[:2000],
    }


def normalize_batch_assessment_payload(data):
    items = data.get("items") or []
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    out = {}
    for raw in items:
        review_id = str((raw or {}).get("review_id", "")).strip()
        if not review_id:
            continue
        payload = normalize_assessment_payload(raw or {})
        payload["review_id"] = review_id
        out[review_id] = payload
    if not out:
        raise ValueError("no verdict items returned")
    return out


def normalize_change_verdict(value):
    raw = str(value or "").strip().lower()
    legacy = {
        "risk": "medium",
        "no_risk": "low",
        "unclear": "low",
        "unknown": "low",
    }
    normalized = legacy.get(raw, raw)
    if normalized not in ("high", "medium", "low"):
        raise ValueError("invalid verdict")
    return normalized


def normalize_change_assessment_payload(data):
    verdict = normalize_change_verdict((data or {}).get("verdict", ""))
    recommended_comment = str(data.get("recommended_comment", ""))[:2200]
    if verdict == "low":
        recommended_comment = ""
    return {
        "verdict": verdict,
        "confidence": data.get("confidence"),
        "reasoning": str(data.get("reasoning", ""))[:2200],
        "recommended_comment": recommended_comment,
    }


def normalized_change_signature(item):
    old_text = re.sub(r"\s+", " ", str(item.get("old_text", "") or "").strip().lower())
    new_text = re.sub(r"\s+", " ", str(item.get("new_text", "") or "").strip().lower())
    if not old_text and not new_text:
        return None
    return f"{old_text} -> {new_text}"


def harmonize_change_items(items):
    rank = {"low": 0, "medium": 1, "high": 2}
    groups = {}
    for item in items or []:
        sig = normalized_change_signature(item)
        if not sig:
            continue
        groups.setdefault(sig, []).append(item)
    for group_items in groups.values():
        if len(group_items) < 2:
            continue
        counts = {}
        for it in group_items:
            verdict = str(it.get("verdict", "low"))
            counts[verdict] = counts.get(verdict, 0) + 1
        target = sorted(counts.keys(), key=lambda v: (-counts[v], rank.get(v, 0)))[0]
        for it in group_items:
            it["verdict"] = target
            if target == "low":
                it["recommended_comment"] = ""
    return items


def normalize_change_batch_assessment_payload(data):
    items = data.get("items") or []
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    out = {}
    for raw in items:
        change_id = str((raw or {}).get("change_id", "")).strip()
        if not change_id:
            continue
        payload = normalize_change_assessment_payload(raw or {})
        payload["change_id"] = int(change_id)
        out[payload["change_id"]] = payload
    if not out:
        raise ValueError("no verdict items returned")
    return out


def openai_tool_call_args(arguments):
    if isinstance(arguments, dict):
        return arguments
    raw = str(arguments or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}

