"""P4-4 T2 可视化大盘：benchmark 历史runs查询 + 触发 dry run。

GET /api/v1/benchmark/runs       — 历史运行列表（含分数、趋势数据）
POST /api/v1/benchmark/run       — 触发 dry-run benchmark（CI 接线验证，无需 LLM）
GET /api/v1/benchmark/tasks      — 任务清单（供前端展示覆盖面）
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse


def register_to(app):
    @app.get('/api/v1/benchmark/runs')
    async def list_benchmark_runs(limit: int = 50, request: Request = None):
        """返回历史 benchmark runs，最新在前。"""
        from vermes_cli.scholarforge.benchmark import load_runs
        runs = load_runs()
        # 最新在前
        runs = list(reversed(runs))[:limit]
        # 精简：每条只取摘要 + results 摘要
        summary = []
        for r in runs:
            summary.append({
                'mode': r.get('mode'),
                'llm_tier': r.get('llm_tier'),
                'timestamp': r.get('timestamp'),
                'summary': r.get('summary', {}),
                'results': [
                    {
                        'task_id': t.get('task_id'),
                        'kind': t.get('kind'),
                        'pass': t.get('pass'),
                        'wired': t.get('wired'),
                        'wall_time_s': t.get('wall_time_s'),
                        'error': t.get('error'),
                    }
                    for t in r.get('results', [])
                ],
            })
        return {'runs': summary, 'total': len(load_runs())}

    @app.get('/api/v1/benchmark/tasks')
    async def list_benchmark_tasks(request: Request = None):
        """返回 benchmark 任务清单（供前端展示覆盖面）。"""
        from vermes_cli.scholarforge.benchmark import TASKS
        tasks = []
        for t in TASKS:
            tasks.append({
                'id': t.id,
                'title': t.title,
                'kind': t.kind,
                'tools': t.tools,
                'llm_required': t.llm_required,
                'expected_artifact': t.expected_artifact,
            })
        return {'tasks': tasks, 'total': len(tasks)}

    @app.post('/api/v1/benchmark/run')
    async def trigger_benchmark_run(
        request: Request,
        mode: str = 'dry',
        llm_tier: str = 'strong',
    ):
        """触发 benchmark（默认 dry 模式，无需 LLM）。"""
        from vermes_cli.scholarforge.benchmark import run_benchmark
        if mode not in ('dry', 'live'):
            raise HTTPException(status_code=400, detail="mode 须为 dry 或 live")
        if llm_tier not in ('weak', 'mid', 'strong'):
            raise HTTPException(status_code=400, detail="llm_tier 须为 weak/mid/strong")
        # live 模式需要宿主 API，端点不支持（需 CLI / agent 内部调用）
        if mode == 'live':
            raise HTTPException(
                status_code=501,
                detail="live 模式需宿主 API 注入，请通过 CLI `vermes benchmark --mode live` 触发",
            )
        try:
            report = run_benchmark(mode='dry', llm_tier=llm_tier)
            return report
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"benchmark 运行失败: {e}")
