"""ScholarForge golden-set 离线评测 harness。

不依赖 pytest —— 既能被 ``tests/eval/test_eval_gate.py`` 导入，也能被
``scripts/eval_gate.py`` 在 CI 里直接调用。

设计要点（对齐 P4_EVAL_LOOP_DESIGN.md，并建立在已落地的 ``__verified__`` 信号之上）：

1. **真 handler、假 LLM。** ScholarForge 的 26 个 handler 全部经模块级漏斗
   ``tools._call_llm`` / ``tools.stream_call_llm``。只替换这两个函数，就能离线、
   确定性地驱动**真实 handler**走完整业务链路（resolve_project_id → 质量闸门
   → save_section → section_contents 表 → _with_usage 埋点）。业务代码一行不打桩，
   所以这套评测能抓到真实回归，而不是"测试镜像实现"。
   ``run_quality_gate`` 是纯规则实现、零网络，可安全参与离线评测。

2. **外证判定。** case 通过与否由 ``predicates`` 查数据库裁定，不采信 handler
   返回的自述文案（"已保存"不算数，表里查得到才算数）。

3. **零污染。** ``database.DB_PATH`` 指向临时目录后 ``init_db()``；已确认全仓
   没有任何模块 ``from database import DB_PATH``（只在函数体内引用模块属性），
   因此 setattr 模块属性是可靠隔离点。进程全局的「激活项目」也一并存取还原。

4. **不 fail-open。** 与运行时刻意相反：harness/谓词内部任何异常都记为该 case
   失败。评测语义是「拿不出证据 = 不通过」。是否因此阻断 CI 由上层 eval_gate 决定。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.eval.golden.predicates import PredicateContext, get_predicate  # noqa: E402

DEFAULT_MANIFEST = Path(__file__).with_name("scholarforge.golden.json")

# 未指定 llm_response 时的兜底罐头（足够长，能过 quality_gate 的长度类检查）
_FALLBACK_LLM_RESPONSE = "## 评测占位内容\n\n" + ("这是用于离线评测的确定性占位文本。" * 20)


# ──────────────────────────────────────────────────────────────
# 结果数据结构
# ──────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    predicate: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"predicate": self.predicate, "ok": self.ok, "detail": self.detail}


@dataclass
class CaseResult:
    id: str
    tool: str
    passed: bool
    checks: List[CheckResult] = field(default_factory=list)
    verify_ok: Optional[bool] = None
    verify_detail: str = ""
    error: str = ""
    duration_ms: int = 0
    result_preview: str = ""

    @property
    def failures(self) -> List[str]:
        out = [f"{c.predicate}: {c.detail}" for c in self.checks if not c.ok]
        if self.error:
            out.append(f"error: {self.error}")
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
            "verify_ok": self.verify_ok,
            "verify_detail": self.verify_detail,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "result_preview": self.result_preview,
        }


@dataclass
class RunReport:
    manifest: str
    cases: List[CaseResult] = field(default_factory=list)
    duration_ms: int = 0
    started_at: float = 0.0

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def failed(self) -> int:
        return self.total - self.passed

    @property
    def task_success_rate(self) -> float:
        return (self.passed / self.total) if self.total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest": self.manifest,
            "started_at": self.started_at,
            "duration_ms": self.duration_ms,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "task_success_rate": round(self.task_success_rate, 4),
            "cases": [c.to_dict() for c in self.cases],
        }


# ──────────────────────────────────────────────────────────────
# Harness
# ──────────────────────────────────────────────────────────────

class GoldenHarness:
    """上下文管理器：进入时接管 DB + LLM，退出时原样还原。"""

    def __init__(self, keep_workdir: bool = False) -> None:
        self.keep_workdir = keep_workdir
        self.workdir: Optional[Path] = None
        self.llm_calls: List[Dict[str, Any]] = []
        self._responses: List[str] = [_FALLBACK_LLM_RESPONSE]
        self._entered = False
        self._db = None
        self._tools_mod = None
        self._ap = None
        self._orig: Dict[str, Any] = {}

    # -- lifecycle ------------------------------------------------

    def __enter__(self) -> "GoldenHarness":
        self.workdir = Path(tempfile.mkdtemp(prefix="vermes-golden-"))

        import vermes_cli.scholarforge.database as db
        import vermes_cli.scholarforge.tools as sf_tools
        import vermes_cli.scholarforge.active_project as ap

        self._db, self._tools_mod, self._ap = db, sf_tools, ap

        # 1) DB 隔离
        self._orig["DB_PATH"] = db.DB_PATH
        db.DB_PATH = str(self.workdir / "scholarforge.db")
        db.init_db()

        # 2) LLM 漏斗替换（唯一的打桩点）
        self._orig["_call_llm"] = sf_tools._call_llm
        self._orig["stream_call_llm"] = sf_tools.stream_call_llm
        sf_tools._call_llm = self._fake_call_llm
        sf_tools.stream_call_llm = self._fake_stream_call_llm

        # 3) 进程全局「激活项目」隔离
        self._orig["_active_pid"] = ap._active_pid
        self._orig["_active_by_session"] = dict(ap._active_by_session)
        ap._active_pid = 0
        ap._active_by_session.clear()

        # 4) 注册工具（register 是覆盖写，重复调用安全）
        sf_tools.register_tools()

        self._entered = True
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._tools_mod is not None:
            self._tools_mod._call_llm = self._orig["_call_llm"]
            self._tools_mod.stream_call_llm = self._orig["stream_call_llm"]
        if self._db is not None:
            self._db.DB_PATH = self._orig["DB_PATH"]
        if self._ap is not None:
            self._ap._active_pid = self._orig["_active_pid"]
            self._ap._active_by_session.clear()
            self._ap._active_by_session.update(self._orig["_active_by_session"])
        if self.workdir and self.workdir.exists() and not self.keep_workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)
        self._entered = False

    # -- LLM 替身 --------------------------------------------------

    def set_llm_response(self, response: Any) -> None:
        """设置本 case 的罐头响应。str = 每次调用都返回它；list = 按调用序号取，超出用最后一条。"""
        if isinstance(response, list) and response:
            self._responses = [str(r) for r in response]
        elif isinstance(response, str) and response:
            self._responses = [response]
        else:
            self._responses = [_FALLBACK_LLM_RESPONSE]

    def _next_response(self) -> str:
        idx = len(self.llm_calls) - 1
        if idx < 0:
            idx = 0
        return self._responses[min(idx, len(self._responses) - 1)]

    async def _fake_call_llm(self, *args: Any, **kwargs: Any) -> str:
        prompt = args[0] if args else kwargs.get("prompt", "")
        system = args[1] if len(args) > 1 else kwargs.get("system", "")
        self.llm_calls.append({"mode": "call", "prompt": prompt, "system": system, "kwargs": dict(kwargs)})
        return self._next_response()

    async def _fake_stream_call_llm(self, *args: Any, **kwargs: Any) -> AsyncGenerator[str, None]:
        prompt = args[0] if args else kwargs.get("prompt", "")
        system = args[1] if len(args) > 1 else kwargs.get("system", "")
        self.llm_calls.append({"mode": "stream", "prompt": prompt, "system": system, "kwargs": dict(kwargs)})
        text = self._next_response()
        step = 64
        for i in range(0, len(text), step):
            yield text[i:i + step]

    # -- setup -----------------------------------------------------

    def _setup(self, setup: Dict[str, Any]) -> int:
        """按 case 的 setup 段准备项目状态，返回 project_id（无 project 段则 0）。"""
        db = self._db
        pid = 0
        proj = setup.get("project")
        if proj:
            row = db.create_project(
                proj.get("title", "评测项目"),
                proj.get("paper_type", "本科论文"),
                int(proj.get("target_words", 8000)),
            )
            pid = int(row["id"])
        if setup.get("outline"):
            db.save_outline(pid, setup["outline"])
        for sec in setup.get("sections", []) or []:
            db.save_section_content(pid, sec["key"], sec["content"])
        if setup.get("set_active") and pid:
            self._ap.set_active_project(pid)
        return pid

    # -- 执行 ------------------------------------------------------

    def _invoke(self, entry: Any, args: Dict[str, Any]) -> str:
        handler = entry.handler
        if entry.is_async or inspect.iscoroutinefunction(handler):
            return asyncio.run(handler(args))
        return handler(args)

    def run_case(self, case: Dict[str, Any]) -> CaseResult:
        if not self._entered:
            raise RuntimeError("GoldenHarness must be used as a context manager")

        cid = str(case.get("id", "<unnamed>"))
        tool = str(case.get("tool", ""))
        res = CaseResult(id=cid, tool=tool, passed=False)
        t0 = time.time()

        try:
            from tools.registry import registry

            entry = registry.get_entry(tool)
            if entry is None:
                res.error = f"tool '{tool}' not registered"
                return res

            project_id = self._setup(case.get("setup") or {})
            args = dict(case.get("args") or {})
            if project_id and "project_id" not in args and not case.get("omit_project_id"):
                args["project_id"] = project_id

            self.llm_calls = []
            self.set_llm_response(case.get("llm_response"))

            result = self._invoke(entry, args)
            result = result if isinstance(result, str) else str(result)
            res.result_preview = result[:400]

            ctx = PredicateContext(
                project_id=project_id,
                args=args,
                result=result,
                db=self._db,
                llm_calls=self.llm_calls,
                workdir=self.workdir,
                tool=tool,
            )

            expects = case.get("expect") or []
            if not expects:
                res.error = "case declares no expectations"
                return res

            for exp in expects:
                name = str(exp.get("predicate", ""))
                try:
                    ok, detail = get_predicate(name)(ctx, exp)
                except Exception as e:  # noqa: BLE001 —— 谓词异常判失败，不 fail-open
                    ok, detail = False, f"predicate raised: {e}"
                res.checks.append(CheckResult(name, bool(ok), str(detail)))

            # 生产验证器（verify_fn）作为独立第二信号：默认只记录，
            # case 显式声明 expect_verify 时才参与判定。
            if entry.verify_fn is not None:
                try:
                    v_ok, v_detail = entry.verify_fn(
                        tool, args, result, result.lstrip().startswith("❌")
                    )
                    res.verify_ok, res.verify_detail = bool(v_ok), str(v_detail)
                except Exception as e:  # noqa: BLE001
                    res.verify_ok, res.verify_detail = None, f"verify_fn raised: {e}"

            expect_verify = case.get("expect_verify")
            verify_ok_for_pass = True
            if expect_verify is not None:
                verify_ok_for_pass = (res.verify_ok is bool(expect_verify))
                res.checks.append(
                    CheckResult(
                        "verify_fn",
                        verify_ok_for_pass,
                        "" if verify_ok_for_pass
                        else f"verify_fn={res.verify_ok}, expected {expect_verify} ({res.verify_detail})",
                    )
                )

            res.passed = all(c.ok for c in res.checks)
        except Exception:  # noqa: BLE001
            res.error = traceback.format_exc(limit=6)
            res.passed = False
        finally:
            res.duration_ms = int((time.time() - t0) * 1000)

        return res


# ──────────────────────────────────────────────────────────────
# 顶层入口
# ──────────────────────────────────────────────────────────────

def load_manifest(path: str | Path | None = None) -> Dict[str, Any]:
    p = Path(path) if path else DEFAULT_MANIFEST
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def run_manifest(
    path: str | Path | None = None,
    *,
    only: Optional[List[str]] = None,
    keep_workdir: bool = False,
) -> RunReport:
    """跑完整 manifest，返回聚合报告。``only`` 可按 case id 过滤。"""
    p = Path(path) if path else DEFAULT_MANIFEST
    manifest = load_manifest(p)
    cases = manifest.get("cases", [])
    if only:
        wanted = set(only)
        cases = [c for c in cases if c.get("id") in wanted]

    report = RunReport(manifest=str(p), started_at=time.time())
    t0 = time.time()
    with GoldenHarness(keep_workdir=keep_workdir) as h:
        for case in cases:
            report.cases.append(h.run_case(case))
    report.duration_ms = int((time.time() - t0) * 1000)
    return report
