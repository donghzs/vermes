"""User-defined (custom) literature source definitions.

Institutions — universities, hospitals, corporate / enterprise R&D labs — often
run **internal** multi-database literature portals behind a gateway, API key, or
SSO. Vermes lets the user register an **unlimited** number of such sources from
the Settings UI (the very same "文献源设置" form used for built-in sources).

Each definition is persisted to ``<VERMES_HOME>/literature_custom_sources.json``
and merged into the unified :mod:`agent.service_credentials` registry at request
time, so the form rendering, credential whitelist, set-status display, and value
masking that built-in sources enjoy apply to custom sources for free — no
hardcoded vendor names, no framework changes per source.

The credentials themselves are NOT stored here; only the *metadata* (which
fields a source needs). Actual API keys / usernames / passwords are written to
the central ``.env`` via ``PUT /api/env`` under the namespaced env var names
generated below (``LIT_<ID>_API_KEY`` …), so they get the same masking + audit
treatment as every other secret.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = __import__("logging").getLogger(__name__)

# Env-var prefix for every custom-source credential field. Namespaced so a
# user's "API Key" for their hospital portal can never collide with a built-in
# service's key.
ENV_PREFIX = "LIT_"

# Canonical credential-field types the user can toggle on per source. Order is
# fixed so generated env var names are deterministic across edits.
_FIELD_TYPES: Dict[str, Dict[str, Any]] = {
    "api_key":  {"suffix": "API_KEY",  "label": "API Key",  "secret": True,  "kind": "api_key"},
    "base_url": {"suffix": "BASE_URL", "label": "网关地址", "secret": False, "kind": "base_url"},
    "user":     {"suffix": "USER",     "label": "账号",     "secret": False, "kind": "user"},
    "password": {"suffix": "PASSWORD", "label": "密码",     "secret": True,  "kind": "password"},
}
_FIELD_ORDER = ("api_key", "base_url", "user", "password")

# Authentication schemes a custom source may use. ``form`` = card-number /
# password login (POST credentials → session cookie) then search with that
# session — the typical pattern for purchased third-party literature portals
# (e.g. 书童 shutong, 各类卡号卡密文献网关).
AUTH_SCHEMES = ("none", "bearer", "basic", "header", "query", "form")

# In-memory cache keyed on file mtime to avoid re-parsing on every request.
_cache: Dict[str, Any] = {"mtime": 0.0, "data": None}
_store_path: Optional[Path] = None


def _resolve_store_path() -> Path:
    global _store_path
    if _store_path is None:
        try:
            from vermes_cli.config import get_vermes_home

            base = Path(get_vermes_home())
        except Exception:  # noqa: BLE001
            base = Path.home() / ".vermes"
        _store_path = base / "literature_custom_sources.json"
    return _store_path


def _read_all() -> List[Dict[str, Any]]:
    """Return the list of raw custom-source definitions (cache by mtime)."""
    path = _resolve_store_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    if _cache["data"] is not None and _cache["mtime"] == mtime:
        return _cache["data"]  # type: ignore[return-value]
    try:
        raw = path.read_text(encoding="utf-8") or "[]"
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        data = []
    if not isinstance(data, list):
        data = []
    _cache["mtime"] = mtime
    _cache["data"] = data
    return data


def _write_all(items: List[Dict[str, Any]]) -> None:
    path = _resolve_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    _cache["mtime"] = path.stat().st_mtime
    _cache["data"] = items


def _slugify(text: str) -> str:
    """Turn a human label into a safe source id fragment."""
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = s.strip("_")
    # ids must start with a letter (env-var / provider-name safety)
    if s and s[0].isdigit():
        s = "s_" + s
    return s


def _build_fields(source_id: str, field_types: List[str]) -> List[Dict[str, Any]]:
    """Generate the credential-field list for a source id from chosen types."""
    fields: List[Dict[str, Any]] = []
    seen = set()
    for ft in _FIELD_ORDER:
        if ft not in field_types:
            continue
        meta = _FIELD_TYPES[ft]
        env_key = f"{ENV_PREFIX}{source_id.upper()}_{meta['suffix']}"
        if env_key in seen:
            continue
        seen.add(env_key)
        fields.append(
            {
                "key": env_key,
                "kind": meta["kind"],
                "label": meta["label"],
                "secret": bool(meta["secret"]),
            }
        )
    return fields


def _normalize_definition(raw: Dict[str, Any], *, source_id: str) -> Dict[str, Any]:
    """Coerce a stored/created definition into canonical shape (id preserved)."""
    field_types = [ft for ft in _FIELD_ORDER if ft in (raw.get("field_types") or [])]
    auth = raw.get("auth_scheme", "bearer")
    if auth not in AUTH_SCHEMES:
        auth = "bearer"
    method = (raw.get("method") or "GET").upper()
    if method not in ("GET", "POST"):
        method = "GET"
    login_url = (raw.get("login_url") or raw.get("url") or "").strip()
    search_url = (raw.get("search_url") or raw.get("url") or raw.get("base_url") or "").strip()
    extra = raw.get("login_extra_fields")
    if not isinstance(extra, dict):
        extra = {}
    return {
        "id": source_id,
        "label": (raw.get("label") or source_id).strip() or source_id,
        "description": (raw.get("description") or "").strip(),
        "url": (raw.get("url") or "").strip(),
        "base_url": (raw.get("base_url") or "").strip(),
        "provider_type": (raw.get("provider_type") or "").strip(),
        "auth_scheme": auth,
        "api_key_header": (raw.get("api_key_header") or "X-API-KEY").strip(),
        "query_param": (raw.get("query_param") or "q").strip() or "q",
        "method": method,
        "field_types": field_types,
        "fields": _build_fields(source_id, field_types),
        # ── form-login (card-gateway) config ──
        "login_url": login_url,
        "login_user_field": (raw.get("login_user_field") or "user").strip() or "user",
        "login_password_field": (raw.get("login_password_field") or "password").strip() or "password",
        "login_extra_fields": extra,
        "search_url": search_url,
        "sso_url": (raw.get("sso_url") or "").strip(),
        "sso_referer": (raw.get("sso_referer") or "").strip(),
        "token_scheme": (raw.get("token_scheme") or "bearer").strip().lower() or "bearer",
        "created_at": raw.get("created_at") or time.time(),
        "updated_at": time.time(),
    }


def list_custom_sources() -> List[Dict[str, Any]]:
    """Return all persisted custom-source definitions (raw, with ``fields``)."""
    return _read_all()


def get_custom_source(source_id: str) -> Optional[Dict[str, Any]]:
    for d in _read_all():
        if d.get("id") == source_id:
            return d
    return None


def _unique_id(desired: str) -> str:
    base = _slugify(desired)
    # Non-ASCII labels (e.g. Chinese) slugify to empty — fall back to a random
    # but stable fragment so the env-var / provider name is still unique.
    if not base:
        base = "src_" + uuid.uuid4().hex[:6]
    existing = {d.get("id") for d in _read_all()}
    if base not in existing:
        return base
    return f"{base}_{uuid.uuid4().hex[:6]}"


def add_custom_source(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a custom source. Returns the persisted definition."""
    label = (payload.get("label") or "").strip()
    if not label:
        raise ValueError("自定义文献库名称不能为空")
    source_id = _unique_id(label)
    definition = _normalize_definition(payload, source_id=source_id)
    items = _read_all()
    items.append(definition)
    _write_all(items)
    logger.info("Added custom literature source '%s' (id=%s)", label, source_id)
    return definition


def update_custom_source(source_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update a custom source's metadata. Env-var names stay tied to *source_id*,
    so credentials already saved in ``.env`` survive an edit."""
    items = _read_all()
    for i, d in enumerate(items):
        if d.get("id") == source_id:
            merged = dict(d)
            for k in (
                "label", "description", "url", "base_url", "provider_type", "auth_scheme",
                "api_key_header", "query_param", "method", "field_types",
                "login_url", "login_user_field", "login_password_field",
                "login_extra_fields", "search_url", "sso_url", "sso_referer", "token_scheme",
            ):
                if k in payload:
                    merged[k] = payload[k]
            items[i] = _normalize_definition(merged, source_id=source_id)
            _write_all(items)
            logger.info("Updated custom literature source id=%s", source_id)
            return items[i]
    return None


def delete_custom_source(source_id: str) -> bool:
    """Remove a custom source and best-effort purge its saved credentials."""
    items = _read_all()
    kept = [d for d in items if d.get("id") != source_id]
    if len(kept) == len(items):
        return False
    _write_all(kept)
    # purge orphaned credentials so no secrets linger in .env
    try:
        from vermes_cli.env import remove_env_value
    except Exception:  # noqa: BLE001
        try:
            from vermes_cli.config import remove_env_value  # type: ignore
        except Exception:  # noqa: BLE001
            remove_env_value = None  # type: ignore
    if remove_env_value:
        for d in items:
            if d.get("id") == source_id:
                for f in d.get("fields", []):
                    try:
                        remove_env_value(f.get("key"))
                    except Exception:  # noqa: BLE001
                        pass
    logger.info("Deleted custom literature source id=%s", source_id)
    return True


def get_custom_service_entries() -> Dict[str, Dict[str, Any]]:
    """Return registry-shaped entries (category='literature') merged into
    :func:`agent.service_credentials.get_registered_services`."""
    out: Dict[str, Dict[str, Any]] = {}
    for d in _read_all():
        sid = d.get("id")
        if not sid:
            continue
        fields = d.get("fields") or []
        norm_fields = [
            {
                "key": f["key"],
                "kind": f.get("kind", "extra"),
                "label": f.get("label", f["key"]),
                "secret": bool(f.get("secret")),
            }
            for f in fields
        ]
        out[sid] = {
            "id": sid,
            "label": d.get("label", sid),
            "description": d.get("description", ""),
            "url": d.get("url", ""),
            "category": "literature",
            "custom": True,
            "fields": norm_fields,
        }
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 凭证块「输入识别」：把用户粘贴的卡号/密码/网址文本解析成结构化字段，
# 并一键注册为自定义文献源（同时把凭证落盘到 .env，复用掩码/审计）。
# 典型输入（中文第三方卡号卡密文献网关）：
#     卡号：83219570
#     密码：335779
#     【使用方法】复制网址 http://3.shutong2.com/ 到浏览器登录即可。
# ─────────────────────────────────────────────────────────────────────────────

# 宽松匹配的识别规则（兼容中文全/半角冒号与空格）。
_URL_RE = re.compile(
    r"https?://[^\s，。、）)】\]\n<>\"'\u3002\uff1b\uff0c\uff1a]+"
)
_DOMAIN_RE = re.compile(r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+\.[a-zA-Z]{2,}(?:/[^\s，。、）)】\n]*)?")
_USER_RE = re.compile(
    r"(?:卡\s*号|账\s*号|用\s*户\s*名|用\s*户|学\s*号|工\s*号|会员\s*号|编\s*号)\s*[:：]\s*([^\s，。、）)】\n]+)"
)
_PWD_RE = re.compile(
    r"(?:密\s*码|口\s*令|卡\s*密\s*码|查\s*询\s*密\s*码|登\s*录\s*密\s*码)\s*[:：]\s*([^\s，。、）)】\n]+)"
)
_KEY_RE = re.compile(
    r"(?:api[_ ]?key|密钥|令牌|token|access[_ ]?key)\s*[:：]\s*([^\s，。、）)】\n]+)",
    re.IGNORECASE,
)
_LABEL_RE = re.compile(r"(?:名称|文献库名|图书馆名|站点名|平台名)\s*[:：]\s*([^\n]{1,40})")
_LABEL_KEY_RE = re.compile(
    r"(?:网址|网关|站点|链接|接口地址|地址|域名)\s*[:：]\s*([^\s，。、）)】\n]+)"
)


def _clean(v: str) -> str:
    if not v:
        return ""
    return v.strip().strip("\"'“”‘’").strip()


def _mask(v: str) -> str:
    """脱敏展示：保留首尾各两位，中间打码。"""
    if not v:
        return ""
    if len(v) <= 2:
        return "*" * len(v)
    return v[:2] + "*" * max(2, len(v) - 4) + v[-2:]


def parse_literature_credential_block(text: str) -> Dict[str, Any]:
    """从用户粘贴的凭证文本中识别文献库字段。

    Returns::

        {
          "ok": bool,
          "label": str,          # 建议的文献库名称（缺省取域名）
          "url": str,            # 网关/站点地址
          "user": str | None,    # 卡号 / 账号
          "password": str | None,
          "api_key": str | None,
          "detected_auth": str,  # "form" / "bearer" / None
          "warnings": List[str],
        }

    识别规则对中文常见格式鲁棒：``卡号：``/``密码：``/``账号：``/``用户名：``，
    以及散落在说明文字里的 ``复制网址 http://...`` / ``网关地址：http://...``。
    """
    if not text or not text.strip():
        return {"ok": False, "label": "", "url": "", "user": None,
                "password": None, "api_key": None, "detected_auth": None,
                "warnings": ["文本为空"]}

    url = ""
    m = _URL_RE.search(text)
    if m:
        url = m.group(0).rstrip("/")
    if not url:  # 退而求其次：标签后的裸域名
        km = _LABEL_KEY_RE.search(text)
        if km:
            cand = _clean(km.group(1))
            if "." in cand:
                url = ("http://" + cand) if not cand.startswith("http") else cand
        if not url:
            dm = _DOMAIN_RE.search(text)
            if dm:
                url = "http://" + dm.group(0)

    user = _clean(_USER_RE.search(text).group(1)) if _USER_RE.search(text) else None
    password = _clean(_PWD_RE.search(text).group(1)) if _PWD_RE.search(text) else None
    api_key = _clean(_KEY_RE.search(text).group(1)) if _KEY_RE.search(text) else None

    label = ""
    lm = _LABEL_RE.search(text)
    if lm:
        label = _clean(lm.group(1))
    if not label and url:
        from urllib.parse import urlparse

        netloc = urlparse(url).netloc or url
        label = netloc.replace("www.", "") or "第三方文献库"
    if not label:
        label = "第三方文献库"

    warnings: List[str] = []
    detected_auth: Optional[str] = None
    if user and password:
        detected_auth = "form"  # 卡号+密码 → 表单登录网关
        if not url:
            warnings.append("已识别卡号与密码，但未识别到网址；请在设置中补全网关/登录地址")
    elif api_key:
        detected_auth = "bearer"
        if not url:
            warnings.append("已识别 API Key，但未识别到网址；请在设置中补全网关地址")
    elif (user or password) and not (user and password):
        warnings.append("卡号与密码需成对出现才能使用表单登录；已识别其一，请补齐全")
    elif url:
        warnings.append("仅识别到网址，未识别卡号/密码/API Key；将按无认证创建，可能需要手动设置")

    ok = bool(url or user or password or api_key)
    return {
        "ok": ok,
        "label": label,
        "url": url,
        "user": user,
        "password": password,
        "api_key": api_key,
        "detected_auth": detected_auth,
        "warnings": warnings,
    }


def _build_credential_summary(parsed: Dict[str, Any]) -> str:
    parts = []
    if parsed.get("user"):
        parts.append(f"卡号 {_mask(parsed['user'])}")
    if parsed.get("password"):
        parts.append("密码 已保存")
    if parsed.get("api_key"):
        parts.append(f"API Key {_mask(parsed['api_key'])}")
    if parsed.get("url"):
        parts.append(f"网址 {parsed['url']}")
    return " · ".join(parts)


_EMPIRECMS_SIG = "/e/member/"


def _looks_like_empirecms(url: str) -> bool:
    """轻量探测：站点是否为 EmpireCMS（购买到的第三方中文文献库多为帝国CMS）。

    这类站点登录表单字段是 ``username``（非通用的 ``user``），且需
    ``enews=login`` 等隐藏域作为登录动作标识；通用 ``form`` 注册若不修正字段
    会登录失败。这里只做一次只读 GET 检查 ``/e/member/`` 特征，失败则保守返回 False。
    """
    if not url:
        return False
    candidates = [url.rstrip("/") + "/e/member/login/", url.rstrip("/") + "/"]
    try:
        import httpx

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        with httpx.Client(timeout=12, follow_redirects=True, headers=headers) as c:
            for cand in candidates:
                try:
                    r = c.get(cand)
                except Exception:  # noqa: BLE001
                    continue
                if r.status_code == 200 and _EMPIRECMS_SIG in r.text:
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


def register_source_from_credential_block(
    text: str, *, persist_credentials: bool = True
) -> Dict[str, Any]:
    """解析粘贴的凭证块并一键注册为自定义文献源。

    返回的 ``summary`` 已脱敏；真实凭证（若有）写入 ``.env`` 的命名空间
    ``LIT_<ID>_*``（与 UI 行为一致，受掩码/审计保护），**不**进入本函数或日志。
    """
    parsed = parse_literature_credential_block(text)
    if not parsed["ok"]:
        return {
            "success": False,
            "error": "未能从文本中识别到文献库凭证（需要 网址 / 卡号 / 密码 / API Key 中至少一个）",
            "parsed": parsed,
        }

    url = parsed["url"]
    field_types: List[str] = []
    if parsed.get("api_key"):
        field_types.append("api_key")
    if url:
        field_types.append("base_url")
    if parsed.get("user"):
        field_types.append("user")
    if parsed.get("password"):
        field_types.append("password")
    if not field_types:
        field_types = ["user", "password"]

    auth = parsed["detected_auth"] or "form"
    definition: Dict[str, Any] = {
        "label": parsed["label"],
        "description": "自动识别接入的第三方文献库（卡号+密码表单登录）"
        if auth == "form" else "自动识别接入的第三方文献库",
        "url": url,
        "base_url": url,
        "auth_scheme": auth,
        "query_param": "q",
        "method": "GET",
        "field_types": field_types,
    }

    # 书童 shutong 专用适配器识别：域名命中即标注 provider_type，
    # 并预填 EmpireCMS 登录隐藏域（enews/lifetime/ecmsfrom）。
    is_shutong = bool(url) and "shutong" in (url or "").lower()
    if is_shutong:
        definition["provider_type"] = "shutong"

    if auth == "form":
        empirecms = is_shutong or _looks_like_empirecms(url)
        if empirecms:
            # EmpireCMS 真机抓包（书童/文轩等第三方中文文献代理）：真实登录处理
            # 脚本是 /e/member/doaction.php，表单字段名是 username（非通用的
            # user），且需 enews=login/tobind/lifetime/ecmsfrom 等帝国CMS登录动作
            # 隐藏域。不修正则登录 POST 到根路径、字段名错误 → 登录失败、SSO 不
            # 跳转、search 静默 0 篇（用户"粘贴网站+账号+密码"本应直接可用）。
            definition["login_url"] = url.rstrip("/") + "/e/member/doaction.php"
            definition["login_user_field"] = "username"
        else:
            definition["login_url"] = url
            definition["login_user_field"] = "user"
        definition["search_url"] = url
        definition["login_password_field"] = "password"
        if is_shutong:
            definition["login_extra_fields"] = {
                "enews": "login",
                "tobind": "0",
                "lifetime": "0",
                "ecmsfrom": "/zhongwenku/",
            }
            definition["sso_url"] = (url.rstrip("/") + "/l77.php")
            definition["sso_referer"] = (url.rstrip("/") + "/zhongwenku/")
            definition["token_scheme"] = "bearer"
        elif _looks_like_empirecms(url):
            definition["login_extra_fields"] = {
                "enews": "login",
                "tobind": "0",
                "lifetime": "0",
                "ecmsfrom": "/",
            }
        else:
            definition["login_extra_fields"] = {}

    try:
        defn = add_custom_source(definition)
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "error": f"创建自定义文献源失败: {exc}",
            "parsed": parsed,
        }

    source_id = defn["id"]
    cred_keys: List[str] = []
    if persist_credentials and (parsed.get("user") or parsed.get("password")
                                or parsed.get("api_key") or url):
        try:
            from vermes_cli.config import save_env_value

            prefix = f"{ENV_PREFIX}{source_id.upper()}_"
            if parsed.get("api_key"):
                save_env_value(prefix + "API_KEY", parsed["api_key"])
                cred_keys.append(prefix + "API_KEY")
            if url:
                save_env_value(prefix + "BASE_URL", url)
                cred_keys.append(prefix + "BASE_URL")
            if parsed.get("user"):
                save_env_value(prefix + "USER", parsed["user"])
                cred_keys.append(prefix + "USER")
            if parsed.get("password"):
                save_env_value(prefix + "PASSWORD", parsed["password"])
                cred_keys.append(prefix + "PASSWORD")
        except Exception as exc:  # noqa: BLE001
            logger.warning("register_source_from_credential_block: 凭证落盘失败: %s", exc)
            cred_keys.append(f"(凭证落盘失败: {exc})")

    return {
        "success": True,
        "source_id": source_id,
        "label": defn["label"],
        "auth_scheme": auth,
        "summary": _build_credential_summary(parsed),
        "credential_keys": cred_keys,
        "warnings": parsed.get("warnings", []),
        "source": {
            "id": source_id,
            "label": defn["label"],
            "auth_scheme": auth,
            "url": url,
        },
    }
