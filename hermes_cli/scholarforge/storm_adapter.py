"""
STORM 适配器 — 将 ScholarForge 的 provider 体系桥接到 STORM 引擎

核心设计：
1. ScholarForgeLM — 继承 dspy.LM，实现 basic_request 和 __call__
2. AcademicRM — 继承 dspy.Retrieve，用 arXiv/Crossref/S2 替代 STORM 默认的 web 搜索
3. StormAdapter — 封装 STORM 管线，SSE 事件桥接，子线程中先 dspy.configure
"""
import asyncio
import json
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger("scholarforge.storm_adapter")


# ============================================================================
# 1. ScholarForgeLM — 继承 dspy.LM，兼容 dspy.ChainOfThought
# ============================================================================

def create_scholarforge_lm(provider: str, model: str, api_key: str, base_url: str):
    """创建一个兼容 dspy.LM 的 ScholarForge LM 实例"""

    import dspy

    class ScholarForgeLM(dspy.LM):
        """用 httpx 直接调 OpenAI 兼容接口，兼容 dspy.ChainOfThought 调用"""

        def __init__(self, model, api_key, base_url, **kwargs):
            super().__init__(model=model)
            self.model = model  # dspy.LM 把 model 存到 kwargs，但我们也存一份
            self._api_key = api_key
            self._base_url = base_url.rstrip("/")
            # 覆盖 dspy.LM 默认 kwargs
            self.kwargs.update({
                "temperature": kwargs.get("temperature", 0.7),
                "max_tokens": kwargs.get("max_tokens", 4096),
                "top_p": 1,
                "n": 1,
            })
            self.provider = "scholarforge"
            self.history = []

        def basic_request(self, prompt, **kwargs):
            """dspy.LM abstractmethod — 返回原始响应 dict"""
            import httpx

            merged = {**self.kwargs, **kwargs}
            body = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": merged.get("temperature", 0.7),
                "max_tokens": merged.get("max_tokens", 4096),
                "n": merged.get("n", 1),
            }

            try:
                # NOTE: dspy.LM 接口是同步的，STORM 在子线程中调用此方法。
                # 使用连接池复用 + 合理超时，避免在子线程中创建过多连接。
                with httpx.Client(timeout=120, limits=httpx.Limits(max_connections=5, max_keepalive_connections=2)) as client:
                    resp = client.post(
                        f"{self._base_url}/chat/completions",
                        json=body,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                    )
                    if resp.status_code != 200:
                        err = resp.text[:300]
                        logger.error(f"ScholarForgeLM error: {resp.status_code} {err}")
                        return {"choices": [{"message": {"content": ""}}], "usage": {}}

                    data = resp.json()
                    # 记录 history
                    self.history.append({
                        "prompt": prompt,
                        "response": data,
                    })
                    return data
            except Exception as e:
                logger.error(f"ScholarForgeLM basic_request failed: {e}")
                return {"choices": [{"message": {"content": ""}}], "usage": {}}

        def __call__(self, prompt, only_completed=True, return_sorted=False, **kwargs):
            """dspy.LM abstractmethod — 返回 list[str]
            
            dspy 内部 dsp.predict._generate 调用此方法，
            期望返回 list[str]，然后 template.extract 处理。
            """
            data = self.basic_request(prompt, **kwargs)
            choices = data.get("choices", [])
            # 返回 list[str] — dspy template.extract 期望每个元素是 str
            results = []
            for choice in choices:
                content = choice.get("message", {}).get("content", "")
                if not content and "text" in choice:
                    content = choice["text"]
                results.append(content)
            
            if not results:
                results = [""]
            
            # only_completed 过滤
            if only_completed:
                results = [r for r in results if r.strip()]
                if not results:
                    results = [""]
            
            return results

    return ScholarForgeLM(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )


# ============================================================================
# 2. AcademicRM — 学术检索器，替代 STORM 默认的 web 搜索
# ============================================================================

def create_academic_rm(k: int = 3):
    """创建学术检索器"""
    from dspy.retrieve import Retrieve
    from dspy.primitives.prediction import Prediction

    class AcademicRM(Retrieve):
        """学术文献检索器 — arXiv + Crossref"""

        def __init__(self, k=3):
            super().__init__(k=k)
            self._papers_cache: dict[str, dict] = {}

        def forward(self, query_or_queries, exclude_urls=None, k=None, **kwargs):
            import requests
            import re

            queries = [query_or_queries] if isinstance(query_or_queries, str) else query_or_queries
            queries = [q.strip().split("\n")[0].strip() for q in queries]

            k = k or self.k
            exclude_urls = exclude_urls or []
            all_results = []

            for query in queries[:3]:
                # arXiv
                try:
                    resp = requests.get(
                        "https://export.arxiv.org/api/query",
                        params={"search_query": f"all:{query}", "max_results": str(k)},
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        entries = re.findall(r"<entry>(.*?)</entry>", resp.text, re.DOTALL)
                        for entry in entries[:k]:
                            title = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
                            summary = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
                            link = re.search(r'<id>(.*?)</id>', entry)
                            if title and summary:
                                url = link.group(1).strip() if link else ""
                                snippet = summary.group(1).strip()[:500]
                                if url not in exclude_urls:
                                    all_results.append({
                                        "url": url,
                                        "title": title.group(1).strip(),
                                        "description": title.group(1).strip(),
                                        "snippets": [snippet],  # STORM 期望 list
                                        "meta": {"source": "arxiv"},
                                    })
                except Exception as e:
                    logger.warning(f"arXiv search failed for '{query}': {e}")

                # Crossref
                try:
                    resp = requests.get(
                        "https://api.crossref.org/works",
                        params={"query": query, "rows": str(k), "select": "title,abstract,URL,author"},
                        headers={"User-Agent": "ScholarForge/1.0 (mailto:research@scholarforge.ai)"},
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        items = resp.json().get("message", {}).get("items", [])
                        for item in items[:k]:
                            titles = item.get("title", [])
                            abstract = item.get("abstract", "")
                            url = item.get("URL", "")
                            if titles and url and url not in exclude_urls:
                                if abstract:
                                    abstract = re.sub(r"<[^>]+>", "", abstract)[:500]
                                else:
                                    abstract = titles[0][:200]
                                all_results.append({
                                    "url": url,
                                    "title": titles[0],
                                    "description": titles[0],
                                    "snippets": [abstract],  # STORM 期望 list
                                    "meta": {"source": "crossref"},
                                })
                except Exception as e:
                    logger.warning(f"Crossref search failed for '{query}': {e}")

            # 返回 list[dict] — 与 STORM 的 YouRM 保持一致
            # 每个 dict: {url, title, description, snippets: [str], meta}
            return all_results[:k * 3]

    return AcademicRM(k=k)


# ============================================================================
# 3. StormAdapter — 封装 STORM 管线 + SSE 事件桥接
# ============================================================================

class StormAdapter:
    """STORM 管线适配器
    
    将 STORM 的同步管线包装成异步生成器，产出 SSE 事件。
    子线程中先 dspy.configure(lm=...)，再跑 STORM pipeline。
    """

    def __init__(self, provider: str, model: str, api_key: str, base_url: str):
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self._events: list[dict] = []
        self._lock = threading.Lock()

    def _emit(self, event: dict):
        with self._lock:
            self._events.append(event)

    async def run(self, topic: str):
        """运行 STORM 管线，异步产出事件"""
        self._emit({"type": "thinking", "message": f"初始化 STORM 引擎 (provider={self.provider}, model={self.model})..."})

        # 1. 创建 LM
        lm = create_scholarforge_lm(
            provider=self.provider,
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
        )

        # 2. 在主线程先 dspy.configure（确保 dspy.settings 全局有 LM）
        import dspy
        dspy.configure(lm=lm)
        self._emit({"type": "thinking", "message": "LM 配置完成，dspy.settings 已绑定"})

        # 3. 配置 STORM LMConfigs
        from knowledge_storm import STORMWikiLMConfigs

        lm_configs = STORMWikiLMConfigs()
        lm_configs.conv_simulator_lm = lm
        lm_configs.question_asker_lm = lm
        lm_configs.outline_gen_lm = lm
        lm_configs.article_gen_lm = lm
        lm_configs.article_polish_lm = lm

        self._emit({"type": "thinking", "message": "生成多视角研究角色..."})

        # 4. 创建检索器
        rm = create_academic_rm(k=3)
        self._emit({"type": "searching", "message": "学术检索器就绪 (arXiv + Crossref)"})

        # 5. 配置参数
        from knowledge_storm import STORMWikiRunnerArguments

        import tempfile
        output_dir = tempfile.mkdtemp(prefix="scholarforge_storm_")

        args = STORMWikiRunnerArguments(
            output_dir=output_dir,
            max_conv_turn=2,
            max_perspective=2,
            max_search_queries_per_turn=2,
            search_top_k=3,
            retrieve_top_k=3,
            max_thread_num=3,
        )

        # 6. Callback handler (STORM 要求非 None)
        from knowledge_storm.storm_wiki.modules.callback import BaseCallbackHandler as STORMCallback

        class ScholarForgeCallback(STORMCallback):
            """空实现 — STORM 需要 callback_handler 非 None"""
            pass

        # 6.5. 输出目录占位 — 实际在 _run_storm 中创建
        import tempfile

        # 7. 运行 pipeline（在子线程中跑）
        self._emit({"type": "thinking", "message": f"开始 STORM 管线（主题：{topic}）"})

        result_holder = {"error": None, "article": None}

        def _run_storm():
            try:
                # 子线程中重新 configure（dspy.settings 是线程局部的）
                dspy.configure(lm=lm)

                from knowledge_storm import STORMWikiRunner
                runner = STORMWikiRunner(args=args, lm_configs=lm_configs, rm=rm)
                runner.topic = topic
                runner.article_output_dir = tempfile.mkdtemp(prefix="scholarforge_storm_")

                # Step 1: 知识收集
                self._emit({"type": "searching", "message": "多视角知识收集中..."})
                info_table = runner.run_knowledge_curation_module(
                    callback_handler=ScholarForgeCallback()
                )
                self._emit({"type": "searching", "message": "知识收集完成"})

                # Step 2: 大纲生成
                self._emit({"type": "writing", "message": "生成论文大纲..."})
                outline = runner.run_outline_generation_module(
                    info_table,
                    callback_handler=ScholarForgeCallback()
                )
                self._emit({"type": "writing", "message": "大纲生成完成"})

                # Step 3: 文章生成
                self._emit({"type": "writing", "message": "撰写论文正文..."})
                draft_article = runner.run_article_generation_module(
                    outline,
                    info_table,
                    callback_handler=ScholarForgeCallback()
                )
                self._emit({"type": "writing", "message": "正文撰写完成"})

                # Step 4: 润色
                self._emit({"type": "writing", "message": "润色中..."})
                polished = runner.run_article_polishing_module(draft_article)

                # 获取最终文本
                if hasattr(polished, "get_article_as_plain_text"):
                    article_text = polished.get_article_as_plain_text()
                elif hasattr(polished, "dump_article_as_plain_text"):
                    article_path = os.path.join(output_dir, "storm_gen_article_polished.txt")
                    polished.dump_article_as_plain_text(article_path)
                    with open(article_path, encoding="utf-8") as f:
                        article_text = f.read()
                else:
                    article_text = str(polished)

                result_holder["article"] = article_text
                self._emit({"type": "writing", "message": "润色完成"})

            except Exception as e:
                logger.error(f"STORM pipeline error: {e}", exc_info=True)
                result_holder["error"] = str(e)

        # 在子线程中运行
        thread = threading.Thread(target=_run_storm, daemon=True)
        thread.start()

        # 主线程 yield 事件
        while thread.is_alive() or self._events:
            with self._lock:
                while self._events:
                    yield self._events.pop(0)
            if thread.is_alive():
                await asyncio.sleep(0.3)
            else:
                with self._lock:
                    while self._events:
                        yield self._events.pop(0)
                break

        thread.join(timeout=5)

        if result_holder["error"]:
            yield {"type": "error", "message": f"STORM 管线错误：{result_holder['error']}"}
        elif result_holder["article"]:
            yield {"type": "content", "text": result_holder["article"]}
            yield {"type": "done", "message": "STORM 全链路完成"}
        else:
            yield {"type": "error", "message": "STORM 管线未产出结果"}


# ============================================================================
# 测试入口
# ============================================================================

if __name__ == "__main__":
    import asyncio

    async def test():
        from hermes_cli.scholarforge import _load_vermes_config
        cfg, _ = _load_vermes_config()
        agnes_key = cfg.get("providers", {}).get("agnes", {}).get("api_key", "")

        adapter = StormAdapter(
            provider="agnes",
            model="agnes-2.0-flash",
            api_key=agnes_key,
            base_url="https://apihub.agnes-ai.com/v1",
        )
        async for event in adapter.run("deep learning for medical image analysis"):
            print(f"EVENT: {event}")

    asyncio.run(test())
