"""
mfgcad engine — programmatic entry point (subprocess bridge target).

Vermes' ``mfg_text_to_cad`` tool shells out to THIS script with the MAC
engine's own isolated venv (build123d / cadquery-ocp / trimesh / langgraph /
aider / cadpy).  The heavy native deps are intentionally NOT installed in
Vermes' main venv (numpy pin conflict — see MAC requirements.txt), so the
engine runs as its own process.

Contract:
  * ALL progress / debug output goes to STDERR.
  * The FINAL line on STDOUT is a single JSON object describing the result:
        {
          "ok":          bool,
          "error_type":  "NONE" | "FATAL" | "DIMENSION" | ...,
          "step_path":   str | null,
          "stl_path":    str | null,
          "volume_mm3":  float | null,
          "qa":          {"passed": int, "failed": int, "issues": [str]},
          "iterations":  int,
          "message":     str
        }
  * Exit code 0 = JSON emitted (caller still checks ``ok``); non-zero = crash.

Usage:
  python run_mac.py --request "做一个笔筒..." --output-dir /tmp/mfg_out \\
                   [--workflow-id original|aider] [--max-retries N]

Env (read by the engine's own _llm_client):
  DASHSCOPE_API_KEY   API key for the OpenAI-compatible endpoint in config.py
                      (the POC points this at DeepSeek; name is legacy).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import traceback
from pathlib import Path

# Make ``multi_agent_cad`` importable regardless of CWD.
_ENGINE_DIR = Path(__file__).resolve().parent
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))


def _compute_volume(step_path: str | None) -> float | None:
    """Return the solid volume (mm^3) of a STEP file via build123d/OCC.

    Returns None if the file is missing or build123d is unavailable.
    """
    if not step_path or not Path(step_path).is_file():
        return None
    try:
        import warnings
        warnings.filterwarnings("ignore")
        from build123d import Compound, import_step
        res = import_step(step_path)
        shape = Compound(res) if isinstance(res, list) else res
        return float(shape.volume)
    except Exception as exc:  # pragma: no cover - diagnostic only
        print(f"[run_mac] volume compute failed: {exc}", file=sys.stderr)
        return None


def _qa_summary(qa) -> dict:
    """Extract a small serializable summary from a QA report object."""
    if qa is None:
        return {"passed": 0, "failed": 0, "issues": []}
    out = {"passed": 0, "failed": 0, "issues": []}
    try:
        out["passed"] = int(getattr(qa, "passed_count", 0) or 0)
        out["failed"] = int(getattr(qa, "failed_count", 0) or 0)
        details = getattr(qa, "error_details", None) or []
        out["issues"] = [str(d) for d in details[:8]]
    except Exception:
        pass
    return out


def _run(request: str, output_dir: Path, workflow_id: str, max_retries: int) -> dict:
    # Import lazily so a missing key / bad config fails with a clean JSON
    # rather than a hard import-time crash.
    from multi_agent_cad.graph import build_graph, get_default_initial_state
    from multi_agent_cad.config import MAX_RETRIES as _CFG_MAX_RETRIES
    from multi_agent_cad import nodes as _nodes

    # ── Fix: isolate the planner/architecture cache PER REQUEST ──────────
    # MAC's nodes.py caches cad_brief.json / architect_plan.json under a
    # FIXED filename in a SHARED pipeline_cache dir. Without isolation, a
    # second run with a *different* request reuses the first run's cached
    # brief/plan and silently generates the wrong part. Key the cache by a
    # hash of the request so identical requests still reuse, but different
    # requests never collide.
    import hashlib
    _req_hash = hashlib.sha1(request.encode("utf-8")).hexdigest()[:16]
    _nodes._CACHE_DIR = (_ENGINE_DIR / "pipeline_cache" / _req_hash)

    # Output files land in Path.cwd() inside the engine nodes, so run from
    # the requested output dir.
    output_dir.mkdir(parents=True, exist_ok=True)
    # ── Fix: purge stale generated artifacts from a previous run in the
    # SAME output dir (e.g. re-running a session with a new request), so
    # the deterministic coder regenerates instead of reusing an old STEP.
    for _pat in ("temp_output_*.step", "temp_output_*.stl", "temp_design_*.py",
                 "temp_measurements_*.json"):
        for _f in output_dir.glob(_pat):
            try:
                _f.unlink()
            except OSError:
                pass
    os.chdir(output_dir)

    # Build initial state and override the user request (config.USER_REQUEST
    # is only a fallback; the bridge always supplies an explicit request).
    state = dict(get_default_initial_state(workflow_id=workflow_id))
    state["user_request"] = request
    if max_retries and max_retries > 0:
        state["max_iterations"] = max_retries

    app = build_graph()
    final = app.invoke(state, {"recursion_limit": 50})

    step_path = final.get("current_step_path")
    stl_path = final.get("current_stl_path")
    error_type = final.get("error_type")
    error_str = str(error_type.value if hasattr(error_type, "value") else error_type) \
        if error_type is not None else "NONE"

    ok = error_str in ("NONE", "None", "none", "")
    volume = _compute_volume(step_path)
    qa = _qa_summary(final.get("qa_report"))

    message = (
        "✅ 建模成功" if ok else f"❌ 建模失败（{error_str}）"
    )
    if step_path:
        message += f"；STEP: {step_path}"
    if volume is not None:
        message += f"；体积: {volume:.2f} mm³（{volume/1000:.3f} cm³）"

    return {
        "ok": ok,
        "error_type": error_str,
        "step_path": step_path,
        "stl_path": stl_path,
        "volume_mm3": volume,
        "qa": qa,
        "iterations": int(final.get("iteration_count", 0) or 0),
        "message": message,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mfgcad MAC engine runner")
    parser.add_argument("--request", required=True, help="Natural-language CAD request")
    parser.add_argument("--output-dir", required=True, help="Directory for STEP/STL output")
    parser.add_argument("--workflow-id", default="original",
                        choices=["original", "aider"])
    parser.add_argument("--max-retries", type=int, default=0,
                        help="Outer retry budget (0 = use config default)")
    args = parser.parse_args(argv)

    # Redirect ALL engine stdout chatter to stderr so STDOUT stays pure JSON.
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = _run(
                request=args.request,
                output_dir=Path(args.output_dir),
                workflow_id=args.workflow_id,
                max_retries=args.max_retries,
            )
    except Exception as exc:
        result = {
            "ok": False,
            "error_type": "CRASH",
            "step_path": None,
            "stl_path": None,
            "volume_mm3": None,
            "qa": {"passed": 0, "failed": 0, "issues": [str(exc)]},
            "iterations": 0,
            "message": f"❌ 引擎崩溃: {type(exc).__name__}: {exc}",
        }
        traceback.print_exc(file=sys.stderr)

    # Emit the single JSON result line on the REAL stdout.
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
