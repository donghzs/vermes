"""参数化重建地基（切片①核心）。

纯标准库实现，不依赖 build123d/cadquery，可离线单元测试。

职责：
  1. 从 build123d / cadquery-ocp 源码抽取「顶部数值常量」作为可调参数
     （如 ``HEIGHT = 20.0`` / ``RADIUS: float = 12.5``），推断单位与滑块范围；
  2. 在源码级重写这些常量的值，保留注释、结构与其余代码不变 —— 这是
     「拖滑块改参后重建」的关键：改的是 build123d 源码，不是网格；
  3. 把源码与抽取出的参数持久化到 session 目录，供重建链路复用。

设计边界：
  - 只抽「模块级」的简单数值常量（Constant / UnaryOp / 纯常量 BinOp）。
    不抽函数内、循环变量、含函数调用/属性访问的复杂 RHS —— 这些是安全红线，
    避免把无关语句误写成滑块、或在重写时破坏表达式语义。
  - ``run_mac.py`` 是外部黑盒引擎，不保证落盘 ``build123d_source.py``。
    ``acquire_source`` 采用优雅降级：优先读 session 目录，其次读 output_dir
    下引擎可能落盘的源；都没有则返回 None，由上层决定是否跳过参数化。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "extract_parameters",
    "apply_parameters",
    "infer_unit",
    "suggest_range",
    "persist_source",
    "load_source",
    "save_parameters",
    "load_parameters",
    "acquire_source",
]


# ── 本地复刻 _mfg_home：避免与 tools.py 循环 import ──
def _mfg_home() -> Path:
    return Path.home() / ".vermes" / "mfgcad"


def _session_dir(session_id: str) -> Path:
    return _mfg_home() / "sessions" / session_id


# ──────────────────────────────────────────────────────────────
# 1. 常量求值（仅限安全的字面量组合）
# ──────────────────────────────────────────────────────────────

_MAX_LITERAL_DEPTH = 8


def _literal_number(node: ast.AST, depth: int = 0) -> Optional[float]:
    """返回 node 代表的安全数值常量，否则 None。

    仅接受：Constant(int/float 非 bool) / UnaryOp(USub|UAdd) / 纯常量 BinOp。
    含 Call、Attribute、Name、比较、布尔等一律拒绝 → 不作为参数。
    """
    if depth > _MAX_LITERAL_DEPTH:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _literal_number(node.operand, depth + 1)
        if v is None:
            return None
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Pow)):
        a = _literal_number(node.left, depth + 1)
        b = _literal_number(node.right, depth + 1)
        if a is None or b is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if isinstance(node.op, ast.Div) or isinstance(node.op, ast.FloorDiv):
                return a / b
            if isinstance(node.op, ast.Pow):
                return a ** b
        except ZeroDivisionError:
            return None
    return None


def _is_simple_name(target: ast.AST) -> Optional[str]:
    if isinstance(target, ast.Name):
        return target.id
    return None


# ──────────────────────────────────────────────────────────────
# 2. 单位与滑块范围推断
# ──────────────────────────────────────────────────────────────

def infer_unit(name: str, value: float) -> str:
    """基于参数名关键词推断单位，默认 'mm'（制造场景绝大多数长度为 mm）。"""
    n = name.lower()
    angle_hits = ("angle", "rotat", "theta", "phi", "deg", "degree")
    count_hits = ("count", "number", "num", "teeth", "hole", "holes", "screw", "bolt", "qty", "quantity", "segment", "seg")
    if any(k in n for k in angle_hits):
        return "deg"
    if any(k in n for k in count_hits):
        return "pcs"
    # 长度/尺寸类关键词显式标 mm；其余默认 mm
    return "mm"


def suggest_range(value: float, unit: str) -> dict:
    """推断滑块 (min, max, step)。保证 max > min，且 step > 0。"""
    if unit == "deg":
        lo, hi, step = 0.0, 360.0, 5.0
        if abs(value) < 5:
            step = 1.0
    elif unit == "pcs":
        base = int(round(value)) if value else 1
        lo = float(max(1, base // 2))
        hi = float(max(lo + 1, base * 2 + 1))
        step = 1.0
    else:  # mm 及默认
        if value == 0:
            lo, hi, step = 0.1, 10.0, 0.1
        else:
            lo = max(0.1, round(value * 0.5, 2))
            hi = round(value * 1.5, 2)
            step = round(max(abs(value) * 0.05, 0.1), 2)
            if hi <= lo:
                hi = lo + step
    # 最终兜底：绝不允许退化区间
    if hi <= lo:
        hi = lo + (step if step > 0 else 1.0)
    if step <= 0:
        step = 0.1
    return {"min": lo, "max": hi, "step": step}


# ──────────────────────────────────────────────────────────────
# 3. 抽参
# ──────────────────────────────────────────────────────────────

def extract_parameters(source: str) -> dict:
    """从 build123d 源码抽取模块级数值常量参数。

    返回 dict: {name: {value, unit, min, max, step}}。解析失败返回 {}。
    仅抽取模块级 ``NAME = <字面量>`` / ``NAME: T = <字面量>``。
    """
    if not source or not source.strip():
        return {}
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}

    params: dict[str, dict] = {}
    for node in tree.body:
        name: Optional[str] = None
        value_node: Optional[ast.AST] = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = _is_simple_name(node.targets[0])
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            name = _is_simple_name(node.target)
            value_node = node.value
        if not name or value_node is None:
            continue
        val = _literal_number(value_node)
        if val is None:
            continue  # 复杂 RHS：跳过，不当参数
        # 同名后者覆盖前者（脚本通常无重复，取最后一处）
        unit = infer_unit(name, val)
        rng = suggest_range(val, unit)
        params[name] = {
            "value": val,
            "unit": unit,
            **rng,
        }
    return params


# ──────────────────────────────────────────────────────────────
# 4. 改参（源码级重写，保留注释/结构）
# ──────────────────────────────────────────────────────────────

def _line_starts(text: str) -> list[int]:
    """返回每行起始字符索引（1-based 行号 → 索引）。

    starts[0] = 第 1 行起始；starts[k] = 第 k+1 行起始。
    """
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _format_value(v: Any) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, int):
        return str(v)
    # float：用 repr 取最短往返字面量（20.0 -> '20.0'）
    return repr(float(v))


def apply_parameters(source: str, params: dict) -> str:
    """把 params（name -> 新数值）写回源码对应常量处，其余原样保留。

    - 只重写 name 命中且值为数值的项；
    - 用 AST 节点精确字符区间替换 RHS，注释/其余结构与代码完全不动；
    - 找不到该常量（name 不匹配）时忽略该项，不报错。
    返回重写后的源码字符串（原样不可改时返回原 source）。
    """
    if not source or not params:
        return source
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return source

    line_starts = _line_starts(source)
    replacements: list[tuple[int, int, str]] = []

    for node in tree.body:
        name: Optional[str] = None
        value_node: Optional[ast.AST] = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = _is_simple_name(node.targets[0])
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            name = _is_simple_name(node.target)
            value_node = node.value
        if not name or value_node is None:
            continue
        if name not in params:
            continue
        new_val = params[name]
        if isinstance(new_val, bool) or not isinstance(new_val, (int, float)):
            continue
        # 取 value 节点的精确字符区间
        if (
            value_node.col_offset is None
            or value_node.end_col_offset is None
            or value_node.lineno is None
            or value_node.end_lineno is None
        ):
            continue
        start = line_starts[value_node.lineno - 1] + value_node.col_offset
        end = line_starts[value_node.end_lineno - 1] + value_node.end_col_offset
        if end <= start:
            continue
        replacements.append((start, end, _format_value(new_val)))

    if not replacements:
        return source

    # 从右往左替换，保持偏移有效
    replacements.sort(key=lambda r: r[0], reverse=True)
    out = source
    for start, end, text in replacements:
        out = out[:start] + text + out[end:]
    return out


# ──────────────────────────────────────────────────────────────
# 5. 持久化（落 session 目录）
# ──────────────────────────────────────────────────────────────

def persist_source(session_id: str, source: str) -> str:
    """把 build123d 源码写入 session 目录下的 build123d_source.py。"""
    d = _session_dir(session_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "build123d_source.py"
    p.write_text(source, encoding="utf-8")
    return str(p)


def load_source(session_id: str) -> Optional[str]:
    p = _session_dir(session_id) / "build123d_source.py"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def save_parameters(session_id: str, params: dict) -> None:
    d = _session_dir(session_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "parameters.json").write_text(
        json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_parameters(session_id: str) -> dict:
    p = _session_dir(session_id) / "parameters.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def acquire_source(session_id: str, output_dir: Optional[str] = None) -> Optional[str]:
    """优雅降级获取 build123d 源码：session 目录 → output_dir 引擎落盘 → None。"""
    src = load_source(session_id)
    if src:
        return src
    if output_dir:
        out = Path(output_dir)
        # 优先找规范命名的 build123d_source.py
        cand = out / "build123d_source.py"
        if cand.is_file():
            s = cand.read_text(encoding="utf-8")
            persist_source(session_id, s)
            return s
        # 兼容旧引擎产出：temp_design_*.py（选最新的）
        candidates = sorted(out.glob("temp_design_*.py"), key=lambda p: p.stat().st_mtime, reverse=True)
        for cand in candidates:
            try:
                s = cand.read_text(encoding="utf-8")
                if "build123d" in s or "cadquery" in s or "export_step" in s:
                    persist_source(session_id, s)
                    return s
            except Exception:
                continue
    return None
