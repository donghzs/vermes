"""标准件库测试。"""
import pytest
from vermes_cli.mfgcad.standard_parts import (
    BUILTIN_CATALOG,
    list_parts,
    get_part,
    search_parts,
    list_categories,
)


def test_catalog_not_empty():
    assert len(BUILTIN_CATALOG) >= 15


def test_all_parts_have_required_fields():
    for p in BUILTIN_CATALOG:
        assert p.id
        assert p.name
        assert p.category
        assert p.file_path
        assert p.standard


def test_unique_ids():
    ids = [p.id for p in BUILTIN_CATALOG]
    assert len(ids) == len(set(ids))


def test_list_parts_all():
    parts = list_parts()
    assert len(parts) == len(BUILTIN_CATALOG)


def test_list_parts_by_category():
    screws = list_parts("screws")
    assert len(screws) >= 5
    assert all(p["category"] == "screws" for p in screws)

    nuts = list_parts("nuts")
    assert len(nuts) >= 5
    assert all(p["category"] == "nuts" for p in nuts)


def test_list_parts_invalid_category():
    assert list_parts("nonexistent") == []


def test_get_part_existing():
    info = get_part("M5_screw")
    assert info is not None
    assert info["id"] == "M5_screw"
    assert info["name"] == "M5 内六角螺钉"
    assert "diameter" in info["parameters"]


def test_get_part_nonexisting():
    assert get_part("nonexistent") is None


def test_search_by_id():
    results = search_parts("M5")
    assert len(results) >= 2  # M5_screw + M5_nut + M5_washer
    ids = [r["id"] for r in results]
    assert "M5_screw" in ids


def test_search_by_name():
    results = search_parts("轴承")
    assert len(results) >= 3
    assert all("轴承" in r["name"] for r in results)


def test_search_no_match():
    assert search_parts("nonexistent_xyz") == []


def test_list_categories():
    cats = list_categories()
    assert "screws" in cats
    assert "nuts" in cats
    assert "bearings" in cats


def test_part_availability_flag():
    parts = list_parts()
    # 未下载时 available=False
    for p in parts:
        assert "available" in p
        assert isinstance(p["available"], bool)
