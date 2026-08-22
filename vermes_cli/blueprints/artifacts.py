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
        Path.home() / '.vermes',
        Path('/tmp'),
    ]


def _is_safe_path(path_str: str) -> Path:
    """路径安全校验：规范化 + 白名单根目录检查"""
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
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
}

_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def register_to(app):
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
                return Response(content=f.read(), media_type=mime)

        # 文本类返回内容
        try:
            with open(safe_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError:
            # 二进制文件 fallback
            with open(safe_path, 'rb') as f:
                return Response(content=f.read(), media_type=mime)

        return PlainTextResponse(content, media_type=mime)
