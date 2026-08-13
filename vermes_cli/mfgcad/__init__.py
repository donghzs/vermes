"""mfgcad — Vermes 制造业 text-to-CAD 模块。

把清华 IEI Lab 开源 Multi-Agent-CAD (MAC) 作为独立引擎嵌入：
重依赖（build123d / cadquery-ocp / trimesh / langgraph / aider / cadpy）
只装在引擎自己的 venv（engine/.venv 或外部 MFG_CAD_ENGINE_PY），
Vermes 主 venv 不装这些（numpy 锁版本冲突）。

Vermes 侧只做薄编排：mfg_text_to_cad 把自然语言需求经子进程桥接
丢给 engine/run_mac.py，解析其 JSON 结果返回给模型。禁止自动打印/
自动打开查看器——只回结构化摘要 + STEP 路径，由人工或下游工具定稿。
"""

from .tools import register_tools

__all__ = ["register_tools"]
