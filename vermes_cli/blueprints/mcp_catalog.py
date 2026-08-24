"""Blueprint: MCP Catalog — 目录发现 + 安装 + 安全校验

P1-1: 提供内置 MCP 服务器目录（file/git/browser/db 等常用项），
一键安装 + mcp_security 安全校验。
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from vermes_cli.mcp_security import validate_mcp_server_entry

log = logging.getLogger(__name__)
mcp_bp = APIRouter(tags=["mcp"])

# ── 内置 MCP 目录（精选常用服务器）──────────────────────────────

_CATALOG: List[Dict[str, Any]] = [
    {
        "name": "filesystem",
        "description": "文件系统读写操作（读/写/搜索/目录遍历）",
        "category": "文件",
        "transport": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]},
        "auth": {"type": "none"},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    },
    {
        "name": "git",
        "description": "Git 仓库操作（status/diff/log/commit/branch）",
        "category": "开发",
        "transport": {"type": "stdio", "command": "uvx", "args": ["mcp-server-git"]},
        "auth": {"type": "none"},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
    },
    {
        "name": "github",
        "description": "GitHub API（issues/PRs/search/repo 管理）",
        "category": "开发",
        "transport": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"]},
        "auth": {"type": "api_key", "env": [{"name": "GITHUB_PERSONAL_ACCESS_TOKEN", "prompt": "GitHub Personal Access Token", "required": True, "secret": True}]},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/github",
    },
    {
        "name": "sqlite",
        "description": "SQLite 数据库查询与管理",
        "category": "数据库",
        "transport": {"type": "stdio", "command": "uvx", "args": ["mcp-server-sqlite", "--db-path"]},
        "auth": {"type": "none"},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/sqlite",
    },
    {
        "name": "postgres",
        "description": "PostgreSQL 数据库查询",
        "category": "数据库",
        "transport": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres"]},
        "auth": {"type": "api_key", "env": [{"name": "DATABASE_URL", "prompt": "PostgreSQL 连接字符串", "required": True, "secret": True}]},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
    },
    {
        "name": "brave-search",
        "description": "Brave 搜索引擎 API",
        "category": "搜索",
        "transport": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"]},
        "auth": {"type": "api_key", "env": [{"name": "BRAVE_API_KEY", "prompt": "Brave Search API Key", "required": True, "secret": True}]},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
    },
    {
        "name": "memory",
        "description": "持久化知识图谱记忆（实体/关系/观察）",
        "category": "知识",
        "transport": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
        "auth": {"type": "none"},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
    },
    {
        "name": "puppeteer",
        "description": "浏览器自动化（截图/点击/填表/JS 执行）",
        "category": "浏览器",
        "transport": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-puppeteer"]},
        "auth": {"type": "none"},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/puppeteer",
    },
    {
        "name": "fetch",
        "description": "网页抓取与内容提取",
        "category": "搜索",
        "transport": {"type": "stdio", "command": "uvx", "args": ["mcp-server-fetch"]},
        "auth": {"type": "none"},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
    },
    {
        "name": "time",
        "description": "时间与时区转换",
        "category": "工具",
        "transport": {"type": "stdio", "command": "uvx", "args": ["mcp-server-time"]},
        "auth": {"type": "none"},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/time",
    },
    {
        "name": "sequential-thinking",
        "description": "思维链推理服务器（动态问题分解）",
        "category": "推理",
        "transport": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]},
        "auth": {"type": "none"},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
    },
    {
        "name": "everart",
        "description": "EverArt AI 图像生成",
        "category": "创意",
        "transport": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everart"]},
        "auth": {"type": "api_key", "env": [{"name": "EVERART_API_KEY", "prompt": "EverArt API Key", "required": True, "secret": True}]},
        "trust": "official",
        "homepage": "https://github.com/modelcontextprotocol/servers/tree/main/src/everart",
    },
]


class InstallRequest(BaseModel):
    name: str
    env_values: Optional[Dict[str, str]] = None


@mcp_bp.get("/api/mcp/catalog")
async def list_catalog():
    """列出内置 MCP 目录。"""
    installed = _get_installed_names()
    return {
        "catalog": [
            {**entry, "installed": entry["name"] in installed}
            for entry in _CATALOG
        ],
        "total": len(_CATALOG),
    }


@mcp_bp.get("/api/mcp/catalog/{name}")
async def get_catalog_entry(name: str):
    """获取目录中单个 MCP 服务器详情。"""
    for entry in _CATALOG:
        if entry["name"] == name:
            installed = _get_installed_names()
            return {**entry, "installed": name in installed}
    raise HTTPException(status_code=404, detail=f"目录中未找到 '{name}'")


@mcp_bp.post("/api/mcp/catalog/install")
async def install_from_catalog(request: Request):
    """从目录安装 MCP 服务器（含安全校验）。

    Body: {"name": "github", "env_values": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx"}}
    """
    body = await request.json()
    name = body.get("name", "").strip()
    env_values = body.get("env_values") or {}

    entry = None
    for e in _CATALOG:
        if e["name"] == name:
            entry = e
            break
    if not entry:
        raise HTTPException(status_code=404, detail=f"目录中未找到 '{name}'")

    transport = entry.get("transport") or {}
    server_config: Dict[str, Any] = {
        "command": transport.get("command", ""),
        "args": transport.get("args", []),
    }

    # 环境变量
    auth = entry.get("auth") or {}
    env_specs = auth.get("env") or []
    env: Dict[str, str] = {}
    for spec in env_specs:
        var_name = spec["name"]
        val = env_values.get(var_name, "")
        if not val and spec.get("required", True):
            raise HTTPException(
                status_code=400,
                detail=f"缺少必需环境变量: {var_name} ({spec.get('prompt', '')})",
            )
        if val:
            env[var_name] = val
    if env:
        server_config["env"] = env

    # 安全校验
    warnings = validate_mcp_server_entry(name, server_config)
    if warnings:
        raise HTTPException(
            status_code=403,
            detail=f"安全校验未通过: {'; '.join(warnings)}",
        )

    # 写入配置
    from vermes_cli.mcp_config import _save_mcp_server, _get_mcp_servers
    _save_mcp_server(name, server_config)

    # env 写入 ~/.vermes/.env
    if env:
        from pathlib import Path
        env_file = Path.home() / ".vermes" / ".env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if env_file.exists():
            existing = env_file.read_text()
        new_lines = []
        for k, v in env.items():
            line = f"{k}={v}"
            if line not in existing:
                new_lines.append(line)
        if new_lines:
            with open(env_file, "a") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write("\n".join(new_lines) + "\n")

    servers = _get_mcp_servers()
    return {
        "ok": True,
        "name": name,
        "message": f"已安装 {name}",
        "server": servers.get(name),
        "installed_count": len(servers),
    }


@mcp_bp.post("/api/mcp/security-check")
async def security_check(request: Request):
    """对任意 MCP 服务器配置做安全校验（不安装）。

    Body: {"name": "evil", "config": {"command": "bash", "args": ["-c", "curl ..."]}}
    """
    body = await request.json()
    name = body.get("name", "unknown")
    config = body.get("config") or {}
    warnings = validate_mcp_server_entry(name, config)
    return {
        "name": name,
        "suspicious": bool(warnings),
        "warnings": warnings,
    }


@mcp_bp.get("/api/mcp/security/rules")
async def security_rules():
    """返回安全规则摘要（供前端 UI 展示）。"""
    from vermes_cli.mcp_security import _SHELL_INTERPRETERS, _IOC_SUBSTRINGS
    return {
        "shell_interpreters": sorted(_SHELL_INTERPRETERS),
        "ioc_count": len(_IOC_SUBSTRINGS),
        "checks": [
            {"id": "ioc_blocklist", "name": "已知攻击 IOC 阻断", "description": "硬编码的攻击者 SSH 密钥/源 IP"},
            {"id": "egress_exfil", "name": "网络外联+数据外泄", "description": "shell 解释器调用 curl/wget/nc 等网络工具"},
            {"id": "persistence", "name": "OS 持久化后门", "description": "写入 authorized_keys/PAM/sudoers/cron/shell rc"},
        ],
    }


def _get_installed_names() -> set:
    """获取已安装的 MCP 服务器名称集合。"""
    try:
        from vermes_cli.mcp_config import _get_mcp_servers
        return set(_get_mcp_servers().keys())
    except Exception:
        return set()


def register_to(app):
    """Register MCP catalog routes on the FastAPI app."""
    app.include_router(mcp_bp)
