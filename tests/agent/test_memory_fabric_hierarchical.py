"""Slice 2: hierarchical recall pipeline (agent.memory_fabric.recall_hierarchical).

Verifies layer-ordered unification, de-duplication, and fail-closed federation
hooks. No heavy subsystems are imported — the L4 federation hook is injected as
a plain callable.
"""
import os

import pytest


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    h = tmp_path / "hermes"
    h.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(h))
    import agent.memory_fabric as mf

    db = mf._get_index_db()
    if db.exists():
        os.remove(db)
    # hooks are module globals — reset so tests are isolated
    mf.set_l4_federation_hook(None)
    mf.set_l3_live_hook(None)
    yield h
    mf.set_l4_federation_hook(None)
    mf.set_l3_live_hook(None)


def _seed_all_layers():
    """Index one matching entry in each of L1-L4 under the token MIG_HIER_XYZ."""
    from agent.memory_fabric import (
        L3_EPISODIC,
        L4_REFERENCE,
        index_note,
        index_skills,
        record,
    )

    index_note("memory", "MIG_HIER_XYZ curated note about apples")
    index_skills([{"name": "apple_skill", "description": "MIG_HIER_XYZ does apple things"}])
    record(
        {
            "source": "recall",
            "layer": L3_EPISODIC,
            "type": "raw_event",
            "pointer": "recall:self-model.db#1",
            "fts_content": "MIG_HIER_XYZ episodic recall about apples",
        }
    )
    record(
        {
            "source": "rag",
            "layer": L4_REFERENCE,
            "type": "document",
            "pointer": "rag:documents.db#1",
            "fts_content": "MIG_HIER_XYZ reference document about apples",
        }
    )


def test_recall_hierarchical_orders_layers_l1_to_l4(hermes_home):
    from agent.memory_fabric import recall_hierarchical

    _seed_all_layers()
    hits = recall_hierarchical("MIG_HIER_XYZ", limit=10)
    layers = [h["layer"] for h in hits]
    assert layers == ["note", "procedural", "episodic", "reference"], layers


def test_recall_hierarchical_dedupes_by_pointer(hermes_home):
    from agent.memory_fabric import index_note, recall_hierarchical

    index_note("memory", "MIG_DUP_XYZ first version")
    # same pointer (note:memory) → should replace, not duplicate
    index_note("memory", "MIG_DUP_XYZ first version updated")
    hits = recall_hierarchical("MIG_DUP_XYZ", limit=10)
    assert len(hits) == 1, hits
    assert "updated" in hits[0]["content"]


def test_recall_hierarchical_l4_federation_hook(hermes_home):
    from agent.memory_fabric import recall_hierarchical, set_l4_federation_hook

    set_l4_federation_hook(
        lambda q, limit: [
            {
                "content": "MIG_HOOK_XYZ external kb passage",
                "pointer": "ext:kb#1",
                "source": "honcho",
                "score": 0.9,
            }
        ]
    )
    hits = recall_hierarchical("MIG_HOOK_XYZ", limit=10)
    assert len(hits) == 1
    assert hits[0]["layer"] == "reference"
    assert hits[0]["source"] == "honcho"
    assert hits[0]["content"] == "MIG_HOOK_XYZ external kb passage"


def test_recall_hierarchical_l1_surfaces_before_l4_hook(hermes_home):
    from agent.memory_fabric import (
        index_note,
        recall_hierarchical,
        set_l4_federation_hook,
    )

    index_note("memory", "MIG_MIX_XYZ curated note")
    set_l4_federation_hook(
        lambda q, limit: [
            {"content": "MIG_MIX_XYZ external kb", "pointer": "ext:1", "source": "honcho"}
        ]
    )
    hits = recall_hierarchical("MIG_MIX_XYZ", limit=10)
    assert len(hits) == 2
    assert hits[0]["layer"] == "note"  # curated wins over reference
    assert hits[1]["layer"] == "reference"


def test_recall_hierarchical_hook_failure_is_non_fatal(hermes_home):
    from agent.memory_fabric import recall_hierarchical, set_l4_federation_hook

    def _boom(q, limit):
        raise RuntimeError("KB down")

    set_l4_federation_hook(_boom)
    # must not raise; returns empty (no index hits)
    assert recall_hierarchical("MIG_ANYTHING_XYZ", limit=5) == []


def test_recall_hierarchical_empty_query_returns_empty(hermes_home):
    from agent.memory_fabric import recall_hierarchical

    assert recall_hierarchical("   ", limit=5) == []
