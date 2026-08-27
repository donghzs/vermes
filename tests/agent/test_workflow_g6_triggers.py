"""G6 — 工作流触发器（cron + webhook）接入测试。

覆盖：
  A. cron 数据层：create_job / cronjob 工具对 workflow 字段的归一化、互斥校验、透传。
  B. _run_workflow_job 单元：调 run_workflow_template_sync 并映射结果 / 异常降级（反向承重墙）。
  C. run_job 集成：job 带 workflow 时走工作流分支（旁路单 prompt 路径），结果并入交付链路。
  D. webhook：run_workflow payload → 202 并异步跑工作流；无签名 → 401。

纪律（§A2 测试纪律）：每个核心行为配反向承重墙——掏空/移除实现，对应断言必红。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cron_env(tmp_path, monkeypatch):
    """隔离 VERMES_HOME 并重载 cron/tools 模块，避免真实 jobs.json 被踩。"""
    home = tmp_path / ".vermes"
    home.mkdir()
    (home / "scripts").mkdir()
    (home / "cron").mkdir()
    monkeypatch.setenv("VERMES_HOME", str(home))

    import importlib

    import vermes_constants
    importlib.reload(vermes_constants)
    import cron.jobs
    importlib.reload(cron.jobs)
    import cron.scheduler
    importlib.reload(cron.scheduler)
    import tools.cronjob_tools
    importlib.reload(tools.cronjob_tools)
    return home


def _fake_run_workflow(name, parent_agent, session_id, user_message=None, version=None,
                       step_pool=None, concurrent=False):
    """被测试替换用的伪 run_workflow_template_sync；默认返回成功摘要。"""
    _fake_run_workflow.calls.append({
        "name": name, "parent_agent": parent_agent, "session_id": session_id,
        "user_message": user_message, "version": version, "step_pool": step_pool,
    })
    return {"summary": "wf done", "final_response": "wf done", "deadlocked": False}


# ---------------------------------------------------------------------------
# A. cron 数据层：workflow 字段归一化 / 互斥 / 透传
# ---------------------------------------------------------------------------

class TestCronWorkflowField:
    def test_create_job_stores_workflow(self, cron_env):
        from cron.jobs import create_job, get_job
        job = create_job(prompt="x", schedule="every 1h", workflow="mytpl", deliver="local")
        assert get_job(job["id"])["workflow"] == "mytpl"

    def test_create_job_normalizes_whitespace_to_none(self, cron_env):
        from cron.jobs import create_job, get_job
        job = create_job(prompt="x", schedule="every 1h", workflow="   ", deliver="local")
        assert get_job(job["id"])["workflow"] is None

    def test_create_job_no_workflow_field_is_none(self, cron_env):
        """反向墙：不带 workflow 时字段应为 None（不是空串/缺字段）。"""
        from cron.jobs import create_job, get_job
        job = create_job(prompt="x", schedule="every 1h", deliver="local")
        assert "workflow" in job
        assert job["workflow"] is None

    def test_create_job_workflow_conflicts_no_agent(self, cron_env):
        """反向墙：workflow 与 no_agent 互斥，必抛 ValueError。
        若移除互斥校验，create_job 会成功 → 此测试必红。"""
        from cron.jobs import create_job
        script = cron_env / "scripts" / "w.sh"
        script.write_text("echo hi\n")
        with pytest.raises(ValueError, match="mutually exclusive"):
            create_job(
                prompt=None, schedule="every 1h",
                script="w.sh", no_agent=True, workflow="mytpl", deliver="local",
            )

    def test_cronjob_tool_create_accepts_workflow_without_prompt(self, cron_env):
        """反向墙：带 workflow 时不应再强制要求 prompt/skills。
        若移除 `and not workflow` 放宽，此创建会因缺 prompt 失败 → 必红。"""
        from tools.cronjob_tools import cronjob
        result = json.loads(cronjob(
            action="create", schedule="every 1h", workflow="mytpl", deliver="local",
        ))
        assert result.get("success") is True
        assert result["job"]["workflow"] == "mytpl"

    def test_cronjob_tool_workflow_conflicts_no_agent(self, cron_env):
        from tools.cronjob_tools import cronjob
        script = cron_env / "scripts" / "w.sh"
        script.write_text("echo hi\n")
        result = json.loads(cronjob(
            action="create", schedule="every 1h", script="w.sh",
            no_agent=True, workflow="mytpl", deliver="local",
        ))
        assert result.get("success") is False
        assert "mutually exclusive" in result.get("error", "")

    def test_cronjob_tool_update_clears_workflow(self, cron_env):
        from tools.cronjob_tools import cronjob
        created = json.loads(cronjob(
            action="create", schedule="every 1h", workflow="mytpl", deliver="local",
        ))
        job_id = created["job_id"]
        cleared = json.loads(cronjob(action="update", job_id=job_id, workflow=""))
        assert cleared["success"] is True
        assert cleared["job"].get("workflow") is None


# ---------------------------------------------------------------------------
# B. _run_workflow_job 单元（反向承重墙）
# ---------------------------------------------------------------------------

class TestRunWorkflowJobUnit:
    def test_maps_runner_result_into_response_shape(self, cron_env):
        """反向墙：若 _run_workflow_job 体被掏空（不再调 run_workflow_template_sync），
        返回 {} 而非预期 dict → 必红。"""
        from cron.scheduler import _run_workflow_job

        fake_agent = MagicMock()
        with patch("agent.workflow_runtime.run_workflow_template_sync",
                   return_value={"summary": "wf done", "final_response": "wf done",
                                  "deadlocked": False}) as m:
            result = _run_workflow_job(
                {"id": "j1", "workflow": "mytpl"}, fake_agent, "sid", "prompt"
            )
        assert m.called
        assert result == {
            "final_response": "wf done",
            "workflow_completed": True,
            "workflow_deadlocked": False,
        }

    def test_runner_exception_is_caught_and_reported(self, cron_env):
        """反向墙：若移除 try/except，异常会直接抛出而非返回 failed dict → 必红。"""
        from cron.scheduler import _run_workflow_job

        fake_agent = MagicMock()
        with patch("agent.workflow_runtime.run_workflow_template_sync",
                   side_effect=ValueError("boom")):
            result = _run_workflow_job(
                {"id": "j2", "workflow": "mytpl"}, fake_agent, "sid", "prompt"
            )
        assert result.get("failed") is True
        assert "boom" in result.get("error", "")

    def test_deadlock_flag_propagates(self, cron_env):
        from cron.scheduler import _run_workflow_job

        fake_agent = MagicMock()
        with patch("agent.workflow_runtime.run_workflow_template_sync",
                   return_value={"summary": "s", "final_response": "s", "deadlocked": True}):
            result = _run_workflow_job(
                {"id": "j3", "workflow": "mytpl"}, fake_agent, "sid", "prompt"
            )
        assert result["workflow_deadlocked"] is True


# ---------------------------------------------------------------------------
# C. run_job 集成：workflow 分支被实际走到（旁路单 prompt 路径）
# ---------------------------------------------------------------------------

class TestRunJobWorkflowBranch:
    def test_workflow_branch_invoked_and_single_prompt_skipped(self, tmp_path):
        """反向墙：若 run_job 移除 `if job.get("workflow")` 分支，
        run_workflow_template_sync 不会被调用、agent.run_conversation 会被调用 → 双红。"""
        from cron.scheduler import run_job

        job = {"id": "wf-job", "name": "wf", "prompt": "", "workflow": "mytpl", "deliver": "local"}
        fake_db = MagicMock()

        _fake_run_workflow.calls = []
        with patch("cron.scheduler._vermes_home", tmp_path), \
             patch("cron.scheduler._resolve_origin", return_value=None), \
             patch("dotenv.load_dotenv"), \
             patch("vermes_state.SessionDB", return_value=fake_db), \
             patch("vermes_cli.runtime_provider.resolve_runtime_provider",
                   return_value={"api_key": "k", "base_url": "https://example.invalid/v1",
                                  "provider": "openrouter", "api_mode": "chat_completions"}), \
             patch("run_agent.AIAgent") as mock_agent_cls, \
             patch("agent.workflow_runtime.run_workflow_template_sync", _fake_run_workflow):
            mock_agent = MagicMock()
            mock_agent.run_conversation.return_value = {"final_response": "SHOULD_NOT_HAPPEN"}
            mock_agent_cls.return_value = mock_agent

            success, output, final_response, error = run_job(job)

        assert success is True
        assert error is None
        assert final_response == "wf done"
        # 工作流运行器被调用，且单步 prompt 路径没有被走
        assert _fake_run_workflow.calls, "run_workflow_template_sync was not called"
        assert _fake_run_workflow.calls[0]["name"] == "mytpl"
        mock_agent.run_conversation.assert_not_called()


# ---------------------------------------------------------------------------
# D. webhook：run_workflow 分支
# ---------------------------------------------------------------------------

def _make_webhook_adapter(routes):
    from gateway.config import PlatformConfig
    from gateway.platforms.webhook import WebhookAdapter

    config = PlatformConfig(enabled=True, extra={"host": "0.0.0.0", "port": 0, "routes": routes})
    return WebhookAdapter(config)


def _make_app(adapter):
    from aiohttp import web

    app = web.Application()
    app.router.add_post("/webhooks/{route_name}", adapter._handle_webhook)
    return app


class TestWebhookRunWorkflow:
    @pytest.mark.asyncio
    async def test_run_workflow_payload_returns_202_and_runs(self):
        from gateway.platforms.webhook import _INSECURE_NO_AUTH

        routes = {
            "wf-route": {
                "secret": _INSECURE_NO_AUTH,
                "deliver": "log",
            }
        }
        adapter = _make_webhook_adapter(routes)

        calls: list = []
        fake_agent = MagicMock()

        def fake_run(name, parent_agent, session_id, user_message=None, version=None,
                     step_pool=None, concurrent=False):
            calls.append(name)
            return {"summary": "wf ok", "final_response": "wf ok", "deadlocked": False}

        async def fake_deliver(text, delivery):
            return MagicMock(success=True)

        adapter._direct_deliver = fake_deliver

        body = json.dumps({
            "type": "run_workflow",
            "workflow": "mytpl",
            "event_type": "run_workflow",
        }).encode()

        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        with patch("agent.workflow_runtime.build_agent", return_value=fake_agent), \
             patch("agent.workflow_runtime.run_workflow_template_sync", fake_run):
            app = _make_app(adapter)
            async with TestClient(TestServer(app)) as cli:
                resp = await cli.post(
                    "/webhooks/wf-route",
                    data=body,
                    headers={"Content-Type": "application/json",
                             "X-Request-ID": "wf-deliv-1"},
                )
                assert resp.status == 202
                data = await resp.json()
                assert data["status"] == "accepted"
                assert data["workflow"] == "mytpl"

            # 让后台任务跑完
            await __import__("asyncio").sleep(0.05)

        assert calls == ["mytpl"], "run_workflow_template_sync was not invoked"

    @pytest.mark.asyncio
    async def test_run_workflow_missing_signature_rejected(self):
        """反向墙：run_workflow 仍须过鉴权 —— 无签名（使用真实 secret）返回 401。"""
        from gateway.platforms.webhook import WebhookAdapter

        secret = "wf-real-secret"
        routes = {"wf-route": {"secret": secret, "deliver": "log"}}
        adapter = _make_webhook_adapter(routes)

        body = json.dumps({"type": "run_workflow", "workflow": "mytpl"}).encode()

        from aiohttp.test_utils import TestClient, TestServer

        app = _make_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/webhooks/wf-route",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            assert resp.status == 401
