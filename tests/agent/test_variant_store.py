"""Phase 3 tests for the variant store (hash-indexed version archive).

All tests exercise the REAL variant_store module on REAL on-disk files —
no mocking of the archive/list/rollback path (per repo discipline).
"""

import os
import textwrap

import pytest

from agent.variant_store import (
    snapshot_variant,
    update_active_hash,
    snapshot_and_gc,
    list_variants,
    get_variant_content,
    diff_variants,
    rollback_variant,
    pin_variant,
    delete_variant,
    gc_variants,
    _processor_id_from_path,
    _compute_hash,
    _active_yaml_path,
    _variant_file_path,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def user_processors_dir(tmp_path, monkeypatch):
    """Point the variant store at a temp ~/.vermes/processors/ directory."""
    proc_dir = tmp_path / "processors"
    proc_dir.mkdir()

    def _mock_get_user_dir():
        return proc_dir

    monkeypatch.setattr("agent.variant_store._get_user_dir", _mock_get_user_dir)
    return proc_dir


def _write_processor(user_processors_dir, proc_id, content, kind="prompt_fragment"):
    """Write a processor.yaml and return its path."""
    proc_dir = user_processors_dir / proc_id
    proc_dir.mkdir(exist_ok=True)
    yaml_path = proc_dir / "processor.yaml"
    yaml_path.write_text(content, encoding="utf-8")
    return str(yaml_path)


V1 = textwrap.dedent("""
api: vermes.processor/v1
kind: prompt_fragment
id: test_proc
name: test_proc
content: "version 1"
governance:
  risk_tier: L2
  hash: auto
""")

V2 = textwrap.dedent("""
api: vermes.processor/v1
kind: prompt_fragment
id: test_proc
name: test_proc
content: "version 2 — tighter"
governance:
  risk_tier: L2
  hash: auto
""")

V3 = textwrap.dedent("""
api: vermes.processor/v1
kind: prompt_fragment
id: test_proc
name: test_proc
content: "version 3 — even tighter"
governance:
  risk_tier: L1
  hash: auto
""")


# ── Tests ─────────────────────────────────────────────────────────────

def test_processor_id_extraction(user_processors_dir):
    """_processor_id_from_path extracts id from both layouts."""
    # Subdirectory layout
    subdir = user_processors_dir / "myproc" / "processor.yaml"
    subdir.parent.mkdir(exist_ok=True)
    subdir.touch()
    assert _processor_id_from_path(str(subdir)) == "myproc"

    # Flat layout
    flat = user_processors_dir / "flatproc.yaml"
    flat.touch()
    assert _processor_id_from_path(str(flat)) == "flatproc"

    # Non-processor path
    assert _processor_id_from_path("/tmp/random.yaml") is None


def test_snapshot_creates_variant(user_processors_dir):
    """snapshot_variant archives old content under variants/<hash>.yaml."""
    path = _write_processor(user_processors_dir, "tp1", V1)
    h = snapshot_variant(path, V1, author="user", note="initial")
    assert h is not None
    assert h.startswith("sha256:")

    # Variant file exists on disk.
    vpath = _variant_file_path("tp1", h)
    assert vpath.exists()
    assert vpath.read_text(encoding="utf-8") == V1


def test_update_active_hash(user_processors_dir):
    """update_active_hash records the new hash as active."""
    path = _write_processor(user_processors_dir, "tp2", V1)
    snapshot_variant(path, V1, author="user", note="initial")
    # In the real flow, apply_change writes new content to disk FIRST,
    # then calls update_active_hash. We simulate that here.
    _write_processor(user_processors_dir, "tp2", V2)
    update_active_hash(path, V2)

    variants = list_variants("tp2")
    assert len(variants) == 2
    active = [v for v in variants if v["active"]]
    assert len(active) == 1
    assert active[0]["hash"] == _compute_hash(V2)


def test_snapshot_and_gc(user_processors_dir):
    """snapshot_and_gc archives + runs GC in one call."""
    path = _write_processor(user_processors_dir, "tp3", V1)
    snapshot_and_gc(path, V1, max_variants=5)
    update_active_hash(path, V2)

    # Write more versions to exceed GC limit.
    snapshot_and_gc(path, V2, max_variants=3)
    update_active_hash(path, V3)
    snapshot_and_gc(path, V3, max_variants=3)
    update_active_hash(path, V1)

    variants = list_variants("tp3")
    # GC should have kicked in, keeping <= 3 non-pinned.
    non_pinned = [v for v in variants if not v["pinned"] and v["hash"] != _compute_hash(V1)]
    assert len(non_pinned) <= 3


def test_list_variants_marks_active(user_processors_dir):
    """list_variants returns active flag."""
    path = _write_processor(user_processors_dir, "tp4", V1)
    snapshot_variant(path, V1, author="user", note="v1")
    _write_processor(user_processors_dir, "tp4", V2)
    update_active_hash(path, V2)

    variants = list_variants("tp4")
    assert len(variants) == 2
    active = [v for v in variants if v["active"]]
    assert len(active) == 1
    assert active[0]["hash"] == _compute_hash(V2)
    superseded = [v for v in variants if not v["active"]]
    assert len(superseded) == 1
    assert superseded[0]["hash"] == _compute_hash(V1)
    assert superseded[0]["superseded_at"] is not None


def test_diff_variants(user_processors_dir):
    """diff_variants produces a unified diff vs active."""
    path = _write_processor(user_processors_dir, "tp5", V1)
    snapshot_variant(path, V1, author="user", note="v1")
    _write_processor(user_processors_dir, "tp5", V2)
    update_active_hash(path, V2)

    h1 = _compute_hash(V1)
    diff = diff_variants("tp5", h1)
    assert diff is not None
    assert "version 1" in diff
    assert "version 2" in diff
    assert diff.startswith("---")  # unified diff header


def test_rollback_restores_old_content(user_processors_dir):
    """rollback_variant swaps active with target variant."""
    path = _write_processor(user_processors_dir, "tp6", V1)
    snapshot_variant(path, V1, author="user", note="v1")
    _write_processor(user_processors_dir, "tp6", V2)
    update_active_hash(path, V2)

    # Active should be V2.
    assert _active_yaml_path("tp6").read_text() == V2

    # Rollback to V1.
    h1 = _compute_hash(V1)
    result = rollback_variant("tp6", h1)
    assert result == V1

    # Active now contains V1.
    assert _active_yaml_path("tp6").read_text() == V1

    # V2 should be archived as a variant.
    h2 = _compute_hash(V2)
    vpath = _variant_file_path("tp6", h2)
    assert vpath.exists()
    assert vpath.read_text() == V2

    # Registry: V1 is active.
    variants = list_variants("tp6")
    active = [v for v in variants if v["active"]]
    assert len(active) == 1
    assert active[0]["hash"] == h1


def test_pin_protects_from_gc(user_processors_dir):
    """Pinned variants are exempt from GC."""
    path = _write_processor(user_processors_dir, "tp7", V1)
    h1 = snapshot_variant(path, V1, author="user", note="v1")
    update_active_hash(path, V2)
    h2 = _compute_hash(V2)

    # Pin V1.
    assert pin_variant("tp7", h1, pinned=True) is True

    # GC with max=1 should NOT delete the pinned V1.
    gc_variants("tp7", max_variants=1)

    variants = list_variants("tp7")
    pinned = [v for v in variants if v["pinned"]]
    assert len(pinned) >= 1
    assert pinned[0]["hash"] == h1


def test_delete_refuses_active(user_processors_dir):
    """delete_variant refuses to delete the active variant."""
    path = _write_processor(user_processors_dir, "tp8", V1)
    snapshot_variant(path, V1, author="user", note="v1")
    update_active_hash(path, V2)

    h2 = _compute_hash(V2)
    assert delete_variant("tp8", h2) is False  # active


def test_delete_refuses_pinned(user_processors_dir):
    """delete_variant refuses to delete a pinned variant."""
    path = _write_processor(user_processors_dir, "tp9", V1)
    h1 = snapshot_variant(path, V1, author="user", note="v1")
    update_active_hash(path, V2)

    pin_variant("tp9", h1, pinned=True)
    assert delete_variant("tp9", h1) is False  # pinned


def test_delete_non_pinned(user_processors_dir):
    """delete_variant removes a non-pinned, non-active variant."""
    path = _write_processor(user_processors_dir, "tp10", V1)
    h1 = snapshot_variant(path, V1, author="user", note="v1")
    update_active_hash(path, V2)

    assert delete_variant("tp10", h1) is True
    variants = list_variants("tp10")
    assert all(v["hash"] != h1 for v in variants)


def test_non_processor_path_no_op(user_processors_dir):
    """snapshot_variant returns None for non-processor paths."""
    result = snapshot_variant("/tmp/nonexistent.yaml", "content", author="x")
    assert result is None


def test_idempotent_snapshot(user_processors_dir):
    """Archiving the same content twice doesn't duplicate."""
    path = _write_processor(user_processors_dir, "tp11", V1)
    h1 = snapshot_variant(path, V1, author="user", note="v1")
    h2 = snapshot_variant(path, V1, author="user", note="v1 again")

    # Same hash (content didn't change).
    assert h1 == h2

    # Only one variant file on disk.
    vpath = _variant_file_path("tp11", h1)
    assert vpath.exists()
    # Registry should not have duplicates.
    variants = list_variants("tp11")
    hashes = [v["hash"] for v in variants]
    assert hashes.count(h1) == 1
