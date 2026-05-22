"""vbit.top provider profile.

vbit.top — One-API unified gateway with multi-provider routing.
Supports Qwen, DeepSeek, GPT, Claude, Gemini and more via a single endpoint.
"""

from providers import register_provider
from providers.base import ProviderProfile

vbit = ProviderProfile(
    name="vbit",
    aliases=("vbit-top", "vbit-cloud"),
    env_vars=("VBIT_API_KEY",),
    display_name="vbit.top (胜比特)",
    description="vbit.top — One-API 统一网关，多模型聚合",
    signup_url="https://vbit.top",
    fallback_models=(
        "qwen-turbo",
        "deepseek-chat",
        "deepseek-reasoner",
        "gpt-4o",
        "claude-sonnet-4-20250514",
    ),
    base_url="https://api.vbit.top/v1",
    default_aux_model="qwen-turbo",
)

register_provider(vbit)
