"""BOM + 组装指南生成。

从 mfgcad session 提取零件信息（参数、体积、材料推断），
用 LLM 生成结构化 BOM 表和组装指南。

纯 LLM 文本能力，不依赖引擎——对标 BLUEPRINT 最被称道的卖点。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional


def _mfg_home() -> Path:
    """本地复刻 _mfg_home 避免循环 import。"""
    return Path.home() / ".vermes" / "mfgcad"


def _load_session(session_id: str) -> dict:
    """读取 session.json。"""
    p = _mfg_home() / "sessions" / session_id / "session.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_source(session_id: str) -> Optional[str]:
    """读取 build123d 源码。"""
    p = _mfg_home() / "sessions" / session_id / "build123d_source.py"
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return None


def _load_parameters(session_id: str) -> list[dict]:
    """读取参数化抽出的参数。"""
    p = _mfg_home() / "sessions" / session_id / "parameters.json"
    if not p.is_file():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _infer_material(request: str) -> str:
    """从 NL 请求推断材料。"""
    req_lower = request.lower()
    material_map = {
        "铝": "铝合金 6061", "aluminum": "铝合金 6061",
        "钢": "碳钢 45#", "steel": "碳钢 45#", "不锈钢": "不锈钢 304", "stainless": "不锈钢 304",
        "铜": "黄铜 H62", "copper": "黄铜 H62", "brass": "黄铜 H62",
        "塑料": "ABS", "plastic": "ABS", "abs": "ABS",
        " PLA": "PLA", "pla": "PLA",
        "尼龙": "尼龙 PA66", "nylon": "尼龙 PA66",
        "亚克力": "亚克力 PMMA", "acrylic": "亚克力 PMMA",
    }
    for key, mat in material_map.items():
        if key in req_lower:
            return mat
    return "通用（待指定）"


def _build_bom_prompt(
    session: dict,
    source: Optional[str],
    parameters: list[dict],
    request: str,
    preset: Optional[dict] = None,
) -> str:
    """构造 BOM 生成提示词。"""
    parts = [
        "你是制造业 BOM 工程师。根据以下 3D 建模信息生成结构化 BOM 表和组装指南。",
        "",
        f"## 建模请求\n{request}",
        "",
    ]

    if session.get("volume_mm3") is not None:
        vol = session["volume_mm3"]
        parts.append(f"## 体积\n{vol:.2f} mm³（{vol/1000:.3f} cm³，{vol/1000000:.6f} m³）")
        parts.append("")

    if parameters:
        parts.append("## 参数化尺寸")
        for p in parameters:
            unit = p.get("unit", "mm")
            parts.append(f"- {p.get('name', '?')}: {p.get('value', '?')} {unit}（{p.get('desc', '')}）")
        parts.append("")

    material = _infer_material(request)
    if preset and preset.get("material"):
        material = preset["material"]
    parts.append(f"## 推断材料\n{material}")
    parts.append("")

    if source:
        parts.append("## build123d 源码（参考）")
        parts.append(f"```python\n{source[:2000]}\n```")
        parts.append("")

    parts.append("""## 输出要求

### 1. BOM 表
用 Markdown 表格，列：序号 | 零件名称 | 规格/尺寸 | 材料 | 数量 | 备注
- 单件制品拆成组成部件（如笔筒=筒身+底板）
- 标注关键公差（如果有尺寸参数）
- 备注列写加工工艺建议（如"车削"、"3D 打印 FDM"、"注塑"）

### 2. 组装指南
分步骤写，每步：
- 步骤编号 + 标题
- 操作描述（动词开头）
- 所需工具/夹具
- 注意事项

### 3. 成本估算（粗估）
- 材料成本（按体积 × 密度 × 单价）
- 加工工时估算
- 总估价范围

### 4. 3D 打印建议（如果是塑料材料）
- 推荐工艺（FDM/SLA/SLS）
- 层高、填充率、支撑建议
- 打印时间估算
""")
    return "\n".join(parts)


async def generate_bom(
    session_id: str,
    api_key: str,
    base_url: str,
    model: str,
    preset: Optional[dict] = None,
) -> str:
    """生成 BOM + 组装指南。

    Returns Markdown 文本。
    """
    import httpx

    session = _load_session(session_id)
    if not session:
        return f"❌ 未找到会话 {session_id}，请先执行 mfg_text_to_cad 生成模型。"

    source = _load_source(session_id)
    parameters = _load_parameters(session_id)
    request = session.get("request", "")

    prompt = _build_bom_prompt(session, source, parameters, request, preset)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是资深制造业 BOM 工程师和工艺规划专家。输出纯 Markdown，不要加代码块包裹。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
