"""专家策展目录 schema 校验（锁定 qclaw 展示字段契约，无网络/无导入）。"""

import json
from pathlib import Path

import pytest

CATALOG = Path(__file__).parent.parent.parent / "vermes_cli" / "experts_catalog.json"


def _load():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_catalog_exists_and_is_list():
    assert CATALOG.exists(), "experts_catalog.json 缺失"
    data = _load()
    assert isinstance(data, list) and len(data) > 0


def test_each_expert_has_required_fields():
    data = _load()
    for e in data:
        assert e.get("id"), f"专家缺少 id: {e.get('profession')}"
        assert e.get("profession", {}).get("zh"), f"专家 {e.get('id')} 缺 profession.zh"
        assert e.get("displayName", {}).get("zh"), f"专家 {e.get('id')} 缺 displayName.zh"
        assert e.get("categoryId"), f"专家 {e.get('id')} 缺 categoryId"
        assert e.get("displayDescription", {}).get("zh"), f"专家 {e.get('id')} 缺 displayDescription.zh"
        assert isinstance(e.get("tags"), list) and len(e["tags"]) == 3, \
            f"专家 {e.get('id')} 的 tags 必须恰为 3 个"
        assert isinstance(e.get("quickPrompts"), list) and len(e["quickPrompts"]) == 3, \
            f"专家 {e.get('id')} 的 quickPrompts 必须恰为 3 个"
        for t in e["tags"]:
            assert t.get("zh"), f"专家 {e.get('id')} 有 tag 缺 zh"
        for q in e["quickPrompts"]:
            assert q.get("zh"), f"专家 {e.get('id')} 有 quickPrompt 缺 zh"


def test_ids_unique():
    data = _load()
    ids = [e["id"] for e in data]
    assert len(ids) == len(set(ids)), "专家 id 重复"
