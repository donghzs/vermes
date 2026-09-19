"""B4 — 路由账本粗算：静态单价表 × token → estimated_cost（非精确账单）。"""
from __future__ import annotations

# USD / 1M tokens 的粗算单价（观测用；非厂商报价单，发版文案勿写「精确费用」）
_PROVIDER_PRICE_PER_MTOK = {
    "agnes": {"prompt": 0.0, "completion": 0.0},  # 免费档
    "vbit": {"prompt": 0.0, "completion": 0.0},
    "deepseek": {"prompt": 0.27, "completion": 1.10},
    "openai": {"prompt": 2.50, "completion": 10.0},
    "anthropic": {"prompt": 3.00, "completion": 15.0},
    "zhipu": {"prompt": 0.50, "completion": 1.50},
    "ollama": {"prompt": 0.0, "completion": 0.0},
}


def estimate_cost_usd(provider: str, prompt_tokens: int, completion_tokens: int) -> float:
    """粗算成本（USD）。未知 provider → 0.0（缺单价不编造）。"""
    prices = _PROVIDER_PRICE_PER_MTOK.get((provider or "").lower())
    if not prices:
        return 0.0
    p = max(int(prompt_tokens or 0), 0)
    c = max(int(completion_tokens or 0), 0)
    cost = (p * prices["prompt"] + c * prices["completion"]) / 1_000_000.0
    return round(cost, 6)


def format_cost_hint(estimated_cost, provider: str = "") -> str:
    """路由徽标 tooltip 用；无估算返回空串（UI 暂无信号）。"""
    if estimated_cost is None:
        return ""
    try:
        cost = float(estimated_cost)
    except (TypeError, ValueError):
        return ""
    if cost <= 0:
        return "本次费用粗算：~0（免费档或未计价）"
    return f"本次费用粗算：~${cost:.4f}（非精确账单）"
