"""Human-readable byte formatting for backup/import display."""

from typing import Optional


def format_bytes(n: Optional[float]) -> str:
    """Format a byte count as a human-readable string.

    Returns ``"?"`` for *None* or unparseable values.
    """
    if n is None:
        return "?"
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    if n < 1024:
        return f"{int(n)}B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n / 1024:.1f} TB"
