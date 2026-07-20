"""Tests for memory_fabric usage telemetry (越用越懂用户).

Records one row per capability launch into the local unified index and
aggregates by frequency for "你可能想用" recommendations.
"""
import os
import sys
from pathlib import Path

import pytest

# ensure repo root on sys.path so `agent.memory_fabric` is importable
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    yield


def test_record_and_count():
    from agent.memory_fabric import record_usage, get_usage_counts

    record_usage("expert", "paper-writing", "论文写作")
    record_usage("expert", "paper-writing")  # second launch
    record_usage("expert", "web-research", "网络调研")

    counts = get_usage_counts("expert")
    assert len(counts) == 2
    assert counts[0]["id"] == "paper-writing"
    assert counts[0]["count"] == 2
    assert counts[1]["id"] == "web-research"
    assert counts[1]["count"] == 1


def test_kind_filter():
    from agent.memory_fabric import record_usage, get_usage_counts

    record_usage("expert", "paper-writing")
    record_usage("skill", "web-browse")

    assert len(get_usage_counts("expert")) == 1
    assert get_usage_counts("expert")[0]["id"] == "paper-writing"
    assert len(get_usage_counts("skill")) == 1


def test_empty_index_returns_list():
    from agent.memory_fabric import get_usage_counts

    assert get_usage_counts("expert") == []


def test_limit():
    from agent.memory_fabric import record_usage, get_usage_counts

    for i in range(5):
        record_usage("expert", f"e{i}")

    assert len(get_usage_counts("expert", limit=3)) == 3
