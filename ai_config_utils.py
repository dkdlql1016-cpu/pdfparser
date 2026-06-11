import os
from pathlib import Path

DEFAULT_AI_MODEL = "gpt-5.4"


def resolve_ai_model(env_name, *, fallback=None):
    value = os.environ.get(env_name)
    if value is not None and str(value).strip():
        return str(value).strip()
    if fallback is not None and str(fallback).strip():
        return str(fallback).strip()
    return DEFAULT_AI_MODEL


def ai_provider_for_model(model_name):
    model = str(model_name or "").strip().lower()
    if model.startswith(("gpt", "o1", "o3", "o4")):
        return "openai"
    return "anthropic"


def required_ai_api_key_name(model_name):
    return "OPENAI_API_KEY" if ai_provider_for_model(model_name) == "openai" else "ANTHROPIC_API_KEY"


def required_ai_api_key(model_name):
    return os.environ.get(required_ai_api_key_name(model_name))


def load_system_prompt(prompt_path: Path, fallback_text: str):
    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8").strip()
    return fallback_text

