#!/usr/bin/env python3
"""
MCP server for Agent Studio — 模板驱动的 AI 内容创作。
Tool: run_template — 加载模板 + 执行 pipeline，支持 Writer/Designer/Humanizer/Pipeline
Key: reads AGNES_API_KEY from ~/.vermes/.env at runtime.
"""
import os
import json
import sys
import httpx
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("content-studio")

# ── 配置 ──────────────────────────────────────────────────────

def get_key():
    env_path = os.path.expanduser("~/.vermes/.env")
    try:
        with open(env_path) as f:
            for line in f:
                if line.startswith("AGNES_API_KEY"):
                    k = line.split("=", 1)[1].strip().strip("\"'")
                    if k:
                        return k
    except FileNotFoundError:
        pass
    return os.environ.get("AGNES_API_KEY", "")

API_BASE = "https://apihub.agnes-ai.com/v1"
VERIFY = False  # macOS LibreSSL workaround

# 模板目录
TEMPLATES_DIR = Path(__file__).parent / "builtin-mcp" / "content-studio" / "templates" / "studio"

# ── 工具函数 ──────────────────────────────────────────────────

def _load_template_yaml(template_key: str) -> dict:
    """加载 YAML 模板文件"""
    import yaml
    # 搜索路径: social/xiaohongshu → social-xiaohongshu → xiaohongshu
    search_names = [
        f"{template_key}.yaml",
        f"{template_key}.yml",
    ]
    if "-" in template_key:
        parts = template_key.split("-", 1)
        search_names.insert(0, f"{parts[0]}/{parts[1]}.yaml")

    for name in search_names:
        path = TEMPLATES_DIR / name
        if path.exists():
            with open(path) as f:
                return yaml.safe_load(f)
    raise FileNotFoundError(f"Template {template_key} not found in {TEMPLATES_DIR}")

def _call_llm(prompt: str, system_prompt: str = "", max_tokens: int = 2048, temperature: float = 0.8) -> str:
    """调用 Agnes LLM"""
    key = get_key()
    if not key:
        return "[API Key 未配置]"
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    try:
        resp = httpx.post(
            f"{API_BASE}/chat/completions",
            json={
                "model": "agnes-2.0-flash",
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=120, verify=VERIFY,
        )
        if resp.status_code != 200:
            return f"[LLM 错误: {resp.status_code}] {resp.text[:200]}"
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[LLM 失败: {e}]"

def _call_image(prompt: str, size: str = "1024x1024") -> dict:
    """调用 Agnes Image"""
    key = get_key()
    if not key:
        return {"image_url": "[API Key 未配置]", "error": True}
    try:
        resp = httpx.post(
            f"{API_BASE}/images/generations",
            json={"model": "agnes-image-2.1-flash", "prompt": prompt, "size": size, "n": 1},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=120, verify=VERIFY,
        )
        resp.raise_for_status()
        data = resp.json()
        url = data["data"][0]["url"]
        # 下载到本地
        out_dir = Path(os.path.expanduser("~/Desktop/agent-studio-output/images"))
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
        uid = __import__("uuid").uuid4().hex[:8]
        local_path = out_dir / f"agent_{ts}_{uid}.jpg"
        img_resp = httpx.get(url, timeout=30, verify=VERIFY)
        img_resp.raise_for_status()
        local_path.write_bytes(img_resp.content)
        return {"image_url": str(local_path), "dimensions": size, "local": True}
    except Exception as e:
        return {"image_url": f"[图片失败: {e}]", "dimensions": size, "error": True}

def _call_humanize(text: str) -> str:
    """人类化文案"""
    system = """你是一个文案人类化专家。任务：检查文案是否像真人写的，如果像 AI 就重写它。
要求：使用口语化表达，加入个人感受和体验，避免"首先/其次/最后"等结构词，使用 emoji 和语气词，保持真实感。"""
    return _call_llm(f"请人类化以下文案：\n\n{text}", system_prompt=system, temperature=1.0)

# ── MCP Tools ─────────────────────────────────────────────────

@mcp.tool()
def list_templates() -> str:
    """列出所有可用的内容创作模板"""
    if not TEMPLATES_DIR.exists():
        return f"模板目录不存在: {TEMPLATES_DIR}"
    import yaml
    results = []
    for yaml_file in sorted(TEMPLATES_DIR.rglob("*.yaml")):
        rel = yaml_file.relative_to(TEMPLATES_DIR)
        key = str(rel.with_suffix("")).replace("/", "-")
        try:
            with open(yaml_file) as f:
                data = yaml.safe_load(f)
            info = data.get("template", {})
            results.append({
                "key": key,
                "name": info.get("name", key),
                "description": info.get("description", ""),
                "version": info.get("version", "1.0"),
                "inputs": [i["name"] for i in info.get("inputs", [])],
                "stages": [s.get("name") for s in info.get("pipeline", [])],
            })
        except Exception as e:
            results.append({"key": key, "error": str(e)})
    if not results:
        return "暂无模板"
    return json.dumps(results, ensure_ascii=False, indent=2)


@mcp.tool()
def run_template(template_key: str, params: str) -> str:
    """
    执行一个内容创作模板。
    template_key: 模板名称（如 social-xiaohongshu, social-short-video）
    params: JSON 字符串，模板输入参数
    返回各 stage 执行结果
    """
    import yaml
    import re
    from datetime import datetime

    # 加载模板
    try:
        doc = _load_template_yaml(template_key)
    except FileNotFoundError as e:
        return f"❌ 模板不存在: {e}"
    except Exception as e:
        return f"❌ 加载模板失败: {e}"

    tpl = doc.get("template", {})
    pipeline = tpl.get("pipeline", [])
    if not pipeline:
        return "❌ 模板没有定义 pipeline"

    # 解析参数
    try:
        inputs = json.loads(params) if isinstance(params, str) else params
    except json.JSONDecodeError:
        return f"❌ params 不是有效 JSON: {params[:100]}"

    # 填充默认值
    for inp in tpl.get("inputs", []):
        if inp["name"] not in inputs:
            if "default" in inp:
                inputs[inp["name"]] = inp["default"]
            else:
                inputs[inp["name"]] = ""

    # 执行 pipeline
    stage_outputs = {}
    results = []
    overall_status = "success"

    for stage in pipeline:
        sname = stage["name"]
        sagent = stage.get("agent", "")
        dep = stage.get("depends_on", [])
        prompt_tpl = stage.get("prompt_template", "")
        on_fail = stage.get("on_fail", "skip")

        # 检查依赖
        deps_met = all(d in stage_outputs for d in dep)
        if not deps_met:
            results.append(f"⏭️  {sname} ({sagent}): 跳过 — 依赖未满足 {dep}")
            continue

        # 渲染 prompt
        context = {
            "inputs": inputs,
            "stage": stage_outputs,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        def _replace_var(m):
            expr = m.group(1).strip()
            parts = expr.split(".")
            val = context
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p, "")
                else:
                    val = ""
                    break
            if isinstance(val, (dict, list)):
                return json.dumps(val, ensure_ascii=False)
            return str(val) if val else ""
        rendered_prompt = re.sub(r"\{\{\s*(.+?)\s*\}\}", _replace_var, prompt_tpl)

        # 执行
        try:
            if sagent == "writer":
                output_text = _call_llm(rendered_prompt)
                title = output_text.split("\n")[0][:50] if "\n" in output_text else output_text[:50]
                output = {"copy": output_text, "title": title}
            elif sagent == "designer":
                style = inputs.get("image_style", "写实摄影")
                prompt_text = f"{style}风格: {rendered_prompt}"
                output = _call_image(prompt_text)
            elif sagent == "humanizer":
                prev_copy = ""
                for k, v in stage_outputs.items():
                    if isinstance(v, dict) and "copy" in v:
                        prev_copy = v["copy"]
                        break
                if not prev_copy:
                    prev_copy = rendered_prompt
                final_copy = _call_humanize(prev_copy)
                output = {"final_copy": final_copy, "status": "humanized"}
            else:
                # mock / 自定义 agent
                output = {"mock_output": rendered_prompt[:200], "note": f"自定义 agent ({sagent})，返回渲染后的 prompt"}

            stage_outputs[sname] = output
            preview = json.dumps(output, ensure_ascii=False)[:150]
            results.append(f"✅ {sname} ({sagent}): {preview}")
        except Exception as e:
            if on_fail == "skip":
                results.append(f"⏭️  {sname} ({sagent}): 跳过 — {e}")
            else:
                results.append(f"❌ {sname} ({sagent}): 失败 — {e}")
                overall_status = "failed"
                break

    # 构建最终输出
    summary = f"## 内容创作完成\n\n状态: {overall_status}\n\n"
    summary += "\n".join(results)

    # 提取最终文案
    final_copy = ""
    for k, v in stage_outputs.items():
        if isinstance(v, dict):
            final_copy = v.get("final_copy") or v.get("copy") or final_copy

    if final_copy:
        summary += f"\n\n---\n### 最终文案\n\n{final_copy[:2000]}"

    return summary


@mcp.tool()
def get_template_detail(template_key: str) -> str:
    """获取单个模板的完整详情（输入字段、pipeline 步骤）"""
    import yaml
    try:
        doc = _load_template_yaml(template_key)
    except FileNotFoundError as e:
        return f"❌ 模板不存在: {e}"

    tpl = doc.get("template", {})
    info = {
        "name": tpl.get("name", ""),
        "version": tpl.get("version", ""),
        "description": tpl.get("description", ""),
        "inputs": tpl.get("inputs", []),
        "pipeline": [{"name": s.get("name"), "agent": s.get("agent"), "depends_on": s.get("depends_on", [])} for s in tpl.get("pipeline", [])],
    }
    return json.dumps(info, ensure_ascii=False, indent=2)


# ── 启动 ──────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
