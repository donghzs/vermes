"""mfgcad 歧义澄清 agent + preset 加载器。

P0a：Pro-CAD 式 Clarifier——用户 prompt 缺关键尺寸/形状不定/约束冲突时，
先追问再调引擎，避免 MAC 直生成错件。

纯 Vermes 侧（LLM 一次轻量调用判歧义 + 生成追问），不碰 MAC 源码。
复用统一凭证层的 key（与 mfg_text_to_cad 同源）。

设计原则：
- 框架给积木不给定式——clarify 是独立工具，不嵌入 mfg_text_to_cad 内部
- Agent 可以主动调 mfg_clarify 检查 request，也可以直接调 mfg_text_to_cad（跳过澄清）
- preset 是声明式 YAML，用户可自行扩展（~/.vermes/mfgcad/presets/）
- LLM 不可用时 fail-open（放行原始 request），不阻断建模
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent

# ── preset 加载 ──────────────────────────────────────────

_PRESETS_CACHE: dict[str, dict] | None = None


def _merge_presets(presets: dict, data: dict | None) -> dict:
    """把一份 YAML 数据中的 presets 合并进 presets dict。

    兼容两种 YAML 形态（用户对 list 形态极易写错，曾导致 loader 崩溃）：
      - dict:  {presets: {key: {...}}}
      - list:  {presets: [{name: key, ...}, ...]}
    其他形态（None / 标量）静默忽略，不抛异常。
    """
    raw = (data or {}).get("presets")
    if isinstance(raw, dict):
        for key, val in raw.items():
            if isinstance(val, dict):
                presets[key] = val
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                name = item.get("name") or item.get("key")
                if name:
                    presets[name] = item
    return presets


def _load_presets() -> dict[str, dict]:
    """加载 preset 定义，合并内置 + 用户目录。

    健壮性：单文件解析失败只 warn + 跳过，不让一次坏 preset 拖垮全部 clarify。
    """
    global _PRESETS_CACHE
    if _PRESETS_CACHE is not None:
        return _PRESETS_CACHE

    import logging
    import yaml

    _log = logging.getLogger(__name__)
    presets: dict[str, dict] = {}

    # 内置 preset
    builtin = _HERE / "presets.yaml"
    if builtin.is_file():
        try:
            data = yaml.safe_load(builtin.read_text(encoding="utf-8")) or {}
        except Exception as e:
            _log.warning("内置 presets.yaml 解析失败: %s", e)
            data = {}
        presets = _merge_presets(presets, data)

    # 用户自定义 preset（覆盖同名内置）；逐文件 try，坏文件不影响整体
    user_dir = Path.home() / ".vermes" / "mfgcad" / "presets"
    if user_dir.is_dir():
        for pkl in sorted(user_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(pkl.read_text(encoding="utf-8")) or {}
            except Exception as e:
                _log.warning("用户 preset %s 解析失败，已跳过: %s", pkl.name, e)
                continue
            presets = _merge_presets(presets, data)

    _PRESETS_CACHE = presets
    return presets


def get_preset_names() -> list[str]:
    """返回所有可用 preset 名。"""
    return sorted(_load_presets().keys())


def get_preset(name: str) -> dict | None:
    """取单个 preset 定义。"""
    return _load_presets().get(name)


# ── 歧义检测 ─────────────────────────────────────────────

_CLARIFY_SYSTEM = """你是一个 CAD 需求分析专家。你的任务是检查用户的自然语言建模请求是否包含足够信息来生成精确的 3D 模型。

给你一个场景 preset（定义了需要的槽位）和用户的原始请求，你需要：

1. 从用户请求中提取已给出的信息，匹配到 preset 的槽位上
2. 检查 required=true 的槽位是否都已提供
3. 检查尺寸是否完整（如说了"圆柱体"但只给了直径没给高度）
4. 检查是否存在矛盾（如壁厚 > 外径/2）

输出 JSON：
{
  "is_clear": true/false,           // true=信息足够可直接建模，false=需要追问
  "extracted": {                    // 从用户请求中提取到的槽位值
    "geometry_type": "...",
    "dimensions": "..."
  },
  "missing": [                      // 缺失的必填槽位
    {"name": "dimensions", "label": "关键尺寸", "reason": "用户说了圆柱体但未给高度"}
  ],
  "conflicts": [                    // 矛盾项
    {"items": ["壁厚3mm", "外径4mm"], "reason": "壁厚超过外径的一半，几何上不可行"}
  ],
  "clarification_question": "..."   // is_clear=false 时，给用户的追问（自然语言，一次问完所有缺失项）
}

注意：
- 只对 required=true 的缺失项判定为 missing
- 尺寸缺失是最高优先级的 missing（会导致生成错件）
- 如果用户请求已经足够清晰，直接返回 is_clear=true
- 追问要简洁友好，一次问完所有问题，不要逐个问
"""


def _resolve_api_key() -> str:
    """复用 mfgcad tools.py 的凭证解析。"""
    try:
        from vermes_cli.mfgcad.tools import _resolve_api_key as _resolve
        return _resolve()
    except Exception:
        return ""


def _resolve_base_url() -> str:
    """取活跃 provider 的 base_url。"""
    try:
        from vermes_cli.auth import get_active_provider, resolve_api_key_provider_credentials
        pid = get_active_provider()
        if pid:
            creds = resolve_api_key_provider_credentials(pid) or {}
            bu = creds.get("base_url") or ""
            if bu:
                return bu
    except Exception:
        pass
    return "https://api.deepseek.com/v1"


async def check_clarity(
    request: str,
    preset_name: str | None = None,
) -> dict[str, Any]:
    """检查建模请求是否清晰，返回歧义分析结果。

    Args:
        request: 用户原始自然语言建模请求
        preset_name: 场景 preset 名（如 mechanical_part / print_part）。
                     None 则自动猜测。

    Returns:
        {
            "is_clear": bool,
            "preset": str,           # 匹配到的 preset 名
            "extracted": dict,       # 已提取的槽位
            "missing": list,         # 缺失项
            "conflicts": list,       # 矛盾项
            "clarification_question": str,  # 追问（is_clear=false 时）
            "enhanced_request": str  # 增强后的 request（is_clear=true 时，补全默认值）
        }
    """
    presets = _load_presets()

    # 自动猜测 preset
    if not preset_name:
        preset_name = _guess_preset(request, presets)

    preset = presets.get(preset_name) if preset_name else None

    # 无 preset 或无 API key → fail-open
    api_key = _resolve_api_key()
    if not api_key or not preset:
        return {
            "is_clear": True,
            "preset": preset_name or "unknown",
            "extracted": {},
            "missing": [],
            "conflicts": [],
            "clarification_question": "",
            "enhanced_request": request,
        }

    # 构建 slot 描述给 LLM
    slot_desc = _format_slots_for_llm(preset)
    user_msg = f"""场景 preset: {preset_name}（{preset.get('label', '')}）
{preset.get('description', '')}

需要检查的槽位:
{slot_desc}

用户请求: {request}"""

    # 调 LLM
    try:
        result = await _call_llm_for_clarify(api_key, user_msg)
        if not result:
            return _fail_open(request, preset_name)

        # 补全 enhanced_request
        if result.get("is_clear"):
            result["enhanced_request"] = _build_enhanced_request(request, result.get("extracted", {}), preset)
        else:
            result["enhanced_request"] = request

        result["preset"] = preset_name
        return result

    except Exception:
        return _fail_open(request, preset_name)


def _guess_preset(request: str, presets: dict) -> str | None:
    """根据请求关键词猜测场景 preset。"""
    r = request.lower()
    # 3D 打印关键词
    if any(k in r for k in ["打印", "print", "fdm", "sla", "切片", "gcode", "pla", "petg", "abs材料"]):
        if "print_part" in presets:
            return "print_part"
    # 电商关键词
    if any(k in r for k in ["展示", "电商", "商品", "glb", "纹理", "pbr", "ar展示"]):
        if "ecommerce_display" in presets:
            return "ecommerce_display"
    # 影视关键词
    if any(k in r for k in ["道具", "影视", "短视频", "手办", "潮玩", "赛博", "古风", "科幻"]):
        if "film_prop" in presets:
            return "film_prop"
    # 默认机械零件
    if "mechanical_part" in presets:
        return "mechanical_part"
    return None


def _format_slots_for_llm(preset: dict) -> str:
    """格式化 preset 槽位为 LLM 可读描述。"""
    lines = []
    for slot in preset.get("slots", []):
        req = "必填" if slot.get("required") else "可选"
        default = f"（默认: {slot['default']}）" if slot.get("default") else ""
        lines.append(
            f"- {slot['label']}（{slot['name']}）[{req}]: {slot.get('description', '')}{default}"
        )
    return "\n".join(lines)


def _build_enhanced_request(request: str, extracted: dict, preset: dict) -> str:
    """构建增强 request——补全默认值，保留原文语义。"""
    if not extracted:
        return request

    parts = [request]
    for slot in preset.get("slots", []):
        name = slot.get("name", "")
        if name not in extracted and not slot.get("required") and slot.get("default"):
            parts.append(f"（{slot.get('label', name)}: {slot['default']}）")

    enhanced = " ".join(parts)
    return enhanced


def _fail_open(request: str, preset_name: str | None) -> dict:
    """fail-open：放行原始 request。"""
    return {
        "is_clear": True,
        "preset": preset_name or "unknown",
        "extracted": {},
        "missing": [],
        "conflicts": [],
        "clarification_question": "",
        "enhanced_request": request,
    }


async def _call_llm_for_clarify(api_key: str, user_msg: str) -> dict | None:
    """调 LLM 做歧义分析，返回结构化结果。"""
    import httpx

    base_url = _resolve_base_url()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": os.environ.get("MFGCAD_CLARIFY_MODEL", "deepseek-chat"),
                "messages": [
                    {"role": "system", "content": _CLARIFY_SYSTEM},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.1,
                "max_tokens": 800,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]

    import json
    return json.loads(content)
