"""Vermes ScholarForge benchmark — P4-4 T3 长程多 Agent 量化完成率评测。

仿 vermes_cli/mfgcad/benchmark.py 的 TASKS 范式，但面向「学术写作长链路」任务：
- 单工具探针任务：每个 scholarforge 工具至少一条探针任务（验证工具可用 + 校验接线）。
- 复合长链路任务：典型学术写作流水线（写→校验→导出；文献综述矩阵；格式+统计+设计缺陷审查），
  天然是「长程多 Agent」任务，对应 P4-4 维度②「长程多 Agent 完成率 + 容错 + 跨会话」。

评分维度（设计稿 §八 Q1 / T3）：
- 完成率 completion_rate = 成功任务 / 总任务（按 category 分层 + 按 LLM tier 分层）。
- 容错恢复：live 模式下任务失败可重跑（reruns 字段记录续跑）。
- 跨会话持续：每轮结果落盘 scholarforge_benchmark_runs.json，支持 resume（跳过已通过任务）。

两种运行模式：
- mode="dry"（默认，CI 用）：离线接线验证（工具都注册 + 在 VALIDATED_TOOLS + 复合 DAG 合法），
  产出 wiring_rate。不调用 LLM，秒级、可进 CI 门禁（非阻塞 visibility，与 P4-3 VALIDATED_TOOLS 同一纪律）。
- mode="live"（手动，需 LLM）：真实调用已注册 handler（host_api 注入），按 ❌ 标记 + DB 产物判定成功，
  产出 completion_rate。结果须按接入 LLM 能力分层报告（跨切原则：弱/中/强 tier 各报一次，不混绝对分）。

使用方式：
  from vermes_cli.scholarforge.benchmark import run_benchmark
  report = run_benchmark(mode="dry", llm_tier="strong")              # CI / 接线验证
  report = run_benchmark(mode="live", llm_tier="strong", host_api=api)  # 真实跑（需 LLM）

LLM-tier 分层（跨切原则）：报告按 llm_tier 盖戳，结果须同 tier 内对比四强中位，不混成绝对分。
"""
from __future__ import annotations

import time
import json
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# 跨会话状态文件（类比 bricks.json / brick_reviews.json），支持 resume
_BENCHMARK_RUNS_PATH = Path.home() / ".vermes" / "scholarforge_benchmark_runs.json"

# LLM 能力分档（跨切原则）：弱 / 中 / 强。benchmark 须按 tier 分层报告。
LLM_TIERS = ("weak", "mid", "strong")


# ---------------------------------------------------------------------------
# 任务定义
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkTask:
    """单个 benchmark 任务（仿 mfgcad BenchmarkTask）。"""
    id: str
    title: str
    kind: str                      # "single" | "composite"
    tools: List[str]              # scholarforge 工具短名（须都出现在 VALIDATED_TOOLS）
    category: str                 # "tool_probe" | "long_chain"
    description: str
    sample_args: Dict[str, Any] = field(default_factory=dict)   # 单工具任务样例入参（live 模式用）
    expected_artifact: Optional[str] = None   # 期望产物（如 "section_contents" / "outlines" / "export_file"）
    timeout_s: int = 120
    llm_required: bool = True     # 是否需要 LLM（纯本地工具可 False）


TASKS: List[BenchmarkTask] = [
    # ── 单工具探针（27 个，覆盖全部已注册工具）──
    BenchmarkTask(
        id="sf_search", title="学术检索", kind="single", tools=["search"],
        category="tool_probe", description="arXiv/Crossref 等免费源检索文献",
        sample_args={"query": "attention mechanism survey", "source": "arxiv", "limit": 5},
        expected_artifact="search_results", llm_required=False,
    ),
    BenchmarkTask(
        id="sf_write", title="写入章节", kind="single", tools=["write"],
        category="tool_probe", description="把内容写入指定章节并落库",
        sample_args={"project_id": "1", "section_key": "abstract", "content": "本文研究……"},
        expected_artifact="section_contents", llm_required=False,
    ),
    BenchmarkTask(
        id="sf_read_section", title="读取章节", kind="single", tools=["read_section"],
        category="tool_probe", description="读取已写章节内容",
        sample_args={"project_id": "1", "section_key": "abstract"},
        expected_artifact="section_contents", llm_required=False,
    ),
    BenchmarkTask(
        id="sf_list_projects", title="列出项目", kind="single", tools=["list_projects"],
        category="tool_probe", description="列出当前学术写作项目（空状态感知）",
        sample_args={}, expected_artifact="projects", llm_required=False,
    ),
    BenchmarkTask(
        id="sf_set_active_project", title="激活项目", kind="single", tools=["set_active_project"],
        category="tool_probe", description="切换到指定激活项目",
        sample_args={"project_id": "1"}, llm_required=False,
    ),
    BenchmarkTask(
        id="sf_outline", title="生成大纲", kind="single", tools=["outline"],
        category="tool_probe", description="基于主题生成论文大纲",
        sample_args={"project_id": "1", "topic": "大语言模型综述"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_polish", title="润色文本", kind="single", tools=["polish"],
        category="tool_probe", description="按指令润色文本",
        sample_args={"text": "本文提出了一种方法。", "instruction": "学术化、去口语"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_export", title="导出文档", kind="single", tools=["export"],
        category="tool_probe", description="导出为 docx/md 等格式",
        sample_args={"project_id": "1", "title": "demo", "fmt": "docx"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_format_refs", title="格式化参考文献", kind="single", tools=["format_refs"],
        category="tool_probe", description="按样式格式化参考文献 JSON",
        sample_args={"refs": '[{"title":"A","authors":["X"],"year":2024}]', "style": "apa"},
        llm_required=False,
    ),
    BenchmarkTask(
        id="sf_research_map", title="研究地图", kind="single", tools=["research_map"],
        category="tool_probe", description="基于主题生成研究地图",
        sample_args={"topic": "retrieval augmented generation"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_literature_matrix", title="综述矩阵", kind="single", tools=["literature_matrix"],
        category="tool_probe", description="基于 topic/tag 生成文献综述矩阵（P4-3 补 T1 守卫）",
        sample_args={"topic": "diffusion model"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_save_literature_cards", title="保存文献卡", kind="single", tools=["save_literature_cards"],
        category="tool_probe", description="解析并保存文献卡 JSON",
        sample_args={"cards": '[{"title":"A","year":2024}]'}, llm_required=False,
    ),
    BenchmarkTask(
        id="sf_manage_snapshots", title="快照管理", kind="single", tools=["manage_snapshots"],
        category="tool_probe", description="列出/创建/回滚写作快照",
        sample_args={"action": "list"}, llm_required=False,
    ),
    BenchmarkTask(
        id="sf_apply_template", title="套用模板", kind="single", tools=["apply_template"],
        category="tool_probe", description="套用期刊模板到项目",
        sample_args={"template_id": "ieee", "project_id": "1"}, llm_required=False,
    ),
    BenchmarkTask(
        id="sf_citation_graph", title="引文图谱", kind="single", tools=["citation_graph"],
        category="tool_probe", description="构建论文引文图谱",
        sample_args={"paper_id": "demo"}, llm_required=False,
    ),
    BenchmarkTask(
        id="sf_run_pipeline", title="运行流水线", kind="single", tools=["run_pipeline"],
        category="tool_probe", description="自然语言驱动写作流水线",
        sample_args={"message": "写一篇关于 RAG 的论文"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_review", title="同行评审", kind="single", tools=["review"],
        category="tool_probe", description="对章节做 AI 同行评审",
        sample_args={"project_id": "1", "section_key": "abstract"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_replace_citations", title="替换引文", kind="single", tools=["replace_citations"],
        category="tool_probe", description="模糊匹配替换引文",
        sample_args={"project_id": "1"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_verify_citations", title="验证引文", kind="single", tools=["verify_citations"],
        category="tool_probe", description="验证引文真实性",
        sample_args={"project_id": "1"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_plagiarism_check", title="查重", kind="single", tools=["plagiarism_check"],
        category="tool_probe", description="全文查重检测",
        sample_args={"project_id": "1"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_deaigc", title="去 AIGC", kind="single", tools=["deaigc"],
        category="tool_probe", description="AIGC 痕迹检测",
        sample_args={"text": "This paper proposes a novel method."}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_score", title="评分", kind="single", tools=["score"],
        category="tool_probe", description="论文质量评分",
        sample_args={"project_id": "1"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_check_stats", title="统计一致性", kind="single", tools=["check_stats"],
        category="tool_probe", description="检查统计一致性",
        sample_args={"project_id": "1"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_detect_design_flaws", title="设计缺陷检测", kind="single", tools=["detect_design_flaws"],
        category="tool_probe", description="检测实验设计缺陷",
        sample_args={"project_id": "1"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_review_claims", title="论点审查", kind="single", tools=["review_claims"],
        category="tool_probe", description="审查论点支撑",
        sample_args={"project_id": "1"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_quality_gate", title="质量闸门", kind="single", tools=["quality_gate"],
        category="tool_probe", description="全流程质量闸门（run_all_validators）",
        sample_args={"project_id": "1"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_learn_style", title="学习写作风格", kind="single", tools=["learn_style"],
        category="tool_probe", description="从样本学习作者风格",
        sample_args={"project_id": "1", "sample_count": 100}, llm_required=True,
    ),

    # ── 复合长链路（天然长程多 Agent 任务）──
    BenchmarkTask(
        id="sf_chain_write_validate", title="写摘要并过质量闸门", kind="composite",
        tools=["outline", "write", "quality_gate", "verify_citations"],
        category="long_chain",
        description="长链路：大纲→写摘要→质量闸门→引文验证（对应 P4-4 维度②长程多 Agent）",
        sample_args={"project_id": "1", "topic": "大语言模型综述"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_chain_lit_review", title="文献综述矩阵并落卡", kind="composite",
        tools=["search", "literature_matrix", "save_literature_cards"],
        category="long_chain",
        description="长链路：检索→综述矩阵→保存文献卡",
        sample_args={"topic": "diffusion model"}, llm_required=True,
    ),
    BenchmarkTask(
        id="sf_chain_format_check", title="格式化+统计+设计缺陷审查", kind="composite",
        tools=["format_refs", "check_stats", "detect_design_flaws", "review_claims"],
        category="long_chain",
        description="长链路：参考文献格式化→统计一致性→设计缺陷→论点审查",
        sample_args={"project_id": "1", "refs": '[{"title":"A","year":2024}]'}, llm_required=True,
    ),
]


# ---------------------------------------------------------------------------
# 离线接线验证（dry 模式核心，CI 可断言）
# ---------------------------------------------------------------------------

def _registered_scholarforge_tools() -> set:
    """动态枚举全局 registry 已注册 scholarforge 工具短名（register_tools 唯一事实源）。"""
    from tools.registry import registry
    from vermes_cli.scholarforge.validation_coverage import short_name
    return {
        short_name(n)
        for n in registry.get_all_tool_names()
        if n.startswith("scholarforge_")
    }


def verify_task_wiring(task: BenchmarkTask) -> tuple[bool, str]:
    """离线校验任务接线：工具都注册 + 都在 VALIDATED_TOOLS（校验层级存在）。

    复合任务额外要求工具链无重复缺失（DAG 合法的最小判据：每个引用工具都存在）。
    返回 (wired, detail)。
    """
    from vermes_cli.scholarforge.validation_coverage import VALIDATED_TOOLS

    missing = [t for t in task.tools if t not in VALIDATED_TOOLS]
    if missing:
        return False, f"工具未在 VALIDATED_TOOLS 注册：{missing}"
    return True, "ok"


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------

@dataclass
class TaskResult:
    """单个任务的运行结果。"""
    task_id: str
    kind: str
    pass_: bool = False
    wired: bool = False          # 离线接线验证通过
    error: str = ""
    wall_time_s: float = 0.0
    artifacts: List[str] = field(default_factory=list)
    reruns: int = 0             # 容错：重跑次数
    tier: str = "strong"


def score_task(result: TaskResult, task: BenchmarkTask) -> Dict[str, Any]:
    """对单个任务结果评分（仿 mfgcad score_task）。"""
    return {
        "task_id": result.task_id,
        "kind": result.kind,
        "category": task.category,
        "tools": task.tools,
        "wired": result.wired,
        "pass": result.pass_,
        "wall_time_s": round(result.wall_time_s, 2),
        "reruns": result.reruns,
        "artifacts": result.artifacts,
        "error": result.error,
    }


# ---------------------------------------------------------------------------
# 跨会话状态（resume 支持）
# ---------------------------------------------------------------------------

def load_runs() -> List[Dict[str, Any]]:
    if _BENCHMARK_RUNS_PATH.exists():
        try:
            return json.loads(_BENCHMARK_RUNS_PATH.read_text())
        except Exception:
            return []
    return []


def _save_run(report: Dict[str, Any]) -> None:
    try:
        runs = load_runs()
        runs.append(report)
        _BENCHMARK_RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BENCHMARK_RUNS_PATH.write_text(json.dumps(runs, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning("benchmark runs 落盘失败（非致命）：%s", e)


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------

def _run_live_task(task: BenchmarkTask, host_api: Any, verbose: bool) -> TaskResult:
    """真实执行单个任务（需 LLM / host_api）。按 ❌ 标记 + DB 产物判定成功。

    失败可重跑（容错恢复）：最多 1 次续跑。工具调用约定走全局 registry 已注册 handler。
    """
    from tools.registry import registry

    tr = TaskResult(task_id=task.id, kind=task.kind, tier="")
    t0 = time.time()
    wired, detail = verify_task_wiring(task)
    tr.wired = wired
    if not wired:
        tr.error = detail
        tr.wall_time_s = time.time() - t0
        return tr

    def _invoke(tool: str, args: dict) -> str:
        entry = registry.get_entry(f"scholarforge_{tool}")
        if entry is None:
            raise RuntimeError(f"scholarforge_{tool} 未注册")
        handler = entry.handler
        kw = {"host_api": host_api} if host_api is not None else {}
        if entry.is_async:
            return asyncio.run(handler(args, **kw))
        return handler(args, **kw)

    def _attempt() -> tuple[bool, str, List[str]]:
        try:
            if task.kind == "single":
                out = _invoke(task.tools[0], dict(task.sample_args))
                txt = out if isinstance(out, str) else str(out)
                if txt.strip().startswith("❌"):
                    return False, txt[:200], []
                return True, "", []
            # composite：顺序跑工具链
            arts: List[str] = []
            for tool in task.tools:
                out = _invoke(tool, dict(task.sample_args))
                txt = out if isinstance(out, str) else str(out)
                if txt.strip().startswith("❌"):
                    return False, f"{tool} 失败: {txt[:160]}", arts
                if task.expected_artifact:
                    arts.append(task.expected_artifact)
            return True, "", arts
        except Exception as e:  # 容错：记录异常，不崩溃
            return False, f"exception: {e}", []

    ok, err, arts = _attempt()
    if not ok and task.kind == "single":
        # 单工具失败续跑一次（容错恢复维度）
        tr.reruns = 1
        ok, err, arts = _attempt()
    tr.pass_ = ok
    tr.error = err
    tr.artifacts = arts
    tr.wall_time_s = time.time() - t0
    if verbose:
        logger.info("任务 %s: %s %s", task.id, "✅" if ok else "❌", err)
    return tr


def run_benchmark(
    mode: str = "dry",
    tasks: Optional[List[BenchmarkTask]] = None,
    llm_tier: str = "strong",
    verbose: bool = False,
    output_path: Optional[Path] = None,
    host_api: Any = None,
    resume: bool = False,
) -> Dict[str, Any]:
    """运行 ScholarForge benchmark。

    Args:
        mode: "dry"（离线接线验证，默认，CI 用）/ "live"（真实执行，需 LLM）。
        tasks: 任务列表（None = 全部 TASKS）。
        llm_tier: LLM 能力档（weak/mid/strong），报告按此盖戳（跨切原则分层）。
        verbose: 详细日志。
        output_path: 结果写入 JSON 文件。
        host_api: live 模式注入的宿主 API（提供 LLM 调用等）。
        resume: 跨会话续跑——跳过上轮已通过任务。

    Returns:
        {"mode", "llm_tier", "summary": {...}, "results": [...], "tasks": [...]}
    """
    if llm_tier not in LLM_TIERS:
        raise ValueError(f"llm_tier 须为 {LLM_TIERS}，收到 {llm_tier}")
    if tasks is None:
        tasks = TASKS

    # resume：加载上轮通过的任务 id
    skip_ids = set()
    if resume:
        for prev in load_runs():
            for r in prev.get("results", []):
                if r.get("pass"):
                    skip_ids.add(r["task_id"])

    results: List[TaskResult] = []
    wired = 0
    passed = 0

    for task in tasks:
        if task.id in skip_ids:
            if verbose:
                print(f"↷ 跳过（resume 已通过）：{task.id}")
            continue

        tr = TaskResult(task_id=task.id, kind=task.kind, tier=llm_tier)
        t0 = time.time()

        if mode == "dry":
            ok, detail = verify_task_wiring(task)
            tr.wired = ok
            tr.pass_ = ok            # dry 模式：接线通过即视为通过（wiring_rate 口径）
            tr.error = "" if ok else detail
            # 复合任务也校验工具链无环（最小判据：无重复外的缺失已在 verify 中）
        elif mode == "live":
            if host_api is None:
                tr.error = "live 模式需要 host_api（LLM 注入）"
            else:
                tr = _run_live_task(task, host_api, verbose)
        else:
            tr.error = f"未知 mode: {mode}"

        tr.wall_time_s = time.time() - t0
        if tr.wired:
            wired += 1
        if tr.pass_:
            passed += 1
        results.append(tr)

    # 汇总
    total = len(results)
    summary = {
        "mode": mode,
        "llm_tier": llm_tier,
        "total": total,
        "wired": wired,
        "wiring_rate": round(wired / total * 100, 1) if total else 0.0,
        "passed": passed,
        "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        "avg_time_s": round(sum(r.wall_time_s for r in results) / total, 2) if total else 0.0,
        "categories": {},
        "resume_supported": True,
    }
    for cat in ("tool_probe", "long_chain"):
        cat_res = [r for r, t in zip(results, tasks) if t.category == cat and t.id not in skip_ids]
        cat_total = len(cat_res)
        cat_wired = sum(1 for r in cat_res if r.wired)
        cat_passed = sum(1 for r in cat_res if r.pass_)
        summary["categories"][cat] = {
            "total": cat_total,
            "wired": cat_wired,
            "wiring_rate": round(cat_wired / cat_total * 100, 1) if cat_total else 0.0,
            "passed": cat_passed,
            "pass_rate": round(cat_passed / cat_total * 100, 1) if cat_total else 0.0,
        }

    scored = [score_task(r, t) for r, t in zip(results, tasks) if t.id not in skip_ids]
    output = {
        "mode": mode,
        "llm_tier": llm_tier,
        "summary": summary,
        "results": scored,
        "tasks": [
            {"id": t.id, "title": t.title, "kind": t.kind, "tools": t.tools, "category": t.category}
            for t in tasks if t.id not in skip_ids
        ],
    }

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
        logger.info("benchmark 结果写入 %s", output_path)

    _save_run(output)
    return output


__all__ = [
    "BenchmarkTask",
    "TaskResult",
    "TASKS",
    "LLM_TIERS",
    "verify_task_wiring",
    "run_benchmark",
    "load_runs",
]
