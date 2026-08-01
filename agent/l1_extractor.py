"""P3-⑪: L1 语义记忆自动抽取规则引擎。

每轮对话结束扫描最近的用户输入 + 助手最终回复，用正则规则抽取高价值
事实（IP / API Key / 密码 / 偏好声明），自动写入 L1 note 层，无需用户
手动 /remember。

设计原则
--------
- 规则优先、零 LLM：纯正则，确定性、零成本、可审计、可复现。
- fail-open：任何抽取/写入异常都不应影响主对话流程（由调用方兜底）。
- 幂等：每条事实用稳定 pointer（kind + 归一化值哈希）upsert，重复轮次
  不会在 memories 表累积重复行。
- 隐私护栏：密码类凭据默认**脱敏存储**（只留首尾 + 长度 + 上下文提示），
  不把明文口令落本地库；API Key / IP 因复用需要保留原值。行为由
  ``MASK_PASSWORDS`` 常量控制，置 False 可改为原文存储（不推荐）。
- 边界：只抽取"显式结构化事实"，不臆测；误抽概率低，且 L1 可被用户随时
  检视/删除。
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

from agent._preference_keywords import (
    ZH_PREFERENCE_TRIGGERS,
    EN_PREFERENCE_TRIGGERS,
)

logger = logging.getLogger(__name__)

# 密码是否脱敏存储（默认 True，安全优先；设为 False 改存明文）
MASK_PASSWORDS: bool = True

# 每条事实在 memories 表的 source（与手动 note 区分，便于检索/清理）
L1_AUTO_SOURCE = "l1_auto"


# ── 规则：正则 ────────────────────────────────────────────────────────────

# IPv4（octet 0-255 由 _valid_ipv4 校验）
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# API Key：已知前缀 + 通用赋值式（赋值式要求 ≥16 字符避免误抽短词）
_API_KEY_RES: List[re.Pattern] = [
    re.compile(r"\b(sk-[A-Za-z0-9_\-]{20,})\b"),               # OpenAI / 兼容
    re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),                     # AWS
    re.compile(r"\b(AIza[0-9A-Za-z_\-]{35})\b"),               # Google
    re.compile(r"\b(xox[baprs]-[0-9A-Za-z\-]{10,})\b"),        # Slack
    re.compile(r"\b(ghp_[0-9A-Za-z]{36})\b"),                  # GitHub
    re.compile(r"\b(glpat-[0-9A-Za-z_\-]{20,})\b"),            # GitLab
    re.compile(r"\b(pk_live_[0-9A-Za-z]{20,})\b"),             # Stripe
    re.compile(r"\b(pk_test_[0-9A-Za-z]{20,})\b"),             # Stripe
    re.compile(r"\b([A-Za-z0-9]{32,})\b(?![\w\-])"),           # 长随机串兜底
    # 赋值式：api_key/token/secret/access_key = <值>
    re.compile(
        r"(?:api[_-]?key|apikey|token|secret|access[_-]?key|accesskey)"
        r"[\"'\s:=：]+([A-Za-z0-9_\-\.]{16,})",
        re.IGNORECASE,
    ),
]

# 密码：英文赋值式 password/passwd/pwd = <值>；中文「密码 是/为/: <值>」
_PASSWORD_RES: List[re.Pattern] = [
    re.compile(
        r"(?:password|passwd|pwd)[\"'\s:=：]+([^\s\"']{6,64})",
        re.IGNORECASE,
    ),
    # 中文：密码 + 可选空白 + 可选连接词(是/为/：:) + 可选空白 + 值
    re.compile(
        r"密码[\s]*[是为:：]?[\s]*([^\s\"'，。；：]{6,64})",
    ),
]

# 偏好声明：用户显式表达偏好/习惯/总是/从不 等 + 后续从句
# 触发词来自共享常量 _preference_keywords（与 memory_fabric 推断口径统一，
# 修复"抽到 preference 但 fabric 推断成 reference"的层级矛盾）。
_ZH_PREF_ALT = "|".join(sorted(ZH_PREFERENCE_TRIGGERS))
_EN_PREF_ALT = "|".join(sorted(EN_PREFERENCE_TRIGGERS))
_PREFERENCE_RES: List[re.Pattern] = [
    # 中文：我(们)(更)? + 偏好触发词 + 后续从句
    # (?:更)? 结构性修饰，让"我更喜欢/我更爱"等变体也能命中（非新增硬编码词）
    re.compile(r"(?:我(?:们)?(?:更)?(?:" + _ZH_PREF_ALT + r"))" r"([^。\n；;]{2,60})"),
    # 英文：I + 偏好触发词 + 后续从句
    re.compile(
        r"\b(I\s+(?:" + _EN_PREF_ALT + r"))\b" r"([^.\n]{2,60})",
        re.IGNORECASE,
    ),
]


# ── 数据结构 ──────────────────────────────────────────────────────────────

@dataclass
class L1Fact:
    kind: str                       # ip | api_key | password | preference
    value: str                      # 归一化后用于去重/存储的值（密码可能已脱敏）
    raw_value: str                  # 原始命中值（密码脱敏前）
    snippet: str                    # 命中原文的短片段（用于 recall 上下文）
    pointer_key: str = ""           # 稳定去重键（kind + 值哈希）
    content: str = ""               # 写入 L1 的笔记正文
    lifecycle_tag: str = "reference"

    def __post_init__(self):
        if not self.pointer_key:
            self.pointer_key = hashlib.sha1(
                f"{self.kind}:{self.value}".encode("utf-8")
            ).hexdigest()[:16]


# ── 工具 ──────────────────────────────────────────────────────────────────

def _valid_ipv4(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _mask_secret(secret: str, keep_head: int = 2, keep_tail: int = 1) -> str:
    """脱敏：保留首尾若干字符，中间用 * 替代，并标注长度。"""
    if len(secret) <= keep_head + keep_tail:
        return "*" * max(len(secret), 3)
    head = secret[:keep_head]
    tail = secret[-keep_tail:]
    return f"{head}{'*' * (len(secret) - keep_head - keep_tail)}{tail}"


def _context_hint(snippet: str, window: int = 24) -> str:
    """从命中片段截取前后文提示（去掉值本身，给 L1 笔记一点语境）。"""
    s = snippet.strip()
    return s[:window]


# ── 抽取 ──────────────────────────────────────────────────────────────────

def extract_facts(text: str) -> List[L1Fact]:
    """扫描单段文本，返回抽取到的事实列表（同段内按 pointer_key 去重）。"""
    facts: List[L1Fact] = []
    if not text:
        return facts
    seen: set = set()

    def _add(fact: L1Fact) -> None:
        if fact.pointer_key in seen:
            return
        seen.add(fact.pointer_key)
        facts.append(fact)

    # 1) IP
    for m in _IP_RE.finditer(text):
        ip = m.group(0)
        if not _valid_ipv4(ip):
            continue
        _add(L1Fact(
            kind="ip", value=ip, raw_value=ip, snippet=m.group(0),
            content=f"IP 地址: {ip} （自动抽取自对话，可用于服务定位/白名单）",
            lifecycle_tag="reference",
        ))

    # 2) API Key
    for rx in _API_KEY_RES:
        for m in rx.finditer(text):
            val = m.group(1) if m.groups() and m.group(1) else m.group(0)
            if not val or len(val) < 12:
                continue
            # 排除纯 IP 段被长随机串兜底误抽
            if _IP_RE.fullmatch(val):
                continue
            _add(L1Fact(
                kind="api_key", value=val, raw_value=val, snippet=m.group(0),
                content=f"API Key: {val} （自动抽取，注意属敏感凭据）",
                lifecycle_tag="reference",
            ))

    # 3) 密码
    for rx in _PASSWORD_RES:
        for m in rx.finditer(text):
            val = m.group(1)
            if not val or len(val) < 6:
                continue
            stored = _mask_secret(val) if MASK_PASSWORDS else val
            _add(L1Fact(
                kind="password",
                value=stored,
                raw_value=val,
                snippet=m.group(0),
                content=(
                    f"密码（已脱敏）: {stored} 长度={len(val)} "
                    f"上下文: {_context_hint(m.group(0))}"
                    if MASK_PASSWORDS else
                    f"密码: {val} 上下文: {_context_hint(m.group(0))}"
                ),
                lifecycle_tag="reference",
            ))

    # 4) 偏好声明
    for rx in _PREFERENCE_RES:
        for m in rx.finditer(text):
            val = (m.group(2) if m.groups() and len(m.groups()) > 1 and m.group(2)
                   else m.group(1)).strip()
            if not val or len(val) < 2:
                continue
            _add(L1Fact(
                kind="preference",
                value=val,
                raw_value=val,
                snippet=m.group(0).strip(),
                content=f"偏好: {val} （自动抽取自用户表述）",
                lifecycle_tag="preference",
            ))

    return facts


# ── 提交 ──────────────────────────────────────────────────────────────────

def commit_facts(facts: List[L1Fact]) -> int:
    """把抽取到的事实写入 L1（按 pointer_key 跨事实去重）。返回成功条数。"""
    if not facts:
        return 0
    from agent.memory_fabric import record as _mf_record, L1_NOTE

    committed = 0
    seen: set = set()
    for f in facts:
        if f.pointer_key in seen:
            continue
        seen.add(f.pointer_key)
        try:
            _mf_record({
                "source": L1_AUTO_SOURCE,
                "pointer": f"l1auto#{f.pointer_key}",
                "layer": L1_NOTE,
                "type": f"l1_{f.kind}",
                "scope": "",
                "fts_content": f.content,
                "lifecycle_tag": f.lifecycle_tag,
            })
            committed += 1
        except Exception:
            logger.warning("L1 commit failed for %s (skip)", f.kind, exc_info=True)
    return committed


def run_l1_extraction_for_turn(user_text: str, assistant_text: str) -> int:
    """每轮对话结束调用：扫描用户语 + 助手最终回复，抽取并提交 L1 事实。

    Args:
        user_text: 本轮用户原始输入。
        assistant_text: 本轮助手最终回复正文。
    Returns:
        本次成功写入 L1 的事实条数。
    """
    facts = extract_facts(user_text) + extract_facts(assistant_text)
    if not facts:
        return 0
    return commit_facts(facts)


def extract_facts_multi(texts: List[str]) -> List[L1Fact]:
    """对多段文本合并抽取（段间去重）。便于测试/批量扫描。"""
    out: List[L1Fact] = []
    seen: set = set()
    for t in texts:
        for f in extract_facts(t):
            if f.pointer_key in seen:
                continue
            seen.add(f.pointer_key)
            out.append(f)
    return out
