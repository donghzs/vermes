"""ProToolAdapter 统一接口契约（M1-1）。

来源：`PRO_TOOL_ADAPTER_DESIGN.md` §3。纯标准库，无 FreeCAD / build123d 依赖。

为什么需要这层抽象
------------------
Vermes 不做「又一个 AI CAD」，而是做**行业专业软件的 AI 编排层**
（模型无关 × 工具无关）。每款专业软件（FreeCAD / Blender / 未来的
SolidWorks·Fusion BYO 等）都是一个 `ProToolAdapter` 参考实现，对上
暴露同一组语义操作（import_step / get_feature_tree / apply_edit_op /
export …），对下把语义翻译为该软件的原生 API（见 §4.3 翻译表）。

上层（web_server 的 `POST /api/mfgcad/edit`、agent 的 `mfg_edit_feature`
工具、前端 ThreeDStudio 编辑面板）只跟这套契约对话，不关心后端是谁。
这决定了「可回滚特征树」「mold-ready 模具变现」等能力可以被复用、被测试。

术语
----
- session_id：一次建模会话的稳定 id，对应 `sessions/<sid>/` 目录。
- FeatureNode：专业软件特征树的一个节点（body / fillet / draft / pattern …）。
- EditOp：对特征树施加的一次语义编辑（圆角 / 拔模 / 阵列 / 布尔 …）。
- native_doc：专业软件原生文档（如 FreeCAD `.FCStd`），是会话真相源。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class FeatureNode:
    """专业软件特征树中的一个节点。

    id 在 session 内稳定唯一，是 agent / 前端多轮编辑的锚点（§11）。
    children 表达特征嵌套（如 Body 下挂 Fillet / Pattern）。
    """

    id: str  # 稳定节点 id（session 内唯一）
    kind: str  # "body"|"fillet"|"draft"|"pattern"|"boolean"|"sketch"|...
    label: str
    params: dict[str, Any] = field(default_factory=dict)
    children: list["FeatureNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为可 JSON 化的字典（供特征树 API / 前端渲染）。"""
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "params": self.params,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeatureNode":
        if not isinstance(data, dict):
            raise TypeError(f"FeatureNode.from_dict 期望 dict，收到 {type(data).__name__}")
        missing = {"id", "kind", "label"} - set(data)
        if missing:
            raise ValueError(f"FeatureNode 缺少必填字段: {sorted(missing)}")
        return cls(
            id=str(data["id"]),
            kind=str(data["kind"]),
            label=str(data["label"]),
            params=dict(data.get("params", {}) or {}),
            children=[cls.from_dict(c) for c in (data.get("children", []) or [])],
        )


@dataclass
class EditOp:
    """对特征树施加的一次语义编辑。

    op/target/params 由前端控件或 agent 工具生成，经 `apply_edit_op`
    翻译为后端原生原语（§4.3 翻译表）。
    """

    op: str  # fillet|draft|pattern|boolean|scale|split|...（含 mold-ready 专属见 §12）
    target: str  # "edges_all"|"edge:<id>"|"face:<id>"|"body:<id>"|"tool:<id>"
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"op": self.op, "target": self.target, "params": self.params}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EditOp":
        if not isinstance(data, dict):
            raise TypeError(f"EditOp.from_dict 期望 dict，收到 {type(data).__name__}")
        missing = {"op", "target"} - set(data)
        if missing:
            raise ValueError(f"EditOp 缺少必填字段: {sorted(missing)}")
        return cls(
            op=str(data["op"]),
            target=str(data["target"]),
            params=dict(data.get("params", {}) or {}),
        )


@dataclass
class AdapterResult:
    """一次适配器调用的结果。

    ok=False 时必须带 error（几何非法 / 引擎未就绪等），上层据此标红节点、
    不破坏已有特征树（§9 风险缓解）。
    """

    ok: bool
    feature_tree: Optional[list[FeatureNode]] = None
    native_doc: Optional[Path] = None  # .FCStd 等原生文档（真相源）
    exports: dict[str, Path] = field(default_factory=dict)  # {"stl":..., "step":...}
    error: Optional[str] = None

    @classmethod
    def ok_result(
        cls,
        feature_tree: Optional[list[FeatureNode]] = None,
        native_doc: Optional[Path] = None,
        exports: Optional[dict[str, Path]] = None,
        error: Optional[str] = None,
    ) -> "AdapterResult":
        return cls(
            ok=True,
            feature_tree=feature_tree,
            native_doc=native_doc,
            exports=exports or {},
            error=error,
        )

    @classmethod
    def err(cls, error: str) -> "AdapterResult":
        return cls(ok=False, error=error)

    def to_dict(self) -> dict[str, Any]:
        native = self.native_doc
        return {
            "ok": self.ok,
            "feature_tree": [n.to_dict() for n in self.feature_tree] if self.feature_tree else None,
            "native_doc": str(native) if native is not None else None,
            "exports": {k: str(v) for k, v in self.exports.items()},
            "error": self.error,
        }


class ProToolAdapter(ABC):
    """专业软件适配器统一抽象（模型无关 × 工具无关）。

    每个行业 / 每款专业软件一个参考实现。FreeCAD 为首个开源后端。

    实现契约要点（见设计文档 §3 / §9）：
    - `is_available()` 必须在 FreeCAD 缺失时返回 False（优雅降级，不抛异常）。
    - `ensure_ready(auto_setup=True)` 失败只返回 False，由上层提示去装引擎。
    - `apply_edit_op` 几何非法时返回 `ok=False` + error，绝不破坏已有树。
    - 所有路径以原生文档（.FCStd）为真，特征树 JSON 仅缓存。
    """

    name: str = "abstract"

    @abstractmethod
    def is_available(self) -> bool:
        """后端引擎是否在当前环境可用（FreeCAD 缺失 → False，不抛异常）。"""
        ...

    @abstractmethod
    def ensure_ready(self, auto_setup: bool = False) -> bool:
        """确保后端就绪；auto_setup=True 时尝试自动安装/下载引擎。失败时返回 False。"""
        ...

    @abstractmethod
    def create_doc(self, session_id: str) -> Path:
        """为会话新建原生文档，返回其路径。"""
        ...

    @abstractmethod
    def open(self, doc_path: str) -> bool:
        """打开已有原生文档（如 .FCStd）；成功返回 True。"""
        ...

    @abstractmethod
    def import_step(self, session_id: str, step_path: str) -> AdapterResult:
        """把 STEP 导入为可编辑特征树（D2：进 PartDesign::Body）。"""
        ...

    @abstractmethod
    def get_feature_tree(self, session_id: str) -> list[FeatureNode]:
        """提取当前会话的特征树（D4）。"""
        ...

    @abstractmethod
    def apply_edit_op(self, session_id: str, op: EditOp) -> AdapterResult:
        """对特征树施加一次语义编辑，返回更新后的结果（D3 翻译表）。"""
        ...

    @abstractmethod
    def export(self, session_id: str, formats: list[str]) -> dict[str, Path]:
        """导出指定格式（如 ["stl","step"]），返回 {格式: 路径}。"""
        ...

    @abstractmethod
    def close(self, session_id: str) -> None:
        """关闭并释放会话资源（含同步删 .FCStd 由上层负责）。"""
        ...
