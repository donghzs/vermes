"""SlashCommandsMixin — composes the domain /command handlers.

The 38 /command handlers were split (W1) into gateway/slash_handlers/.
This module keeps ``SlashCommandsMixin`` as the single composed class so
gateway.run and all downstream imports are unchanged. It also re-exports
the shared namespace for backward compatibility.
"""
from gateway.slash_handlers._common import *  # noqa: F401,F403  back-compat namespace
from gateway.slash_handlers.session_handlers import SessionCommandsMixin
from gateway.slash_handlers.config_handlers import ConfigCommandsMixin
from gateway.slash_handlers.system_handlers import SystemCommandsMixin
from gateway.slash_handlers.capability_handlers import CapabilityCommandsMixin


class SlashCommandsMixin(
    SessionCommandsMixin,
    ConfigCommandsMixin,
    SystemCommandsMixin,
    CapabilityCommandsMixin,
):
    """All /command handler methods, composed from the slash_handlers subpackage."""
    pass
