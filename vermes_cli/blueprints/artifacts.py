"""Blueprint: Artifacts（产物文件读取端点）

提供前端 ArtifactPanel 的文件读取能力。
安全：白名单三类根（cwd / ~/.vermes/ / /tmp/），路径规范化防穿越。
"""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, HTMLResponse, Response


def _allowed_roots():
    """返回允许读取的根目录列表（每次调用动态获取 cwd）"""
    return [
        Path.cwd().resolve(),
        (Path.home() / '.vermes').resolve(),
        Path('/tmp').resolve(),
    ]


# 安全响应头：防嗅探 + CSP sandbox（防产物 HTML 内脚本执行）
_SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': "default-src 'none'; img-src data:; style-src 'unsafe-inline'",
}


def _is_safe_path(path_str: str) -> Path:
    """路径安全校验：规范化 + 白名单根目录检查"""
    # 先把 tmp/ 前缀映射到 /tmp/（URL path 丢失前导 / 的常见情况）
    if path_str.startswith('tmp/') and not Path.cwd().joinpath('tmp').exists():
        path_str = '/' + path_str
    raw = Path(path_str)
    # 如果是绝对路径直接 resolve，否则相对于 cwd
    resolved = raw.resolve() if raw.is_absolute() else (Path.cwd() / raw).resolve()
    for root in _allowed_roots():
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise HTTPException(status_code=403, detail=f"路径不在允许范围内: {path_str}")


_MIME_MAP = {
    '.md': 'text/markdown',
    '.html': 'text/html',
    '.htm': 'text/html',
    '.txt': 'text/plain',
    '.csv': 'text/csv',
    '.json': 'application/json',
    '.py': 'text/plain',
    '.js': 'text/plain',
    '.ts': 'text/plain',
    '.sh': 'text/plain',
    '.yaml': 'text/plain',
    '.yml': 'text/plain',
    '.ini': 'text/plain',
    '.cfg': 'text/plain',
    '.log': 'text/plain',
    '.toml': 'text/plain',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
    # 办公/ScholarForge
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.pdf': 'application/pdf',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.doc': 'application/msword',
    '.xls': 'application/vnd.ms-excel',
    '.ppt': 'application/vnd.ms-powerpoint',
    # 制造业/mfgcad
    '.step': 'application/step',
    '.stp': 'application/step',
    '.stl': 'application/sla',
    '.obj': 'application/wavefront-obj',
    '.fcdoc': 'application/octet-stream',
    '.dxf': 'application/dxf',
    '.gcode': 'text/plain',
    '.iges': 'application/iges',
    '.3mf': 'model/3mf',
    '.gltf': 'model/gltf+json',
}

_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def register_to(app):
    @app.get('/api/v1/workspace/tree')
    async def workspace_tree(path: str = ''):
        """列出工作目录文件树（单层），返回子项列表"""
        if path:
            safe = _is_safe_path(path)
        else:
            safe = Path.cwd().resolve()
        if not safe.exists():
            raise HTTPException(status_code=404, detail=f"路径不存在: {path}")
        if not safe.is_dir():
            raise HTTPException(status_code=400, detail=f"不是目录: {path}")

        items = []
        try:
            for entry in sorted(safe.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
                # 跳过隐藏文件和常见忽略目录
                if entry.name.startswith('.'):
                    continue
                if entry.is_dir() and entry.name in {'node_modules', '__pycache__', '.git', 'dist', 'build', '.venv', 'venv'}:
                    continue
                rel = str(entry.relative_to(Path.cwd().resolve())) if str(entry).startswith(str(Path.cwd().resolve())) else str(entry)
                items.append({
                    'name': entry.name,
                    'path': rel,
                    'is_dir': entry.is_dir(),
                    'size': entry.stat().st_size if entry.is_file() else 0,
                    'ext': entry.suffix.lower() if entry.is_file() else '',
                })
        except PermissionError:
            raise HTTPException(status_code=403, detail="无权限读取该目录")
        return {'items': items, 'current': str(safe.relative_to(Path.cwd().resolve())) if str(safe).startswith(str(Path.cwd().resolve())) else str(safe)}

    @app.get('/api/v1/artifacts/{path:path}')
    async def serve_artifact(path: str, request: Request):
        """读取产物文件，返回对应 MIME 类型"""
        safe_path = _is_safe_path(path)

        if not safe_path.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {path}")

        if not safe_path.is_file():
            raise HTTPException(status_code=400, detail=f"不是文件: {path}")

        file_size = safe_path.stat().st_size
        if file_size > _MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"文件过大 ({file_size // 1024 // 1024}MB)，上限 50MB")

        ext = safe_path.suffix.lower()
        mime = _MIME_MAP.get(ext, 'application/octet-stream')

        # 图片直接返回二进制
        if mime.startswith('image/'):
            with open(safe_path, 'rb') as f:
                return Response(content=f.read(), media_type=mime, headers=_SECURITY_HEADERS)

        # 文本类返回内容
        try:
            with open(safe_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            # 二进制文件 fallback
            with open(safe_path, 'rb') as f:
                return Response(content=f.read(), media_type=mime, headers=_SECURITY_HEADERS)

        return PlainTextResponse(content, media_type=mime, headers=_SECURITY_HEADERS)
