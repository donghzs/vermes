"""Agnes AI provider profile — OpenAI-compatible chat completions."""

from providers import register_provider
from providers.base import ProviderProfile

agnes = ProviderProfile(
    name="agnes",
    aliases=("sapiens", "agnes-ai"),
    api_mode="chat_completions",
    env_vars=("AGNES_API_KEY",),
    base_url="https://apihub.agnes-ai.com/v1",
    auth_type="api_key",
    default_aux_model="agnes-2.0-flash",
)

register_provider(agnes)
