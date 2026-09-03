# -*- coding: utf-8 -*-
"""grounded_citation_auto — 自动溯源观察者 hook（② Grounded Citations 注③）。

在每轮 LLM 产出最终响应后，经 ``post_llm_call`` 观察者 hook 自动抽取事实性
主张并调用 scholarforge 通用溯源层（``ground_claims``）核验，结果写入日志/可观测。

关键约束（董董 2026-09-03 审计锁定）：
- ``post_llm_call`` 是**观察者 hook**（vermes_cli/plugins.py:157 注释：
  "Observers only: return values are ignored"；且 _run_post_llm_hooks /
  turn_finalizer 调用方均不消费其返回值）。因此本 hook **仅日志落点**，
  不回写响应、不写入会话记忆——B（写记忆）/C（注入响应末尾）在该 hook 内
  **技术上不可行**（C 需改 transform_llm_output，B 需走 memory provider 写入路径）。
- 默认关闭：受 config ``grounded_citation.auto`` 控制。开启后每轮触发检索+LLM
  精排，属增强能力非核心链路，故默认 off（避免无差别吃掉 LLM/检索预算、不误伤单聊）。
- fail-open：invoke_hook 对每条 callback 包 try/except；本 hook 内部再包一层，
  任何异常只记 warning，绝不波及主链路 / 已算出的响应。

执行上下文：``finalize_turn`` 为同步函数，但 agent 主体跑在 asyncio 内，
调用时已存在 running loop。故 on_post_llm_call 检测到 running loop 时，
用 daemon 线程跑异步溯源（fire-and-forget，响应不阻塞）；无 running loop 时
直接 asyncio.run。
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
from typing import Any, List

logger = logging.getLogger("vermes.grounded_citation_auto")

# 每轮最多溯源的主张条数（避免超长响应触爆检索预算）
_MAX_CLAIMS = 8

# 句末切分（中英文句号/问号/感叹号/换行）
_SENT_SPLIT = re.compile(r"(?<=[。.!?！？\n])")


def split_claims(text: str, max_claims: int = _MAX_CLAIMS) -> List[str]:
    """从自由文本响应抽取候选事实性主张（零依赖启发式，fail-open）。

    过滤：问句、过短碎片（<12 字）、纯代码/标题/列表行、无字母数字的标点行。
    返回前 max_claims 条。精确 claim 抽取可后续接 LLM（注③ 增强，非 P1）。
    """
    if not text:
        return []
    parts = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
    out: List[str] = []
    for c in parts:
        if c.rstrip().endswith("?"):
            continue  # 问句非主张
        if c.startswith((">", "```", "#", "- ", "* ", "1.", "```")):
            continue  # 代码/标题/列表行
        if len(c) < 12:
            continue  # 碎片
        if not re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", c):
            continue  # 纯标点
        out.append(c)
        if len(out) >= max_claims:
            break
    return out


def _auto_ground_enabled() -> bool:
    """读取 config grounded_citation.auto，默认 False（关闭）。"""
    try:
        from vermes_cli.config import cfg_get, load_config
        cfg = load_config()
        return bool(cfg_get(cfg, "grounded_citation", "auto", default=False))
    except Exception as exc:  # 配置读取失败 → 安全默认关闭
        logger.debug("[GroundedCitations] config read failed, default off: %s", exc)
        return False


async def _run_auto_ground(assistant_response: str) -> None:
    """观察者逻辑：抽取主张 → ground_claims → 日志。fail-open。"""
    claims = split_claims(assistant_response)
    if not claims:
        logger.debug("[GroundedCitations] no candidate claims extracted; skip")
        return
    try:
        from vermes_cli.scholarforge.grounded_citation import ground_claims
        # 注④：真实迁移 scholarforge 通用溯源层到 agent/ 时，须连带
        # vermes_cli.scholarforge.tools._call_llm 一起迁（当前自动溯源复用其
        # 私有 LLM 调用，与 grounded_citation_tool 同路径）。
        from vermes_cli.scholarforge.tools import _call_llm, ANALYSIS_MODEL

        async def _llm(prompt: str, **kw: Any) -> str:
            return await _call_llm(
                prompt,
                temperature=kw.get("temperature", 0.2),
                model=kw.get("model") or ANALYSIS_MODEL,
            )

        results = await ground_claims(claims, llm_call_fn=_llm)
        supported = sum(1 for r in results if r.get("verdict") == "supported")
        logger.info(
            "[GroundedCitations] auto-traced %d claim(s): %d supported / %d unsupported",
            len(results), supported, len(results) - supported,
        )
        for r in results:
            logger.debug("[GroundedCitations] %s | %s", r.get("verdict"), r.get("claim"))
    except Exception as exc:
        logger.warning("[GroundedCitations] auto-ground failed (ignored): %s", exc)


def _run_auto_ground_sync(assistant_response: str) -> None:
    """在独立线程里跑 asyncio.run（仅当处于 running loop 时调用）。"""
    try:
        asyncio.run(_run_auto_ground(assistant_response))
    except Exception as exc:
        logger.warning("[GroundedCitations] thread run failed (ignored): %s", exc)


def on_post_llm_call(*, assistant_response: str = "", **_: Any) -> None:
    """post_llm_call 观察者 hook 入口（返回值被丢弃，仅日志侧溯源）。

    配置关闭或无可溯源主张时直接 no-op。配置开启 + 有主张时：
    - 处于 running loop（agent 异步主体）→ daemon 线程 fire-and-forget；
    - 无 running loop（同步 CLI 路径）→ 直接 asyncio.run。
    """
    if not assistant_response:
        return
    if not _auto_ground_enabled():
        return
    try:
        try:
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False
        if in_loop:
            threading.Thread(
                target=_run_auto_ground_sync,
                args=(assistant_response,),
                daemon=True,
                name="vermes-gc-auto",
            ).start()
        else:
            _run_auto_ground_sync(assistant_response)
    except Exception as exc:
        logger.warning("[GroundedCitations] hook dispatch error (ignored): %s", exc)


def register(ctx) -> None:
    """Plugin entry point — register the post_llm_call observer hook."""
    ctx.register_hook("post_llm_call", on_post_llm_call)
