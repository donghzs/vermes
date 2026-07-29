"""Blueprint: Providers（API Key 提供商管理）

Endpoints:
- GET  /api/providers/templates    — 提供商模板列表
- POST /api/provider/add           — 添加提供商
- POST /api/provider/verify        — 验证 API Key
- POST /api/provider/sync-models   — 同步模型列表
"""

import logging
from typing import Optional

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel

from vermes_cli.config import load_config, save_config, save_env_value

_log = logging.getLogger(__name__)


# ── Provider templates ───────────────────────────────────────────────

PROVIDER_TEMPLATES = {
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "models": [],
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "models": [],
    },
    "qwen": {
        "name": "Qwen (阿里通义)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "QWEN_API_KEY",
        "models": [],
    },
    "zhipu": {
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "models": [],
    },
    "doubao": {
        "name": "字节豆包",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key_env": "DOUBAO_API_KEY",
        "models": [],
    },
    "kimi": {
        "name": "Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "KIMI_API_KEY",
        "models": [],
    },
    "ant-ling": {
        "name": "蚂蚁百灵",
        "base_url": "https://api.ant-ling.com/v1",
        "api_key_env": "ANT_LING_API_KEY",
        "models": [],
    },
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "models": [],
    },
    "vbit": {
        "name": "vbit.top (胜比特)",
        "base_url": "https://api.vbit.top/v1",
        "api_key_env": "VBIT_API_KEY",
        "models": [],
    },
    "xiaomi": {
        "name": "小米 MiMo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "api_key_env": "XIAOMI_API_KEY",
        "models": [],
    },
    "minimax": {
        "name": "MiniMax",
        "base_url": "https://api.minimax.chat/v1",
        "api_key_env": "MINIMAX_API_KEY",
        "models": [],
    },
    "baidu": {
        "name": "百度文心",
        "base_url": "https://qianfan.baidubce.com/v2",
        "api_key_env": "BAIDU_API_KEY",
        "models": [],
    },
    "xinghuo": {
        "name": "讯飞星火",
        "base_url": "https://spark-api.xf-yun.com/v1",
        "api_key_env": "XINGHUO_API_KEY",
        "models": [],
    },
    "stepfun": {
        "name": "阶跃星辰 StepFun",
        "base_url": "https://api.stepfun.com/v1",
        "api_key_env": "STEPFUN_API_KEY",
        "models": [],
    },
    "yi": {
        "name": "零一万物 Yi",
        "base_url": "https://api.lingyiwanwu.com/v1",
        "api_key_env": "YI_API_KEY",
        "models": [],
    },
    "baichuan": {
        "name": "百川智能",
        "base_url": "https://api.baichuan-ai.com/v1",
        "api_key_env": "BAICHUAN_API_KEY",
        "models": [],
    },
    "hunyuan": {
        "name": "腾讯混元",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "api_key_env": "HUNYUAN_API_KEY",
        "models": [],
    },
    "moonshot": {
        "name": "Kimi (月之暗面)",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "models": [],
    },
    "groq": {
        "name": "Groq (极速推理)",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "models": [],
    },
    "together": {
        "name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "models": [],
    },
    "anthropic": {
        "name": "Anthropic (Claude)",
        "base_url": "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
        "models": [],
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key_env": "GEMINI_API_KEY",
        "models": [],
    },
    "custom": {
        "name": "自定义提供商",
        "base_url": "",
        "api_key_env": "CUSTOM_API_KEY",
        "models": [],
    },
}


# ── Pydantic models ─────────────────────────────────────────────────

class ProviderAddRequest(BaseModel):
    provider_id: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None


# ── Route handlers ───────────────────────────────────────────────────

def get_provider_templates():
    """Return preset provider templates for cloud model addition."""
    result = {}
    for pid, tpl in PROVIDER_TEMPLATES.items():
        result[pid] = {
            "name": tpl["name"],
            "base_url": tpl["base_url"],
            "api_key_env": tpl["api_key_env"],
            "models": [],
        }
    return {"templates": result}


async def add_provider(body: ProviderAddRequest):
    """Add a new provider by writing API key to .env file.

    An empty / missing ``api_key`` must NOT overwrite a previously stored
    key. The desktop Settings UI saves *all* providers in one pass and only
    supplies a real key for the provider the user just edited; masked
    providers (whose key is already in .env) are sent with an empty key.
    Writing that empty value would silently wipe a working provider's
    credentials — verified regression: saving provider B cleared provider A.
    Clearing a key is the job of DELETE /api/env, not of an empty add.
    """
    template = PROVIDER_TEMPLATES.get(body.provider_id)
    env_key = template["api_key_env"] if template else f"{body.provider_id.upper().replace('-', '_')}_API_KEY"
    # Only persist a key when the caller actually supplies one.
    if body.api_key:
        save_env_value(env_key, body.api_key)

    if body.base_url:
        cfg = load_config()
        providers = cfg.get("providers", {})
        if not isinstance(providers, dict):
            providers = {}
        entry = providers.get(body.provider_id, {})
        if not isinstance(entry, dict):
            entry = {}
        entry["base_url"] = body.base_url
        if not template and body.api_key:
            entry["api_key"] = body.api_key
        providers[body.provider_id] = entry
        cfg["providers"] = providers
        save_config(cfg)

    return {"ok": True, "provider": body.provider_id}


async def verify_provider(body: ProviderAddRequest):
    """Verify API key by calling provider's API."""
    template = PROVIDER_TEMPLATES.get(body.provider_id)
    if not template:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {body.provider_id}")

    base_url = body.base_url or template["base_url"]
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {body.api_key}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail=f"API key verification failed: {resp.status_code}")

            data = resp.json()
            if "data" in data:
                models = [m["id"] for m in data["data"]]
            elif "models" in data:
                models = [m["name"] for m in data["models"]]
            else:
                models = template.get("models", [])
            return {"ok": True, "models": models}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Verification failed: {str(e)}")


async def provider_sync_models(request: Request):
    """Sync available models from a provider.
    Accepts either (provider_id) OR (base_url + api_key).
    """
    body = await request.json()
    provider_id = body.get("provider_id", "")
    base_url = body.get("base_url", "").rstrip("/")
    api_key = body.get("api_key", "")

    if provider_id:
        template = PROVIDER_TEMPLATES.get(provider_id, {})
        if not base_url:
            base_url = template.get("base_url", "")
        env_var = template.get("api_key_env", "")
        if not api_key and env_var:
            from vermes_cli.config import load_env
            env = load_env()
            api_key = env.get(env_var, "")
        if not api_key:
            from vermes_cli.config import get_config_path, load_config
            cfg_path = get_config_path()
            if cfg_path.exists():
                cfg = load_config()
                api_key = cfg.get("providers", {}).get(provider_id, {}).get("api_key", api_key)

    if not base_url:
        return {"ok": False, "error": "base_url required (or provide provider_id)"}

    # SSRF protection
    _allowed_schemes = ("https://", "http://localhost", "http://127.0.0.1", "http://0.0.0.0")
    if not any(base_url.startswith(s) for s in _allowed_schemes):
        return {"ok": False, "error": "base_url must use https:// (or localhost for development)"}

    headers = {}
    if api_key:
        _known_domains = [t.get("base_url", "") for t in PROVIDER_TEMPLATES.values()]
        _is_known = any(base_url.rstrip("/") == u.rstrip("/") for u in _known_domains if u)
        if _is_known or provider_id:
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            _log.warning(f"[SSRF] Refusing to send API key to unknown base_url: {base_url}")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base_url}/models", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                raw = data.get("data", data.get("models", []))
                model_list = []
                for m in raw:
                    if isinstance(m, dict):
                        model_list.append(m.get("id", m.get("name", "")))
                    elif isinstance(m, str):
                        model_list.append(m)
                model_list = [m for m in model_list if m]
                if model_list:
                    return {"ok": True, "models": model_list}
            else:
                return {"ok": False, "error": f"API returned {resp.status_code} from {base_url}/models"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {"ok": False, "error": "No models found"}


# ── Registration ─────────────────────────────────────────────────────

def register_to(app):
    """Register provider management routes on the FastAPI app."""
    app.add_api_route("/api/providers/templates", get_provider_templates, methods=["GET"])
    app.add_api_route("/api/provider/add", add_provider, methods=["POST"])
    app.add_api_route("/api/provider/verify", verify_provider, methods=["POST"])
    app.add_api_route("/api/provider/sync-models", provider_sync_models, methods=["POST"])


blueprint = None  # no APIRouter; uses register_to(app) pattern
