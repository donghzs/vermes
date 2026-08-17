#!/usr/bin/env python
# vermes_freecad_bridge.py — 常驻 headless FreeCAD 子进程（FreeCAD 参考后端 · M1-2）
#
# 启动方式（由 FreeCAD 自带无 GUI 解释器 freecadcmd 拉起，不进 GUI 线程）：
#     freecadcmd vermes_freecad_bridge.py --sessions-dir <DIR>
# 注：设计文档 §4.1 写为 `freecadcmd -c vermes_freecad_bridge.py`；FreeCAD 的 freecadcmd
# 对「脚本文件」用「freecadcmd <脚本路径>」直接跑（sys.argv 透传），对「内联代码」才用 -c。
# 本实现按「脚本文件」方式启动，并在 __main__ 解析 --sessions-dir；若贵司 freecadcmd 仅支持
# -c，可改为 `freecadcmd -c "exec(open('<abs>/vermes_freecad_bridge.py').read())"`（M1-6 真机对齐）。
#
# 协议：从 stdin 逐行读 JSON 请求 {"cmd","session_id","payload"}；每行回一行 JSON 响应。
# 按 session_id 维护 App.Document 句柄表。仅在 freecadcmd 环境运行——本文件不得被主 venv import。
#
# 本文件承载 §4.3 编辑操作 → FreeCAD 原语翻译表 与 §4.4 特征树提取，是 FreeCAD 领域逻辑所在；
# ProToolAdapter（base.py）只负责语义契约，FreeCADAdapter（backends/freecad_adapter.py）只负责
# JSON 传输与响应解析。三者解耦：换后端只需换 bridge/adapter，不动上层。

import sys
import os
import json
import traceback
from pathlib import Path

# —— 仅 freecadcmd 内可用；主 venv import 本文件会失败（符合设计：bridge 是独立进程） ——
import FreeCAD as App  # 控制台里也叫 App
import Import  # STEP/几何导入导出
import Part  # B-rep 几何
import PartDesign  # 特征建模
import Mesh  # 网格导出

# sessions_root：.FCStd 真相源写这里（sessions/<sid>/native.FCStd）
_SESSIONS_DIR = Path(
    os.environ.get("VERMES_MFG_SESSIONS_DIR", "~/.vermes/mfgcad/sessions")
).expanduser()
for _i, _a in enumerate(sys.argv[1:]):
    if _a == "--sessions-dir" and _i + 1 < len(sys.argv[1:]):
        _SESSIONS_DIR = Path(sys.argv[1:][_i + 1]).expanduser()

DOCS = {}  # session_id -> App.Document


# ── 工具 ──────────────────────────────────────────────────

def _doc_path(sid: str) -> Path:
    return _SESSIONS_DIR / sid / "native.FCStd"


def _get_doc(sid: str):
    if sid not in DOCS:
        raise KeyError(f"session {sid} 未创建/打开")
    return DOCS[sid]


def _find_body(doc):
    for o in doc.Objects:
        if o.TypeId == "PartDesign::Body":
            return o
    return None


def _last_feature(doc):
    """取最近的非 Body 特征，作为阵列/编辑的基准（best-effort）。"""
    feat = None
    for o in doc.Objects:
        if o.TypeId != "PartDesign::Body":
            feat = o
    return feat


def _find_tool_body(doc, tool_name: str):
    if not tool_name:
        return None
    for o in doc.Objects:
        if o.Name == tool_name or o.Label == tool_name:
            return o
    return None


# 特征类型 → 语义 kind（供前端渲染 + agent 多轮锚定）
_KIND_MAP = {
    "PartDesign::Body": "body",
    "PartDesign::Fillet": "fillet",
    "PartDesign::Draft": "draft",
    "PartDesign::LinearPattern": "pattern",
    "PartDesign::PolarPattern": "pattern",
    "PartDesign::Boolean": "boolean",
    "PartDesign::Pad": "feature",
    "PartDesign::Pocket": "feature",
    "PartDesign::Hole": "feature",
    "Part::Scale": "scale",
    "Part::Slice": "split",
}


def _extract_params(obj, tid: str) -> dict:
    """抽取关键参数（数值字段），供特征树 JSON 展示与回滚。"""
    p: dict = {}
    try:
        if tid == "PartDesign::Fillet":
            p["radius"] = getattr(obj, "Radius", None)
        elif tid == "PartDesign::Draft":
            p["angle"] = getattr(obj, "Angle", None)
        elif tid in ("PartDesign::LinearPattern", "PartDesign::PolarPattern"):
            p["count"] = getattr(obj, "Count", None)
            if tid == "PartDesign::LinearPattern":
                p["length"] = getattr(obj, "Length", None)
            else:
                p["angle"] = getattr(obj, "Angle", None)
        elif tid == "PartDesign::Boolean":
            p["type"] = getattr(obj, "Type", None)
        elif tid == "Part::Scale":
            p["factor"] = getattr(obj, "Scale", None)
    except Exception:
        pass
    return {k: v for k, v in p.items() if v is not None}


def _extract_feature_tree(doc) -> list:
    nodes = []
    for o in doc.Objects:
        kind = _KIND_MAP.get(o.TypeId, "feature")
        params = _extract_params(o, o.TypeId)
        nodes.append({"id": o.Name, "kind": kind, "label": o.Label, "params": params})
    return nodes


# ── 核心操作 ──────────────────────────────────────────────

def _wrap_step_in_body(doc, step_path: str):
    """§4.2 STEP → PartDesign::Body 可编辑特征（D2）。"""
    Import.insert(step_path, doc.Name)  # STEP → Part.Shape
    shape_obj = doc.Objects[-1]
    body = doc.addObject("PartDesign::Body", "BaseBody")
    body.BaseFeature = shape_obj  # 包成 PartDesign Body，成为可编辑特征
    doc.recompute()
    return body


def _apply_edit_op(doc, op: dict):
    """§4.3 编辑操作词汇表 → FreeCAD 原语翻译表。返回 None；失败抛异常由调用方捕获。"""
    name = op.get("op")
    target = op.get("target", "")
    params = op.get("params", {}) or {}
    body = _find_body(doc)
    if body is None:
        raise RuntimeError("文档中无 PartDesign::Body，无法编辑")

    if name == "fillet":
        radius = float(params.get("radius", 1.0))
        edges = body.Shape.Edges
        f = doc.addObject("PartDesign::Fillet", "Fillet")
        f.Base = body
        f.Radius = radius
        if target.startswith("edge:"):
            idx = int(target.split(":", 1)[1])
            f.Edges = [(idx, radius)]
        else:  # edges_all
            f.Edges = [(i + 1, radius) for i in range(len(edges))]
        doc.recompute()

    elif name == "draft":
        angle = float(params.get("angle", 1.0))
        face_idx = int(target.split(":", 1)[1]) if target.startswith("face:") else 0
        faces = body.Shape.Faces
        if not (0 <= face_idx < len(faces)):
            raise RuntimeError(f"draft 目标面越界：face:{face_idx} / 共 {len(faces)} 面")
        d = doc.addObject("PartDesign::Draft", "Draft")
        d.Base = body
        d.NeutralFace = (face_idx + 1,)
        d.Angle = angle  # PartDesign::Draft.Angle 单位：度
        # 拔模方向取中性面法线（best-effort，版本差异以 try 兜底）
        try:
            d.Direction = faces[face_idx].normalAt(0, 0)
        except Exception:
            pass
        doc.recompute()

    elif name == "pattern":
        mode = params.get("mode", "linear")
        count = int(params.get("count", 2))
        feat = _last_feature(doc) or body
        if mode == "circular":
            p = doc.addObject("PartDesign::PolarPattern", "PolarPattern")
            p.Angle = float(params.get("angle", 360.0))
        else:
            p = doc.addObject("PartDesign::LinearPattern", "LinearPattern")
            p.Length = float(params.get("dist", 5.0))
        p.Base = feat
        p.Count = count
        doc.recompute()

    elif name == "boolean":
        mode = params.get("mode", "cut")  # cut | fuse | common
        type_map = {"cut": "Cut", "fuse": "Fuse", "common": "Common"}
        if mode not in type_map:
            raise RuntimeError(f"boolean 未知 mode：{mode}")
        tool = _find_tool_body(doc, params.get("tool"))
        if tool is None:
            raise RuntimeError("boolean 需要 tool（另一 body 的 Name/Label），未找到")
        b = doc.addObject("PartDesign::Boolean", "Boolean")
        b.Type = type_map[mode]
        b.Base = body
        b.Tool = tool
        doc.recompute()

    elif name == "scale":
        # Part::Scale 对 body.Shape 缩放 → 新形状重新包 Body（best-effort）
        factor = float(params.get("factor", 1.0))
        scaled = body.Shape.scale(factor)  # 返回新 TopoShape
        body.BaseFeature = doc.addObject("Part::Feature", "Scaled")
        body.BaseFeature.Shape = scaled
        doc.recompute()

    elif name == "split":
        # Part::Slice 沿平面切分（best-effort，需 plane 参数）
        raise RuntimeError("split 在 M1-2 为 best-effort，真机版本见 M1-6；请用 boolean 求分型")

    else:
        raise RuntimeError(f"未知编辑 op：{name}")


def _export(doc, formats) -> dict:
    body = _find_body(doc)
    if body is None:
        raise RuntimeError("无 Body 可导出")
    sid = doc.Name
    out = {}
    for fmt in formats:
        if fmt == "stl":
            path = _SESSIONS_DIR / sid / "out.stl"
            Mesh.export([body.Shape], str(path))
            out["stl"] = str(path)
        elif fmt == "step":
            path = _SESSIONS_DIR / sid / "out.step"
            Import.export([body], str(path))
            out["step"] = str(path)
        else:
            raise RuntimeError(f"不支持的导出格式：{fmt}")
    return out


# ── 请求分发 ──────────────────────────────────────────────

def _handle(req: dict) -> dict:
    cmd = req.get("cmd")
    sid = req.get("session_id", "")
    payload = req.get("payload", {}) or {}
    try:
        if cmd == "create_doc":
            _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            doc = App.newDocument(sid)
            DOCS[sid] = doc
            doc.saveAs(str(_doc_path(sid)))
            return _ok(sid, feature_tree=_extract_feature_tree(doc))

        if cmd == "open":
            doc_path = payload.get("doc_path") or str(_doc_path(sid))
            doc = App.openDocument(doc_path)
            DOCS[sid] = doc
            return _ok(sid, feature_tree=_extract_feature_tree(doc), native_doc=doc_path)

        if cmd == "import_step":
            step_path = payload["step_path"]
            if sid not in DOCS:
                _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
                DOCS[sid] = App.newDocument(sid)
            doc = DOCS[sid]
            _wrap_step_in_body(doc, step_path)
            doc.saveAs(str(_doc_path(sid)))
            return _ok(sid, feature_tree=_extract_feature_tree(doc), native_doc=str(_doc_path(sid)))

        if cmd == "feature_tree":
            doc = _get_doc(sid)
            return _ok(sid, feature_tree=_extract_feature_tree(doc), native_doc=str(_doc_path(sid)))

        if cmd == "edit_op":
            doc = _get_doc(sid)
            _apply_edit_op(doc, payload.get("op", {}))
            doc.saveAs(str(_doc_path(sid)))
            exports = {}
            if payload.get("export"):
                exports = _export(doc, payload["export"])
            return _ok(sid, feature_tree=_extract_feature_tree(doc), native_doc=str(_doc_path(sid)), exports=exports)

        if cmd == "export":
            doc = _get_doc(sid)
            exports = _export(doc, payload.get("formats", []))
            return _ok(sid, exports=exports)

        if cmd == "close":
            if sid in DOCS:
                DOCS[sid].saveAs(str(_doc_path(sid)))
                App.closeDocument(sid)
                DOCS.pop(sid, None)
            return {"ok": True}

        if cmd == "ping":
            return {"ok": True, "ready": True}

        return {"ok": False, "error": f"未知 cmd：{cmd}"}
    except Exception as e:  # §9：几何非法/版本差异 → ok=False + error，不破坏已有树
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _ok(sid, feature_tree=None, native_doc=None, exports=None) -> dict:
    d = {"ok": True}
    if feature_tree is not None:
        d["feature_tree"] = feature_tree
    if native_doc is not None:
        d["native_doc"] = native_doc
    if exports:
        d["exports"] = exports
    return d


def _main():
    # 启动就绪信号（JSON），适配器据此确认 bridge 已起。
    sys.stdout.write(json.dumps({"ready": True, "engine": "freecad"}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue  # 容忍 freecadcmd 偶发 banner 行
        resp = _handle(req)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    _main()
