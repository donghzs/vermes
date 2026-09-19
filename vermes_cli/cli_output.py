"""Shared CLI output helpers for Vermes CLI modules.

Extracts the identical ``print_info/success/warning/error`` and ``prompt()``
functions previously duplicated across setup.py, tools_config.py,
mcp_config.py, and memory_setup.py.
"""

import getpass
import sys as _sys

from vermes_cli.colors import Colors, color

import logging

logger = logging.getLogger(__name__)


class _DynamicStdHandler(logging.Handler):
    """Route INFO→stdout, WARNING+→stderr, resolving streams at emit time.

    The brand fork replaced upstream ``print()`` with ``logger.info()`` across
    CLI modules. A plain ``logging.StreamHandler(sys.stdout)`` captures the
    ``sys.stdout`` object *once* at construction, so it ignores
    ``contextlib.redirect_stdout``/``redirect_stderr`` — which ``run_slash``
    and the pytest ``capsys`` fixture rely on to capture CLI output. That made
    every ``logger.info()`` in a redirected context bypass the capture buffer
    (JSON-parse failures, warnings leaking to the real terminal).

    This handler resolves ``sys.stdout`` / ``sys.stderr`` on every emit, and
    routes by severity (WARNING and above → stderr, else stdout) — restoring
    the exact semantics ``print(..., file=sys.stderr)`` had before the fork.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = _sys.stderr if record.levelno >= logging.WARNING else _sys.stdout
            msg = self.format(record)
            stream.write(msg + "\n")
            try:
                stream.flush()
            except Exception:
                pass
        except Exception:
            self.handleError(record)


# CLI 输出模块——确保有 handler 输出到 stdout/stderr（按级别分流，动态跟随重定向）
if not logging.getLogger("vermes_cli").handlers:
    _h = _DynamicStdHandler()
    logging.getLogger("vermes_cli").addHandler(_h)
    logging.getLogger("vermes_cli").setLevel(logging.INFO)



# ─── Print Helpers ────────────────────────────────────────────────────────────


def print_info(text: str) -> None:
    """Print a dim informational message."""
    logger.info(color(f"  {text}", Colors.DIM))


def print_success(text: str) -> None:
    """Print a green success message with ✓ prefix."""
    logger.info(color(f"✓ {text}", Colors.GREEN))


def print_warning(text: str) -> None:
    """Print a yellow warning message with ⚠ prefix."""
    logger.info(color(f"⚠ {text}", Colors.YELLOW))


def print_error(text: str) -> None:
    """Print a red error message with ✗ prefix."""
    logger.info(color(f"✗ {text}", Colors.RED))


def print_header(text: str) -> None:
    """Print a bold yellow header."""
    logger.info(color(f"\n  {text}", Colors.YELLOW))


# ─── Input Prompts ────────────────────────────────────────────────────────────


def prompt(
    question: str,
    default: str | None = None,
    password: bool = False,
) -> str:
    """Prompt the user for input with optional default and password masking.

    Replaces the four independent ``_prompt()`` / ``prompt()`` implementations
    in setup.py, tools_config.py, mcp_config.py, and memory_setup.py.

    Returns the user's input (stripped), or *default* if the user presses Enter.
    Returns empty string on Ctrl-C or EOF.
    """
    suffix = f" [{default}]" if default else ""
    display = color(f"  {question}{suffix}: ", Colors.YELLOW)

    try:
        if password:
            value = getpass.getpass(display)
        else:
            value = input(display)
        value = value.strip()
        return value if value else (default or "")
    except (KeyboardInterrupt, EOFError):
        logger.info()
        return ""


def prompt_yes_no(question: str, default: bool = True) -> bool:
    """Prompt for a yes/no answer. Returns bool."""
    hint = "Y/n" if default else "y/N"
    answer = prompt(f"{question} ({hint})")
    if not answer:
        return default
    return answer.lower().startswith("y")
