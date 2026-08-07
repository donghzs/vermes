"""G4a 反向验证：ScholarForge 的 LLM token 必须真进 tool_usage 表。

家底：ScholarForge 走自有 LLM 通道（_call_llm），此前 _call_llm_request 拿到
data["usage"] 后直接丢弃（黑洞），12+ 重型调用点（write/outline/polish/review/
plagiarism/deaigc...）一个 token 都没进库。修复后 _call_llm 把 usage 累加进
_LLM_USAGE_ACC（ContextVar），_with_usage 在 finally 复用主链路 normalize_usage +
estimate_usage_cost 归一化计价后落 tool_usage。

R5 解药：退回"累加器不生效"旧行为 → token 不落库 → 测试必失败，证测试验真。
"""
import asyncio
import tempfile

import pytest


_FAKE_CONTENT = "这是一段由 LLM 生成的章节内容。"
_FAKE_USAGE = {"prompt_tokens": 1234, "completion_tokens": 567}


def _setup(monkeypatch):
    import vermes_cli.scholarforge.tools as tools
    from vermes_cli.scholarforge import database

    _tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setattr(database, "DB_PATH", _tmp)
    database.init_db()

    # 保留真实 _call_llm 累加逻辑；仅绕过真实凭证与网络 I/O
    monkeypatch.setattr(
        tools, "_resolve_credentials",
        lambda: {
            "provider": "openai", "model": "gpt-4o",
            "base_url": "https://api.openai.com/v1", "api_key": "sk-test",
        },
    )

    async def _fake_request(*a, **kw):
        return _FAKE_CONTENT, _FAKE_USAGE

    monkeypatch.setattr(tools, "_call_llm_request", _fake_request)
    return tools, database


async def _dummy_handler(args):
    import vermes_cli.scholarforge.tools as tools
    return await tools._call_llm("write me a section")


def test_scholarforge_llm_usage_recorded(monkeypatch):
    tools, database = _setup(monkeypatch)
    _wrapped = tools._with_usage("scholarforge_write", _dummy_handler)
    asyncio.run(_wrapped({"dummy": 1}))

    with database.get_conn() as conn:
        rows = conn.execute(
            "SELECT tool_name, input_tokens, output_tokens, estimated_cost_usd, model "
            "FROM tool_usage WHERE tool_name=?", ("scholarforge_write",)
        ).fetchall()
    assert rows, "tool_usage 应有一条 scholarforge_write 记录"
    row = dict(rows[0])
    # 证明原始 token 经 normalize_usage 后仍按原值落库（无 cache 细节时 input=1234）
    assert row["input_tokens"] == 1234, row
    assert row["output_tokens"] == 567, row
    assert row["model"] == "gpt-4o", row
    # 成本应被估算（gpt-4o 有定价 → 正数）；unknown 定价时为 0，但类型必须正确
    assert isinstance(row["estimated_cost_usd"], (int, float)), row
    assert row["estimated_cost_usd"] >= 0, row


def test_r5_no_accumulation_means_no_tokens(monkeypatch):
    """退回旧行为（_accumulate_llm_usage 不生效）→ token 不落库，证测试验真。"""
    tools, database = _setup(monkeypatch)
    # 模拟修复前：usage 被丢弃，累加器从不追加
    monkeypatch.setattr(tools, "_accumulate_llm_usage", lambda *a, **kw: None)

    _wrapped = tools._with_usage("scholarforge_write", _dummy_handler)
    asyncio.run(_wrapped({"dummy": 1}))

    with database.get_conn() as conn:
        rows = conn.execute(
            "SELECT input_tokens, output_tokens FROM tool_usage WHERE tool_name=?",
            ("scholarforge_write",),
        ).fetchall()
    assert rows, "仍应有 tool_usage 记录（仅 token 为空）"
    row = dict(rows[0])
    assert row["input_tokens"] == 0, row
    assert row["output_tokens"] == 0, row
