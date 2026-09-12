"""回归守卫：run_conversation 的"循环内变量"必须在进入主循环前初始化。

真实事故（用户实测）：
* ``api_start_time`` 原在主 while 循环内赋值（旧 :1375），但循环外的
  ``finalize_turn``（旧 :4577）引用它。当循环首轮条件即不满足（预算耗尽 /
  api_call_count 已满）或首轮在赋值点之前 break（用户中断）时，赋值语句根本
  执行不到 → UnboundLocalError，整个回合处理链路崩溃。
* ``approx_tokens`` 完全同类：唯一赋值点在循环内，且位于 4 个提前 break
  （用户中断 / 预算耗尽 / …）之后，同样会在 finalize_turn 处炸。

为什么用 AST 守卫而不是构造 agent 跑一遍：
构造一个能真实跑进 run_conversation 的 agent 需要大量运行时依赖（provider、
DB、scheduler…），成本高且脆；而"变量必须在循环前初始化"是**结构性不变量**，
AST 断言精确、秒级、不会因 mock 而失真 —— 与本项目"monitor-mode 22 条
静态守卫"同套路。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONV_LOOP = REPO_ROOT / "agent" / "conversation_loop.py"

# 这些变量在循环内会被重新赋值，但循环外（finalize_turn）也引用，
# 因此必须在主 while 之前就完成初始化。
LOOP_SCOPED_BUT_USED_AFTER = ("api_start_time", "approx_tokens")


def _run_conversation_node() -> ast.FunctionDef:
    tree = ast.parse(CONV_LOOP.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_conversation":
            return node
    raise AssertionError("agent/conversation_loop.py 中找不到 run_conversation")


def _main_while(fn: ast.FunctionDef) -> ast.While:
    """主循环 = run_conversation 函数体顶层的第一个 while。"""
    for node in fn.body:
        if isinstance(node, ast.While):
            return node
    raise AssertionError("run_conversation 函数体顶层找不到主 while 循环")


def _stores_before(fn: ast.FunctionDef, lineno: int) -> set:
    """收集 lineno 之前被赋值（Store）的变量名。"""
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            if node.lineno < lineno:
                names.add(node.id)
        # 带类型注解的赋值 / walrus 也应计入
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.lineno < lineno:
                names.add(node.target.id)
    return names


@pytest.mark.parametrize("varname", LOOP_SCOPED_BUT_USED_AFTER)
def test_loop_scoped_var_initialized_before_main_loop(varname):
    fn = _run_conversation_node()
    main_while = _main_while(fn)
    assigned_before = _stores_before(fn, main_while.lineno)
    assert varname in assigned_before, (
        f"{varname} 必须在主 while 循环（第 {main_while.lineno} 行）之前初始化："
        f"它在循环内赋值，但 finalize_turn 在循环外引用它；"
        f"一旦循环未进入或在赋值点前 break（用户中断/预算耗尽），"
        f"就会抛 UnboundLocalError 中断整个回合。"
    )


def test_main_while_still_exists():
    """防退化：若主循环被重构（改名/包进别的函数），上面的守卫会静默失效，
    此测负责把这种结构性变化显式暴露出来。"""
    fn = _run_conversation_node()
    main_while = _main_while(fn)
    assert main_while.lineno > fn.lineno
