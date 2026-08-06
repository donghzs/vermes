"""P0-A 通用 Outcome Verifier 注册表测试。

验证链条：
  1. ToolEntry.verify_fn slot 存在且默认 None
  2. register() 接受 verify_fn 形参
  3. verify_tool_outcome 按名查 verify_fn 并执行
  4. R1: is_error=False 但 verify_fn 返回 False → (False, reason)
  5. R2: 不依赖 _FILE_MUTATING_TOOLS，按名查
  6. R4: verify_fn 抛异常 → fail-open (True, "verifier error: ...")
  7. R5: function_name 非空字符串 assert
  8. 无 verify_fn → (True, "") 默认信任
  9. 工具不在 registry → (True, "") 默认信任

照真实调用方形态写（tool_executor.py:558/1100 的调用方式），
不是照实现镜像写。
"""
import pytest
from unittest.mock import MagicMock, patch

from tools.registry import registry, ToolEntry


# ── 1. ToolEntry verify_fn slot ──────────────────────────────────

class TestToolEntryVerifyFnSlot:
    def test_verify_fn_defaults_to_none(self):
        """新建 ToolEntry，verify_fn 默认 None（向后兼容）。"""
        entry = ToolEntry(
            name="test_tool", toolset="test", schema={},
            handler=lambda **kw: None, check_fn=None,
            requires_env=[], is_async=False,
            description="", emoji="",
        )
        assert entry.verify_fn is None

    def test_verify_fn_accepted_in_constructor(self):
        """ToolEntry 构造器接受 verify_fn。"""
        vf = lambda fn, args, result, is_err: (True, "")
        entry = ToolEntry(
            name="test_tool", toolset="test", schema={},
            handler=lambda **kw: None, check_fn=None,
            requires_env=[], is_async=False,
            description="", emoji="", verify_fn=vf,
        )
        assert entry.verify_fn is vf


# ── 2. register() 接受 verify_fn ─────────────────────────────────

class TestRegisterAcceptsVerifyFn:
    def test_register_with_verify_fn(self):
        """register() 接受 verify_fn 并存入 ToolEntry。"""
        vf = lambda fn, args, result, is_err: (True, "")
        registry.register(
            name="__test_verify_tool__",
            toolset="test",
            schema={"name": "__test_verify_tool__", "description": "test"},
            handler=lambda **kw: "ok",
            verify_fn=vf,
            override=True,
        )
        entry = registry.get_entry("__test_verify_tool__")
        assert entry is not None
        assert entry.verify_fn is vf
        # cleanup
        registry.deregister("__test_verify_tool__")

    def test_register_without_verify_fn_defaults_none(self):
        """不传 verify_fn 时默认 None（向后兼容）。"""
        registry.register(
            name="__test_no_verify__",
            toolset="test",
            schema={"name": "__test_no_verify__", "description": "test"},
            handler=lambda **kw: "ok",
            override=True,
        )
        entry = registry.get_entry("__test_no_verify__")
        assert entry is not None
        assert entry.verify_fn is None
        registry.deregister("__test_no_verify__")


# ── 3-9. verify_tool_outcome 行为测试 ────────────────────────────

class TestVerifyToolOutcome:
    """照 tool_executor.py:558/1100 的调用方式写。"""

    def test_no_verify_fn_returns_true(self):
        """无 verify_fn → (True, "") 默认信任（向后兼容）。"""
        registry.register(
            name="__test_no_vf__",
            toolset="test", schema={"name": "__test_no_vf__", "description": "t"},
            handler=lambda **kw: "ok", override=True,
        )
        from harness.outcome_verifier import verify_tool_outcome
        ok, reason = verify_tool_outcome("__test_no_vf__", {}, "result", False)
        assert ok is True
        assert reason == ""
        registry.deregister("__test_no_vf__")

    def test_tool_not_in_registry_returns_true(self):
        """工具不在 registry → (True, "") 默认信任。"""
        from harness.outcome_verifier import verify_tool_outcome
        ok, reason = verify_tool_outcome("__nonexistent_tool__", {}, "result", False)
        assert ok is True
        assert reason == ""

    def test_verify_fn_passes(self):
        """verify_fn 返回 (True, "") → 传播。"""
        vf = lambda fn, args, result, is_err: (True, "")
        registry.register(
            name="__test_vf_pass__",
            toolset="test", schema={"name": "__test_vf_pass__", "description": "t"},
            handler=lambda **kw: "ok", verify_fn=vf, override=True,
        )
        from harness.outcome_verifier import verify_tool_outcome
        ok, reason = verify_tool_outcome("__test_vf_pass__", {"k": "v"}, "result", False)
        assert ok is True
        assert reason == ""
        registry.deregister("__test_vf_pass__")

    def test_verify_fn_fails(self):
        """R1: is_error=False 但 verify_fn 返回 False → (False, reason)。"""
        def vf(fn, args, result, is_err):
            # R1: 不信任 is_error，自己验功能结果
            return (False, "DB write confirmed failed")
        registry.register(
            name="__test_vf_fail__",
            toolset="test", schema={"name": "__test_vf_fail__", "description": "t"},
            handler=lambda **kw: "❌ 写回失败", verify_fn=vf, override=True,
        )
        from harness.outcome_verifier import verify_tool_outcome
        ok, reason = verify_tool_outcome("__test_vf_fail__", {}, "❌ 写回失败", False)
        assert ok is False
        assert "DB write confirmed failed" in reason
        registry.deregister("__test_vf_fail__")

    def test_verify_fn_raises_fail_open(self):
        """R4: verify_fn 抛异常 → fail-open (True, "verifier error: ...")。"""
        def boom(fn, args, result, is_err):
            raise RuntimeError("verifier crashed")
        registry.register(
            name="__test_vf_boom__",
            toolset="test", schema={"name": "__test_vf_boom__", "description": "t"},
            handler=lambda **kw: "ok", verify_fn=boom, override=True,
        )
        from harness.outcome_verifier import verify_tool_outcome
        ok, reason = verify_tool_outcome("__test_vf_boom__", {}, "result", False)
        assert ok is True  # fail-open
        assert "verifier error" in reason
        registry.deregister("__test_vf_boom__")

    def test_verify_fn_bad_return_type_fail_open(self):
        """verify_fn 返回非 bool/str → fail-open。"""
        def bad_return(fn, args, result, is_err):
            return ("not_bool", 42)  # type: ignore
        registry.register(
            name="__test_vf_badret__",
            toolset="test", schema={"name": "__test_vf_badret__", "description": "t"},
            handler=lambda **kw: "ok", verify_fn=bad_return, override=True,
        )
        from harness.outcome_verifier import verify_tool_outcome
        ok, reason = verify_tool_outcome("__test_vf_badret__", {}, "result", False)
        assert ok is True  # fail-open
        assert "bad type" in reason
        registry.deregister("__test_vf_badret__")

    def test_r5_function_name_must_be_string(self):
        """R5: function_name 非空字符串 assert。"""
        from harness.outcome_verifier import verify_tool_outcome
        with pytest.raises(AssertionError):
            verify_tool_outcome("", {}, "result", False)


# ── ScholarForge verify_fn 集成测试 ──────────────────────────────

class TestScholarForgeVerifyFnIntegration:
    """验证 scholarforge register_tools 挂的 verify_fn 真能跑。"""

    @pytest.fixture
    def sf_registered(self):
        """注册 ScholarForge 工具（如果尚未注册）。"""
        from vermes_cli.scholarforge import tools as sf_tools
        # 检查是否已注册
        if registry.get_entry("scholarforge_write") is None:
            sf_tools.register_tools()
        yield
        # 不清理——scholarforge 工具在全局 registry 中是持久的

    def test_write_tool_has_verify_fn(self, sf_registered):
        """scholarforge_write 注册了 verify_fn。"""
        entry = registry.get_entry("scholarforge_write")
        assert entry is not None
        assert entry.verify_fn is not None
        assert callable(entry.verify_fn)

    def test_outline_tool_has_verify_fn(self, sf_registered):
        """scholarforge_outline 注册了 verify_fn。"""
        entry = registry.get_entry("scholarforge_outline")
        assert entry is not None
        assert entry.verify_fn is not None
        assert callable(entry.verify_fn)

    def test_write_verify_fn_no_pid_skips(self, sf_registered):
        """无 project_id 时 verify_fn 跳过验证（返回 True）。"""
        entry = registry.get_entry("scholarforge_write")
        ok, reason = entry.verify_fn("scholarforge_write", {}, "result", False)
        assert ok is True
        assert "skip" in reason.lower()

    def test_write_verify_fn_db_readback(self, sf_registered, tmp_path, monkeypatch):
        """有 project_id + section_key 时走 DB 回读。"""
        import vermes_cli.scholarforge.database as db
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test_verify.db"))
        db.init_db()
        # 创建项目并写入章节
        pid = db.create_project("测试", "本科")["id"]
        db.save_section_content(pid, "intro", "这是绪论内容。" * 10)

        entry = registry.get_entry("scholarforge_write")
        ok, reason = entry.verify_fn(
            "scholarforge_write",
            {"project_id": pid, "section_key": "intro"},
            "result", False,
        )
        assert ok is True

    def test_write_verify_fn_detects_missing_section(self, sf_registered, tmp_path, monkeypatch):
        """回读发现章节不存在 → False。"""
        import vermes_cli.scholarforge.database as db
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test_verify2.db"))
        db.init_db()
        pid = db.create_project("测试2", "本科")["id"]
        # 不写入任何章节

        entry = registry.get_entry("scholarforge_write")
        ok, reason = entry.verify_fn(
            "scholarforge_write",
            {"project_id": pid, "section_key": "nonexistent"},
            "result", False,
        )
        assert ok is False
        assert "not found" in reason.lower()

    def test_outline_verify_fn_db_readback(self, sf_registered, tmp_path, monkeypatch):
        """outline verify_fn 走 DB 回读确认条目在库。"""
        import vermes_cli.scholarforge.database as db
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test_verify3.db"))
        db.init_db()
        pid = db.create_project("测试3", "本科")["id"]
        db.save_outline(pid, [{"id": "intro", "title": "绪论"}])

        entry = registry.get_entry("scholarforge_outline")
        ok, reason = entry.verify_fn(
            "scholarforge_outline",
            {"project_id": pid},
            "result", False,
        )
        assert ok is True
        assert "rows confirmed" in reason

    def test_outline_verify_fn_detects_empty(self, sf_registered, tmp_path, monkeypatch):
        """outline 回读发现 0 条目 → False。

        create_project 自带大纲种子（8 行），需先手动清空。
        """
        import vermes_cli.scholarforge.database as db
        monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test_verify4.db"))
        db.init_db()
        pid = db.create_project("测试4", "本科")["id"]
        # 清空大纲（模拟 save_outline 失败的场景）
        with db.get_conn() as conn:
            conn.execute("DELETE FROM outlines WHERE project_id=?", (pid,))
            conn.commit()

        entry = registry.get_entry("scholarforge_outline")
        ok, reason = entry.verify_fn(
            "scholarforge_outline",
            {"project_id": pid},
            "result", False,
        )
        assert ok is False
        assert "0 rows" in reason
