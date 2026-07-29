"""存储用量查询端点"""
import os
import pathlib

from vermes_cli.config import get_hermes_home


def _dir_size_mb(path: str) -> float:
    """递归计算目录大小，单位 MB"""
    try:
        p = pathlib.Path(path)
        if not p.exists():
            return 0.0
        total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
        return round(total / (1024 * 1024), 1)
    except (PermissionError, OSError):
        return 0.0


async def get_storage_usage():
    hermes_home = str(get_hermes_home())

    sessions_db = os.path.join(hermes_home, "state.db")
    memories_dir = os.path.join(hermes_home, "memories")
    skills_dir = os.path.join(hermes_home, "skills")

    sessions_mb = (
        round(os.path.getsize(sessions_db) / (1024 * 1024), 1)
        if os.path.isfile(sessions_db)
        else 0.0
    )
    memories_mb = _dir_size_mb(memories_dir)
    skills_mb = _dir_size_mb(skills_dir)
    total = round(sessions_mb + memories_mb + skills_mb, 1)

    return {
        "sessions_db": sessions_mb,
        "memories": memories_mb,
        "skills": skills_mb,
        "total": total,
    }


# ── registration ───────────────────────────────────────────────


def register_to(app):
    """Register storage usage endpoint."""
    app.add_api_route(
        "/api/storage/usage", get_storage_usage, methods=["GET"], name="get_storage_usage"
    )
