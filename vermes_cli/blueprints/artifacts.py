"""Blueprint: Artifacts（产物文件读取端点）

提供前端 ArtifactPanel 的文件读取能力。
安全：白名单三类根（cwd / ~/.vermes/ / /tmp/），路径规范化防穿越。
"""
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, HTMLResponse, Response, JSONResponse
import base64
import json
import time
import sqlite3
import hashlib
import threading
import difflib
from contextlib import contextmanager


def _allowed_roots():
    """返回允许读取的根目录列表（每次调用动态获取 cwd）"""
    return [
        Path.cwd().resolve(),
        (Path.home() / '.vermes').resolve(),
        Path('/tmp').resolve(),
        # 用户常用目录：产物文件常在 Desktop/Downloads/Documents
        (Path.home() / 'Desktop').resolve(),
        (Path.home() / 'Downloads').resolve(),
        (Path.home() / 'Documents').resolve(),
    ]


# 安全响应头：防嗅探 + CSP sandbox（防产物 HTML 内脚本执行）
_SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'Content-Security-Policy': "default-src 'none'; img-src data:; style-src 'unsafe-inline'",
}


def _is_safe_path(path_str: str) -> Path:
    """路径安全校验：规范化 + 白名单根目录检查"""
    # 展开 ~ / ~user（前端消息流常出现 ~/Documents/... 这类用户目录路径）
    path_str = os.path.expanduser(path_str)
    # 先把 tmp/ 前缀映射到 /tmp/（URL path 丢失前导 / 的常见情况）
    if path_str.startswith('tmp/') and not Path.cwd().joinpath('tmp').exists():
        path_str = '/' + path_str

    def _check(p: str) -> Optional[Path]:
        raw = Path(p)
        resolved = raw.resolve() if raw.is_absolute() else (Path.cwd() / raw).resolve()
        for root in _allowed_roots():
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        return None

    ok = _check(path_str)
    if ok is not None:
        # 若路径实际存在，直接返回
        if ok.exists():
            return ok
        # 裸相对路径（agent 工具常把产物写到 /tmp 或 ~/.vermes，却在结果文本里只报裸文件名，
        # 而桌面端后端进程 cwd 可能是 /，导致按 cwd 解析不到真实文件）：
        # 依次在常见产物目录下找同名文件，找到即返回。
        if not path_str.startswith('/') and not path_str.startswith('~'):
            # 1) 直接在常见产物根目录下找同名文件
            for fallback_root in (Path('/tmp'), Path.home() / '.vermes', Path.home()):
                candidate = _check(str(fallback_root / path_str))
                if candidate is not None and candidate.exists():
                    return candidate
            # 2) 递归搜索子目录（agent 常写到 ~/.vermes/logs/ 等子目录，
            #    但结果文本只报裸文件名如 agent.log）
            bare_name = Path(path_str).name
            for search_root in (Path.home() / '.vermes', Path('/tmp')):
                try:
                    for hit in search_root.rglob(bare_name):
                        # 只取文件、且在白名单内、且不超过 3 层深度
                        if hit.is_file():
                            verified = _check(str(hit))
                            if verified is not None and verified.exists():
                                return verified
                except (PermissionError, OSError):
                    continue
        return ok

    # 兜底：agent 常把桌面/下载/文档写成根级绝对路径（/Desktop/.. 而非 ~/Desktop/..），
    # 这类路径明显是 home 同名目录的误写，重定向到 Path.home() 下再校验，避免前端读产物时 403。
    # 仅精确匹配这三个根级目录名，不波及 /Desktopfoo 等。
    home_redirects = {
        '/Desktop': Path.home() / 'Desktop',
        '/Downloads': Path.home() / 'Downloads',
        '/Documents': Path.home() / 'Documents',
    }
    for prefix, target in home_redirects.items():
        if path_str == prefix or path_str.startswith(prefix + '/'):
            redirected = target / path_str[len(prefix):].lstrip('/')
            ok = _check(str(redirected))
            if ok is not None:
                return ok

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


# ─────────────────────────────────────────────────────────────
# A1 版本快照基建（复用 ScholarForge snapshots 的 LRU 裁剪模式）
# 存储：~/.vermes/artifacts_versions.db（独立库，不与 scholarforge.db 耦合）
# 表：artifact_versions(artifact_path, session_id, version, content_hash, kind, content, size, created_at, note)
# 触发：write_artifact_content 写回前自动快照「覆盖前状态」；可 /versions 列表 /revert 回退 /diff 对比
# ─────────────────────────────────────────────────────────────
_VERSIONS_DB_PATH = os.path.expanduser("~/.vermes/artifacts_versions.db")
_versions_lock = threading.Lock()
# 单产物版本上限：超出按 version 升序淘汰最旧（复用 ScholarForge create_snapshot 的 LRU 裁剪模式）。
# 方案稿写「8 条」，但真实 ScholarForge 上限为 30；产物编辑频次高，取 30 避免历史过快丢失，可调。
MAX_VERSIONS_PER_ARTIFACT = 30
# 超此大小的产物不进版本库（避免大二进制把 SQLite 撑爆），仅记元信息占位
_MAX_VERSIONED_SIZE = 25 * 1024 * 1024  # 25MB


@contextmanager
def _versions_conn():
    """线程安全连接（仿 ScholarForge get_conn）"""
    conn = sqlite3.connect(_VERSIONS_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_versions_db():
    """建表 — 幂等"""
    with _versions_lock, _versions_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_path TEXT NOT NULL,
                session_id TEXT,
                version INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'text',
                content BLOB NOT NULL,
                size INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                note TEXT DEFAULT ''
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_av_path_ver "
            "ON artifact_versions(artifact_path, version)"
        )


def _content_kind(b: bytes) -> str:
    """判断内容是否可被版本 diff/预览：UTF-8 解码成功即 text，否则 binary"""
    try:
        b.decode('utf-8')
        return 'text'
    except UnicodeDecodeError:
        return 'binary'


def _record_version(artifact_path: Path, prev_bytes: bytes, session_id: str | None, note: str) -> int:
    """写回前自动快照「覆盖前状态」。返回新建版本 id。

    调用方应已确认 prev_bytes 与即将写入内容不同（避免无变化刷版本）。
    超大文件仅记元信息占位（kind='oversize'），不存内容、不可回退。
    """
    _init_versions_db()
    with _versions_lock, _versions_conn() as conn:
        version = conn.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM artifact_versions WHERE artifact_path=?",
            (str(artifact_path),)
        ).fetchone()[0]
        if len(prev_bytes) > _MAX_VERSIONED_SIZE:
            cur = conn.execute(
                "INSERT INTO artifact_versions (artifact_path, session_id, version, content_hash, kind, content, size, created_at, note) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (str(artifact_path), session_id, version, hashlib.sha256(prev_bytes).hexdigest(),
                 'oversize', b'', len(prev_bytes), int(time.time()), note + '（超 25MB 未存内容）')
            )
            _evict_versions(conn, str(artifact_path))
            return cur.lastrowid
        cur = conn.execute(
            "INSERT INTO artifact_versions (artifact_path, session_id, version, content_hash, kind, content, size, created_at, note) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (str(artifact_path), session_id, version, hashlib.sha256(prev_bytes).hexdigest(),
             _content_kind(prev_bytes), prev_bytes, len(prev_bytes), int(time.time()), note)
        )
        _evict_versions(conn, str(artifact_path))
        return cur.lastrowid


def _evict_versions(conn, artifact_path: str):
    """超出 MAX_VERSIONS_PER_ARTIFACT 时按 version 升序淘汰最旧（仿 ScholarForge）"""
    count = conn.execute("SELECT COUNT(*) FROM artifact_versions WHERE artifact_path=?", (artifact_path,)).fetchone()[0]
    if count > MAX_VERSIONS_PER_ARTIFACT:
        excess = count - MAX_VERSIONS_PER_ARTIFACT
        old = conn.execute(
            "SELECT id FROM artifact_versions WHERE artifact_path=? ORDER BY version ASC LIMIT ?",
            (artifact_path, excess)
        ).fetchall()
        for (oid,) in old:
            conn.execute("DELETE FROM artifact_versions WHERE id=?", (oid,))


def _check_origin(request: Request):
    """纵深防御：校验请求来源是否为本应用（挡掉跨站调用）"""
    origin = request.headers.get('origin', '')
    host = request.headers.get('host', '')
    # Electron 无 origin（file:// 协议）或 origin 包含 host（同源）
    if origin and host:
        # 提取 origin 的 host 部分比对
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        if parsed.hostname and parsed.hostname not in ('localhost', '127.0.0.1', '0.0.0.0'):
            raise HTTPException(status_code=403, detail="跨站请求被拒绝")
    # 无 origin（Electron 内部请求）或 localhost 来源 → 放行


def _apply_docx_paragraphs(path, paragraphs):
    """P4-4 T1：把编辑后的段落写回 docx（python-docx 已随环境存在）。

    按段落 index 回填，保结构（空段/非编辑段不动）。仅处理正文段落，表格不在 v1 范围。
    """
    from docx import Document
    doc = Document(str(path))
    text_by_index = {int(p['i']): p.get('text', '') for p in paragraphs if 'i' in p}
    updated = 0
    for i, para in enumerate(doc.paragraphs):
        if i in text_by_index and para.text != text_by_index[i]:
            para.text = text_by_index[i]
            updated += 1
    doc.save(str(path))
    return updated


# ─────────────────────────────────────────────────────────────
# A2 选区编辑（局部重生成）的「模型重生成」接缝（模块级）
# 默认未注入真实 LLM 单轮补全时抛清晰错误（不静默假成功）。
# chat 蓝图在启动时调用 register_region_regenerator(...) 注入真实实现：
#   fn(selection, instruction, before, after, model_hint) -> new_text
# 复用既有 _resolve_model_provider / 单轮补全。
# ─────────────────────────────────────────────────────────────
_REGION_REGENERATOR = None


def register_region_regenerator(fn):
    """注入真实「选区→重生成」实现。fn(selection, instruction, before, after, model_hint)->new_text。"""
    global _REGION_REGENERATOR
    _REGION_REGENERATOR = fn


def _regenerate_region(selection, instruction, before, after, model_hint=None):
    if _REGION_REGENERATOR is None:
        raise RuntimeError(
            "局部重生成未接入模型：请在 chat 蓝图启动时调用 "
            "artifacts.register_region_regenerator(...) 注入单轮补全实现"
        )
    return _REGION_REGENERATOR(selection, instruction, before, after, model_hint)


def _regenerate_pdf_from_md(path, md):
    """P4-4 T1：用 pandoc 从 markdown 重生成 pdf（pdf 不编辑回存，只重生成）。

    PDF 引擎选择：优先 weasyprint（纯 Python，可随 .venv 打包，无需 LaTeX 全家桶）；
    否则回退 pandoc 默认引擎（LaTeX，需 pdflatex 等）。二者皆缺则报清晰错误。
    """
    import shutil
    import subprocess
    import tempfile

    pandoc = shutil.which('pandoc')
    if not pandoc:
        raise RuntimeError('pandoc 未安装，无法重生成 pdf（请 brew install pandoc 或 apt install pandoc）')

    engine_args = []
    # weasyprint 优先：轻量、可打包进 .venv；需其 Python 模块可正常 import
    if shutil.which('weasyprint'):
        try:
            import weasyprint  # noqa: F401
            engine_args = ['--pdf-engine=weasyprint']
        except Exception:
            pass

    with tempfile.NamedTemporaryFile('w', suffix='.md', delete=False, encoding='utf-8') as f:
        f.write(md or '')
        md_path = f.name
    try:
        proc = subprocess.run(
            [pandoc, '-f', 'markdown', '-t', 'pdf', *engine_args, '-o', str(path), md_path],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(f'pandoc 重生成失败: {proc.stderr[:300]}')
    finally:
        os.unlink(md_path)


def register_to(app):
    @app.get('/api/v1/workspace/tree')
    async def workspace_tree(path: str = '', request: Request = None):
        """列出工作目录文件树（单层），返回子项列表"""
        _check_origin(request)
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

    @app.get('/api/v1/artifacts/{path:path}/editable')
    async def editable_units(path: str, request: Request):
        """P4-4 T1 协同编辑：返回可编辑单元。
        - docx：python-docx 提取段落列表 [{i, text}]（含空段，保结构）
        - pdf：若同目录存在同名 .md 源，返回 {regenerable:true, source_md}
        - xlsx：前端用 SheetJS 就地管理，返回 {managed:'frontend'}
        """
        _check_origin(request)
        safe_path = _is_safe_path(path)
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
        ext = safe_path.suffix.lower()
        if ext == '.docx':
            try:
                from docx import Document
            except ImportError:
                return JSONResponse({'type': 'docx', 'error': 'python-docx 未安装'}, status_code=501)
            try:
                doc = Document(str(safe_path))
                paragraphs = [{'i': i, 'text': p.text} for i, p in enumerate(doc.paragraphs)]
                return JSONResponse({'type': 'docx', 'paragraphs': paragraphs})
            except Exception as e:
                return JSONResponse({'type': 'docx', 'error': f'解析失败: {e}'}, status_code=422)
        if ext == '.pdf':
            md_src = safe_path.with_suffix('.md')
            if md_src.exists():
                try:
                    return JSONResponse({'type': 'pdf', 'regenerable': True, 'source_md': md_src.read_text(encoding='utf-8')})
                except Exception as e:
                    return JSONResponse({'type': 'pdf', 'regenerable': True, 'error': f'读取 md 源失败: {e}'}, status_code=422)
            return JSONResponse({'type': 'pdf', 'regenerable': False, 'reason': '未找到同名 .md 源，无法重生成'})
        if ext == '.xlsx':
            return JSONResponse({'type': 'xlsx', 'managed': 'frontend'})
        return JSONResponse({'type': 'unsupported', 'reason': f'不支持的可编辑类型: {ext}'}, status_code=415)

    @app.get('/api/v1/artifacts/{path:path}/resolve')
    async def resolve_artifact(path: str, request: Request):
        """返回产物文件的绝对路径，供桌面端 shell.showItemInFolder 使用"""
        _check_origin(request)
        safe_path = _is_safe_path(path)
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
        return {'path': str(safe_path), 'name': safe_path.name, 'size': safe_path.stat().st_size}

    # ═════════════════════════════════════════════════════════════
    # A1 版本快照端点：/versions 列表 · /versions/{vid} 取内容 · /revert 回退 · /diff 对比
    # （注册在 serve_artifact 通配 GET 之前，避免被 {path:path} 通配抢匹配）
    # ═════════════════════════════════════════════════════════════
    @app.get('/api/v1/artifacts/{path:path}/versions')
    async def list_artifact_versions(path: str, request: Request):
        """列出某产物的全部历史版本（元信息，不含内容体）。"""
        _check_origin(request)
        safe_path = _is_safe_path(path)
        _init_versions_db()
        with _versions_conn() as conn:
            rows = conn.execute(
                "SELECT id, version, content_hash, kind, size, created_at, note, session_id "
                "FROM artifact_versions WHERE artifact_path=? ORDER BY version DESC",
                (str(safe_path),)
            ).fetchall()
        return {'artifact': str(safe_path), 'versions': [
            {'id': r['id'], 'version': r['version'], 'content_hash': r['content_hash'],
             'kind': r['kind'], 'size': r['size'], 'created_at': r['created_at'],
             'note': r['note'], 'session_id': r['session_id'],
             'restorable': r['kind'] in ('text', 'binary')}
            for r in rows
        ]}

    @app.get('/api/v1/artifacts/{path:path}/versions/{vid:int}')
    async def get_artifact_version(path: str, vid: int, request: Request):
        """取单个版本完整内容：text 直接返回文本，binary 返回 base64，oversize 不可回退。"""
        _check_origin(request)
        safe_path = _is_safe_path(path)
        _init_versions_db()
        with _versions_conn() as conn:
            row = conn.execute(
                "SELECT id, version, kind, content, note, created_at FROM artifact_versions "
                "WHERE id=? AND artifact_path=?",
                (vid, str(safe_path))
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"版本不存在: v{vid}")
            kind = row['kind']
            if kind == 'oversize':
                return {'id': row['id'], 'version': row['version'], 'kind': kind,
                        'restorable': False, 'note': row['note'], 'created_at': row['created_at']}
            raw = bytes(row['content'])
            if kind == 'text':
                content = raw.decode('utf-8')
            else:
                content = base64.b64encode(raw).decode('ascii')
            return {'id': row['id'], 'version': row['version'], 'kind': kind,
                    'restorable': True, 'content': content, 'note': row['note'],
                    'created_at': row['created_at']}

    @app.post('/api/v1/artifacts/{path:path}/versions/{vid:int}/revert')
    async def revert_artifact_version(path: str, vid: int, request: Request):
        """回退到指定版本：写回该版本内容，并自动快照「回退前状态」使回退本身可撤销。"""
        _check_origin(request)
        safe_path = _is_safe_path(path)
        session_id = request.headers.get('X-Session-Id') or request.query_params.get('session_id')
        _init_versions_db()
        with _versions_conn() as conn:
            row = conn.execute(
                "SELECT id, version, kind, content FROM artifact_versions WHERE id=? AND artifact_path=?",
                (vid, str(safe_path))
            ).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"版本不存在: v{vid}")
            if row['kind'] == 'oversize':
                raise HTTPException(status_code=409, detail="该版本超 25MB 未存内容，不可回退")
            target = bytes(row['content'])
        # 回退前快照当前状态（让回退可撤销）
        try:
            current = safe_path.read_bytes()
        except OSError:
            current = b''
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_bytes(target)
        if current != target:
            _record_version(safe_path, current, session_id, note=f"回退到 v{row['version']}（回退前快照）")
        return {'ok': True, 'reverted_to': row['version'], 'path': str(safe_path),
                'size': safe_path.stat().st_size}

    @app.get('/api/v1/artifacts/{path:path}/versions/{vid:int}/diff')
    async def diff_artifact_version(path: str, vid: int, request: Request, base: str = 'current'):
        """对比某版本与基准：base=current（默认，文件现状）或另一版本 id。仅 text 可 diff。"""
        _check_origin(request)
        safe_path = _is_safe_path(path)
        _init_versions_db()
        with _versions_conn() as conn:
            ver = conn.execute(
                "SELECT id, version, kind, content FROM artifact_versions WHERE id=? AND artifact_path=?",
                (vid, str(safe_path))
            ).fetchone()
            if not ver:
                raise HTTPException(status_code=404, detail=f"版本不存在: v{vid}")
            if ver['kind'] != 'text':
                return {'diffable': False, 'reason': '仅文本类产物支持 diff'}
            ver_text = bytes(ver['content']).decode('utf-8')
            if base == 'current':
                try:
                    base_text = safe_path.read_text(encoding='utf-8')
                except (OSError, UnicodeDecodeError):
                    return {'diffable': False, 'reason': '当前文件不可作为文本基准'}
                base_label, ver_label = '当前', f"v{ver['version']}"
            else:
                try:
                    base_id = int(base)
                except ValueError:
                    raise HTTPException(status_code=400, detail="base 须为 'current' 或版本 id")
                brow = conn.execute(
                    "SELECT version, kind, content FROM artifact_versions WHERE id=? AND artifact_path=?",
                    (base_id, str(safe_path))
                ).fetchone()
                if not brow or brow['kind'] != 'text':
                    return {'diffable': False, 'reason': '基准版本不存在或非文本'}
                base_text = bytes(brow['content']).decode('utf-8')
                base_label, ver_label = f"v{brow['version']}", f"v{ver['version']}"
        diff_lines = difflib.unified_diff(
            base_text.splitlines(), ver_text.splitlines(),
            fromfile=base_label, tofile=ver_label, lineterm=''
        )
        return {'diffable': True, 'base': base_label, 'version': ver_label,
                'diff': '\n'.join(diff_lines)}

    # ═══════════════════════════════════════════════════════════
    # A2 选区编辑（局部重生成）：/patch 把「选区上下文 + 修改意图」交给 regenerator，
    # 只重生成选区并写回（文本类复用 A1 自动快照；docx 复用 _apply_docx_paragraphs）。
    # 关键设计：选区锚点（行号区间 / 文本指纹 / 段落 index），避免「整文件重发」。
    # regenerator 接缝在模块级定义：artifacts.register_region_regenerator(...) 注入真实实现。
    # ═══════════════════════════════════════════════════════════

    @app.post('/api/v1/artifacts/{path:path}/patch')
    async def patch_artifact_region(path: str, request: Request):
        """A2 选区编辑（局部重生成）。

        请求体 JSON：
          anchor:      文本类 {type:'line_range', start, end} | {type:'text_fingerprint', text}
                      docx   {type:'block_index', index}
          selection:   选中的原文（指纹校验 / 回显）
          instruction: 修改意图（必填）
          session_id / model: 可选
        返回：{ok, type, before, after, line_range|block_index, path}
        """
        _check_origin(request)
        safe_path = _is_safe_path(path)
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
        try:
            body = json.loads((await request.body()).decode('utf-8'))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}")
        anchor = body.get('anchor') or {}
        selection = body.get('selection', '') or ''
        instruction = (body.get('instruction') or '').strip()
        session_id = (body.get('session_id') or request.headers.get('X-Session-Id')
                      or request.query_params.get('session_id'))
        model_hint = body.get('model')
        if not instruction:
            raise HTTPException(status_code=400, detail="instruction（修改意图）不能为空")

        ext = safe_path.suffix.lower()

        # ── docx：按段落 index 回填（复用 _apply_docx_paragraphs）──
        if ext == '.docx':
            if anchor.get('type') != 'block_index':
                raise HTTPException(status_code=400,
                                    detail="docx 局部重生成需 anchor.type='block_index'")
            idx = anchor.get('index')
            if not isinstance(idx, int) or idx < 0:
                raise HTTPException(status_code=400, detail="block_index 需为非负整数")
            try:
                new_text = _regenerate_region(selection, instruction, '', '', model_hint)
            except RuntimeError as e:
                raise HTTPException(status_code=501, detail=str(e))
            if not new_text:
                raise HTTPException(status_code=422, detail="模型返回为空，未执行替换")
            prev_bytes = safe_path.read_bytes()
            n = _apply_docx_paragraphs(safe_path, [{'i': idx, 'text': new_text}])
            if n > 0:
                _record_version(safe_path, prev_bytes, session_id,
                                note=f"docx 选区重生成 段{idx}")
            return {'ok': True, 'type': 'docx', 'updated': n,
                    'block_index': idx, 'path': str(safe_path)}

        # ── 文本类：md / code / html / csv / json / 其它 ──
        try:
            cur = safe_path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            raise HTTPException(status_code=415, detail="该文件非文本，无法选区重生成")
        lines = cur.split('\n')
        atype = anchor.get('type')
        if atype == 'line_range':
            start = anchor.get('start'); end = anchor.get('end')
            if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
                raise HTTPException(status_code=400,
                                    detail="line_range 需 start>=1 且 end>=start（均为行号）")
            region_start, region_end = start, end
        elif atype == 'text_fingerprint':
            fp = anchor.get('text') or selection
            if not fp:
                raise HTTPException(status_code=400,
                                    detail="text_fingerprint 需提供 anchor.text 或 selection")
            pos = cur.find(fp)
            if pos < 0:
                raise HTTPException(status_code=404, detail="未在当前文件中找到该选区指纹")
            region_start = cur.count('\n', 0, pos) + 1
            region_end = region_start + fp.count('\n')
        else:
            raise HTTPException(status_code=400,
                                detail="文本类需 anchor.type='line_range' 或 'text_fingerprint'")

        region = '\n'.join(lines[region_start - 1:region_end])
        before_ctx = '\n'.join(lines[max(0, region_start - 3):region_start - 1])
        after_ctx = '\n'.join(lines[region_end:min(len(lines), region_end + 2)])
        try:
            new_region = _regenerate_region(region, instruction, before_ctx, after_ctx, model_hint)
        except RuntimeError as e:
            raise HTTPException(status_code=501, detail=str(e))
        if not new_region:
            raise HTTPException(status_code=422, detail="模型返回为空，未执行替换")

        new_lines = lines[:region_start - 1] + new_region.split('\n') + lines[region_end:]
        new_text = '\n'.join(new_lines)
        prev_bytes = safe_path.read_bytes()
        try:
            safe_path.write_text(new_text, encoding='utf-8')
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"写入失败: {e}")
        if prev_bytes != new_text.encode('utf-8'):
            _record_version(safe_path, prev_bytes, session_id, note="选区重生成")
        return {'ok': True, 'type': 'text', 'before': region, 'after': new_region,
                'line_range': [region_start, region_end], 'path': str(safe_path)}

    @app.get('/api/v1/artifacts/{path:path}/preview')
    async def preview_artifact(path: str, request: Request):
        """Office 文档静态预览（零依赖）：当前支持 pptx，复用 python-pptx 抽取每页文本与图片。

        返回 JSON：{'kind':'pptx','pages':[{'i':页码,'text':文本,'images':[data_url...]}]}
        不支持的格式（doc/ppt 老二进制等）返回 {'kind':'unsupported'}，前端回退为下载卡片。
        """
        _check_origin(request)
        safe_path = _is_safe_path(path)
        if not safe_path.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
        ext = safe_path.suffix.lower()
        if ext == '.pptx':
            try:
                from pptx import Presentation
            except ImportError:
                return JSONResponse({'kind': 'unsupported', 'reason': 'python-pptx 未安装（请安装 office 能力：pip install python-pptx 或 vermes 的 office extra）'}, status_code=501)
            try:
                prs = Presentation(str(safe_path))
            except Exception as e:
                return JSONResponse({'kind': 'unsupported', 'reason': f'解析失败: {e}'}, status_code=422)
            pages = []
            for idx, slide in enumerate(prs.slides, 1):
                texts, images = [], []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        t = shape.text_frame.text.strip()
                        if t:
                            texts.append(t)
                    if shape.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE
                        try:
                            blob = shape.image.blob
                            img_ext = (shape.image.ext or 'png').lower()
                            mime = 'image/png' if img_ext == 'png' else ('image/jpeg' if img_ext in ('jpg', 'jpeg') else 'image/png')
                            images.append(f'data:{mime};base64,{base64.b64encode(blob).decode()}')
                        except Exception:
                            pass
                pages.append({'i': idx, 'text': '\n'.join(texts), 'images': images})
            return JSONResponse({'kind': 'pptx', 'pages': pages})
        return JSONResponse({'kind': 'unsupported', 'reason': '仅支持 pptx 预览'}, status_code=415)

    @app.get('/api/v1/artifacts/{path:path}')
    async def serve_artifact(path: str, request: Request):
        """读取产物文件，返回对应 MIME 类型"""
        _check_origin(request)
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

    @app.post('/api/v1/artifacts/{path:path}/content')
    async def write_artifact_content(path: str, request: Request):
        """回存产物文件内容（轻量可编辑右栏的"人改→保存"落点）。

        安全：复用产物白名单根 + 路径穿越防护 + 跨站校验；仅允许已存在的文本类文件回写，
        写入前再确认父目录存在。大小上限与读取一致（50MB）。
        """
        _check_origin(request)
        safe_path = _is_safe_path(path)

        if not safe_path.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {path}")
        if not safe_path.is_file():
            raise HTTPException(status_code=400, detail=f"不是文件: {path}")

        # A1：写回前抓取「覆盖前状态」，供自动快照
        prev_bytes = safe_path.read_bytes()
        session_id = request.headers.get('X-Session-Id') or request.query_params.get('session_id')

        body = await request.body()
        if len(body) > _MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail=f"内容过大（{len(body) // 1024 // 1024}MB），上限 50MB")

        ctype = request.headers.get('Content-Type', '') or ''
        # P4-4 T1：docx/pdf 走结构化 JSON 回存；其余（md/code/xlsx 字节）保持原文本/二进制写回
        if 'application/json' in ctype:
            try:
                payload = json.loads(body.decode('utf-8'))
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}")
            etype = payload.get('type')
            try:
                if etype == 'docx':
                    n = _apply_docx_paragraphs(safe_path, payload.get('paragraphs', []))
                    if n > 0:
                        _record_version(safe_path, prev_bytes, session_id, note=f"docx 编辑 {n} 段")
                    return {'ok': True, 'type': 'docx', 'updated': n, 'path': str(safe_path)}
                if etype == 'pdf':
                    _regenerate_pdf_from_md(safe_path, payload.get('md', ''))
                    _record_version(safe_path, prev_bytes, session_id, note="pdf 从 md 重生成")
                    return {'ok': True, 'type': 'pdf', 'path': str(safe_path), 'size': safe_path.stat().st_size}
            except RuntimeError as e:
                raise HTTPException(status_code=422, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"结构化回存失败: {e}")
            raise HTTPException(status_code=400, detail=f"不支持的结构化回存类型: {etype}")

        # 文本类以 UTF-8 写回；二进制（如 xlsx 被前端 SheetJS 重生成后回存）按字节写回
        try:
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                safe_path.write_text(body.decode('utf-8'), encoding='utf-8')
            except UnicodeDecodeError:
                safe_path.write_bytes(body)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"写入失败: {e}")

        # A1：覆盖前状态与写入后不同才记版本（无变化跳过，避免刷版本）
        if prev_bytes != body:
            _record_version(safe_path, prev_bytes, session_id, note="编辑保存")

        return {'ok': True, 'path': str(safe_path), 'size': safe_path.stat().st_size}
