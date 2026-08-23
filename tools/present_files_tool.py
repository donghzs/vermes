"""present_files — Agent 主动推送产物到右侧工作台面板。

对标 WorkBuddy present_files 工具（v5.1.0+），让 Agent 执行完任务后
主动声明交付物，而非等用户从回复文本中发现。

工具行为：
  1. 接收文件路径列表
  2. 验证文件存在
  3. 通过 SSE tool_end 事件的 artifacts 字段推送到前端
  4. 返回简短确认文本

SSE 推送由 chat.py 的 _extract_artifact_paths 自动处理（已有逻辑），
本工具的核心价值是让 Agent 显式声明产物，而非依赖路径正则启发式提取。
"""

import json
import logging
import os
from pathlib import Path

from tools.registry import registry

logger = logging.getLogger(__name__)

PRESENT_FILES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "present_files",
        "description": (
            "向用户展示任务交付产物。当完成文档、报告、图表、代码文件等交付物后，"
            "调用此工具将产物推送到用户界面右侧的产物面板，用户可点击预览、下载或在文件夹中查看。"
            "应在任务完成时调用，列出所有交付文件路径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "交付文件路径列表（绝对路径或相对工作目录的路径）",
                },
                "description": {
                    "type": "string",
                    "description": "对交付物的简要说明（可选）",
                },
            },
            "required": ["files"],
        },
    },
}


def _handle_present_files(args, **kwargs):
    files = args.get("files", [])
    description = args.get("description", "")

    if not files:
        return json.dumps({"error": "files is required"}, ensure_ascii=False)

    results = []
    for fpath in files:
        # 展开路径
        full = Path(fpath).expanduser()
        if not full.is_absolute():
            # 尝试相对于 cwd
            full = Path.cwd() / fpath

        exists = full.exists()
        size = full.stat().st_size if exists else 0
        fname = full.name or fpath

        results.append({
            "path": str(full),
            "title": fname,
            "exists": exists,
            "size": size,
            "source": "present_files",
        })

    # 构造 preview 文本，让 chat.py 的 _extract_artifact_paths 也能提取到
    # 同时在返回值中包含结构化 JSON
    preview_lines = []
    if description:
        preview_lines.append(f"交付说明: {description}")
    preview_lines.append(f"交付产物 {len(results)} 个:")
    for r in results:
        status = "✅" if r["exists"] else "⚠️ (文件不存在)"
        size_str = f"{r['size']:,} bytes" if r["exists"] else "N/A"
        preview_lines.append(f"  {r['path']} ({size_str}) {status}")

    preview = "\n".join(preview_lines)

    # 关键：在 preview 中包含文件路径，让 chat.py 的 _ARTIFACT_EXT_RE 能提取
    # 这样 SSE tool_end 事件的 artifacts 字段会自动填充
    return preview


def _check_present_files_requirements(**kwargs):
    """present_files 无特殊环境要求，始终可用。"""
    return True


registry.register(
    name="present_files",
    toolset="file",
    schema=PRESENT_FILES_SCHEMA,
    handler=_handle_present_files,
    check_fn=_check_present_files_requirements,
    emoji="📦",
    max_result_size_chars=10_000,
)
