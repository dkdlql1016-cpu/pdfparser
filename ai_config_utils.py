import os
from pathlib import Path

# AuditSay Azure 모델별 endpoint/deployment 환경변수 매핑
AUDITSAY_MODEL_ENV_MAP: dict = {
    "gpt-5.1": ("AZURE_ENDPOINT_GPT51", "AZURE_DEPLOYMENT_GPT51"),
    "gpt-5.4": ("AZURE_ENDPOINT_GPT54", "AZURE_DEPLOYMENT_GPT54"),
    "gpt-5.4-mini": ("AZURE_ENDPOINT_GPT54_MINI", "AZURE_DEPLOYMENT_GPT54_MINI"),
    "gpt-5.4-nano": ("AZURE_ENDPOINT_GPT54_NANO", "AZURE_DEPLOYMENT_GPT54_NANO"),
}

DEFAULT_AZURE_API_VERSION = "2024-02-15-preview"

_LOCAL_ENV_LOADED = False


def _load_local_env() -> None:
    """프로젝트 루트의 .env 파일을 os.environ에 주입한다 (한 번만 실행)."""
    global _LOCAL_ENV_LOADED
    if _LOCAL_ENV_LOADED:
        return
    _LOCAL_ENV_LOADED = True
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def is_azure_mode() -> bool:
    """AZURE_OPENAI_ENDPOINT와 AZURE_OPENAI_API_KEY가 모두 설정된 경우 Azure 모드로 동작."""
    _load_local_env()
    return bool(os.environ.get("AZURE_OPENAI_ENDPOINT")) and bool(os.environ.get("AZURE_OPENAI_API_KEY"))


def resolve_azure_settings(model_name: str) -> dict:
    """Azure OpenAI 접속에 필요한 설정값을 환경변수에서 조합해 반환한다."""
    _load_local_env()
    model = str(model_name or "").strip().lower()

    endpoint = (os.environ.get("AZURE_OPENAI_ENDPOINT") or "").strip().rstrip("/")
    deployment = (os.environ.get("AZURE_OPENAI_DEPLOYMENT") or "").strip()

    # AuditSay 스타일 모델별 환경변수 fallback
    if (not endpoint or not deployment) and model in AUDITSAY_MODEL_ENV_MAP:
        ep_env, dep_env = AUDITSAY_MODEL_ENV_MAP[model]
        endpoint = endpoint or (os.environ.get(ep_env) or "").strip().rstrip("/")
        deployment = deployment or (os.environ.get(dep_env) or "").strip()

    api_key = (os.environ.get("AZURE_OPENAI_API_KEY") or "").strip()
    api_version = (
        os.environ.get("AZURE_OPENAI_API_VERSION") or DEFAULT_AZURE_API_VERSION
    ).strip()

    if not endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not set")
    if not deployment:
        raise RuntimeError(
            "AZURE_OPENAI_DEPLOYMENT (or model-specific AZURE_DEPLOYMENT_*) is not set"
        )
    if not api_key:
        raise RuntimeError("AZURE_OPENAI_API_KEY is not set")

    return {
        "endpoint": endpoint,
        "deployment": deployment,
        "api_key": api_key,
        "api_version": api_version,
    }


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

