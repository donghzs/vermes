"""ScholarForge — 论文写作 Agent 模块
完全独立于 Vermes 核心，通过 Blueprint 注册。

Phase 3 (2026-06-29):  +3 Agent Tools (search/write/review) 供 Vermes Agent 在对话中直接使用。
"""

import os


def _load_vermes_config() -> tuple[dict, dict]:
    """读取 ~/.vermes/{config.yaml,.env}，返回 (cfg, env_vars)。
    blueprint.py 和 tools.py 的 _resolve_credentials 共用此入口，
    消除两处重复的 yaml + .env 解析逻辑。
    """
    import yaml

    home = os.path.expanduser("~/.vermes")
    cfg_path = os.path.join(home, "config.yaml")
    env_path = os.path.join(home, ".env")

    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    env_vars = {}
    if os.path.exists(env_path):
        for line in open(env_path).read().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip('"').strip("'")

    return cfg, env_vars


# 导入 tools 模块，触发全局 registry 注册（3 个 Agent 工具）
from hermes_cli.scholarforge import tools  # noqa: F401 — side-effect: registers scholarforge_search/write/review
