"""Golden-set 确定性谓词注册表。

设计约束（对齐 P4_EVAL_LOOP_DESIGN.md §2.3「predicate 类 CI 可离线跑」）：

1. **外证优先**：谓词的判定依据是数据库真实状态（section_contents / outlines /
   tool_usage 表），而不是工具返回的自述文案。工具说「已保存」不算数，
   表里查得到才算数 —— 这正是 P0「写回不落库却返回 ok=1」那类幻影进度的解药。
2. **零 eval / 零 exec**：manifest 是数据不是代码。谓词只能从本注册表按名字取，
   参数是 JSON 标量。禁止把表达式字符串塞进 manifest 求值。
3. **确定性**：不依赖 LLM、不联网、不看时钟。同样输入必得同样结论。
4. **不 fail-open**：谓词内部异常一律判 False 并回传异常文本。
   评测语义是「没有证据 = 不通过」，与运行时 fail-open 的取向刻意相反。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple


@dataclass
class PredicateContext:
    """单个 case 执行完毕后交给谓词的证据包。"""

    project_id: int
    args: Dict[str, Any]
    result: str
    db: Any                                   # vermes_cli.scholarforge.database 模块
    llm_calls: List[Dict[str, Any]] = field(default_factory=list)
    workdir: Path | None = None
    tool: str = ""


PredicateFn = Callable[[PredicateContext, Dict[str, Any]], Tuple[bool, str]]

_REGISTRY: Dict[str, PredicateFn] = {}


def predicate(name: str) -> Callable[[PredicateFn], PredicateFn]:
    def _wrap(fn: PredicateFn) -> PredicateFn:
        if name in _REGISTRY:
            raise ValueError(f"duplicate predicate: {name}")
        _REGISTRY[name] = fn
        return fn

    return _wrap


def get_predicate(name: str) -> PredicateFn:
    """按名取谓词；未知名字直接抛错（manifest 写错必须暴露，不能静默跳过）。"""
    fn = _REGISTRY.get(name)
    if fn is None:
        raise KeyError(
            f"unknown predicate '{name}'. available: {sorted(_REGISTRY)}"
        )
    return fn


def list_predicates() -> List[str]:
    return sorted(_REGISTRY)


# ──────────────────────────────────────────────────────────────
# 落库类谓词（外证：查表）
# ──────────────────────────────────────────────────────────────

@predicate("section_persisted")
def _section_persisted(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    """section_contents 表中存在该 section_key 且内容长度达标。"""
    key = p.get("section_key") or ctx.args.get("section_key") or ctx.args.get("section_type")
    min_chars = int(p.get("min_chars", 1))
    if not ctx.project_id or not key:
        return False, f"missing project_id({ctx.project_id}) or section_key({key})"
    try:
        with ctx.db.get_conn() as conn:
            row = conn.execute(
                "SELECT content FROM section_contents WHERE project_id=? AND section_key=?",
                (ctx.project_id, key),
            ).fetchone()
    except Exception as e:  # noqa: BLE001
        return False, f"query error: {e}"
    if row is None:
        return False, f"no row for (pid={ctx.project_id}, key={key})"
    content = row["content"] or ""
    if len(content) < min_chars:
        return False, f"content too short: {len(content)} < {min_chars}"
    return True, f"{len(content)} chars persisted"


@predicate("section_contains")
def _section_contains(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    """落库内容包含指定子串 —— 证明落库的是「生成的那份」而非空壳/占位。"""
    key = p.get("section_key") or ctx.args.get("section_key") or ctx.args.get("section_type")
    needle = p.get("text", "")
    if not needle:
        return False, "predicate misconfigured: empty 'text'"
    try:
        with ctx.db.get_conn() as conn:
            row = conn.execute(
                "SELECT content FROM section_contents WHERE project_id=? AND section_key=?",
                (ctx.project_id, key),
            ).fetchone()
    except Exception as e:  # noqa: BLE001
        return False, f"query error: {e}"
    if row is None:
        return False, f"no row for (pid={ctx.project_id}, key={key})"
    content = row["content"] or ""
    if needle not in content:
        return False, f"'{needle}' not in persisted content ({len(content)} chars)"
    return True, ""


@predicate("outline_rows_at_least")
def _outline_rows_at_least(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    """outlines 表条目数下限。"""
    n = int(p.get("count", 1))
    try:
        with ctx.db.get_conn() as conn:
            got = conn.execute(
                "SELECT COUNT(*) FROM outlines WHERE project_id=?", (ctx.project_id,)
            ).fetchone()[0]
    except Exception as e:  # noqa: BLE001
        return False, f"query error: {e}"
    if got < n:
        return False, f"outlines rows {got} < {n}"
    return True, f"{got} rows"


@predicate("outline_section_keys")
def _outline_section_keys(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    """outlines 落库的 section_key 必须匹配期望前缀。

    存在的意义：曾出现过「合并 save_outline 时丢字段归一化，落库 key 从
    section_1 退化成 sec_0」的回归 —— 行数对、键名错，只查 COUNT 抓不到。
    """
    prefix = p.get("prefix", "section_")
    try:
        with ctx.db.get_conn() as conn:
            rows = conn.execute(
                "SELECT section_key FROM outlines WHERE project_id=? ORDER BY sort_order",
                (ctx.project_id,),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        return False, f"query error: {e}"
    if not rows:
        return False, "outlines empty"
    keys = [r["section_key"] for r in rows]
    bad = [k for k in keys if not (k or "").startswith(prefix)]
    if bad:
        return False, f"keys not starting with '{prefix}': {bad}"
    return True, f"keys={keys}"


@predicate("literature_rows_at_least")
def _literature_rows_at_least(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    n = int(p.get("count", 1))
    try:
        with ctx.db.get_conn() as conn:
            got = conn.execute(
                "SELECT COUNT(*) FROM literatures WHERE project_id=?", (ctx.project_id,)
            ).fetchone()[0]
    except Exception as e:  # noqa: BLE001
        return False, f"query error: {e}"
    return (got >= n, f"literatures rows={got} (need >= {n})")


@predicate("tool_usage_ok")
def _tool_usage_ok(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    """_with_usage 埋点把本工具记为成功（ok=1）。

    第二路外证：埋点判定依据是「返回串是否以 ❌ 开头」，与 predicate 的
    查表判定相互独立。两者不一致本身就是有价值的信号（静默假成功）。
    """
    expected_ok = bool(p.get("ok", True))
    tool = p.get("tool") or ctx.tool
    try:
        with ctx.db.get_conn() as conn:
            row = conn.execute(
                "SELECT ok FROM tool_usage WHERE tool_name=? ORDER BY id DESC LIMIT 1",
                (tool,),
            ).fetchone()
    except Exception as e:  # noqa: BLE001
        return False, f"query error: {e}"
    if row is None:
        return False, f"no tool_usage row for {tool}"
    got = bool(row["ok"])
    if got != expected_ok:
        return False, f"tool_usage.ok={got}, expected {expected_ok}"
    return True, ""


# ──────────────────────────────────────────────────────────────
# 返回串类谓词（弱证据，只作辅助）
# ──────────────────────────────────────────────────────────────

@predicate("result_not_error")
def _result_not_error(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    """返回串不以 ❌ / 🚫 开头。"""
    text = (ctx.result or "").lstrip()
    for mark in ("❌", "🚫"):
        if text.startswith(mark):
            return False, f"result starts with {mark}: {text[:120]}"
    return True, ""


@predicate("result_is_error")
def _result_is_error(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    """负例 case 专用：期望工具明确报错而不是静默假成功。"""
    text = (ctx.result or "").lstrip()
    if text.startswith("❌") or text.startswith("🚫"):
        return True, ""
    return False, f"expected error marker, got: {text[:120]}"


@predicate("result_contains")
def _result_contains(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    needle = p.get("text", "")
    if not needle:
        return False, "predicate misconfigured: empty 'text'"
    if needle not in (ctx.result or ""):
        return False, f"'{needle}' not in result ({len(ctx.result or '')} chars)"
    return True, ""


@predicate("result_min_chars")
def _result_min_chars(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    n = int(p.get("count", 1))
    got = len(ctx.result or "")
    return (got >= n, f"result {got} chars (need >= {n})")


# ──────────────────────────────────────────────────────────────
# 磁盘落地类谓词（导出物的外证）
# ──────────────────────────────────────────────────────────────

_PATH_RE = re.compile(r"(/[^\s:：，,]+\.(?:md|docx|pdf|tex|bib|json|txt|html))")


def _extract_export_path(result: str) -> str:
    """从工具返回串里提取真实存在的导出文件路径。

    export 类工具返回的是路径而非正文，所以「返回串里有没有正文」证明不了任何事——
    必须落到磁盘上验。这与 compute_verified 的 file_landed 分量同源。
    """
    for cand in reversed(_PATH_RE.findall(result or "")):
        if Path(cand).exists():
            return cand
    return ""


@predicate("exported_file_exists")
def _exported_file_exists(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    min_bytes = int(p.get("min_bytes", 1))
    path = _extract_export_path(ctx.result)
    if not path:
        return False, f"no existing export path found in result: {(ctx.result or '')[:120]}"
    size = Path(path).stat().st_size
    if size < min_bytes:
        return False, f"{path} is {size} bytes < {min_bytes}"
    return True, f"{path} ({size} bytes)"


@predicate("exported_file_contains")
def _exported_file_contains(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    """导出文件的实际内容包含指定文本。

    这是「写了但导不出」断链的守门谓词：只断言 export 不报错是不够的，
    曾经出现过 content 为空仍导出成功、落地一个空壳文件的情况。
    """
    needle = p.get("text", "")
    if not needle:
        return False, "predicate misconfigured: empty 'text'"
    path = _extract_export_path(ctx.result)
    if not path:
        return False, f"no existing export path found in result: {(ctx.result or '')[:120]}"
    try:
        body = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return False, f"read error: {e}"
    if needle not in body:
        return False, f"'{needle}' not in exported file ({len(body)} chars): {path}"
    return True, ""


# ──────────────────────────────────────────────────────────────
# 提示词类谓词（验证不落库的行为：上下文注入）
# ──────────────────────────────────────────────────────────────

@predicate("prompt_contains")
def _prompt_contains(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    """发给 LLM 的 prompt/system 中包含指定文本。

    用于验证「项目上下文 / 已学习风格 确实注入了提示词」这类
    不落库、只体现在调用参数上的行为。
    """
    needle = p.get("text", "")
    if not needle:
        return False, "predicate misconfigured: empty 'text'"
    if not ctx.llm_calls:
        return False, "no LLM call recorded"
    for call in ctx.llm_calls:
        blob = f"{call.get('prompt', '')}\n{call.get('system', '')}"
        if needle in blob:
            return True, ""
    return False, f"'{needle}' not found in {len(ctx.llm_calls)} LLM call(s)"


@predicate("llm_call_count")
def _llm_call_count(ctx: PredicateContext, p: Dict[str, Any]) -> Tuple[bool, str]:
    """LLM 调用次数精确匹配 —— 抓「多打一次 LLM」这类成本回归。"""
    n = int(p.get("count", 1))
    got = len(ctx.llm_calls)
    return (got == n, f"llm calls={got}, expected {n}")
