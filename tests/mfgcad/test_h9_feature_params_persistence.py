"""H9 根治测试：_FEATURE_PARAMS 子进程重启后参数丢失 → VermesMeta 自定义属性持久化。

验证：
1. save_vermes_meta 把参数 JSON 存入对象自定义属性
2. load_vermes_meta 从对象自定义属性恢复参数
3. round-trip：save → 清空内存缓存 → load 仍能恢复
4. 无属性时返回 None，坏 JSON 返回 None
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad.feature_params import save_vermes_meta, load_vermes_meta


class _FakeObj:
    """模拟 FreeCAD 对象（有 addProperty + 属性存储）。"""

    def __init__(self, name="Fillet"):
        self.Name = name
        self._props = {}

    def addProperty(self, ptype, pname, group, doc=""):
        self._props[pname] = ""

    def setPropertyStatus(self, name, status):
        pass

    def __setattr__(self, key, value):
        if key.startswith("_") or key == "Name":
            object.__setattr__(self, key, value)
        else:
            self._props[key] = value

    def __getattr__(self, name):
        if name == "_props":
            raise AttributeError(name)
        props = object.__getattribute__(self, "_props")
        if name in props:
            return props[name]
        raise AttributeError(name)

    def __hasattr__(self, name):
        return name in self._props


class TestVermesMetaPersistence:

    def test_save_and_load_roundtrip(self):
        obj = _FakeObj()
        meta = {"fillet": {"radius": 2.5}}
        save_vermes_meta(obj, meta)
        loaded = load_vermes_meta(obj)
        assert loaded == meta

    def test_load_returns_none_when_no_prop(self):
        obj = _FakeObj()
        assert load_vermes_meta(obj) is None

    def test_load_returns_none_on_invalid_json(self):
        obj = _FakeObj()
        obj.addProperty("App::PropertyString", "VermesMeta", "Vermes", "")
        obj.VermesMeta = "not-json{"
        assert load_vermes_meta(obj) is None

    def test_save_stores_json_string(self):
        obj = _FakeObj()
        save_vermes_meta(obj, {"fillet": {"radius": 1.0}})
        raw = obj.VermesMeta
        assert isinstance(raw, str)
        assert json.loads(raw) == {"fillet": {"radius": 1.0}}

    def test_recovery_after_memory_cache_clear(self):
        """子进程重启 = 内存缓存清空，VermesMeta 持久化恢复。"""
        obj = _FakeObj(name="Fillet001")
        meta = {"fillet": {"radius": 5.0}}
        save_vermes_meta(obj, meta)

        # 模拟子进程重启：新对象实例但从同一 .FCStd 恢复（属性已持久化）
        obj2 = _FakeObj(name="Fillet001")
        obj2.addProperty("App::PropertyString", "VermesMeta", "Vermes", "")
        obj2.VermesMeta = obj.VermesMeta  # 模拟从 .FCStd 加载

        recovered = load_vermes_meta(obj2)
        assert recovered == meta
        assert recovered["fillet"]["radius"] == 5.0

    def test_overwrite_existing_meta(self):
        """多次 save 覆盖旧值。"""
        obj = _FakeObj()
        save_vermes_meta(obj, {"fillet": {"radius": 1.0}})
        save_vermes_meta(obj, {"fillet": {"radius": 3.0}})
        loaded = load_vermes_meta(obj)
        assert loaded["fillet"]["radius"] == 3.0

    def test_unicode_in_meta(self):
        """参数值含中文（如标签）正确序列化。"""
        obj = _FakeObj()
        meta = {"label": "倒角", "params": {"radius": 2.0}}
        save_vermes_meta(obj, meta)
        loaded = load_vermes_meta(obj)
        assert loaded == meta
        assert loaded["label"] == "倒角"
