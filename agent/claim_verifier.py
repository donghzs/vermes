"""操作链验证器（Phase 2.2 从 conversation_loop + run_agent 抽出）。

职责单一：检测 AI 回复中「声称执行了操作」的文本，并据本回合真实工具调用
证据判定是否拦截（凭空声称 / 全失败仍称成功 → 拒绝）。

为什么抽出来：
- 原逻辑散落在 ``run_agent.AIAgent``（patterns + detect 静态方法）与
  ``agent.conversation_loop._apply_operator_claim_verifier``（判定 + 拦截），
  跨文件、紧耦合于 AIAgent，难以独立测试。
- 抽到本模块后：``detect_operation_claims`` 是纯函数，可脱离 agent 直接验证；
  ``apply_operator_claim_verifier`` 仅经 ``agent`` 的三个 seam 交互——
  ``_detect_operation_claims`` / ``_operator_claim_verifier_enabled`` /
  ``_operator_claim_rejection_count``——可在不拉起整个 AIAgent 的前提下单测。

向后兼容：``run_agent.AIAgent._detect_operation_claims`` 与
``agent.conversation_loop._apply_operator_claim_verifier`` 仍可被 import
（二者改为委托到本模块），既有调用方与测试零改动。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型注解，避免运行期循环 import
    from run_agent import AIAgent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 声称执行了操作的关键词模式
# 设计原则：
# - 匹配操作完成后的宣称（"已安装""训练完成"），而不是操作建议（"建议安装""需要修改"）
# - 排除常见误报："配置文件"、"已经知道答案了"、"配置完成"等
# - 优先匹配明确的过去完成时操作声称
# 三个作用：
# 1. Layer1: 验证 AI 回复中声称的操作是否有 tool_call 支撑
# 2. Layer2: 生成工具操作的完整性签名
# 3. 跨回合验证：确保"已完成"的操作确实有执行记录
# ---------------------------------------------------------------------------
OPERATION_CLAIM_PATTERNS: list[str] = [
    # 安装/构建类 — 匹配"已安装""安装成功""构建完成"
    r"(?:已|成功|刚刚)(?:安装|构建|编译|部署|生成)(?:了|成功|完成|完毕)?",
    r"(?:安装|构建|编译|部署|生成)(?:成功|完成|完毕)(?:了)?",
    # 文件修改类 — 避免匹配"配置文件"（普通名词）
    r"(?:已|成功|刚刚)(?:覆盖|替换|删除|重命名|写入)(?:了|成功|完成)?",
    r"(?:覆盖|替换|删除|重命名|写入)(?:成功)(?:了)?",
    r"(?:已|成功|刚刚)修改(?:了)?(?!设置|参数|方案)",  # 排除"修改设置/方案"
    r"修改完成(?:了)?",
    # 训练/运行类 — "已训练""训练完成""运行成功"
    # 注意：不含"正在/开始"（进行时/未来时不是完成声称）
    # 注意：不含"验证"（"已验证"是确认状态不是操作声称）
    # 注意：不含"测试"（"测试完成"常是正常状态描述非操作声称）
    r"(?:已|刚刚)(?:训练|运行|执行|修复)(?:了|完成|成功|完毕)?",
    r"(?:训练|运行|执行|修复)(?:完成|成功|完毕)(?:了)?",
    r"(?:训练|运行|执行).{0,6}(?:进度)",
    # 下载/获取类 — 包含"克隆成功"等无前缀匹配
    r"(?:已|成功)(?:下载|克隆|拉取|导入|导出)(?:了|完成|成功)?",
    r"(?:下载|克隆|拉取|导入|导出)(?:成功|完成)(?:了)?",
    # pip/npm 命令类 — 精确匹配"pip install 已完成"等
    r"(?:pip|npm|apt).{1,10}(?:完成|成功)",
    r"(?:完成)(?:pip|npm|apt).{0,8}(?:安装)",
    # 创建/更新类 — "保存成功"需排除UI提示语
    r"(?:已|成功|刚刚)(?:创建|更新)(?:了|完成|成功)?",
    r"(?:创建|更新)(?:完成|成功)(?:了)?",
    r"(?:已|成功|刚刚)保存(?:了|完成|成功)?(?!设置|偏好|配置)",  # 排除"保存设置/偏好/配置"
    r"保存成功(?!设置|偏好|配置)",  # "保存成功"无前缀匹配
    # 口语化完成表达 — 高频口语声称
    r"(?:已|已经|刚刚)(?:搞定|处理好?(?:了)?|做好?(?:了)?|跑完|配好?(?:了)?|修复|上传|启动|停掉|关掉|重启)(?:了)?",
    r"(?:已|已经)(?:帮你|把|已).{0,6}(?:做好?了|搞定|发过去|发给你|发送完毕)",
    r"(?:已经|已)(?:发过去|发给你|发给你了|发送完毕)",
    r"(?:^|[。，！\n])(?:搞定|处理好?(?:了)?|做好?(?:了)?|跑完|配好?(?:了)?)(?:了)?",  # 无前缀口语化完成
    # English completion claims
    r"(?i)(?:I|I've|I have) (?:already )?(?:installed|built|compiled|deployed|generated|downloaded|created|updated|saved|uploaded|started|stopped|restarted|fixed|repaired|executed|ran|completed|finished|done|configured|set up)",
    r"(?i)(?:successfully |just )?(?:installed|built|compiled|deployed|generated|downloaded|created|updated|saved|uploaded|started|stopped|restarted|fixed|executed|completed|finished|configured)",
    r"(?i)(?:pip|npm|apt) (?:install|build|run) (?:has )?(?:completed|finished|succeeded)",
    r"(?i)(?:done|finished|complete|all set|good to go)(?:[.!\n]|$)",
]


def detect_operation_claims(text: str) -> list[dict]:
    """检测文本中是否有声称执行了操作的声明。

    返回 ``[{claim, start, end, context}]``，空列表表示无操作声明。
    纯函数、无副作用，可脱离 agent 独立测试。
    """
    if not text:
        return []
    claims = []
    for pattern in OPERATION_CLAIM_PATTERNS:
        for match in re.finditer(pattern, text):
            # 取匹配前后文（最多前后 40 字）作为上下文
            ctx_start = max(0, match.start() - 40)
            ctx_end = min(len(text), match.end() + 40)
            context = text[ctx_start:ctx_end].replace("\n", " ")
            claims.append({
                "claim": match.group(),
                "start": match.start(),
                "end": match.end(),
                "context": context,
            })
    return claims


def any_keyword_in(text: str, keywords: set[str]) -> bool:
    """检查 text 中是否包含 keywords 中任一关键词（子串匹配）。"""
    text_lower = text.lower()
    for kw in keywords:
        if kw and kw.lower() in text_lower:
            return True
    return False


def apply_operator_claim_verifier(
    agent: "AIAgent",
    messages: list[dict],
    final_response: str | None,
    interrupted: bool,
) -> tuple[str | None, list[dict]]:
    """Operator-chain verifier (Layer 1) — hard rejection mode.

    Detects fabricated operation claims in the AI response (claims of
    having performed actions without actual tool calls). Returns the
    potentially modified ``final_response`` and ``messages``.

    Four blind spots fixed:
    1. Claim-tool correspondence: checks if claimed operations map to actual tool names
    2. Failed tool results: detects ❌ in tool results even if tools were called
    3. First-turn trigger: no longer requires _tool_history > 0
    4. English patterns: added English completion claims

    - Hard reject (1st): replaces response with a rejection notice and
      injects a system feedback message into ``messages``.
    - Soft reject (2nd+): appends a warning to the response.
    - Resets rejection counter when the turn has real tool calls.

    Seams on ``agent``（仅这三处，便于单测隔离）:
      - ``agent._operator_claim_verifier_enabled()`` 是否启用
      - ``agent._detect_operation_claims(text)`` 声称检测
      - ``agent._operator_claim_rejection_count`` 拒绝计数（读+写）
    """
    if final_response and not interrupted:
        try:
            # 盲区5：判定维度修复。
            # ``messages`` 是完整会话历史（``_prepare_messages`` 以
            # ``list(conversation_history)`` 构造），并非单回合切片。因此：
            #   - 旧的 reversed()+break 只看最后一条 assistant → 多轮工具执行后的
            #     总结回复（最后一条无 tool_calls）几乎必被误杀；
            #   - 但直接全量遍历又会把历史回合的工具调用误计入本回合 → 验证器失效。
            # 正确边界：最后一条 user 消息之后的切片 == 本回合。等价于
            # ``_prepare_messages`` 的 ``current_turn_user_idx + 1``，但刻意
            # 就地计算：硬拒绝会注入一条 role="user" 反馈，让它成为新的回合
            # 起点，与注入文案「直接调工具重新开始」的语义一致（重试只按重试
            # 自身的工具证据判定，不继承被拒那半程）。
            _turn_start = 0
            for _i in range(len(messages) - 1, -1, -1):
                _mi = messages[_i]
                if isinstance(_mi, dict) and _mi.get("role") == "user":
                    _turn_start = _i + 1
                    break
            _turn_msgs = messages[_turn_start:]

            _turn_has_tool_calls = False
            _turn_tool_names: set[str] = set()
            _turn_failed_tools: set[str] = set()
            _turn_succeeded_tools: set[str] = set()
            # 本回合内累计：任一轮 assistant 有 tool_calls 即视为执行过工具
            for _m in _turn_msgs:
                if isinstance(_m, dict) and _m.get("role") == "assistant" and _m.get("tool_calls"):
                    _turn_has_tool_calls = True
                    for _tc in (_m.get("tool_calls") or []):
                        try:
                            _fn = _tc.get("function", {}).get("name", "")
                            if _fn:
                                _turn_tool_names.add(_fn)
                        except Exception:
                            pass
            # 盲区2：检查 tool 结果是否有失败（❌ 开头）vs 成功。
            # 同样必须限定在本回合内，否则历史回合的成功记录会永久压制失败判定。
            for _m in _turn_msgs:
                if isinstance(_m, dict) and _m.get("role") == "tool":
                    _content = _m.get("content", "")
                    _tname = _m.get("name", "")
                    if isinstance(_content, str) and _content.strip().startswith("❌"):
                        if _tname:
                            _turn_failed_tools.add(_tname)
                    else:
                        if _tname:
                            _turn_succeeded_tools.add(_tname)
            if agent._operator_claim_verifier_enabled():
                _claims = agent._detect_operation_claims(final_response)
                if _claims:
                    # 判定逻辑：
                    # - 有 tool_calls 且至少一个成功 → 不拒绝（工具真的执行了）
                    # - 有 tool_calls 但全部失败 → 拒绝（声称的操作没成功）
                    # - 无 tool_calls → 拒绝（凭空声称）
                    _should_reject = False
                    _reject_reason = ""
                    if not _turn_has_tool_calls:
                        _should_reject = True
                        _reject_reason = "本回合未调用任何工具"
                    elif _turn_failed_tools and not _turn_succeeded_tools:
                        # 所有工具调用都失败了
                        _should_reject = True
                        _reject_reason = f"所有工具调用都失败了（{', '.join(_turn_failed_tools)}）"
                    elif _turn_failed_tools and _turn_succeeded_tools:
                        # 部分失败 — 只有声称涉及失败工具时才拒绝
                        _failed_claim_match = any(
                            _claim.get("claim", "") and any_keyword_in(_claim["claim"], _turn_failed_tools)
                            for _claim in _claims
                        )
                        if _failed_claim_match:
                            _should_reject = True
                            _reject_reason = f"部分工具调用失败（{', '.join(_turn_failed_tools)}）且声称涉及失败操作"
                    if _should_reject:
                        agent._operator_claim_rejection_count += 1
                        _first_claim = _claims[0]
                        _failed_info = f"（{_reject_reason}）" if _reject_reason else ""
                        if agent._operator_claim_rejection_count >= 2:
                            # 降级：软拒绝（保留原回复 + 警告）
                            final_response = final_response.rstrip() + (
                                "\n\n⚠️ **操作链验证器警告**: 上述回答中有 "
                                f"{len(_claims)} 处操作声称（如「{_first_claim['claim']}」），"
                                f"但{_failed_info}。"
                                "请确认这些操作是否真实完成。"
                            )
                            logger.info(
                                "operator-claim verifier SOFT reject (count=%d) "
                                "with %d claims (first: %s) reason=%s",
                                agent._operator_claim_rejection_count,
                                len(_claims), _first_claim['claim'], _reject_reason,
                            )
                        else:
                            # 硬拒绝（第1次）：注入 system 反馈，自动 retry 而非等用户发"继续"
                            _rejection = (
                                "\n\n[System: 检测到你的回复中包含未经验证的操作声称"
                                f"（如「{_first_claim['claim']}」），"
                                f"但{_failed_info}。\n"
                                "请在下一轮中调用对应的工具来实际执行这些操作，"
                                "而不是在文本中声称完成。"
                                "不要复述或继续上述回复的内容，直接调工具重新开始。]"
                            )
                            messages.append({
                                "role": "user",
                                "content": _rejection,
                            })
                            final_response = (
                                "⚠️ **操作链验证器拦截**\n\n"
                                f"检测到 {len(_claims)} 处操作声称（如「{_first_claim['claim']}」），"
                                f"但{_failed_info}。\n\n"
                                "正在自动重试，请稍候…"
                            )
                            logger.info(
                                "operator-claim verifier HARD reject (count=%d) "
                                "with %d claims (first: %s) reason=%s, injected retry",
                                agent._operator_claim_rejection_count,
                                len(_claims), _first_claim['claim'], _reject_reason,
                            )
                elif _turn_has_tool_calls and _turn_succeeded_tools:
                    # 本回合有工具调用且至少一个成功，重置拒绝计数器
                    agent._operator_claim_rejection_count = 0
        except Exception as _oc_err:
            logger.warning("operator-claim verifier failed: %s", _oc_err)
            # 累计失败计数，复用 trust_gate 统计模式使失效可观测
            _oc_fail_count = getattr(agent, '_oc_verifier_fail_count', 0) + 1
            setattr(agent, '_oc_verifier_fail_count', _oc_fail_count)
            if _oc_fail_count <= 3:
                logger.warning("operator-claim verifier failure #%d (repeated failures mean anti-hallucination net is silently down)", _oc_fail_count)
    return final_response, messages
