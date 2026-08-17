"""mfgcad 专业软件后端适配层。

把 Vermes 3D 从「纯 AI 一次出图」升级为「AI 编排层 × 行业专业软件」。
`ProToolAdapter` 是统一抽象（模型无关 × 工具无关）：每款行业专业软件
对应一个参考实现，FreeCAD 为首个开源后端（见 `freecad_adapter.py`）。

设计来源：`PRO_TOOL_ADAPTER_DESIGN.md` §3。

本包不依赖 FreeCAD / build123d 等重引擎，纯标准库即可 import，
因此可作为契约被 web_server / 前端 / 测试独立加载。
"""

from .base import AdapterResult, EditOp, FeatureNode, ProToolAdapter

__all__ = ["ProToolAdapter", "FeatureNode", "EditOp", "AdapterResult"]
