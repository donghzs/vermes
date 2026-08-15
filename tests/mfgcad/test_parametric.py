"""切片①参数化重建：parametric 核心 + 引擎/工具接线测试。

契约导向（非镜像实现）：
  - apply_parameters 的断言以「重写后源码能被 ast 重新解析且抽回新值 + 注释保留 +
    其余语句原样」为准，不依赖 len()/字符串片段的弱断言；
  - MACBackend.rebuild_from_script 用真实假引擎子进程跑通 JSON 契约；
  - _handle_mfg_rebuild_parametric 用 stub backend 真实落盘新 session.json。
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad import parametric as P
from vermes_cli.mfgcad import tools as T
from vermes_cli.mfgcad.engine_backends import (
    EngineBackend,
    EngineResult,
    MACBackend,
    resolve_backend,
)


SAMPLE = '''# 笔筒参数
from build123d import *

HEIGHT = 100.0        # 总高 mm
WALL_THICKNESS = 3.0
OUTER_RADIUS: float = 30.0
HOLE_COUNT = 8        # 数量
TILT_ANGLE = 15.0     # 倾角 deg
INSET = 2 + 3         # 简单 BinOp -> 5.0
COMPLEX = max(1, 2)   # 复杂 RHS -> 不抽
NEG = -5.0            # 负常数

def build():
    local = 99        # 函数内 -> 不抽
    return HEIGHT
'''


# ──────────────────────────────────────────────────────────
# 1. 抽参
# ──────────────────────────────────────────────────────────

class TestExtractParameters:
    def test_extract_module_level_constants(self):
        params = P.extract_parameters(SAMPLE)
        assert params["HEIGHT"]["value"] == 100.0
        assert params["WALL_THICKNESS"]["value"] == 3.0
        assert params["OUTER_RADIUS"]["value"] == 30.0
        assert params["INSET"]["value"] == 5.0
        assert params["NEG"]["value"] == -5.0

    def test_unit_inference(self):
        params = P.extract_parameters(SAMPLE)
        assert params["HEIGHT"]["unit"] == "mm"
        assert params["HOLE_COUNT"]["unit"] == "pcs"
        assert params["TILT_ANGLE"]["unit"] == "deg"

    def test_complex_rhs_skipped(self):
        params = P.extract_parameters(SAMPLE)
        assert "COMPLEX" not in params  # max(1,2) 不抽

    def test_function_local_skipped(self):
        params = P.extract_parameters(SAMPLE)
        assert "local" not in params

    def test_range_sane(self):
        params = P.extract_parameters(SAMPLE)
        for name, p in params.items():
            assert p["max"] > p["min"], name
            assert p["step"] > 0, name

    def test_empty_source(self):
        assert P.extract_parameters("") == {}
        assert P.extract_parameters("not valid (((") == {}


# ──────────────────────────────────────────────────────────
# 2. 改参重写（契约：回抽 + 注释保留 + 语法有效）
# ──────────────────────────────────────────────────────────

class TestApplyParameters:
    def test_rewrite_and_re_extract(self):
        new = P.apply_parameters(SAMPLE, {"HEIGHT": 120.0, "HOLE_COUNT": 12, "TILT_ANGLE": 45.0})
        # 重写后源码必须仍是合法 Python
        ast = __import__("ast")
        ast.parse(new)
        # 回抽必须得到新值（真实契约，非镜像）
        p2 = P.extract_parameters(new)
        assert p2["HEIGHT"]["value"] == 120.0
        assert p2["HOLE_COUNT"]["value"] == 12.0
        assert p2["TILT_ANGLE"]["value"] == 45.0

    def test_comments_preserved(self):
        new = P.apply_parameters(SAMPLE, {"HEIGHT": 120.0})
        assert "# 总高 mm" in new
        assert "# 倾角 deg" in new

    def test_untouched_lines_unchanged(self):
        new = P.apply_parameters(SAMPLE, {"HEIGHT": 120.0})
        assert "COMPLEX = max(1, 2)" in new  # 未改项原样
        assert "local = 99" in new
        assert "WALL_THICKNESS = 3.0" in new

    def test_unknown_param_ignored(self):
        new = P.apply_parameters(SAMPLE, {"NOPE": 1.0})
        assert new == SAMPLE  # 无匹配项不改动

    def test_no_params_returns_original(self):
        assert P.apply_parameters(SAMPLE, {}) == SAMPLE
        assert P.apply_parameters("", {}) == ""

    def test_binop_constant_rewritten_to_flat(self):
        new = P.apply_parameters(SAMPLE, {"INSET": 9.0})
        assert "INSET = 9.0" in new


# ──────────────────────────────────────────────────────────
# 3. 单位/范围推断
# ──────────────────────────────────────────────────────────

class TestInferSuggest:
    def test_zero_value_mm_range(self):
        lo, hi, step = (lambda d: (d["min"], d["max"], d["step"]))(P.suggest_range(0.0, "mm"))
        assert lo > 0 and hi > lo and step > 0

    def test_deg_range(self):
        r = P.suggest_range(15.0, "deg")
        assert r["min"] == 0.0 and r["max"] == 360.0

    def test_pcs_range(self):
        r = P.suggest_range(8.0, "pcs")
        assert r["min"] >= 1 and r["max"] > r["min"] and r["step"] == 1.0


# ──────────────────────────────────────────────────────────
# 4. 持久化
# ──────────────────────────────────────────────────────────

class TestPersistence:
    def test_persist_load_source(self, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "_mfg_home", lambda: tmp_path)
        P.persist_source("s1", SAMPLE)
        assert P.load_source("s1") == SAMPLE
        assert P.load_source("nope") is None

    def test_save_load_parameters(self, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "_mfg_home", lambda: tmp_path)
        ps = P.extract_parameters(SAMPLE)
        P.save_parameters("s1", ps)
        loaded = P.load_parameters("s1")
        assert loaded["HEIGHT"]["value"] == 100.0
        assert P.load_parameters("nope") == {}

    def test_acquire_from_output_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "_mfg_home", lambda: tmp_path)
        out = tmp_path / "output" / "s1"
        out.mkdir(parents=True)
        (out / "build123d_source.py").write_text(SAMPLE, encoding="utf-8")
        got = P.acquire_source("s1", str(out))
        assert got == SAMPLE
        # 应固化到 session 目录
        assert (tmp_path / "sessions" / "s1" / "build123d_source.py").is_file()


# ──────────────────────────────────────────────────────────
# 5. MACBackend.rebuild_from_script（真实假引擎子进程）
# ──────────────────────────────────────────────────────────

_FAKE_ENGINE = '''
import sys, json
from pathlib import Path
argv = sys.argv
d = {}
for i in range(len(argv) - 1):
    if argv[i].startswith("--"):
        d[argv[i].lstrip("-")] = argv[i + 1]
out = Path(d["output-dir"]); out.mkdir(parents=True, exist_ok=True)
(out / "rebuilt.step").write_text("STEP")
(out / "rebuilt.stl").write_text("STL")
print(json.dumps({"ok": True, "step_path": str(out / "rebuilt.step"),
                  "stl_path": str(out / "rebuilt.stl"), "volume_mm3": 250.0,
                  "qa": {"passed": 3}}))
'''


class TestMacRebuildScript:
    def test_rebuild_via_fake_engine(self, tmp_path, monkeypatch):
        # 布置假引擎
        engine_dir = tmp_path / "engine"
        engine_dir.mkdir()
        (engine_dir / "run_mac.py").write_text(_FAKE_ENGINE, encoding="utf-8")
        monkeypatch.setenv("MFG_CAD_ENGINE_DIR", str(engine_dir))
        monkeypatch.setenv("MFG_CAD_ENGINE_PY", sys.executable)

        out_dir = tmp_path / "out"
        result = asyncio.run(MACBackend().rebuild_from_script(SAMPLE, str(out_dir)))

        assert result.ok is True
        assert result.files["step"].endswith("rebuilt.step")
        assert result.volume_mm3 == 250.0
        # 源码必须已落盘
        assert (out_dir / "build123d_source.py").is_file()
        assert (out_dir / "build123d_source.py").read_text(encoding="utf-8") == SAMPLE

    def test_base_default_not_implemented(self):
        class Stub(EngineBackend):
            @property
            def name(self): return "x"
            @property
            def output_formats(self): return []
            async def generate(self, *a, **k): return None
        with pytest.raises(NotImplementedError):
            asyncio.run(Stub().rebuild_from_script("x", "/tmp"))


# ──────────────────────────────────────────────────────────
# 6. _handle_mfg_rebuild_parametric（stub backend 真实落库）
# ──────────────────────────────────────────────────────────

class _StubBackend(EngineBackend):
    @property
    def name(self): return "mac"
    @property
    def output_formats(self): return ["step", "stl", "3mf"]
    async def generate(self, *a, **k): return EngineResult(ok=True)
    async def rebuild_from_script(self, source, output_dir, workflow_id="original", env=None):
        p = Path(output_dir)
        p.mkdir(parents=True, exist_ok=True)
        (p / "out.step").write_text("STEP")
        (p / "out.stl").write_text("STL")
        return EngineResult(
            ok=True,
            files={"step": str(p / "out.step"), "stl": str(p / "out.stl")},
            volume_mm3=123.0,
            qa={"passed": 2},
        )


def _scan_child(tmp_home, base_id):
    sess_root = tmp_home / "sessions"
    best = None
    best_ts = -1
    for sf in sess_root.glob("*/session.json"):
        d = json.loads(sf.read_text(encoding="utf-8"))
        if d.get("base_session_id") == base_id:
            if d.get("ts", 0) > best_ts:
                best_ts = d.get("ts", 0)
                best = d
    return best


class TestRebuildHandler:
    def test_rebuild_writes_new_session(self, tmp_path, monkeypatch):
        # 隔离 home
        monkeypatch.setattr(P, "_mfg_home", lambda: tmp_path)
        monkeypatch.setattr(T, "_mfg_home", lambda: tmp_path)
        # stub 引擎 + 空 key
        monkeypatch.setattr("vermes_cli.mfgcad.engine_backends.resolve_backend", lambda *_a, **_k: _StubBackend())
        monkeypatch.setattr(T, "_resolve_api_key", lambda: "")

        # 准备源会话
        base_id = "base_1"
        P.persist_source(base_id, SAMPLE)
        P.save_parameters(base_id, P.extract_parameters(SAMPLE))

        msg = asyncio.run(T._handle_mfg_rebuild_parametric({
            "base_session_id": base_id,
            "parameters": {"HEIGHT": 120.0, "HOLE_COUNT": 12},
        }))

        assert "✅" in msg
        child = _scan_child(tmp_path, base_id)
        assert child is not None
        assert child["ok"] is True
        assert child["has_parameters"] is True
        assert child["build123d_source"]
        assert (tmp_path / "sessions" / child["session_id"] / "build123d_source.py").is_file()
        # 子会话源码应含改后值
        child_src = (tmp_path / "sessions" / child["session_id"] / "build123d_source.py").read_text(encoding="utf-8")
        assert "HEIGHT = 120.0" in child_src

    def test_rebuild_unknown_param_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "_mfg_home", lambda: tmp_path)
        monkeypatch.setattr(T, "_mfg_home", lambda: tmp_path)
        base_id = "base_2"
        P.persist_source(base_id, SAMPLE)
        msg = asyncio.run(T._handle_mfg_rebuild_parametric({
            "base_session_id": base_id,
            "parameters": {"NOPE": 1.0},
        }))
        assert "❌" in msg
        assert "不存在" in msg

    def test_rebuild_no_source_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(P, "_mfg_home", lambda: tmp_path)
        monkeypatch.setattr(T, "_mfg_home", lambda: tmp_path)
        msg = asyncio.run(T._handle_mfg_rebuild_parametric({
            "base_session_id": "ghost",
            "parameters": {"HEIGHT": 1.0},
        }))
        assert "❌" in msg
        assert "无可用" in msg
