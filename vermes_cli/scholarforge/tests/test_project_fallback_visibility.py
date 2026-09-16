"""兜底项目「显形」机制验证（2026-09-16）。

背景：A1 的 `_ensure_default_project()` 解决了「静默丢内容」，但留下另一半问题——
**它替用户挑了一个项目（按最近更新），却不告诉用户**。用户有多个论文项目又忘了选，
内容就默默写进「最近那个」，输出里只出现一个 `#52`，用户未必联想到「写错项目了」。

本轮不改 A1 的取舍（仍保证有落库目标），只补「显形」：
  · 真走第三级兜底时记一笔（ContextVar，asyncio 并发安全）；
  · `_with_usage` 统一包装器读出并追加到工具结果末尾 → 23 个调用点零改动、不可能漏。

覆盖点：
  1. 真的兜底时提示出现，且带项目 id / 标题；
  2. 显式带 project_id 或已有激活项目 → **不**出现（不打扰正常路径）；
  3. 提示只出现一次（兜底会把该项目设为激活，第二次走激活分支）；
  4. ❌ 失败结果不追加提示（没写成却说「已落到项目 X」是自相矛盾）；
  5. ContextVar 不跨调用泄漏、嵌套安全；
  6. 静态守卫：所有调用 resolve_project_id 的 handler 都被 _with_usage 包裹。

为避免污染真实 SQLite（~/.vermes/scholarforge.db），所有 DB 触碰函数均 mock 到源模块。
"""
import ast
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import vermes_cli.scholarforge.active_project as ap
import vermes_cli.scholarforge.tools as _sf_tools
from vermes_cli.scholarforge.tools import _with_usage
from tools.registry import registry as _sf_registry

if _sf_registry.get_entry("scholarforge_list_projects") is None:
    _sf_tools.register_tools()

_NOTE_KEY = "你这次没有指定论文项目"
_TOOLS_PY = os.path.join(os.path.dirname(__file__), "..", "tools.py")


def _patch_no_project(fake_pid=7, projects=None):
    """构造「无激活项目 + 触发兜底」的上下文管理栈。"""
    return (
        patch("vermes_cli.scholarforge.active_project.list_projects",
              return_value=projects if projects is not None else []),
        patch("vermes_cli.scholarforge.active_project.create_project",
              return_value={"id": fake_pid}),
    )


# ───────────────────────── 1-4 _with_usage 包装器行为（单元级） ─────────────────────────
class TestWithUsageAppendsFallbackNote(unittest.TestCase):
    """用合成 handler 隔离验证包装器契约，不依赖任何真实工具的内部流程。"""

    def setUp(self):
        ap.set_active_project(0)
        ap.clear_project_fallback()

    def _run(self, handler, args=None):
        return asyncio.run(_with_usage("fake_tool", handler)(args or {}))

    def test_note_appended_when_fallback_happened(self):
        async def handler(args, **kw):
            ap.resolve_project_id(args)  # 无激活 → 触发兜底
            return "正文内容"

        p_no, p_new = _patch_no_project(fake_pid=7)
        with p_no, p_new:
            result = self._run(handler)
        self.assertIn(_NOTE_KEY, result)
        self.assertIn("#7", result)
        self.assertIn("Vermes 默认项目", result)  # 空库时新建，标题要显形
        self.assertTrue(result.startswith("正文内容"))  # 追加而非覆盖

    def test_note_shows_existing_project_title(self):
        """有多项目时提示要带出真实标题，而不是只给一个裸 id。"""
        async def handler(args, **kw):
            ap.resolve_project_id(args)
            return "正文内容"

        p_no, p_new = _patch_no_project(
            projects=[{"id": 52, "title": "幼小衔接研究"}, {"id": 3, "title": "旧项目"}])
        with p_no, p_new:
            result = self._run(handler)
        self.assertIn(_NOTE_KEY, result)
        self.assertIn("#52", result)
        self.assertIn("幼小衔接研究", result)

    def test_no_note_when_explicit_project_id(self):
        async def handler(args, **kw):
            ap.resolve_project_id(args)
            return "正文内容"

        p_no, p_new = _patch_no_project(fake_pid=7)
        with p_no, p_new:
            result = self._run(handler, {"project_id": 99})
        self.assertNotIn(_NOTE_KEY, result)

    def test_no_note_when_active_project_exists(self):
        """已选过项目 → 走激活分支，不该打扰用户。"""
        ap.set_active_project(52)

        async def handler(args, **kw):
            self.assertEqual(ap.resolve_project_id(args), 52)
            return "正文内容"

        result = self._run(handler)
        self.assertNotIn(_NOTE_KEY, result)

    def test_note_not_appended_to_error_result(self):
        """❌ 结果不追加：没写成却说「已自动落到项目 X」是自相矛盾。"""
        async def handler(args, **kw):
            ap.resolve_project_id(args)  # 仍会触发兜底记账
            return "❌ 生成失败"

        p_no, p_new = _patch_no_project(fake_pid=7)
        with p_no, p_new:
            result = self._run(handler)
        self.assertTrue(result.lstrip().startswith("❌"))
        self.assertNotIn(_NOTE_KEY, result)

    def test_non_string_result_is_untouched(self):
        async def handler(args, **kw):
            ap.resolve_project_id(args)
            return {"ok": True}

        p_no, p_new = _patch_no_project(fake_pid=7)
        with p_no, p_new:
            result = self._run(handler)
        self.assertEqual(result, {"ok": True})

    def test_exception_path_does_not_swallow(self):
        async def handler(args, **kw):
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            self._run(handler)


# ───────────────────────── 5 ContextVar 生命周期 ─────────────────────────
class TestFallbackContextVarLifecycle(unittest.TestCase):
    def setUp(self):
        ap.set_active_project(0)
        ap.clear_project_fallback()

    def test_take_is_idempotent_second_call_empty(self):
        ap._note_fallback(7, "T")
        self.assertIn("#7", ap.take_project_fallback_note())
        self.assertEqual(ap.take_project_fallback_note(), "")  # 取走即清空

    def test_no_leak_across_calls(self):
        """上一次调用残留的兜底记录，不得渗到下一次调用。"""
        ap._note_fallback(7, "T")  # 模拟「无包装器消费」的残留

        async def handler(args, **kw):
            return "第二次调用"

        result = asyncio.run(_with_usage("fake_tool", handler)({}))
        self.assertNotIn(_NOTE_KEY, result)

    def test_clear_and_restore_token_nested_safe(self):
        ap._note_fallback(1, "outer")
        token = ap.clear_project_fallback()
        self.assertEqual(ap.take_project_fallback_note(), "")
        ap._note_fallback(2, "inner")
        ap.restore_project_fallback(token)
        # token 恢复后回到外层上下文的值（恢复的是 token 位置，不是值本身）
        self.assertIsNotNone(token)
        ap.clear_project_fallback()

    def test_note_fallback_ignores_bad_pid(self):
        ap._note_fallback("not-an-int")  # 不应抛异常污染调用链
        ap.clear_project_fallback()


# ───────────────────────── 3 真实工具端到端：提示只出现一次 ─────────────────────────
class TestFallbackVisibleEndToEnd(unittest.TestCase):
    """走 registry.dispatch 真实链路，确认提示真的出现在工具输出里且只出现一次。"""

    def setUp(self):
        ap.set_active_project(0)
        ap.clear_project_fallback()

    def _write(self, args):
        # 注意：registry.dispatch 是**同步**的（不是协程），别套 asyncio.run。
        return _sf_registry.dispatch("scholarforge_write", args)

    def test_first_call_warns_second_call_does_not(self):
        base = [
            patch("vermes_cli.scholarforge.active_project.list_projects",
                  return_value=[{"id": 52, "title": "幼小衔接研究"}]),
            patch("vermes_cli.scholarforge.project_context.auto_snapshot"),
            patch("vermes_cli.scholarforge.project_context.format_project_context_prompt",
                  return_value="【项目】上下文"),
            patch("vermes_cli.scholarforge.project_context.load_project_context",
                  return_value={"title": "幼小衔接研究", "paper_type": "硕士论文"}),
            patch("vermes_cli.scholarforge.project_context.get_style_prompt", return_value=""),
            patch("vermes_cli.scholarforge.project_context.save_section"),
            patch("vermes_cli.scholarforge.quality_gate.run_quality_gate",
                  return_value=("# 引言\n正文段落。", "", False)),
        ]
        with patch("vermes_cli.scholarforge.tools._call_llm", return_value="# 引言\n正文段落。"):
            for p in base:
                p.start()
            try:
                first = self._write({"topic": "t", "section_type": "abstract"})
                second = self._write({"topic": "t", "section_type": "conclusion"})
            finally:
                for p in base:
                    p.stop()

        self.assertNotIn("❌", first[:5])
        self.assertIn(_NOTE_KEY, first, msg="首次调用（无项目）必须显形兜底选择")
        self.assertIn("#52", first)
        self.assertIn("幼小衔接研究", first)
        self.assertNotIn(_NOTE_KEY, second, msg="第二次已记住激活项目，不应重复提示")


# ───────────────────────── 6 静态守卫：解析点必须被包装器覆盖 ─────────────────────────
class TestFallbackGuardCoverage(unittest.TestCase):
    """AST 扫描：凡调用 resolve_project_id 的 handler，必须被 _with_usage 包裹。

    这是本轮方案的命门 —— 提示靠 `_with_usage` 统一追加，若将来新增了写回类工具
    却漏了 `_with_usage`（或有人把它摘掉），兜底就会重新变回「静默」。这个测试
    把「不可能有人忘记」从一句设计意图变成可执行的约束。
    """

    # 已知且已论证安全的例外：
    # _verify_scholarforge_write 是「外证回读器」（嵌套在 register_tools 内），
    # 它有守卫 `if not explicit_pid and not get_active_project(): skip`，
    # 因此永远走不到 _ensure_default_project 兜底分支，不会自己触发记账。
    KNOWN_EXEMPT = {"_verify_scholarforge_write"}

    def _scan(self):
        src = open(_TOOLS_PY, encoding="utf-8").read()
        tree = ast.parse(src)
        # 顶层 + 嵌套函数都要收（_verify_scholarforge_write 就嵌在 register_tools 里）
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

        wrapped = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_with_usage":
                for a in node.args:
                    if isinstance(a, ast.Name):
                        wrapped.add(a.id)

        # 关键：调用点必须归给**最内层**的 enclosing 函数。
        # 否则嵌套在 register_tools 里的 _verify_scholarforge_write 的调用
        # 会同时记到外层 register_tools 名下，制造假缺口。
        callers = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "resolve_project_id":
                owners = [f for f in funcs
                          if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)]
                if not owners:
                    continue
                innermost = max(owners, key=lambda f: f.lineno)  # 起点最晚 = 最内层
                callers[innermost.name] = node.lineno
        return wrapped, callers

    def test_every_resolve_caller_is_wrapped(self):
        wrapped, callers = self._scan()
        self.assertGreaterEqual(len(callers), 20, msg="解析点数量骤降，可能扫描口径失效了")
        uncovered = {f: ln for f, ln in callers.items()
                     if f not in wrapped and f not in self.KNOWN_EXEMPT}
        self.assertEqual(uncovered, {},
                         msg=f"以下函数解析 project_id 却未被 _with_usage 包裹，兜底会静默：{uncovered}")

    def test_known_exempt_still_exists(self):
        """例外名单里的函数若被删/改名，本例外就失效了 —— 提醒重审而不是默默放过。"""
        _, callers = self._scan()
        self.assertIn("_verify_scholarforge_write", callers,
                      msg="_verify_scholarforge_write 不再调用 resolve_project_id —— KNOWN_EXEMPT 需重审")
        # 并确认它确实不是被 _with_usage 包裹的（否则该从例外名单里删掉）
        wrapped, _ = self._scan()
        self.assertNotIn("_verify_scholarforge_write", wrapped)

    def test_wrapper_uses_the_note_api(self):
        """包装器必须真的调用三个 API，否则静态覆盖范围再全也是空壳。"""
        src = open(_TOOLS_PY, encoding="utf-8").read()
        for api in ("clear_project_fallback", "take_project_fallback_note", "restore_project_fallback"):
            self.assertIn(api, src, msg=f"tools.py 未使用 {api}")


if __name__ == "__main__":
    unittest.main()
