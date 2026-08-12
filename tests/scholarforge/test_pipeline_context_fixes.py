"""ScholarForge pipeline 上下文恢复 / 落库 / 工具路径回归测试。

覆盖审计发现的三个阻断性 bug（修复前会因「正确原因」失败）：

- #384 (_get_ctx 用 dict 调 add_paper 致上下文恢复断裂)：
    根因：`_get_ctx` 把 db.get_project 返回的 literatures(元素为 dict) 直接喂
    `ProjectContext.add_paper(lit)`，而 add_paper 内部读 `card.paper_id` 抛
    AttributeError，被 `except Exception: logger.debug(...)` 静默吞掉 →
    恢复循环中断、outline 丢失、ctx.papers 被 dict 污染。
    本测试断言：恢复后 ctx.papers 全部是 PaperCard（不是 dict），且
    global_citation_map 已按入池顺序注册编号。

- #385 (_outline_hook 传字符串列表致大纲永不落库)：
    根因：OutlineAgent.run 把 ctx.outline["sections"] 设成纯字符串列表
    (["引言","方法",...])，而 db.save_outline 经 _norm_outline_section 调
    `sec.get("id")` 对字符串抛 AttributeError，且该异常在 save_outline 内部
    try 之外，穿出后被 hook 的 except 静默吞掉。
    本测试断言：_outline_hook 运行后 outlines 表确有行，且形状对齐
    OutlineAgent 发往前端的 outline_for_frontend({id,number,title,wordCount,status})。

- #383 (blueprint add_message 参数错位致对话历史损坏)：
    根因：非 pipeline 分支用 `add_message(pid, "user", req.message, agent_name)`，
    但签名是 `(pid, agent, role, content)` —— 列错位导致 list_messages 过滤失效。
    本测试直接按两个根因调用顺序写库并断言 list_messages 能按 agent 过滤出来；
    反向验证见文件末尾注释（old order 下 list_messages 返回 0 行）。

- #379 (scholarforge_run_pipeline 工具端到端)：
    复用 blueprint._run_pipeline_core（与 SSE 路径同一套 Stage 组装/落库 hook），
    用 _call_llm 桩驱动真实 handler，断言大纲与正文经 hook 落库。
"""
import asyncio

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    import vermes_cli.scholarforge.database as db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "pipeline_ctx.db"))
    db.init_db()
    yield db


@pytest.fixture
def pid(tmp_db):
    return tmp_db.create_project("上下文恢复测试论文", "本科论文")["id"]


def _seed_papers(tmp_db, pid, n=4):
    for i in range(n):
        tmp_db.add_literature(
            pid, title=f"文献{i}", authors=["张三", "李四"], year=2020 + i,
            venue="JMLR", abstract=f"摘要{i}", url=f"https://x/{i}", doi=f"10.0/x{i}",
        )


def _fake_llm_factory(responses: dict):
    """_call_llm 替身（async，单位置参数）。

    优先按调用方传入的 (stage 名→回复) 命中；命中不到时按 prompt 内容兜底，
    保证 OutlineAgent 能解析出 '## 标题' 章节（否则 ctx.outline 为空、pipeline 报错）。
    """
    async def _fake(prompt, system="", **kw):
        for key, val in responses.items():
            if key in prompt:
                return val
        if "大纲" in prompt or "章节" in prompt or "outline" in prompt.lower():
            return "## 引言\n## 相关工作\n## 研究方法\n## 实验与分析"
        if "选题" in prompt or "topic" in prompt.lower():
            return "选题可行，方向明确。"
        if "撰写" in prompt or "写作" in prompt or "writing" in prompt.lower():
            return "这是自动生成的论文章节正文。"
        if "润色" in prompt or "refinement" in prompt.lower():
            return "润色后的正文。"
        if "审稿" in prompt or "reviewer" in prompt.lower():
            return ('{"创新性":7,"方法论":7,"论证逻辑":7,"语言表达":7,'
                    '"引用完整性":7,"数据真实性":7,"总体印象":"结构完整",'
                    '"致命问题":"无","修改建议":["建议补充实验"],"综合评分":75}')
        if ("{" in prompt and "}" in prompt) or "JSON" in prompt or "json" in prompt:
            # 任意要求 JSON 的解析（如 RefinementAgent 的 JSON 校验）返回合法 JSON
            return '{"ok": true, "items": []}'
        return "占位回复"
    return _fake


class TestOutlineHookSavesNormalized:
    """#385 回归：_outline_hook 必须把字符串列表落库成 dict 形状。"""

    def test_hook_persists_string_list_outline(self, tmp_db, pid):
        import vermes_cli.scholarforge.blueprint as bp
        from vermes_cli.scholarforge.agents import ProjectContext

        # create_project 会预置默认大纲，先清空再验证 hook 落库的精确行数
        with tmp_db.get_conn() as _c:
            _c.execute("DELETE FROM outlines WHERE project_id=?", (pid,))

        ctx = ProjectContext(project_id=pid)
        ctx.outline = {"sections": ["引言", "相关工作", "方法"]}

        bp._outline_hook(ctx, pid, "outline")

        rows = tmp_db.get_outline(pid)
        assert len(rows) == 3, "大纲必须落库 3 行（修复前因 AttributeError 永零行）"
        assert [r["title"] for r in rows] == ["引言", "相关工作", "方法"]
        # 形状对齐 OutlineAgent 发往前端的 outline_for_frontend
        assert all({"id", "number", "wordCount", "status"} <= set(r.keys()) for r in rows)
        # SQLite 类型亲和：section_number 回读为字符串，断言按业务等价（非类型）
        assert [str(r["number"]) for r in rows] == ["1", "2", "3"]

    def test_hook_skips_when_stage_not_outline(self, tmp_db, pid):
        import vermes_cli.scholarforge.blueprint as bp
        from vermes_cli.scholarforge.agents import ProjectContext

        # create_project 会预置默认大纲，先清空再验证 writing 阶段不写库
        with tmp_db.get_conn() as _c:
            _c.execute("DELETE FROM outlines WHERE project_id=?", (pid,))

        ctx = ProjectContext(project_id=pid)
        ctx.outline = {"sections": ["引言"]}
        # 阶段名不是 outline → hook 不应写库
        bp._outline_hook(ctx, pid, "writing")
        assert tmp_db.get_outline(pid) == []


class TestGetCtxRestoresPaperCards:
    """#384 回归：_get_ctx 恢复后 ctx.papers 必须是 PaperCard，不是 dict。"""

    def test_papers_restored_as_papercard_not_dict(self, tmp_db, pid):
        import vermes_cli.scholarforge.blueprint as bp
        from vermes_cli.scholarforge.agents import PaperCard, ProjectContext

        _seed_papers(tmp_db, pid, n=3)

        # 模拟跨请求重建 ctx（清空内存缓存）
        bp._session_contexts.clear()
        ctx = asyncio.get_event_loop().run_until_complete(
            bp._get_ctx(str(pid), client_id="")
        )
        assert isinstance(ctx, ProjectContext)
        assert len(ctx.papers) == 3, "应恢复 3 篇文献"
        assert all(isinstance(p, PaperCard) for p in ctx.papers), (
            "修复前恢复的是 dict（直接调 add_paper(lit)），下游 "
            "to_context_text()/build_global_ref_list() 会因 .paper_id 崩溃"
        )
        # global_citation_map 按入池顺序注册
        nums = [ctx.global_citation_map.get(p.paper_id) for p in ctx.papers]
        assert nums == [1, 2, 3], "全局引用编号必须按入池顺序递增"

    def test_restored_paper_fields_populated(self, tmp_db, pid):
        import vermes_cli.scholarforge.blueprint as bp

        _seed_papers(tmp_db, pid, n=1)
        bp._session_contexts.clear()
        ctx = asyncio.get_event_loop().run_until_complete(
            bp._get_ctx(str(pid), client_id="")
        )
        card = ctx.papers[0]
        assert card.title == "文献0"
        assert card.authors == ["张三", "李四"], "authors 字段契约（list）必须保留"
        assert card.year == "2020", "year 必须转成字符串（PaperCard.year: str）"


class TestAddMessageSignature:
    """#383 回归：add_message 必须是 (pid, agent, role, content)。"""

    def test_correct_order_stored_filterable_by_agent(self, tmp_db, pid):
        # 修复后的调用顺序
        tmp_db.add_message(pid, "topic", "user", "请帮我写引言")
        rows = tmp_db.list_messages(pid, "topic")
        assert len(rows) == 1
        assert rows[0]["role"] == "user"
        assert rows[0]["content"] == "请帮我写引言"
        assert rows[0]["agent"] == "topic"

    def test_old_buggy_order_breaks_list_filter(self, tmp_db, pid):
        # 旧 bug 的调用顺序：add_message(pid, "user", req.message, agent_name)
        # 即 agent="user", role=req.message(正文), content=agent_name
        tmp_db.add_message(pid, "user", "请帮我写引言", "topic")
        # 按 agent="topic" 过滤（前端 api_list_messages 的真实查询）应一无所获
        assert tmp_db.list_messages(pid, "topic") == [], (
            "旧参数错位下，list_messages(agent='topic') 返回 0 行 → 对话历史丢失。"
            "这正是 #383 的破坏性后果：历史损坏且前端永远读不到。"
        )


class TestRunPipelineToolEndToEnd:
    """#379 回归：scholarforge_run_pipeline 工具驱动真实 handler，大纲+正文落库。"""

    def test_pipeline_saves_outline_and_draft(self, tmp_db, pid, monkeypatch):
        import vermes_cli.scholarforge.tools as tools

        _seed_papers(tmp_db, pid, n=4)

        async def _fake_search(topic, limit=12):
            from vermes_cli.scholarforge.agents import PaperCard

            for i in range(3):
                yield PaperCard(
                    paper_id=f"auto{i}", title=f"自动文献{i}", authors=["A"],
                    year="2021", venue="X", abstract="...", url="", source="auto",
                )

        monkeypatch.setattr(
            "vermes_cli.scholarforge.agents.search_papers", _fake_search
        )

        # 按 stage 名返回桩回复
        stage_resp = {
            "选题": "# 选题分析\n论文选题可行。",
            "大纲": "## 引言\n## 相关工作\n## 研究方法\n## 实验与分析",
            "撰写": "这是正文内容。",
            "润色": "润色后正文。",
            "审稿": "审稿意见。",
        }
        monkeypatch.setattr(tools, "_call_llm", _fake_llm_factory(stage_resp))

        result = asyncio.get_event_loop().run_until_complete(
            tools._handle_scholarforge_run_pipeline({
                "project_id": pid, "message": "写一篇关于大语言模型的论文",
            })
        )
        assert isinstance(result, str)
        assert "✅" in result, f"工具应返回成功摘要，实际: {result[:200]}"

        # 大纲必须落库（_outline_hook 经 _normalize_outline_for_save 写入）
        outlines = tmp_db.get_outline(pid)
        assert len(outlines) >= 1, "pipeline 生成的大纲必须经 hook 落库（#385 修复后）"
        # 正文必须落库（_writing_hook → save_section_content(full_paper)）
        draft = tmp_db.get_section_content(pid, "full_paper")
        assert draft and len(draft) > 20, (
            "pipeline 正文必须经 hook 落库（full_paper 章节）。"
            f"实际 draft 长度={len(draft or '')}"
        )
