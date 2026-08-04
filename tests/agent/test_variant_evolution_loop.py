"""Phase 4 no-mock end-to-end closed-loop test.

Exercises the REAL loop with no mocking of the import/rank/promote path:
  tool outcomes (raw_events.variant_hash) → rank_variants (group-relative)
  → promote_best_variant (L1 auto-land via apply_change / L2·inline proposal)
  → evolution_injector._variant_evolution_block (prompt injection)

Per repo discipline ("测试全绿≠功能可用"): the variant_store rollback test
(P3) only covered the store-level swap, not the chat handler import; here we
cover the FULL P4 chain on real on-disk files + a real self_model.db.
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def isolated_vermes_home(monkeypatch, tmp_path):
    """Point VERMES_HOME at a temp dir so the loop runs on real isolated files."""
    home = tmp_path / "vermes_home"
    home.mkdir()
    monkeypatch.setenv("VERMES_HOME", str(home))
    # self_model.db parent must exist
    (home / "evolution").mkdir(parents=True, exist_ok=True)
    yield home


def _processor_yaml(risk_tier="L1", inline=False, tool_id="test_tool"):
    """Build a minimal valid tool-processor YAML."""
    doc = {
        "api": "v1",
        "kind": "tool",
        "id": tool_id,
        "name": "Test Tool",
        "version": "1.0.0",
        "enabled": True,
        "priority": 100,
        "layer": "stable",
        "content": {"description": "test"},
        "handler": {"ref": "_handle_test"},
        "governance": {"risk_tier": risk_tier, "hash": "auto", "replaceable": True},
    }
    if inline:
        doc["handler"] = {"inline": {"type": "http", "url": "https://example.com"}}
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def _seed_processor(home: Path, processor_id: str, active_content: str, archived: list):
    """Create a processor dir with active processor.yaml + variants/ + registry.

    archived: list of (hash, content) tuples to write as variant files.
    The active content's hash is computed and set as active_hash.
    """
    from agent.variant_store import _compute_hash

    pdir = home / "processors" / processor_id
    pdir.mkdir(parents=True)
    (pdir / "processor.yaml").write_text(active_content, encoding="utf-8")

    active_hash = _compute_hash(active_content)
    vdir = pdir / "variants"
    vdir.mkdir()

    variants_meta = []
    # archive a copy of the active too (so list_variants finds it)
    all_variants = [(active_hash, active_content)] + archived
    for h, content in all_variants:
        safe = h.replace(":", "_")
        (vdir / f"{safe}.yaml").write_text(content, encoding="utf-8")
        variants_meta.append({
            "hash": h,
            "created_at": datetime.now().isoformat(),
            "author": "test",
            "pinned": False,
            "superseded_at": None,
        })

    registry = {
        "processor_id": processor_id,
        "active_hash": active_hash,
        "variants": variants_meta,
    }
    (vdir / "_registry.json").write_text(json.dumps(registry), encoding="utf-8")
    return active_hash


def _seed_outcomes(home: Path, variant_hash: str, n_success: int, n_error: int, tool_name="test_tool"):
    """Write raw_events with the given variant_hash directly to self_model.db."""
    from agent.evolution_manager import get_self_model_db
    from agent.raw_event import ensure_raw_events_table

    db_path = str(get_self_model_db())
    conn = sqlite3.connect(db_path)
    ensure_raw_events_table(conn)
    now = datetime.now()
    for i in range(n_success):
        conn.execute(
            "INSERT INTO raw_events (timestamp, tool_name, args_preview, result_preview, success, duration, session_id, turn_number, variant_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            ((now - timedelta(seconds=i)).isoformat(), tool_name, "{}", "ok", 1, 0.1, "s1", i, variant_hash),
        )
    for i in range(n_error):
        conn.execute(
            "INSERT INTO raw_events (timestamp, tool_name, args_preview, result_preview, success, duration, session_id, turn_number, variant_hash) VALUES (?,?,?,?,?,?,?,?,?)",
            ((now - timedelta(seconds=100+i)).isoformat(), tool_name, "{}", "err", 0, 0.1, "s1", 100+i, variant_hash),
        )
    conn.commit()
    conn.close()


# ── Test 1: L1 auto-promotion end-to-end ───────────────────────────────

def test_l1_auto_promote_full_loop(isolated_vermes_home):
    """Poor active (0.2 success) vs Good archived (0.9 success) → Good promoted.

    Full chain: outcomes → rank → promote (L1, force=True, apply_change) →
    active swaps → injector sees the new active.
    """
    from agent.variant_store import _compute_hash, _load_registry, promote_best_variant
    from agent.variant_ranker import rank_variants, get_variant_scores
    from agent.evolution_manager import get_self_model_db
    from agent.evolution_injector import _variant_evolution_block

    home = isolated_vermes_home
    poor_content = _processor_yaml(risk_tier="L1")  # active, performs poorly
    good_content = _processor_yaml(risk_tier="L1")  # archived, performs well
    # make them actually different so hashes differ
    good_content = good_content.replace("Test Tool", "Test Tool v2")

    poor_hash = _compute_hash(poor_content)
    good_hash = _compute_hash(good_content)
    assert poor_hash != good_hash, "fixtures must differ"

    _seed_processor(home, "test_tool", poor_content, archived=[(good_hash, good_content)])
    # outcomes: poor active = 2/10 success; good (when it was active) = 9/10
    _seed_outcomes(home, poor_hash, n_success=2, n_error=8)
    _seed_outcomes(home, good_hash, n_success=9, n_error=1)

    db_path = str(get_self_model_db())

    # 1) Rank: good should score higher than poor
    scored = rank_variants("test_tool", db_path)
    scores = {s["hash"]: s for s in scored}
    assert good_hash in scores and poor_hash in scores
    assert scores[good_hash]["score"] > scores[poor_hash]["score"], (
        f"good {scores[good_hash]['score']} should beat poor {scores[poor_hash]['score']}"
    )
    # both have ≥ EXPLORATION_K samples (10 each) → not exploring
    assert not scores[good_hash].get("exploring")

    # 2) Promote (L1 → auto-land via apply_change force=True)
    decision = promote_best_variant("test_tool", db_path)
    assert decision["action"] == "promoted", f"expected promoted, got {decision}"
    assert decision["target_hash"] == good_hash
    assert decision["tier"] == "L1"

    # 3) Active swapped on disk + registry
    registry = _load_registry("test_tool")
    assert registry["active_hash"] == good_hash, "active should now be good"
    active_on_disk = (home / "processors" / "test_tool" / "processor.yaml").read_text()
    assert "Test Tool v2" in active_on_disk, "active file should contain good content"

    # 4) Injector sees the new active variant
    block = _variant_evolution_block()
    assert "test_tool" in block
    assert good_hash[:8] in block, "injector should show the promoted active hash"


# ── Test 2: L2/inline → proposal, NOT auto-land ────────────────────────

def test_inline_variant_proposes_not_lands(isolated_vermes_home):
    """An inline variant that beats active must NOT auto-land.

    Governance (P4 拍板 ④ + Phase 2.5 self-proving lesson): inline forces L2,
    so promote_best_variant creates a proposal (status=proposed) and leaves
    active unchanged.
    """
    from agent.variant_store import _compute_hash, _load_registry, promote_best_variant
    from agent.variant_ranker import rank_variants
    from agent.evolution_manager import get_self_model_db, get_proposals

    home = isolated_vermes_home
    poor_content = _processor_yaml(risk_tier="L1")  # active, poor (non-inline)
    inline_content = _processor_yaml(inline=True)   # archived, good, but INLINE → L2
    inline_content = inline_content.replace("Test Tool", "Inline Tool v2")

    poor_hash = _compute_hash(poor_content)
    inline_hash = _compute_hash(inline_content)

    _seed_processor(home, "test_tool", poor_content, archived=[(inline_hash, inline_content)])
    _seed_outcomes(home, poor_hash, n_success=2, n_error=8)
    _seed_outcomes(home, inline_hash, n_success=9, n_error=1)

    db_path = str(get_self_model_db())
    rank_variants("test_tool", db_path)

    decision = promote_best_variant("test_tool", db_path)
    assert decision["action"] == "proposed", f"inline must propose not land: {decision}"
    assert decision["tier"] == "L2"

    # Active must NOT have changed
    registry = _load_registry("test_tool")
    assert registry["active_hash"] == poor_hash, "inline promotion must not auto-swap active"

    # A proposal must exist (status=proposed, phase=variant_selector)
    proposals = get_proposals(status="proposed")
    variant_props = [p for p in proposals if p.get("phase") == "variant_selector"]
    assert variant_props, "expected a variant_selector proposal"
    assert decision.get("proposal_id") is not None


# ── Test 3: exploring variant is not promoted (cold-start budget) ──────

def test_exploring_variant_not_promoted(isolated_vermes_home):
    """A variant with < EXPLORATION_K samples must not be promoted even if its
    raw success_rate is high (cold-start ε-exploration, P4 拍板 ⑥)."""
    from agent.variant_store import _compute_hash, promote_best_variant
    from agent.variant_ranker import rank_variants, EXPLORATION_K
    from agent.evolution_manager import get_self_model_db

    home = isolated_vermes_home
    active_content = _processor_yaml(risk_tier="L1")
    new_content = _processor_yaml(risk_tier="L1").replace("Test Tool", "New Variant")
    active_hash = _compute_hash(active_content)
    new_hash = _compute_hash(new_content)

    _seed_processor(home, "test_tool", active_content, archived=[(new_hash, new_content)])
    # active: plenty of data (decent); new: only 2 samples (both success → 1.0 raw)
    _seed_outcomes(home, active_hash, n_success=8, n_error=2)
    _seed_outcomes(home, new_hash, n_success=2, n_error=0)  # 2 < EXPLORATION_K

    db_path = str(get_self_model_db())
    rank_variants("test_tool", db_path)

    decision = promote_best_variant("test_tool", db_path)
    # new has < EXPLORATION_K → skipped; active isn't beaten by a non-exploring variant
    assert decision["action"] in ("none", "blocked"), (
        f"exploring variant must not be promoted: {decision}"
    )
    assert "exploring" in decision["reason"].lower() or "beats" in decision["reason"].lower() or decision["action"]=="none"


# ── Test 4: attribution — record_raw_event writes variant_hash ─────────

def test_record_raw_event_attribution(isolated_vermes_home):
    """P4-A: record_raw_event persists variant_hash to raw_events (no-mock)."""
    from agent.raw_event import record_raw_event
    from agent.evolution_manager import get_self_model_db

    db_path = str(get_self_model_db())
    rid = record_raw_event(
        "test_tool", {"x": 1}, "ok", False, 0.1,
        session_id="s1", variant_hash="sha256:attrtest",
    )
    assert rid is not None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT variant_hash FROM raw_events WHERE id=?", (rid,)).fetchone()
    conn.close()
    assert row["variant_hash"] == "sha256:attrtest"
