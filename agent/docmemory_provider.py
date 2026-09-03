"""⑮ 腿 C — DocMemoryProvider（第一方文档记忆层）。

与 RAGProvider 平级的第一方 MemoryProvider，实现完整 MemoryProvider 接口。
Always-on（不经 memory.provider config 单选），与任何外部 KB（Honcho/Mem0 等）共存。

核心价值（⑮ 三件套之「索引本」）：
  - 跨本索引：跨 project_handoffs / session_handoffs / cron_notepad / 文档 跨源检索
  - Markdown 落盘：~/.vermes/docs/<scope>/<slug>.md，天然免疫 prune_context 轮删/压缩
  - 四工具：doc_write / doc_read / doc_list / doc_search
  - 自动落盘钩子：on_pre_compress（压缩前落「结论+依据+未完成项」）、on_session_end（会话结论落盘）

落盘门控复用腿 B 逻辑：有决策/待办才落，纯问答不污染。
边界：
  - cron_notepad (④)：job 级瞬时状态
  - project_handoffs (腿 B)：结构化任务态（进度/阶段）
  - docmemory (本层)：人类可读完整结论+依据+索引
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger("agent.docmemory_provider")

# ── 落盘约定 ──
_DOCS_DIR_NAME = "docs"
_SCOPES = {"project", "agent", "task"}

# ── Markdown front-matter ──
_FM_RE = re.compile(r"^---\n(.*?\n)---\n", re.DOTALL)


def _docs_root(vermes_home: str = "") -> Path:
    """返回 ~/.vermes/docs/ 或 VERMES_HOME/docs/。"""
    base = Path(vermes_home) if vermes_home else Path.home() / ".vermes"
    return base / _DOCS_DIR_NAME


def _slugify(text: str) -> str:
    """把任意文本转为文件系统安全的 slug（≤60 字符）。"""
    slug = re.sub(r"[^\w\u4e00-\u9fff\-]", "_", text.strip())[:60]
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug or "untitled"


def _front_matter(meta: dict[str, str]) -> str:
    """生成 YAML front-matter。"""
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    """把查询切成可匹配词元：英文/数字整词 + 中文 bigram（滑窗）。

    为何必须分词：prefetch() 收到的是**用户整句提问**（见
    conversation_loop.py:1259 传入 _query），若拿整串去做子串包含判断，
    措辞稍有不同就恒不命中，自动召回等于没接。中文无空格，故用 2-gram
    滑窗近似分词——零依赖、对"记忆层方案"这类查询足够。
    """
    tokens: list[str] = []
    for w in _WORD_RE.findall(text.lower()):
        if len(w) >= 2:
            tokens.append(w)
    for seg in _CJK_RE.findall(text):
        if len(seg) < 2:
            continue                      # 单字信息量过低，丢弃防噪声
        tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    # 去重保序
    seen: set[str] = set()
    return [t for t in tokens if not (t in seen or seen.add(t))]


def _term_weight(term: str) -> int:
    """词元权重：越长的英文词/中文 bigram 越具区分度。"""
    if _CJK_RE.match(term):
        return 2
    return 3 if len(term) >= 4 else 2


def _parse_front_matter(content: str) -> tuple[dict[str, str], str]:
    """解析 Markdown front-matter，返回 (meta, body)。"""
    m = _FM_RE.match(content)
    if not m:
        return {}, content
    fm_block = m.group(1)
    body = content[m.end():]
    meta: dict[str, str] = {}
    for line in fm_block.split("\n"):
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body


class DocMemoryProvider(MemoryProvider):
    """文档记忆层 — always-on 第一方 provider。"""

    def __init__(self):
        self._docs_root: Optional[Path] = None
        self._session_id: str = ""
        self._initialized = False
        self._vermes_home: str = ""

    @property
    def name(self) -> str:
        return "docmemory"

    def is_available(self) -> bool:
        return True  # Always available — just needs filesystem

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._vermes_home = kwargs.get("VERMES_home", "")
        self._docs_root = _docs_root(self._vermes_home)
        # 确保 scope 目录存在
        for scope in _SCOPES:
            (self._docs_root / scope).mkdir(parents=True, exist_ok=True)
        self._initialized = True
        logger.info("DocMemoryProvider initialized (docs_root=%s)", self._docs_root)

    def system_prompt_block(self) -> str:
        if not self._initialized:
            return ""
        try:
            doc_count = sum(
                1 for f in self._docs_root.rglob("*.md") if f.is_file()
            )
            if doc_count == 0:
                return ""
            return f"[文档记忆] 已落盘 {doc_count} 篇文档，可用 doc_search / doc_list 工具检索。"
        except Exception:
            return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """召回与 query 相关的文档摘要（仅摘要，省 token）。"""
        if not self._initialized or not query.strip():
            return ""
        try:
            hits = self._search_internal(query, limit=3)
            if not hits:
                return ""
            parts = ["[文档记忆上下文]"]
            for h in hits:
                preview = h.get("preview", "")[:200]
                parts.append(f"📄 {h['scope']}/{h['slug']}: {preview}")
            return "\n".join(parts)
        except Exception as e:
            logger.debug("docmemory prefetch failed: %s", e)
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        pass  # 同步搜索足够快

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages=None,
        scope: str = "",
    ) -> None:
        pass  # docmemory 不按 turn 落盘 — 只在 on_pre_compress / on_session_end 落

    # -- 四工具 ──

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "doc_write",
                "description": (
                    "把一段结论/依据/笔记写入文档记忆层（Markdown 落盘，"
                    "免疫上下文压缩）。适合保存阶段结论、决策依据、索引条目。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "文档标题（生成 slug 作文件名）",
                        },
                        "content": {
                            "type": "string",
                            "description": "Markdown 正文（结论+依据+未完成项）",
                        },
                        "scope": {
                            "type": "string",
                            "enum": ["project", "agent", "task"],
                            "default": "task",
                            "description": "落盘范围",
                        },
                        "tags": {
                            "type": "string",
                            "description": "逗号分隔的标签（可选）",
                        },
                    },
                    "required": ["title", "content"],
                },
            },
            {
                "name": "doc_read",
                "description": "读取一篇文档记忆的完整内容。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {"type": "string", "enum": ["project", "agent", "task"]},
                        "slug": {"type": "string", "description": "文档 slug（doc_list 获取）"},
                        "title": {"type": "string", "description": "文档标题（可替代 slug，内部自动映射）"},
                    },
                },
            },
            {
                "name": "doc_list",
                "description": "列出文档记忆层中某 scope 下的所有文档。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "enum": ["project", "agent", "task"],
                            "default": "task",
                        },
                    },
                },
            },
            {
                "name": "doc_search",
                "description": "全文搜索文档记忆层（跨所有 scope）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if tool_name == "doc_write":
            return self._handle_write(args)
        elif tool_name == "doc_read":
            return self._handle_read(args)
        elif tool_name == "doc_list":
            return self._handle_list(args)
        elif tool_name == "doc_search":
            return self._handle_search(args)
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    # -- 工具实现 --

    def _handle_write(self, args: Dict[str, Any]) -> str:
        title = args.get("title", "").strip()
        content = args.get("content", "")
        scope = args.get("scope", "task")
        tags = args.get("tags", "")
        if not title or not content:
            return json.dumps({"error": "title and content are required"})
        if scope not in _SCOPES:
            scope = "task"
        slug = _slugify(title)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        meta = {
            "title": title,
            "created": ts,
            "session": self._session_id or "unknown",
            "scope": scope,
            "tags": tags,
        }
        fm = _front_matter(meta)
        full = fm + "\n" + content
        out_path = self._docs_root / scope / f"{slug}.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(full, encoding="utf-8")
        return json.dumps({
            "status": "ok",
            "path": str(out_path),
            "scope": scope,
            "slug": slug,
        }, ensure_ascii=False)

    def _handle_read(self, args: Dict[str, Any]) -> str:
        scope = args.get("scope", "task")
        slug = args.get("slug", "")
        # 支持按 title 直读：内部 _slugify 映射，消除「知道标题却读不了」的困惑
        if not slug and args.get("title"):
            slug = _slugify(args["title"])
        if scope not in _SCOPES or not slug:
            return json.dumps({"error": "scope 和 slug（或 title）至少给一个"}, ensure_ascii=False)
        path = self._docs_root / scope / f"{slug}.md"
        if not path.is_file():
            return json.dumps({"error": f"not found: {scope}/{slug}"})
        return json.dumps({
            "scope": scope,
            "slug": slug,
            "content": path.read_text(encoding="utf-8"),
        }, ensure_ascii=False)

    def _handle_list(self, args: Dict[str, Any]) -> str:
        scope = args.get("scope", "task")
        if scope not in _SCOPES:
            scope = "task"
        d = self._docs_root / scope
        if not d.is_dir():
            return json.dumps({"docs": []}, ensure_ascii=False)
        items = []
        for f in sorted(d.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
            meta, _ = _parse_front_matter(f.read_text(encoding="utf-8"))
            items.append({
                "slug": f.stem,
                "title": meta.get("title", f.stem),
                "created": meta.get("created", ""),
                "tags": meta.get("tags", ""),
            })
        return json.dumps({"scope": scope, "docs": items}, ensure_ascii=False)

    def _handle_search(self, args: Dict[str, Any]) -> str:
        query = args.get("query", "").strip()
        limit = min(max(args.get("limit", 5), 1), 10)
        if not query:
            return json.dumps({"error": "query is required"})
        hits = self._search_internal(query, limit=limit)
        return json.dumps({"results": hits}, ensure_ascii=False)

    def _search_internal(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """跨所有 scope 搜索：词元匹配，覆盖 front-matter 的 title/tags + 正文。

        两点刻意设计（审计后修正，见 Git 提交说明）：
          1. **分词**：查询先经 _tokenize() 切成词元再逐个匹配，而非整串子串
             判断——prefetch 传入的是用户整句提问，整串匹配命中率≈0。
          2. **覆盖 front-matter**：title/tags 一并参与匹配，否则用户按标题
             （如"记忆层方案"）检索会漏——标题并不出现在正文里。
        """
        if not self._docs_root:
            return []
        terms = _tokenize(query)
        if not terms:
            return []
        hits: list[dict[str, Any]] = []
        for md_file in self._docs_root.rglob("*.md"):
            if not md_file.is_file():
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
                meta, body = _parse_front_matter(content)
                title = meta.get("title", md_file.stem)
                tags = meta.get("tags", "")
                head_lower = f"{title} {tags}".lower()
                body_lower = body.lower()
                score = 0
                for t in terms:
                    w = _term_weight(t)
                    if t in head_lower:
                        score += w * 2          # 标题/标签命中权重加倍
                    elif t in body_lower:
                        score += w
                if score < 2:                   # 低于一个词元的权重 → 视为噪声
                    continue
                rel_dir = md_file.parent.name   # scope
                hits.append({
                    "scope": rel_dir,
                    "slug": md_file.stem,
                    "title": title,
                    "preview": body[:300].replace("\n", " "),
                    "score": score,
                    "source": "docmemory",
                    "pointer": f"docmemory:{rel_dir}/{md_file.stem}",
                })
            except Exception:
                continue
        hits.sort(key=lambda x: x["score"], reverse=True)
        return hits[:limit]

    # -- 自动落盘钩子（⑮ 灵魂：自动落盘+召回）--

    def on_pre_compress(self, messages: list[dict[str, Any]]) -> str:
        """压缩前落「结论+依据+未完成项」，天然免疫 prune_context 轮删。

        复用腿 B 的门控逻辑（有决策/待办才落）。
        """
        if not messages:
            return ""
        try:
            from agent.session_handoff import (
                _extract_user_request,
                _extract_decisions,
                _extract_pending_tasks,
            )
        except Exception:
            return ""

        try:
            user_request = _extract_user_request(messages)
            decisions = _extract_decisions(messages)
            pending = _extract_pending_tasks(messages)
        except Exception as e:
            logger.debug("docmemory on_pre_compress extract failed: %s", e)
            return ""

        # 门控：纯问答不落盘
        if not decisions and not pending:
            return ""

        title = user_request[:60] or f"会话 {self._session_id or 'unknown'}"
        content_parts = []
        if decisions:
            content_parts.append("## 结论\n")
            for d in decisions[:5]:
                content_parts.append(f"- {d['decision']}")
        if pending:
            content_parts.append("\n## 未完成项\n")
            for p in pending[:5]:
                content_parts.append(f"- {p['task']}")
        content_parts.append(f"\n---\n来源会话: {self._session_id or 'unknown'}")
        content = "\n".join(content_parts)

        self._handle_write({
            "title": title,
            "content": content,
            "scope": "task",
            "tags": "pre-compress,auto",
        })

        # 返回给压缩器的提示（会拼入压缩 summary）
        return f"[文档记忆] 已落盘 {len(decisions)} 结论 / {len(pending)} 待办 → docs/task/{_slugify(title)}.md"

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        """会话结束落一份会话结论文档（与腿 B 自动发射同源）。"""
        if not messages:
            return
        # 复用 on_pre_compress 的落盘逻辑（同样门控）
        self.on_pre_compress(messages)

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        **kwargs,
    ) -> None:
        """会话切换时更新 session_id（后续落盘挂到新会话）。"""
        self._session_id = new_session_id

    def backup_paths(self) -> list[str]:
        """把 ~/.vermes/docs/ 纳入既有备份机制。"""
        if self._docs_root and self._docs_root.is_dir():
            return [str(self._docs_root)]
        return []

    def shutdown(self) -> None:
        pass  # 无资源需清理

    def get_config_schema(self) -> list[dict[str, Any]]:
        # 对齐 ⑨ 隐私硬化：提供独立开关，关掉 docmemory 不影响 RAG。
        # 该开关在 agent_init.py 侧读取 memory.docmemory_enabled 决定是否注册。
        #
        # ⚠️ 本 schema 目前【无 UI 消费者】，仅供自文档化，勿误以为
        # `VERMES memory setup` 会展示它：get_config_schema() 全仓三处调用点
        # 都在 vermes_cli/memory_setup.py（:166 / :264 / :419），且只遍历
        # plugins/memory/ 发现的外部 provider；docmemory 是第一方 provider，
        # 不在该列表中，故既不会被展示、save_config() 也不会被调用。
        # 真正的开关落点在 config.yaml → memory.docmemory_enabled
        # （默认值声明见 vermes_cli/config.py 的 DEFAULT_CONFIG）。
        return [
            {
                "key": "docmemory_enabled",
                "description": "是否启用文档记忆层（自动落盘+召回）；关闭不影响 RAG/外部 KB",
                "default": True,
                "choices": [True, False],
            },
        ]

    def save_config(self, values: dict[str, Any], vermes_home: str) -> None:
        pass  # 无配置
