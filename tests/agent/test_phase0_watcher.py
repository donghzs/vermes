"""
Phase 0 模块文件 watcher 编排测试。

watcher 是「热路径文件被绕过 self_modify 直接写入」（patch/write_file）时的
安全网：任何工具改了 ~/.vermes/modules/ 下的 .py/.yaml，watcher 都应兜底
触发 reload_module_tools。本测试只验证 watcher 的**编排逻辑**（检测/去重/
忽略），不依赖完整 registry/host_api 机制——reload_module_tools 用 recorder
替身。
"""
import time
import threading

import pytest

from agent import module_loader as ml


@pytest.fixture
def fake_modules_dir(tmp_path, monkeypatch):
    """造一个含 'modules' 段路径的临时目录，挂到 get_modules_dir。"""
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    mod = modules_dir / "wmtest"
    (mod / "backend").mkdir(parents=True)
    (mod / "module.yaml").write_text("name: wmtest\nversion: 1.0.0\n")
    tools = mod / "backend" / "tools.py"
    tools.write_text("# v1\n")
    monkeypatch.setattr(ml, "get_modules_dir", lambda: modules_dir)
    return modules_dir


@pytest.fixture
def reload_recorder(monkeypatch):
    """用 recorder 替身换掉真实 reload_module_tools，并记录调用。"""
    calls = []
    def _fake(name):
        calls.append(name)
        return {"ok": True, "state": "reloaded", "error": None, "tools_loaded": 1}
    monkeypatch.setattr(ml, "reload_module_tools", _fake)
    return calls


def _touch(path):
    """改文件内容 + 确保 mtime 跳变（部分 FS 分辨率粗，sleep 兜底）。"""
    path.write_text("# changed %s\n" % time.time())
    time.sleep(0.01)


def test_watcher_detects_change_and_reloads(fake_modules_dir, reload_recorder):
    tools = fake_modules_dir / "wmtest" / "backend" / "tools.py"
    ml.start_module_watcher(poll_interval=0.05, debounce=0.05)
    try:
        time.sleep(0.12)          # 让首轮 poll 记录初始 mtime
        _touch(tools)              # 模拟 patch/write_file 绕过 self_modify
        time.sleep(0.4)           # 等 debounce + poll 触发
        assert "wmtest" in reload_recorder, f"watcher 未触发 reload，calls={reload_recorder}"
    finally:
        ml.stop_module_watcher()


def test_watcher_dedups_explicit_reload(fake_modules_dir, reload_recorder):
    tools = fake_modules_dir / "wmtest" / "backend" / "tools.py"
    ml.mark_explicit_reload("wmtest")   # 模拟 self_modify 已显式 reload
    ml.start_module_watcher(poll_interval=0.05, debounce=0.05)
    try:
        time.sleep(0.12)
        _touch(tools)
        time.sleep(0.4)
        # 去重窗口（2s）内，watcher 应跳过 —— 不二次 reload
        assert "wmtest" not in reload_recorder, f"去重失效，watcher 重复 reload：{reload_recorder}"
    finally:
        ml.stop_module_watcher()


def test_watcher_ignores_files_outside_modules(fake_modules_dir, reload_recorder, tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("# x\n")
    ml.start_module_watcher(poll_interval=0.05, debounce=0.05)
    try:
        time.sleep(0.12)
        _touch(outside)
        time.sleep(0.4)
        assert reload_recorder == [], f"watcher 误触发了模块外文件：{reload_recorder}"
    finally:
        ml.stop_module_watcher()


def test_watcher_lifecycle_start_stop():
    # 重复 start 不应起多个线程；stop 后线程退出
    ml.start_module_watcher(poll_interval=0.05, debounce=0.05)
    t1 = ml._module_watcher
    ml.start_module_watcher(poll_interval=0.05, debounce=0.05)  # 幂等
    assert ml._module_watcher is t1 and t1.is_alive()
    ml.stop_module_watcher()
    assert ml._module_watcher is None
    # 关停后可再次启动
    ml.start_module_watcher(poll_interval=0.05, debounce=0.05)
    assert ml._module_watcher is not None and ml._module_watcher.is_alive()
    ml.stop_module_watcher()
