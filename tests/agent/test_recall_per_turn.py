"""H4.5 测试：逐轮回忆细化（refine_recall_per_turn）。

fluent 用户跳过（系统提示已全量注入）；非 fluent 用户仅做轻量 DB 召回，
无匹配则返回空。compute_richness 通过 monkeypatch 控制 tier。
"""

import pytest

from agent.memory_recall import refine_recall_per_turn


def _reset_evolution_state():
    import agent.evolution_manager as em

    em._evolution_active = None
    import agent.raw_event as re

    re._LAST_EMERGENCE_OK = None


@pytest.fixture(autouse=True)
def hermes_home(tmp_path, monkeypatch):
    d = tmp_path / "hermes"
    d.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(d))
    _reset_evolution_state()
    yield d


class _FakeRichness:
    def __init__(self, tier):
        self.tier = tier
        self.value = 0.4
        self.raw_event_count = 0
        self.stable_cluster_count = 0
        self.session_count = 0


def _seed(tool_name: str):
    from agent.evolution_manager import get_evolution_dir, get_self_model_db
    from agent.raw_event import record_raw_event

    # record_raw_event 不自动建目录；确保 evolution 目录存在
    get_evolution_dir().mkdir(parents=True, exist_ok=True)
    record_raw_event(
        tool_name=tool_name,
        tool_args={"x": 1},
        result="ok",
        is_error=False,
        duration=0.1,
        trigger_clustering=False,
    )


def test_fluent_user_skips(monkeypatch):
    monkeypatch.setattr(
        "agent.memory_recall.compute_richness", lambda: _FakeRichness("fluent")
    )
    assert refine_recall_per_turn("write_file the config") == ""


def test_building_user_with_match_returns_block(monkeypatch):
    from agent.evolution_manager import get_self_model_db

    _seed("write_file")
    monkeypatch.setattr(
        "agent.memory_recall.compute_richness", lambda: _FakeRichness("building")
    )
    block = refine_recall_per_turn("please write_file the config now")
    assert "<recalled_context>" in block
    assert "write_file" in block


def test_no_match_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "agent.memory_recall.compute_richness", lambda: _FakeRichness("building")
    )
    assert refine_recall_per_turn("zzz qqq unrelated") == ""


def test_cold_start_no_data_empty(monkeypatch):
    monkeypatch.setattr(
        "agent.memory_recall.compute_richness", lambda: _FakeRichness("cold_start")
    )
    assert refine_recall_per_turn("anything") == ""
