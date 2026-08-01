"""P3-⑪/②: 偏好触发词单一真相源 (single source of truth)。

消除三处不一致：
  - l1_extractor（抽取：用户显式偏好表述 → 写 L1 preference 层）
  - memory_fabric._infer_lifecycle_tag（推断：内容是否偏好 → 决定存储层级）
  - session_handoff（会话交割：捕获用户偏好，关闭 0 覆盖缺口）

历史 bug：l1_extractor 抽到 "我更喜欢 Python" → 写 lifecycle_tag='preference'，
但 memory_fabric._infer_lifecycle_tag 词表更窄（无 "更喜欢"）→ 推断成
'reference' → 语义矛盾。统一到本文件后，抽取与推断用同一套词汇，矛盾消失。

涌现哲学边界：本文件是 L1 确定性规则层的词汇表（零 LLM / 0 假阳性底线），
允许硬编码；禁硬编码的是跨模块映射与结构启发式，与此无关。
不要为"覆盖更多变体"盲目扩词——扩词等于加硬编码，应先看真实召回缺口。
"""

from typing import FrozenSet

# 中文偏好触发词（与 l1_extractor 抽取口径一致；memory_fabric 推断时复用）
ZH_PREFERENCE_TRIGGERS: FrozenSet[str] = frozenset(
    {
        "偏好",
        "喜欢",
        "习惯",
        "总是",
        "从不",
        "一般",
        "通常",
        "倾向于",
        "更爱",
        "更想",
        "更喜欢",
        "更愿意",
        "认定",
        "默认",
        "固定",
    }
)

# 英文偏好触发词
EN_PREFERENCE_TRIGGERS: FrozenSet[str] = frozenset(
    {
        "prefer",
        "prefers",
        "preferred",
        "like",
        "likes",
        "always",
        "usually",
        "typically",
        "never",
        "love to",
        "want to",
        "hate to",
    }
)
