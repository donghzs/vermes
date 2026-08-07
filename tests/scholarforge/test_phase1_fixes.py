"""ScholarForge Phase 1 — "名不副实"修复回归测试

覆盖四项 P0/P1 修复：
1. learn_style 落库 + write 自动仿写（此前 learn_style 只 return 从不落库，孤儿功能）
2. detect_design_flaws 学科无关 LLM 兜底（此前硬编码教育/心理学关键词）
3. plagiarism_check 诚实化（描述明确"文档内部"、不与外部库比对）
4. _call_llm 异步化 + 重试（此前同步 urllib 阻塞事件循环）
额外：review handler project_id 注入不再 UnboundLocalError 崩溃
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_phase1.db")
    import vermes_cli.scholarforge.database as db
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    yield db


@pytest.fixture
def sample_project(tmp_db):
    result = tmp_db.create_project(title="Phase1 测试论文", paper_type="本科论文", target_words=8000)
    return result["id"]


# ─────────────────────────────────────────────────────────
# 1. learn_style 落库 + write 自动仿写
# ─────────────────────────────────────────────────────────

SAMPLE_STYLE_TEXT = (
    "本研究采用混合研究方法。首先，我们收集了大量样本数据。"
    "然而，数据清洗过程存在挑战。因此，我们设计了严格的预处理流程。"
    "此外，本文提出了一种新的分析框架。综上所述，该方法具有显著优势。"
) * 5


class TestLearnStylePersistence:
    @pytest.mark.asyncio
    async def test_learn_style_persists_to_db(self, tmp_db, sample_project):
        """learn_style 传 project_id 后应写入 projects.style_prompt。"""
        from vermes_cli.scholarforge.tools import _handle_scholarforge_learn_style
        out = await _handle_scholarforge_learn_style(
            {"sample_text": SAMPLE_STYLE_TEXT, "project_id": sample_project}
        )
        assert "风格学习完成" in out
        assert f"已保存到项目 #{sample_project}" in out
        proj = tmp_db.get_project(sample_project)
        assert proj.get("style_prompt"), "style_prompt 应已落库"
        assert "写作风格指令" in proj["style_prompt"]

    @pytest.mark.asyncio
    async def test_learn_style_no_project_id_not_persisted(self, tmp_db):
        """未传 project_id 时不落库，提示用户。"""
        from vermes_cli.scholarforge.tools import _handle_scholarforge_learn_style
        out = await _handle_scholarforge_learn_style({"sample_text": SAMPLE_STYLE_TEXT})
        assert "无法确定 project_id" in out or "project_id" in out

    @pytest.mark.asyncio
    async def test_write_injects_learned_style(self, tmp_db, sample_project, monkeypatch):
        """write 应读取项目已落库的风格并注入 LLM prompt。"""
        import vermes_cli.scholarforge.tools as tools
        # 先落库风格
        from vermes_cli.scholarforge.project_context import save_style_profile
        save_style_profile(sample_project, "# 写作风格指令\n请用短句、少用被动语态。")

        captured = {}

        async def fake_llm(prompt, system="", **kwargs):
            captured["prompt"] = prompt
            return "## 引言\n这是生成的内容。"

        monkeypatch.setattr(tools, "_call_llm", fake_llm)
        out = await tools._handle_scholarforge_write({
            "topic": "测试主题", "section_type": "introduction",
            "project_id": sample_project, "quality_gate": "off",
        })
        assert "写作风格要求" in captured["prompt"]
        assert "请用短句" in captured["prompt"]


# ─────────────────────────────────────────────────────────
# 2. detect_design_flaws_llm 学科无关
# ─────────────────────────────────────────────────────────

class TestDesignFlawsLlm:
    @pytest.mark.asyncio
    async def test_llm_parses_flaws(self):
        from vermes_cli.scholarforge.validators import detect_design_flaws_llm

        async def fake_llm(prompt, system="", **kwargs):
            return (
                '{"flaws": [{"severity": "P0", "category": "缺对照组", '
                '"description": "医学试验无安慰剂对照", "evidence": "全文无对照组描述", '
                '"suggestion": "增加随机对照"}]}'
            )

        flaws = await detect_design_flaws_llm("某医学论文全文……", None, call_llm=fake_llm)
        assert len(flaws) == 1
        assert flaws[0].severity == "P0"
        assert flaws[0].category == "缺对照组"

    @pytest.mark.asyncio
    async def test_llm_fenced_json(self):
        from vermes_cli.scholarforge.validators import detect_design_flaws_llm

        async def fake_llm(prompt, system="", **kwargs):
            return '```json\n{"flaws": [{"severity": "P1", "category": "样本量不足"}]}\n```'

        flaws = await detect_design_flaws_llm("工程论文", None, call_llm=fake_llm)
        assert len(flaws) == 1
        assert flaws[0].severity == "P1"

    @pytest.mark.asyncio
    async def test_llm_malformed_fail_open(self):
        from vermes_cli.scholarforge.validators import detect_design_flaws_llm

        async def fake_llm(prompt, system="", **kwargs):
            return "这不是 JSON"

        flaws = await detect_design_flaws_llm("论文", None, call_llm=fake_llm)
        assert flaws == []

    @pytest.mark.asyncio
    async def test_llm_none_callable(self):
        from vermes_cli.scholarforge.validators import detect_design_flaws_llm
        flaws = await detect_design_flaws_llm("论文", None, call_llm=None)
        assert flaws == []

    def test_dedup_flaws(self):
        from vermes_cli.scholarforge.validators import DesignFlaw, _dedup_flaws
        # 前 40 字完全相同 → 视为重复（按 (category, description[:40]) 去重）
        long_desc = "无对照组导致结论不可信，因为缺乏基线比较且样本来源单一未做随机分配所以" + "A" * 20
        f1 = DesignFlaw("P0", "缺对照", long_desc, "", "")
        f2 = DesignFlaw("P0", "缺对照", long_desc + "尾部不同", "", "")  # 前40字同
        f3 = DesignFlaw("P1", "样本量", "样本偏小", "", "")
        out = _dedup_flaws([f1, f2, f3])
        assert len(out) == 2


# ─────────────────────────────────────────────────────────
# 3. plagiarism_check 诚实化
# ─────────────────────────────────────────────────────────

class TestPlagiarismHonesty:
    def test_schema_description_honest(self):
        from vermes_cli.scholarforge.tools import SCHOLARFORGE_PLAGIARISM_CHECK_SCHEMA
        desc = SCHOLARFORGE_PLAGIARISM_CHECK_SCHEMA["description"]
        assert "文档内部" in desc
        assert "不" in desc and "知网" in desc  # 明确不连外部库

    @pytest.mark.asyncio
    async def test_report_states_internal_only(self):
        from vermes_cli.scholarforge.tools import _handle_scholarforge_plagiarism_check
        text = "这是一段用于自相似检测的测试文本。" * 30
        out = await _handle_scholarforge_plagiarism_check({"text": text})
        assert "文档内部" in out
        assert "未连接任何外部查重库" in out or "不与知网" in out


# ─────────────────────────────────────────────────────────
# 4. _call_llm 异步化 + 重试
# ─────────────────────────────────────────────────────────

class TestCallLlmRetry:
    @pytest.mark.asyncio
    async def test_retry_then_success(self, monkeypatch):
        import vermes_cli.scholarforge.tools as tools

        monkeypatch.setattr(tools, "_resolve_credentials", lambda: {
            "api_key": "k", "base_url": "http://x", "model": "m", "provider": "p",
        })
        calls = {"n": 0}

        async def flaky_sync(url, body, headers):
            calls["n"] += 1
            if calls["n"] < 2:
                raise tools._LlmHttpError("5xx", retryable=True, http_code=503)
            return "OK 内容", None

        monkeypatch.setattr(tools, "_call_llm_request", flaky_sync)
        monkeypatch.setattr(tools.asyncio, "sleep", _noop_sleep)
        out = await tools._call_llm("prompt")
        assert out == "OK 内容"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_4xx_no_retry(self, monkeypatch):
        import vermes_cli.scholarforge.tools as tools

        monkeypatch.setattr(tools, "_resolve_credentials", lambda: {
            "api_key": "k", "base_url": "http://x", "model": "m", "provider": "p",
        })
        calls = {"n": 0}

        async def failing_sync(url, body, headers):
            calls["n"] += 1
            raise tools._LlmHttpError("❌ HTTP 401", retryable=False, http_code=401)

        monkeypatch.setattr(tools, "_call_llm_request", failing_sync)
        out = await tools._call_llm("prompt")
        assert "401" in out
        assert calls["n"] == 1, "4xx 不应重试"


async def _noop_sleep(_s):
    return None


# ─────────────────────────────────────────────────────────
# 额外：review handler project_id 不再崩溃
# ─────────────────────────────────────────────────────────

class TestReviewNoCrash:
    @pytest.mark.asyncio
    async def test_review_with_project_id(self, tmp_db, sample_project, monkeypatch):
        import vermes_cli.scholarforge.tools as tools

        async def fake_llm(prompt, system="", **kwargs):
            return "评审意见：结构清晰。"

        monkeypatch.setattr(tools, "_call_llm", fake_llm)
        # 此前传 project_id 会 UnboundLocalError 崩溃
        out = await tools._handle_scholarforge_review({
            "draft": "这是一篇待审阅的论文草稿。" * 20,
            "project_id": sample_project,
        })
        assert "评审意见" in out
