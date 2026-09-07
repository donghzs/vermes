"""Regression tests for the ⑭ 联系人「可编辑」开关 (handoff ①).

Covers the falsy-trap fix in ``upsert_agent_profile`` / ``get_agent_profile``:
``editable = 0`` (锁定) must survive a round-trip, and an upsert that omits the
``editable`` key must NOT clobber an existing ``0`` back to the default ``1``.
"""

import pytest


@pytest.fixture()
def profile_db(tmp_path):
    from vermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    yield db
    try:
        db.close()
    except Exception:
        pass


def test_default_editable_is_1(profile_db):
    profile_db.upsert_agent_profile({"id": "b1", "name": "B"})
    assert profile_db.get_agent_profile("b1")["editable"] == 1


def test_set_editable_0_persists(profile_db):
    profile_db.upsert_agent_profile({"id": "a1", "name": "A", "editable": 0})
    assert profile_db.get_agent_profile("a1")["editable"] == 0


def test_omit_editable_key_preserves_existing_0(profile_db):
    # Regression: a follow-up upsert that does NOT carry ``editable`` used to
    # fall back to the default (1) and overwrite the locked value.
    profile_db.upsert_agent_profile({"id": "a1", "name": "A", "editable": 0})
    profile_db.upsert_agent_profile({"id": "a1", "name": "A2"})  # no editable key
    assert profile_db.get_agent_profile("a1")["editable"] == 0


def test_list_returns_editable(profile_db):
    profile_db.upsert_agent_profile({"id": "a1", "name": "A", "editable": 0})
    profile_db.upsert_agent_profile({"id": "b1", "name": "B"})
    profs = profile_db.list_agent_profiles()
    by_id = {p["id"]: p.get("editable") for p in profs}
    assert by_id == {"a1": 0, "b1": 1}


def test_unlock_back_to_1(profile_db):
    profile_db.upsert_agent_profile({"id": "a1", "name": "A", "editable": 0})
    profile_db.upsert_agent_profile({"id": "a1", "name": "A2", "editable": 1})
    assert profile_db.get_agent_profile("a1")["editable"] == 1
