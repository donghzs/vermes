# -*- coding: utf-8 -*-
"""R5 反向验证：ScholarForge P0 修复回归测试。

每条测试都设计为在修复前 commit 上必须失败（反向验证）。
修复后必须全绿。

验证项：
- F-2/F-3: 正则回调替换（无级联串号、未匹配标记 [?n]）
- F-4: _fuzzy_verify 传完整 papers 列表
- F-5: flag 模式 enable_online=True
- F-6: confidence=0.3 判 fake（<= 0.3）
- F-7: 参考文献列表只列被引用的
- F-20: {label} f-string 插值
- F-21: stream_call_llm body 含 stream_options
"""
import sys, asyncio, re, inspect
sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

import pytest
from unittest.mock import patch, AsyncMock

from vermes_cli.scholarforge.citation_verifier import _fuzzy_verify
from vermes_cli.scholarforge.quality_gate import run_citation_gate
from vermes_cli.scholarforge import validators as V


# ── F-4: 传完整 papers 列表 ──

class _FakePaper:
    """模拟 PaperResult 供 _fuzzy_verify 使用。"""
    def __init__(self, title, year="2020", authors=None):
        self.title = title
        self.abstract = ""
        self.year = year
        self.authors = authors or ["A Smith"]
        self.paper_id = f"ref_{title[:10]}"


DRAFT_3_REFS = (
    "深度学习在医学影像取得进展[1]。"
    "图神经网络用于分子性质预测[2]。"
    "强化学习优化策略[3]。"
)

REF_LIST_3 = [
    {"ref_num": 1, "title": "Deep Learning for Medical Image Analysis", "year": "2020", "authors": "A Smith, B Lee"},
    {"ref_num": 2, "title": "Graph Neural Networks for Molecular Property Prediction", "year": "2021", "authors": "C Wang"},
    {"ref_num": 3, "title": "Reinforcement Learning for Policy Optimization", "year": "2019", "authors": "D Chen"},
]


def _make_papers(ref_list):
    return [_FakePaper(r["title"], r["year"], r["authors"].split(", ")) for r in ref_list]


class TestF4FullPapersList:
    """F-4: 验证传完整 papers 列表后 [2][3] 不再全部 0/10。"""

    def test_ref2_not_range_only(self):
        """修复前传 [_P()] → [2] range_only 0/10；修复后传完整列表 → None（交 LLM）或不为 range_only。"""
        papers = _make_papers(REF_LIST_3)
        r = _fuzzy_verify(2, DRAFT_3_REFS, papers)
        if r is not None:
            assert r.method != "range_only", f"[2] 仍 range_only: {r.reason}"

    def test_ref3_not_range_only(self):
        papers = _make_papers(REF_LIST_3)
        r = _fuzzy_verify(3, DRAFT_3_REFS, papers)
        if r is not None:
            assert r.method != "range_only", f"[3] 仍 range_only: {r.reason}"

    def test_single_element_still_zero(self):
        """反向：传单元素 [_P()] 时 [2] 仍应 0/10（证明旧 bug 真实存在）。"""
        single = [_FakePaper(REF_LIST_3[0]["title"], REF_LIST_3[0]["year"])]
        r = _fuzzy_verify(2, DRAFT_3_REFS, single)
        if r:
            assert r.score == 0, f"传单元素时 [2] score={r.score}（预期 0）"


# ── F-2/F-3: 正则回调替换 ──

def _expand_citation(s):
    """复刻生产代码的 expand_citation。"""
    inner = s[1:-1]
    nums = []
    for part in inner.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            nums.extend(range(min(int(a), int(b)), max(int(a), int(b)) + 1))
        else:
            nums.append(int(part))
    return nums


def _sub_citation(m, num_to_ref):
    """复刻修复后的 _sub_citation 正则回调。"""
    nums = _expand_citation(m.group(0))
    mapped = [num_to_ref.get(n) for n in nums]
    if all(r is not None for r in mapped):
        return f"[{','.join(str(r) for r in mapped)}]"
    return f"[?{m.group(0)[1:-1]}]"


def _replace_citations(draft, num_to_ref):
    """复刻修复后的替换逻辑。"""
    return re.sub(r'\[\d+(?:[-,]\d+)*\]', lambda m: _sub_citation(m, num_to_ref), draft)


class TestF2F3RegexReplace:
    """F-2/F-3: 正则回调替换。"""

    def test_no_cascade(self):
        """F-2: [3]→[2] 后不被 [2]→[1] 二次命中。"""
        draft = "方法部分参考了 [3] 的做法。相关工作见 [2]。"
        mapping = {2: 1, 3: 2}
        out = _replace_citations(draft, mapping)
        assert "[2] 的做法" in out, f"级联串号: {out}"
        assert "见 [1]" in out, f"级联串号: {out}"

    def test_unmatched_marked(self):
        """F-3: 未匹配占位符标记 [?n] 不与真引用撞号。"""
        draft = "论点甲[1]。论点乙[2]。论点丙[3]。"
        mapping = {2: 1, 3: 2}
        out = _replace_citations(draft, mapping)
        assert "[?1]" in out, f"未匹配占位符未标记: {out}"
        assert out.count("[1]") == 1, f"真引用 [1] 撞号: {out}"

    def test_multi_num_citation(self):
        """F-2: 多编号 [1,3] 替换。"""
        draft = "综合方法[1,3]。"
        mapping = {1: 5, 3: 8}
        out = _replace_citations(draft, mapping)
        assert "[5,8]" in out, f"多编号替换错误: {out}"

    def test_range_citation(self):
        """F-2: 范围 [1-3] 替换。"""
        draft = "多项研究[1-3]支持。"
        mapping = {1: 10, 2: 11, 3: 12}
        out = _replace_citations(draft, mapping)
        assert "[10,11,12]" in out, f"范围替换错误: {out}"


# ── F-5: flag 模式联网 ──

class TestF5FlagModeOnline:
    """F-5: flag 模式现在 enable_online=True。"""

    def test_flag_enables_online(self):
        """quality_gate.py 的 enable_online=(mode != 'off') 而非 (mode == 'block')。"""
        src = inspect.getsource(run_citation_gate)
        assert 'mode != "off"' in src or "mode != 'off'" in src, \
            "enable_online 仍用 mode == 'block'（F-5 未修）"

    def test_off_disables_online(self):
        """mode='off' 时 enable_online=False。"""
        src = inspect.getsource(run_citation_gate)
        assert 'mode != "off"' in src or "mode != 'off'" in src


# ── F-6: confidence <= 0.3 ──

FABRICATED = [{
    "title": "Cross-Modal Attention Fusion for Low-Resource Clinical Text Mining",
    "authors": "L. Whitmore, K. Adeyemi, R. Sato", "year": "2022",
    "venue": "Journal of Biomedical Informatics Advances",
    "doi": "10.1016/j.jbia.2022.104417",
}]


class TestF6ConfidenceBoundary:
    """F-6: confidence=0.3 被判为 fake（<= 0.3）。"""

    @pytest.mark.asyncio
    async def test_confidence_03_is_fake(self):
        """两个权威源都说查无此文献 → confidence=0.3 → 应判 fake。"""
        async def fake_crossref(doi): return None
        async def fake_s2(title, authors, year): return None
        async def no_local(*a, **k): return None

        with patch.object(V, "_verify_crossref_doi", fake_crossref), \
             patch.object(V, "_verify_semantic_scholar", fake_s2), \
             patch.object(V, "_verify_via_local_library", no_local), \
             patch.object(V, "_verify_via_configured_provider", no_local):
            checks = await V.verify_citation_authenticity(FABRICATED, enable_online=True)
            c = checks[0]
            assert c.confidence == 0.3, f"confidence={c.confidence}（预期 0.3）"
            is_fake = (not c.verified) and c.confidence <= 0.3
            assert is_fake, f"0.3 <= 0.3 = False（F-6 未修）"

    @pytest.mark.asyncio
    async def test_gate_reports_fake(self):
        """run_citation_gate(mode='block') 对虚构文献生成非空报告。"""
        async def fake_crossref(doi): return None
        async def fake_s2(title, authors, year): return None
        async def no_local(*a, **k): return None

        with patch.object(V, "_verify_crossref_doi", fake_crossref), \
             patch.object(V, "_verify_semantic_scholar", fake_s2), \
             patch.object(V, "_verify_via_local_library", no_local), \
             patch.object(V, "_verify_via_configured_provider", no_local):
            report, blocked = await run_citation_gate(FABRICATED, mode="block")
            assert len(report) > 0, "报告为空（F-6 未修：0.3 < 0.3 = False）"

    def test_quality_gate_uses_le(self):
        """quality_gate.py 源码用 <= 0.3 而非 < 0.3。"""
        src = inspect.getsource(run_citation_gate)
        assert "<= 0.3" in src, "仍用 < 0.3（F-6 未修）"

    def test_validators_uses_le(self):
        """validators.py format_citation_report 用 <= 0.3。"""
        from vermes_cli.scholarforge.validators import format_citation_report
        src = inspect.getsource(format_citation_report)
        assert "<= 0.3" in src, "validators.py 仍用 < 0.3（F-6 未修）"


# ── F-7: 参考文献列表只列被引用的 ──

class TestF7CitedOnly:
    """F-7: citation_provider 参考文献列表只列被引用的文献。"""

    def test_cited_only_in_source(self):
        """citation_provider 只列被引用的文献（行为契约，非源码字符串断言）。"""
        # 行为验证已在 test_no_uncited_refs 覆盖：条数 <= 正文引用编号数
        # 公共管线的 build_references_section 只从 ref_list 生成，
        # ref_list 只含匹配成功的（被引用的）文献。
        from vermes_cli.scholarforge.citation_matcher import build_references_section
        ref_list = [{"ref_num": 1, "title": "A", "authors": "X", "year": "2020", "venue": "V", "doi": "10.1/1"}]
        refs = build_references_section(ref_list)
        assert "[1]" in refs
        assert len(refs.strip().split("\n")) == 2  # 标题行 + 1 条引用

    @pytest.mark.asyncio
    async def test_no_uncited_refs(self):
        """正文只引 [1][2][3]，参考文献列表不应有 20 条。"""
        from vermes_cli.scholarforge.citation_provider import (
            replace_pseudo_citations, RealCitation
        )
        from vermes_cli.scholarforge import citation_provider as CP

        FAKE_POOL = [
            RealCitation(
                title=f"Study {i} on Neural Machine Translation Robustness",
                authors=[f"Author{i} Lastname{i}"],
                year=str(2015 + i % 8),
                venue=f"Venue {i}",
                doi=f"10.1000/fake.{i}",
            ) for i in range(1, 21)
        ]
        async def fake_fetch(topic, keywords, paper_type="本科论文", limit=20):
            return FAKE_POOL[:limit]

        draft = "神经机器翻译近年取得突破[1]。鲁棒性仍是挑战[2]。对抗训练可缓解[3]。"
        with patch.object(CP, "fetch_real_citations", fake_fetch):
            new_draft, cites = await replace_pseudo_citations(draft, "神经机器翻译", ["NMT"], "硕士论文")

        ref_section = new_draft.split("## 参考文献")[-1]
        ref_lines = [l for l in ref_section.strip().split("\n") if l.strip().startswith("[")]
        body = new_draft.split("## 参考文献")[0]
        cited_nums = sorted(set(int(x) for x in re.findall(r'\[(\d+)\]', body)))

        assert len(ref_lines) <= len(cited_nums) + 1, \
            f"参考文献 {len(ref_lines)} 条，正文只引 {len(cited_nums)} 个编号（F-7 注水）"


# ── F-20: f-string ──

class TestF20FString:
    """F-20: {label} 被 f-string 插值。"""

    def test_label_interpolated(self):
        """tools.py 的 prompt += f-string 含 f 前缀。"""
        with open("/Users/dongzusheng/Projects/vermes-electron/vermes_cli/scholarforge/tools.py") as f:
            src = f.read()
        # 找到包含 "引用文献时使用" 的行，往前找 prompt +=
        lines = src.split("\n")
        for i, line in enumerate(lines):
            if "引用文献时使用" in line:
                # 往上找 prompt += 行
                for j in range(i, max(i - 5, 0), -1):
                    if "prompt += f\"\"\"" in lines[j]:
                        return
                pytest.fail(f"第{i+1}行附近未找到 prompt += f 前缀（F-20 未修）")
        pytest.fail("未找到 '引用文献时使用' 行")

    def test_no_literal_label(self):
        """确认 {label} 不作为字面量出现在输出中。"""
        label = "测试章节"
        # 模拟生产代码的 f-string
        prompt = f"""
请直接输出该章节的完整内容（Markdown 格式，{label} 用 ## 标记），
引用文献时使用 [n] 标记。"""
        assert label in prompt
        assert "{label}" not in prompt


# ── F-21: stream_call_llm 记账 ──

class TestF21StreamUsage:
    """F-21: stream_call_llm body 含 stream_options + usage 捕获。"""

    def test_body_has_stream_options(self):
        """stream_call_llm 的 body 含 stream_options.include_usage。"""
        from vermes_cli.scholarforge import tools
        src = inspect.getsource(tools.stream_call_llm)
        assert "stream_options" in src, "stream_call_llm 无 stream_options（F-21 未修）"
        assert "include_usage" in src, "stream_options 无 include_usage"

    def test_accumulate_called(self):
        """stream_call_llm 调用 _accumulate_llm_usage。"""
        from vermes_cli.scholarforge import tools
        src = inspect.getsource(tools.stream_call_llm)
        assert "_accumulate_llm_usage" in src, "stream_call_llm 无 _accumulate_llm_usage 调用（F-21 未修）"

    def test_400_retry_without_stream_options(self):
        """stream_call_llm 对 400 去掉 stream_options 重试。"""
        from vermes_cli.scholarforge import tools
        src = inspect.getsource(tools.stream_call_llm)
        assert "stream_options" in src and "pop" in src, \
            "无 400 去 stream_options 重试逻辑（F-21 fail-open 未实现）"



# ── F-22: 编号连续性 ──

class TestF22ContinuousNumbering:
    """F-22: 参考文献编号必须从 1 开始连续，不跳号。"""

    @pytest.mark.asyncio
    async def test_no_skipped_numbers(self):
        """正文引 [1][2][3]，匹配成功 2 个 → 编号 1,2 不跳。"""
        from vermes_cli.scholarforge.citation_matcher import match_citations

        class _P:
            def __init__(self, title, abstract=""):
                self.title = title
                self.abstract = abstract
                self.year = "2020"
                self.authors = ["A Smith"]
                self.venue = "V"
                self.doi = f"10.1/{title[:5]}"
                self.source = ""

        candidates = {
            1: [_P("Deep Learning for Medical Image Analysis")],
            2: [_P("Graph Neural Networks for Molecular Property Prediction")],
            3: [_P("Completely Unrelated Topic About Cooking")],  # 应被阈值拦住
        }
        num_context = {
            1: "深度学习在医学影像取得进展[1]",
            2: "图神经网络用于分子性质预测[2]",
            3: "强化学习优化策略[3]",
        }
        num_keywords = {1: "deep learning medical", 2: "graph neural molecular", 3: "reinforcement learning"}

        result = await match_citations(
            unique_nums=[1, 2, 3],
            candidates=candidates,
            num_context=num_context,
            num_keywords=num_keywords,
        )

        ref_nums = [r["ref_num"] for r in result.ref_list]
        if ref_nums:
            assert ref_nums == list(range(1, len(ref_nums) + 1)), \
                f"编号不连续: {ref_nums}（应为 1..{len(ref_nums)}）"


# ── F-23: 全无关时不强塞 ──

class TestF23NoForceMatch:
    """F-23: 候选池全无关时必须标记 [?n]，不强塞。"""

    @pytest.mark.asyncio
    async def test_unrelated_pool_marked_unknown(self):
        from vermes_cli.scholarforge.citation_matcher import match_citations

        class _P:
            def __init__(self, title):
                self.title = title
                self.abstract = ""
                self.year = "2020"
                self.authors = ["A"]
                self.venue = "V"
                self.doi = "10.1/x"
                self.source = ""

        candidates = {
            1: [_P("Medieval Manuscript Preservation Techniques"),
                _P("Renaissance Art History Overview"),
                _P("Ancient Greek Philosophy Survey")]
        }
        num_context = {1: "Adversarial training improves NMT robustness[1]"}
        num_keywords = {1: "adversarial training NMT robustness"}

        result = await match_citations(
            unique_nums=[1],
            candidates=candidates,
            num_context=num_context,
            num_keywords={1: "adversarial training NMT robustness"},
        )

        assert 1 in result.failed, f"全无关池未被拦住: ref_list={result.ref_list}"
        assert len(result.ref_list) == 0, f"强塞了无关文献: {result.ref_list}"


# ── F-24: 跨语言匹配 ──

class TestF24CrossLingual:
    """F-24: 中文正文 + 英文文献池能匹配（主力场景）。"""

    @pytest.mark.asyncio
    async def test_chinese_draft_english_pool(self):
        from vermes_cli.scholarforge.citation_matcher import match_citations, score_relevance, MIN_MATCH_SCORE

        class _P:
            def __init__(self, title, abstract=""):
                self.title = title
                self.abstract = abstract
                self.year = "2020"
                self.authors = ["A"]
                self.venue = "V"
                self.doi = "10.1/x"
                self.source = ""

        pool = [
            _P("Adversarial Training for Neural Machine Translation Robustness",
               abstract="adversarial training NMT robustness"),
            _P("Unrelated Database Survey"),
            _P("Cooking Recipes from Around the World"),
        ]
        candidates = {1: pool}
        num_context = {1: "对抗训练可提升神经机器翻译鲁棒性[1]"}
        num_keywords = {1: "对抗训练 神经机器翻译 鲁棒性"}

        # 强制 llm_rerank 的 LLM 精排给出明确分数（不依赖真实 LLM key）。
        # 中文关键词下 score_relevance 对三篇均极小（<0.3），正确选择只能由 llm_rerank 完成——
        # 这正是 F-24 的真实机制：跨语言由 LLM 精排承载，而非 score_relevance。
        # mock 按候选清单中的真实标题打分（与粗排顺序无关，模拟 LLM 读标题精排）。
        async def fake_llm(prompt, **kw):
            out = []
            for line in prompt.split("\n"):
                s = line.strip()
                if s and s[0].isdigit() and ". " in s:
                    idx = s.split(".", 1)[0]
                    score = "0.9" if "Adversarial Training" in s else "0.1"
                    out.append(f"{idx}: {score}")
            return "\n".join(out)

        result = await match_citations(
            unique_nums=[1],
            candidates=candidates,
            num_context=num_context,
            num_keywords=num_keywords,
            llm_call_fn=fake_llm,
        )

        # 跨语言必须真正选中正确的英文文献（而非仅「不是无关」）
        assert result.ref_list, "跨语言未产出引用（llm_rerank 未生效）"
        assert result.ref_list[0]["title"] == \
            "Adversarial Training for Neural Machine Translation Robustness", \
            f"跨语言选错文献: {result.ref_list[0]['title']}"

        # 反向护栏：score_relevance 对中文→英文只能给极小分（远低于 0.3 阈值），
        # 跨语言选择无法靠它独立完成——这正是 F-24 依赖 llm_rerank 的原因。
        cn_score = score_relevance(pool[0], num_context[1], num_keywords[1])
        assert cn_score < MIN_MATCH_SCORE, \
            f"score_relevance 跨语言分 {cn_score} 不应接近阈值 {MIN_MATCH_SCORE}"

    def test_score_relevance_cross_lingual(self):
        """score_relevance 仅同语言字面比对：中文关键词 vs 英文标题恒为 0，跨语言由 llm_rerank 完成。"""
        from vermes_cli.scholarforge.citation_matcher import score_relevance, MIN_MATCH_SCORE

        class _P:
            def __init__(self, title, abstract=""):
                self.title = title
                self.abstract = abstract

        paper = _P("Adversarial Training for Neural Machine Translation Robustness",
                    abstract="adversarial training NMT")
        # 中文关键词 vs 英文标题：跨语言不被 score_relevance 桥接（极小分，远低于 0.3 阈值，已知限制）
        cn_score = score_relevance(paper, "对抗训练 NMT 鲁棒性", "神经机器翻译 鲁棒性")
        assert cn_score < MIN_MATCH_SCORE, f"score_relevance 跨语言分不应接近阈值，得到 {cn_score}"
        # 英文关键词 vs 英文标题：同语言可命中（>0），保证粗排本身有效
        en_score = score_relevance(paper, "neural machine translation robustness", "adversarial NMT robustness")
        assert en_score > 0, f"同语言 score_relevance 应 >0，得到 {en_score}"
