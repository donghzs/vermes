"""⑭ 请神收尾 T3：POST /api/agents/register-profile 路由测试。

覆盖「登堂」注册的完整链路：
recipe 匹配 → 鉴权读取 → upsert agent_profiles → register a2a_agents
→ 健康检查 → 状态回写。

测试隔离（与既有 botmode 测试同纪律）：
- SessionDB 重定向到 pytest 临时库，绝不污染真实状态库；
- shutil.which 打桩控制健康检查（成功/失败两路）；
- 鉴权 env 用 monkeypatch 控制（设置/清除）。

断言值取自真实 recipe 文件 ``vermes_cli/a2a/recipes/*.yaml``，非记忆：
- codex-acp        → provider acp-codex        / env OPENAI_API_KEY    / caps code,search,refactor
- claude-agent-acp → provider acp-claude-agent / env ANTHROPIC_API_KEY / caps code,search,refactor,writing
"""

import sys

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

from types import SimpleNamespace

import pytest

import vermes_state
import vermes_cli.blueprints.chat as chat_bp

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ─────────────────────────── 测试装配 ───────────────────────────

@pytest.fixture
def reg(tmp_path, monkeypatch):
    """每个用例：干净临时库 + 注册端点 TestClient。"""
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(chat_bp, "SessionDB", lambda: vermes_state.SessionDB(db_path))
    app = FastAPI()
    chat_bp.register_to(app)
    return SimpleNamespace(
        client=TestClient(app), db_path=db_path, monkeypatch=monkeypatch
    )


def _profiles(db_path):
    db = vermes_state.SessionDB(db_path)
    try:
        return db.list_agent_profiles()
    finally:
        db.close()


def _a2a(db_path):
    db = vermes_state.SessionDB(db_path)
    try:
        return db.list_a2a_agents()
    finally:
        db.close()


# ─────────────────────────── 用例 ───────────────────────────

def test_register_codex_acp_success(reg):
    """codex-acp 落库全链路：profile + a2a + 状态回写 success。"""
    reg.monkeypatch.setenv("OPENAI_API_KEY", "sk-test-codex")
    reg.monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")

    r = reg.client.post("/api/agents/register-profile", json={"recipe": "codex-acp"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["status"] == "success"
    assert d["recipe"] == "codex-acp"
    assert d["provider"] == "acp-codex"
    assert d["profile_id"] == "a2a:acp-codex"
    assert d["transport"] == "acp"
    # 细节 2：Zed npx 适配器包，非裸 codex CLI
    assert d["spawn_command"] == ["npx", "@agentclientprotocol/codex-acp@1.8.0"]
    assert d["capabilities"] == ["code", "search", "refactor"]
    assert d["health"]["healthy"] is True

    # agent_profiles 落库
    profs = [p for p in _profiles(reg.db_path) if p["id"] == "a2a:acp-codex"]
    assert len(profs) == 1, [p["id"] for p in _profiles(reg.db_path)]
    p = profs[0]
    assert p["name"] == "codex-acp"
    assert p["provider"] == "acp-codex"
    assert p["transport"] == "acp"
    assert p["transport_ref"] == "npx @agentclientprotocol/codex-acp@1.8.0"
    assert p["capability_tags"] == ["code", "search", "refactor"]

    # a2a_agents 注册
    agents = [a for a in _a2a(reg.db_path) if a["profile_id"] == "a2a:acp-codex"]
    assert len(agents) == 1
    assert agents[0]["name"] == "codex-acp"
    assert agents[0]["transport"] == "acp"
    assert agents[0]["capabilities"] == ["code", "search", "refactor"]


def test_register_claude_agent_acp_success(reg):
    """另一条 recipe（Claude）走同一端点，互不干扰（异构登堂）。"""
    reg.monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-claude")
    reg.monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")

    r = reg.client.post("/api/agents/register-profile", json={"recipe": "claude-agent-acp"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "success"
    assert d["provider"] == "acp-claude-agent"
    assert d["profile_id"] == "a2a:acp-claude-agent"
    assert d["spawn_command"] == ["npx", "@agentclientprotocol/claude-agent-acp@0.73.0"]
    assert d["capabilities"] == ["code", "search", "refactor", "writing"]
    assert len(_profiles(reg.db_path)) == 1


def test_register_missing_auth_returns_need_auth(reg):
    """env 鉴权缺失 → need_auth 短路，且不落库（等前端弹授权框）。"""
    reg.monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    r = reg.client.post("/api/agents/register-profile", json={"recipe": "codex-acp"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["status"] == "need_auth"
    assert d["auth_env"] == "OPENAI_API_KEY"
    # 即便需要鉴权，也把 spawn_command 回给前端（展示"将要接入谁"）
    assert d["spawn_command"] == ["npx", "@agentclientprotocol/codex-acp@1.8.0"]

    assert [p for p in _profiles(reg.db_path) if p["id"] == "a2a:acp-codex"] == []
    assert _a2a(reg.db_path) == []


def test_register_unknown_recipe_404(reg):
    r = reg.client.post("/api/agents/register-profile", json={"recipe": "no-such-agent"})
    assert r.status_code == 404
    assert r.json()["detail"]["ok"] is False


def test_register_empty_recipe_400(reg):
    r = reg.client.post("/api/agents/register-profile", json={"recipe": "   "})
    assert r.status_code == 400
    assert r.json()["detail"]["ok"] is False


def test_register_health_fail_when_command_missing(reg):
    """命令不在 PATH → 状态 fail，但仍落库（修好 PATH 后可重试）。"""
    reg.monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reg.monkeypatch.setattr("shutil.which", lambda name: None)

    r = reg.client.post("/api/agents/register-profile", json={"recipe": "codex-acp"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["status"] == "fail"
    assert d["health"]["healthy"] is False
    assert "not found on PATH" in d["health"]["detail"]

    # 仍落库：注册与健康检查解耦，健康检查只是状态回写
    assert [p for p in _profiles(reg.db_path) if p["id"] == "a2a:acp-codex"]


def test_register_idempotent_upsert(reg):
    """重复登堂同一 agent → upsert，不产生重复行。"""
    reg.monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reg.monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")

    for _ in range(2):
        r = reg.client.post("/api/agents/register-profile", json={"recipe": "codex-acp"})
        assert r.status_code == 200, r.text

    assert len([p for p in _profiles(reg.db_path) if p["id"] == "a2a:acp-codex"]) == 1
    assert len(_a2a(reg.db_path)) == 1


def test_list_recipes_returns_acp_recipes(reg):
    """GET /api/agents/recipes：食谱名单由后端下发（前端据此显示登堂按钮）。"""
    r = reg.client.get("/api/agents/recipes")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    names = {x["name"] for x in d["recipes"]}
    assert {"copilot", "codex-acp", "claude-agent-acp"} <= names, names
    # 只下发 ACP transport 的食谱
    assert all(x["transport"] == "acp" for x in d["recipes"])
    # 每条都带前端要展示的字段
    for x in d["recipes"]:
        assert x["provider"] and x["spawn_command"] and x["description"]


def test_list_recipes_zed_adapter_entry_points(reg):
    """细节 2：Codex/Claude 的 entry_point 必须是 Zed npx 适配器包，非裸 CLI。"""
    r = reg.client.get("/api/agents/recipes")
    assert r.status_code == 200, r.text
    by_name = {x["name"]: x for x in r.json()["recipes"]}

    codex = by_name["codex-acp"]
    assert codex["entry_point"] == "npx"
    assert codex["args"] == ["@agentclientprotocol/codex-acp@1.8.0"]
    assert codex["spawn_command"] == ["npx", "@agentclientprotocol/codex-acp@1.8.0"]
    assert codex["auth_env"] == "OPENAI_API_KEY"

    claude = by_name["claude-agent-acp"]
    assert claude["entry_point"] == "npx"
    assert claude["args"] == ["@agentclientprotocol/claude-agent-acp@0.73.0"]
    assert claude["auth_env"] == "ANTHROPIC_API_KEY"

    # Copilot 原生支持 ACP → 走裸 CLI，不需 npx 适配器
    copilot = by_name["copilot"]
    assert copilot["entry_point"] == "copilot"
    assert copilot["args"] == ["--acp", "--stdio"]
    assert copilot["auth_env"] == "COPILOT_API_KEY"
