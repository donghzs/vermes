"""A/B 语料冻结校验（roadmap §8.2 指标 5「不退化」的执行载体）。

冻结语义：
  · 语料 ≥30 条，四类分布均衡，每条带可执行断言；
  · 文件内容与 `corpus.sha256` 必须一致 —— 不一致即红，并提示「升版本目录」而不是改 v1；
  · 目录名 v1 必须与 `version` 字段一致（防止悄悄原地改写后被称仍是 v1）。

为什么钉这个：S2/S3/S4 的验收是「v2.5.2 冻结包 vs 新包」A/B 对比。语料一旦随跑随改，
对比就失去基线，指标 5 会退化成主观判断（E9/E10 教训）。
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import pytest
import yaml

CORPUS_DIR = Path(__file__).resolve().parents[2] / "reports" / "ab-corpus" / "v1"
CORPUS = CORPUS_DIR / "corpus.yaml"
CHECKSUM = CORPUS_DIR / "corpus.sha256"

MIN_CASES = 30
MIN_PER_CATEGORY = 5
REQUIRED_FIELDS = ("id", "category", "scenario", "fixed_context", "turns", "assertions")
REQUIRED_CONTEXT_KEYS = ("model", "platform", "toolset")


@pytest.fixture(scope="module")
def corpus() -> dict:
    if not CORPUS.exists():
        pytest.fail(f"缺少语料文件: {CORPUS}")
    return yaml.safe_load(CORPUS.read_text(encoding="utf-8"))


def test_corpus_is_frozen_by_checksum():
    """内容与校验和必须一致；改语料请升版本目录，不要原地改 v1。"""
    assert CHECKSUM.exists(), f"缺校验和文件: {CHECKSUM}"
    stored = CHECKSUM.read_text(encoding="utf-8").strip().split()[0]
    computed = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    assert stored == computed, (
        "corpus.yaml 已被修改但 sha256 未更新。\n"
        "按 §8.2 冻结纪律：新建 reports/ab-corpus/v2/ 并把新 sha256 写进去，\n"
        "不要在 v1 原地改写（对比会失去基线）。"
    )


def test_directory_version_matches_field(corpus):
    assert CORPUS_DIR.name == corpus["version"], (
        f"目录名 {CORPUS_DIR.name} 与 version={corpus['version']} 不一致"
    )


def test_case_count_and_distribution(corpus):
    cases = corpus["cases"]
    assert len(cases) >= MIN_CASES, f"语料仅 {len(cases)} 条，须 ≥{MIN_CASES}"
    dist = Counter(c["category"] for c in cases)
    for cat, n in dist.items():
        assert n >= MIN_PER_CATEGORY, f"类别 {cat} 仅 {n} 条，须 ≥{MIN_PER_CATEGORY}"
    # 覆盖面：语料设计承诺四类（长会话/记忆/工具链/渠道）
    assert dist, "语料为空"


def test_cases_have_required_fields(corpus):
    cases = corpus["cases"]
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), f"存在重复 case id: {[i for i in ids if ids.count(i) > 1]}"
    for c in cases:
        missing = [k for k in REQUIRED_FIELDS if k not in c or not c[k]]
        assert not missing, f"{c.get('id')} 缺字段: {missing}"
        ctx_missing = [k for k in REQUIRED_CONTEXT_KEYS if k not in c["fixed_context"]]
        assert not ctx_missing, (
            f"{c['id']} 的 fixed_context 缺 {ctx_missing} —— should_inject 依赖这些维度，"
            "不固定则逐字等价断言不可复现"
        )
        assert len(c["assertions"]) >= 2, f"{c['id']} 断言少于 2 条，无法判分"
        for t in c["turns"]:
            assert t.get("role") in {"user", "assistant", "system"}, f"{c['id']} 非法 role"
            assert str(t.get("content", "")).strip(), f"{c['id']} 存在空 turn"


def test_no_vibes_policy_declared(corpus):
    """语料必须显式声明 no_vibes 纪律（否则指标 5 会退回主观判断）。"""
    policy = corpus.get("policy") or {}
    assert "no_vibes" in policy, "policy 缺 no_vibes 纪律声明"
    assert "checksum" in policy, "policy 缺 checksum 纪律声明"
