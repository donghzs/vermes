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
def VERMES_home(tmp_path, monkeypatch):
    d = tmp_path / "Vermes"
    d.mkdir()
    monkeypatch.setenv("VERMES_HOME", str(d))
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


# ── B2: per-turn layered recall (recall_hierarchical_per_turn) ──────────────

from agent.memory_recall import recall_hierarchical_per_turn


def test_b2_empty_when_no_hits(monkeypatch):
    """No recall_hierarchical hits → empty string (fail-open, no injection)."""
    monkeypatch.setattr(
        "agent.memory_fabric.recall_hierarchical", lambda *a, **k: []
    )
    assert recall_hierarchical_per_turn("anything") == ""


def test_b2_formats_layered_block(monkeypatch):
    """recall_hierarchical hits across layers → <memory_recall> block."""
    def _fake(query, limit=8, layers=None, prioritize_tags=None):
        return [
            {"layer": "note", "source": "mem", "pointer": "note#1", "content": "API key lives in services.", "score": 0.9},
            {"layer": "procedural", "source": "skill", "pointer": "skill#git", "content": "Use rebase for feature branches.", "score": 0.8},
            {"layer": "reference", "source": "rag", "pointer": "rag#5#0", "content": "Quarterly report summary.", "score": 0.7},
        ]

    monkeypatch.setattr("agent.memory_fabric.recall_hierarchical", _fake)
    block = recall_hierarchical_per_turn("where is my api key")
    assert "<memory_recall>" in block
    assert "[note] API key lives in services." in block
    assert "[skill] Use rebase for feature branches." in block
    assert "[reference] Quarterly report summary." in block


def test_b2_fluent_skips_episodic_but_keeps_notes(monkeypatch):
    """Fluent users: L3 omitted, but L1/L2/L4 still surfaced."""
    def _fake(query, limit=8, layers=None, prioritize_tags=None):
        # layers should exclude episodic for fluent users
        assert layers is not None and "episodic" not in layers, layers
        all_hits = [
            {"layer": "note", "source": "mem", "pointer": "note#1", "content": "note x", "score": 0.9},
            {"layer": "episodic", "source": "recall", "pointer": "recall#1", "content": "episode y", "score": 0.8},
        ]
        # simulate recall_hierarchical's layer filter
        if layers is not None:
            all_hits = [h for h in all_hits if h["layer"] in layers]
        return all_hits

    monkeypatch.setattr("agent.memory_fabric.recall_hierarchical", _fake)
    monkeypatch.setattr(
        "agent.memory_recall.compute_richness", lambda: _FakeRichness("fluent")
    )
    block = recall_hierarchical_per_turn("hi")
    assert "[note] note x" in block
    assert "episode y" not in block  # fluent → L3 skipped


def test_b2_fail_open_on_exception(monkeypatch):
    """Exception inside recall_hierarchical → empty (never breaks the turn)."""
    def _boom(*a, **k):
        raise RuntimeError("kb down")

    monkeypatch.setattr("agent.memory_fabric.recall_hierarchical", _boom)
    assert recall_hierarchical_per_turn("hi") == ""
