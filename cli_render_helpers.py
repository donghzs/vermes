"""Pure rendering helper functions extracted from cli.py (D3a refactor).

These functions have no dependency on CLI runtime state (no _cprint, no
_OUTPUT_HISTORY, no light-mode cache).  They are imported back into cli.py
via ``from cli_render_helpers import *`` so all existing call sites remain
unchanged.

Functions that tests monkeypatch/patch directly (_cprint, _detect_light_mode,
_maybe_remap_for_light_mode, _install_skin_light_mode_hook, _rich_text_from_ansi,
_render_final_assistant_content, _replay_output_history) are deliberately
NOT in this module — they stay in cli.py.
"""

import re
import os
import sys
import time
import shutil

from rich.text import Text as _RichText

# ────────────────────────────────────────────────────────────────────────
# Constants (copied from cli.py so this module is self-contained)
# ────────────────────────────────────────────────────────────────────────

_ACCENT_ANSI_DEFAULT = "\033[1;38;2;255;215;0m"  # True-color #FFD700 bold — fallback
_STREAM_PAD = "    "  # 4-space indent for streamed response text

_WINDOWS_PATH_WITH_DOT_SEGMENT_RE = re.compile(
    r"(?i)(?:\b[a-z]:\\|\\\\)[^\s`]*\\\.[^\s`]*"
)


# ────────────────────────────────────────────────────────────────────────
# Color helpers
# ────────────────────────────────────────────────────────────────────────

def _hex_to_ansi(hex_color: str, *, bold: bool = False) -> str:
    """Convert a hex color like '#268bd2' to a true-color ANSI escape.

    Auto-remaps known dark-mode-tuned colors to readable light-mode
    equivalents when running on a light terminal (see
    _maybe_remap_for_light_mode + _LIGHT_MODE_REMAP).
    """
    # Late import: _maybe_remap_for_light_mode stays in cli.py (test patch target)
    import cli
    hex_color = cli._maybe_remap_for_light_mode(hex_color)
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        prefix = "1;" if bold else ""
        return f"\033[{prefix}38;2;{r};{g};{b}m"
    except (ValueError, IndexError):
        return _ACCENT_ANSI_DEFAULT if bold else "\033[38;2;184;134;11m"


def _luminance_from_hex(hex_str: str) -> float | None:
    s = (hex_str or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6 or not all(c in "0123456789abcdefABCDEF" for c in s):
        return None
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return None
    # Rec.709 luma
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _query_osc11_background() -> str | None:
    """Ask the terminal for its background color via OSC 11.

    Most modern terminals reply with \x1b]11;rgb:RRRR/GGGG/BBBB\x1b\\
    within a few ms.  We wait up to 100ms total before giving up.
    Returns "#RRGGBB" or None on timeout / non-tty.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
    except Exception:
        return None
    try:
        try:
            tty.setcbreak(fd)
        except Exception:
            return None
        try:
            sys.stdout.write("\x1b]11;?\x1b\\")
            sys.stdout.flush()
        except Exception:
            return None
        # Read up to ~50ms for the response
        import select
        deadline = time.monotonic() + 0.1
        buf = b""
        while time.monotonic() < deadline:
            r, _, _ = select.select([fd], [], [], deadline - time.monotonic())
            if not r:
                continue
            try:
                chunk = os.read(fd, 64)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            if b"\x1b\\" in buf or b"\x07" in buf:
                break
        # Parse: \x1b]11;rgb:RRRR/GGGG/BBBB\x1b\\
        m = re.search(rb"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", buf)
        if not m:
            return None
        # Each component is 1-4 hex digits — normalize to 8-bit
        def norm(h: bytes) -> int:
            v = int(h, 16)
            # Scale to 0-255 based on hex length
            bits = len(h) * 4
            return (v * 255) // ((1 << bits) - 1) if bits else 0
        r, g, b = norm(m.group(1)), norm(m.group(2)), norm(m.group(3))
        return f"#{r:02X}{g:02X}{b:02X}"
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSANOW, old)
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────────
# Skin-aware ANSI wrapper
# ────────────────────────────────────────────────────────────────────────

class _SkinAwareAnsi:
    """Lazy ANSI escape that resolves from the skin engine on first use.

    Acts as a string in f-strings and concatenation.  Call ``.reset()`` to
    force re-resolution after a ``/skin`` switch.
    """

    def __init__(self, skin_key: str, fallback_hex: str = "#FFD700", *, bold: bool = False):
        self._skin_key = skin_key
        self._fallback_hex = fallback_hex
        self._bold = bold
        self._cached: str | None = None

    def __str__(self) -> str:
        if self._cached is None:
            try:
                from hermes_cli.skin_engine import get_active_skin
                self._cached = _hex_to_ansi(
                    get_active_skin().get_color(self._skin_key, self._fallback_hex),
                    bold=self._bold,
                )
            except Exception:
                self._cached = _hex_to_ansi(self._fallback_hex, bold=self._bold)
        return self._cached

    def __add__(self, other: str) -> str:
        return str(self) + other

    def __radd__(self, other: str) -> str:
        return other + str(self)

    def reset(self) -> None:
        """Clear cache so the next access re-reads the skin."""
        self._cached = None


def _accent_hex() -> str:
    """Return the active skin accent color for legacy CLI output lines."""
    try:
        from hermes_cli.skin_engine import get_active_skin
        return get_active_skin().get_color("ui_accent", "#FFBF00")
    except Exception:
        return "#FFBF00"


# ────────────────────────────────────────────────────────────────────────
# Markdown / text helpers
# ────────────────────────────────────────────────────────────────────────

def _strip_markdown_syntax(text: str) -> str:
    """Best-effort markdown marker removal for plain-text display."""
    # Late import: _rich_text_from_ansi stays in cli.py (test import target)
    import cli
    plain = cli._rich_text_from_ansi(text or "").plain
    # Avoid stripping cron-style expressions like "* * * * *" as if they were
    # Markdown horizontal rules. CommonMark treats three or more "*" as an HR,
    # but in Hermes output it's common to display cron schedules verbatim.
    #
    # Keep the behavior for "-" / "_" HR markers, and only strip "*" HR lines
    # when there are exactly 3 asterisks (with optional whitespace).
    plain = re.sub(r"^\s{0,3}(?:[-_]\s*){3,}$", "", plain, flags=re.MULTILINE)
    plain = re.sub(r"^\s{0,3}(?:\*\s*){3}\s*$", "", plain, flags=re.MULTILINE)
    plain = re.sub(r"^\s{0,3}#{1,6}\s+", "", plain, flags=re.MULTILINE)
    # Preserve blockquotes, lists, and checkboxes because they carry structure.
    plain = re.sub(r"(```+|~~~+)", "", plain)
    plain = re.sub(r"`([^`]*)`", r"\1", plain)
    plain = re.sub(r"!\[([^\]]*)\]\([^\)]*\)", r"\1", plain)
    plain = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", plain)
    plain = re.sub(r"\*\*\*([^*]+)\*\*\*", r"\1", plain)
    plain = re.sub(r"(?<!\w)___([^_]+)___(?!\w)", r"\1", plain)
    plain = re.sub(r"\*\*([^*]+)\*\*", r"\1", plain)
    plain = re.sub(r"(?<!\w)__([^_]+)__(?!\w)", r"\1", plain)
    # Only strip `*emphasis*` markers when the inner text is non-whitespace.
    # This avoids corrupting cron expressions like "* * * * *".
    plain = re.sub(r"\*([^\s*][^*]*?[^\s*])\*", r"\1", plain)
    plain = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", plain)
    plain = re.sub(r"~~([^~]+)~~", r"\1", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain.strip("\n")


def _preserve_windows_dot_segments_for_markdown(text: str) -> str:
    r"""Keep Windows path separators before hidden directories in Markdown.

    CommonMark treats ``\.`` as an escaped literal dot, so Rich Markdown would
    render ``D:\repo\.ai`` as ``D:\repo.ai``.  Doubling only that separator
    inside Windows path-looking tokens preserves the path without changing
    ordinary markdown escapes like ``1\. not a list``.
    """
    if "\\." not in text:
        return text

    def _protect(match: re.Match[str]) -> str:
        return re.sub(r"(?<!\\)\\(?=\.)", r"\\\\", match.group(0))

    return _WINDOWS_PATH_WITH_DOT_SEGMENT_RE.sub(_protect, text)


def _terminal_width_for_streaming() -> int:
    """Display cells available inside the streamed response box.

    The streaming path indents every line by ``_STREAM_PAD`` (4 cells)
    inside an open response panel.  The realigner uses this number as
    its budget when deciding whether to keep a horizontal table or
    fall back to vertical key-value rendering.  We subtract a small
    safety margin so terminal-resize races don't push a borderline
    table into mid-cell soft-wrap.
    """

    try:
        cols = shutil.get_terminal_size((80, 24)).columns
    except Exception:
        cols = 80
    return max(20, cols - len(_STREAM_PAD) - 2)
