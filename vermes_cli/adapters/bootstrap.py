"""L2 启动注册（bootstrap）：扫描 PATH 上的 CLI-Anything 适配器并 discover+register。

设计纪律（守「薄」）：
- 只做「扫描已安装的 cli-anything-* + 复用 SoftwareAdapter 全链路」，不写垂直逻辑。
- 失败容错：单个适配器 discover/register 失败不阻塞其他，记 warn 日志继续。
- 调用时机：model_tools 模块级（内置工具注册后），把 L2 工具接进运行态工具表。

CLI-Anything 命名约定 cli-anything-<software>，据此推导 software/backend；
domain 用已知映射（供 route_toolset 阶段一粗筛的双语桥接），未知 software
fallback 到 software 名本身（fail-open，工具仍注册可用，仅 domain 分类通用）。

扫描结果以**绝对路径**驱动 discover：避免「扫到 A 目录的 cli、却因 shutil.which
按全局 PATH 解析而跑 B 目录同名文件」的不一致（paths 参数语义因此完整、可隔离测试）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .discovery import CLI_NATIVE
from .software_adapter import SoftwareAdapter, SoftwareAdapterSpec

logger = logging.getLogger(__name__)

_PREFIX = "cli-anything-"

# 已知软件 → domain 映射（route_toolset 阶段一粗筛的双语桥接用）。
# 未知软件 fallback 到 software 名本身（见 _derive_spec）。
_SOFTWARE_DOMAIN = {
    "freecad": "3d",
    "blender": "3d",
}

# 非 cli-anything- 前缀的 entry_point 名称（从 cli-hub list --json 2026-08-21 提取）。
# 这些命令名会被额外扫描；新增 entry_point 时更新此表。
_NON_STD_ENTRY_POINTS: set[str] = {
    "android", "clibrowser", "cloak", "contentful", "dhq", "dreamina",
    "elevenlabs", "generate-veo", "lark-cli", "minimax-cli", "obsidian",
    "obsidian-agent", "op", "sanity", "sentry-cli", "shopify", "sketch-cli",
    "smithue-cli", "suno", "tracecsr", "vivideo", "wecom-cli", "x-twitter-scraper",
}

# entry_point → domain 映射（非标准 entry_point 的 domain 信息，供 route_toolset 粗筛）。
_NON_STD_DOMAIN: dict[str, str] = {
    "minimax-cli": "ai", "generate-veo": "ai", "dreamina": "ai", "vivideo": "ai",
    "elevenlabs": "audio",
    "lark-cli": "communication", "wecom-cli": "communication", "x-twitter-scraper": "communication",
    "tracecsr": "data-science",
    "sketch-cli": "design",
    "sentry-cli": "devops", "op": "devops", "dhq": "devops",
    "smithue-cli": "devtools",
    "obsidian": "knowledge",
    "android": "mobile",
    "suno": "music",
    "obsidian-agent": "productivity",
    "clibrowser": "web", "contentful": "web", "sanity": "web", "shopify": "web", "cloak": "web",
}


def _iter_cli_anything_bins(paths: list[str] | None = None) -> list[str]:
    """扫描 PATH 各目录，返回已安装 CLI-Anything 适配器的**绝对路径**。

    两层扫描：① cli-anything-* 前缀 glob（主力） ② 非 cli-anything- 前缀的已知 entry_point（补充）。
    去重（同名只保留先出现的目录）、按 basename 排序。目录不可读/无权限时跳过（不抛）。"""
    raw_dirs = paths if paths is not None else os.environ.get("PATH", "").split(os.pathsep)
    found: dict[str, str] = {}  # basename -> abs path
    for d in raw_dirs:
        d = d.strip()
        if not d:
            continue
        try:
            # 主力：cli-anything-* glob
            for p in Path(d).glob(_PREFIX + "*"):
                if p.is_file() and os.access(p, os.X_OK):
                    found.setdefault(p.name, str(p))
            # 补充：非标准 entry_point（精确文件名匹配）
            for name in _NON_STD_ENTRY_POINTS:
                p = Path(d) / name
                if p.is_file() and os.access(p, os.X_OK):
                    found.setdefault(p.name, str(p))
        except OSError:
            continue
    return [found[n] for n in sorted(found)]


def _derive_spec(cli_path: str) -> SoftwareAdapterSpec:
    """从 CLI 路径推导适配器规格（cli_bin 用绝对路径）。

    两种路径模式：① cli-anything-<software>（去前缀得 software）
    ② 非 cli-anything- 前缀（entry_point 名 = software）。
    """
    basename = Path(cli_path).name
    if basename.startswith(_PREFIX):
        software = basename[len(_PREFIX):]
        if not software:
            raise ValueError(f"无法从 CLI 路径推导 software: {cli_path!r}")
        domain = _SOFTWARE_DOMAIN.get(software, software)
    else:
        # 非标准 entry_point：software = basename 本身
        software = basename
        domain = _NON_STD_DOMAIN.get(basename, _SOFTWARE_DOMAIN.get(basename, basename))
    return SoftwareAdapterSpec(
        domain=domain,
        software=software,
        cli_bin=cli_path,
        backend=software,
        operation_mechanism=CLI_NATIVE,
    )


def discover_l2_adapters(paths: list[str] | None = None) -> dict[str, int]:
    """扫描 + discover + register 所有已安装的 CLI-Anything 适配器。

    返回 {software: 注册工具数}；失败的工具值为 -1（标记跳过，不阻塞其他）。
    单个适配器 discover/register 抛异常仅记 warn，继续处理下一个。
    """
    result: dict[str, int] = {}
    for cli_path in _iter_cli_anything_bins(paths):
        try:
            spec = _derive_spec(cli_path)
            software = spec.software
            adapter = SoftwareAdapter(spec)
            tools = adapter.discover_tools()
            n = adapter.register(tools) if tools else 0
            result[software] = n
            logger.info(
                "L2 adapter %s: discovered+registered %d tools (toolset=%s)",
                cli_path, n, adapter.toolset,
            )
        except Exception as exc:  # noqa: BLE001 - 启动容错：单个适配器失败不阻塞其他
            result[software] = -1
            logger.warning(
                "L2 adapter %s discover/register failed (skipped): %s", cli_path, exc
            )
    return result
