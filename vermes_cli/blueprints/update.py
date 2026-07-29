"""Blueprint: Update（应用更新管理）

Vermes update endpoints — download, apply, rollback, and progress tracking.
"""

import asyncio
import json
import logging
import os
import platform as _platform
import shutil
import time

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from vermes_cli.update_manager import (
    UpdateStatus,
    download_with_progress,
    extract_to_staging,
    backup_current_version,
    backup_user_data,
    check_data_compatibility,
    get_data_version,
    list_backups,
    rollback_to_version,
    get_progress,
    get_current_version,
    STAGING_DIR,
    PENDING_FILE,
    UPDATE_DIR,
    _set_progress,
    _reset_progress,
    _progress_event,
    # Agent 框架更新
    get_agent_version,
    agent_download_and_verify,
    agent_apply_update,
    agent_rollback,
    agent_list_backups,
)

_log = logging.getLogger(__name__)


# ── models ─────────────────────────────────────────────────────

class UpdateDownloadRequest(BaseModel):
    version: str
    url: str
    sha256: str = ""           # 预期 SHA256（可选）
    min_data_version: str = ""  # 最低数据版本（可选）


class UpdateApplyRequest(BaseModel):
    version: str


class UpdateRollbackRequest(BaseModel):
    version: str


# ── Agent 框架更新 models ─────────────────────────────────────

class AgentUpdateRequest(BaseModel):
    """Agent 框架更新请求"""
    version: str
    url: str                    # vbit.top 镜像地址
    sha256: str = ""            # 预期 SHA256
    mirror_url: str = ""        # GitHub 备用地址


class AgentRollbackRequest(BaseModel):
    """Agent 框架回滚请求"""
    version: str


# ── route handlers ─────────────────────────────────────────────

async def update_download(body: UpdateDownloadRequest):
    """下载新版本，SSE 流式返回进度

    返回 text/event-stream，每个事件格式：
    data: {"status": "downloading", "progress": 45.2, "message": "...", ...}

    最终事件：
    data: {"status": "done", "progress": 100, "message": "下载完成"}
    或
    data: {"status": "error", "error": "..."}
    """
    url = body.url
    if not url:
        raise HTTPException(status_code=400, detail="缺少下载地址")

    is_dmg = url.endswith(".dmg")
    is_zip = url.endswith(".zip")
    if not is_dmg and not is_zip:
        raise HTTPException(status_code=400, detail="不支持的文件格式（仅支持 .dmg 和 .zip）")

    # 兼容性检查
    if body.min_data_version:
        if not check_data_compatibility(body.min_data_version):
            raise HTTPException(
                status_code=409,
                detail=f"当前数据版本不兼容，需要 {body.min_data_version}+，当前 {get_data_version()}"
            )

    filename = url.split("/")[-1]
    download_path = str(UPDATE_DIR / filename)

    await _reset_progress()
    await _set_progress(version=body.version, message="准备下载...")

    async def sse_stream():
        try:
            # 确保目录存在
            UPDATE_DIR.mkdir(parents=True, exist_ok=True)

            # 下载
            yield f"data: {json.dumps(get_progress())}\n\n"

            sha256 = await download_with_progress(
                url=url,
                dest_path=download_path,
                expected_sha256=body.sha256,
            )

            # 解压
            yield f"data: {json.dumps(get_progress())}\n\n"

            await extract_to_staging(download_path)

            # 清理下载文件
            if os.path.exists(download_path):
                os.remove(download_path)

            await _set_progress(
                status=UpdateStatus.DONE,
                progress=100,
                message="下载完成，准备应用更新",
            )
            yield f"data: {json.dumps(get_progress())}\n\n"

        except Exception as e:
            _log.exception(f"[Update] 下载失败: {e}")
            await _set_progress(
                status=UpdateStatus.ERROR,
                error=str(e),
                message=f"下载失败: {e}",
            )
            yield f"data: {json.dumps(get_progress())}\n\n"
            # 清理残留
            for p in [download_path, str(STAGING_DIR)]:
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p)
                    elif os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def update_progress_sse():
    """SSE 端点：实时推送更新进度

    用于在下载过程中持续获取进度更新。
    """
    async def event_stream():
        last_data = None
        while True:
            await _progress_event.wait()
            _progress_event.clear()

            data = json.dumps(get_progress())
            if data != last_data:
                yield f"data: {data}\n\n"
                last_data = data

            if get_progress()["status"] in ("done", "error", "idle"):
                break

            await asyncio.sleep(0.1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def update_apply(body: UpdateApplyRequest):
    """应用更新：备份 → 原子替换 → 写 pending.json → shutdown

    更新流程：
    1. 备份当前版本到 ~/.vermes/backup/v{version}/
    2. 备份用户数据到 ~/.vermes/backup/user-data-backup.tar.gz
    3. 原子替换应用文件
    4. 写 pending.json（含迁移信息）
    5. 触发 shutdown → 重启时完成最后步骤
    """
    import json as _json

    if not STAGING_DIR.exists():
        raise HTTPException(status_code=400, detail="没有待应用的更新（staging 目录不存在）")

    current_version = get_current_version()

    try:
        # 1. 备份当前版本
        await _set_progress(status=UpdateStatus.BACKING_UP, message="正在备份当前版本...")
        backup_path = backup_current_version(current_version)
        if backup_path:
            _log.info(f"[Update] 已备份 v{current_version} 到 {backup_path}")

        # 2. 备份用户数据
        data_backup = backup_user_data()
        if data_backup:
            _log.info(f"[Update] 已备份用户数据到 {data_backup}")

        # 3. 写 pending.json（重启后由 gui_app 应用）
        pending = {
            "version": body.version,
            "staging_path": str(STAGING_DIR),
            "platform": _platform.system(),
            "timestamp": time.time(),
            "previous_version": current_version,
            "data_version": get_data_version(),
        }
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        PENDING_FILE.write_text(_json.dumps(pending, indent=2), encoding="utf-8")

        _log.info(f"[Update] pending.json 已写入，准备 shutdown 重启...")

        # 4. 触发 shutdown
        try:
            from vermes_cli.shutdown_signal import shutdown_event
            shutdown_event.set()
        except Exception:
            pass

        return {"ok": True, "message": "更新将在重启后生效", "backup_path": backup_path}

    except Exception as e:
        _log.exception(f"[Update] 应用更新失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def get_backups():
    """列出所有可用的备份版本（用于回滚）"""
    return {
        "ok": True,
        "backups": list_backups(),
        "current_version": get_current_version(),
    }


async def update_rollback(body: UpdateRollbackRequest):
    """回滚到指定版本

    1. 从备份中找到目标版本
    2. 写入 pending.json（is_rollback=True）
    3. shutdown → 重启时应用旧版本
    """
    try:
        success = rollback_to_version(body.version)
        if not success:
            raise HTTPException(status_code=500, detail="回滚失败")

        # 触发 shutdown
        try:
            from vermes_cli.shutdown_signal import shutdown_event
            shutdown_event.set()
        except Exception:
            pass

        return {"ok": True, "message": f"将在重启后回滚到 v{body.version}"}

    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        _log.exception(f"[Update] 回滚失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Agent 框架更新端点 ───────────────────────────────────────

AGENT_LATEST_URL = "https://vbit.top/vermes/agent/latest.json"


async def agent_check_update():
    """检查 Agent 框架是否有新版本

    从 vbit.top 镜像获取 latest.json，与本地版本比较。
    """
    import httpx

    current = get_agent_version()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(AGENT_LATEST_URL, params={"t": str(int(time.time()))})
            resp.raise_for_status()
            info = resp.json()
    except Exception as e:
        _log.warning(f"[AgentUpdate] 检查更新失败: {e}")
        return {"ok": True, "has_update": False, "current_version": current, "error": str(e)}

    latest = info.get("version", "0.0.0")
    has_update = _is_newer(latest, current)

    return {
        "ok": True,
        "has_update": has_update,
        "current_version": current,
        "latest_version": latest,
        "download_url": info.get("download_url", ""),
        "sha256": info.get("sha256", ""),
        "mirror_url": info.get("github_url", ""),
        "changelog": info.get("changelog", []),
        "size_bytes": info.get("size_bytes", 0),
    }


def _is_newer(latest: str, current: str) -> bool:
    """比较版本号，latest > current 返回 True"""
    def parse(v):
        return tuple(int(p) for p in v.lstrip("v").split(".")[:3] if p.isdigit())
    try:
        return parse(latest) > parse(current)
    except Exception:
        return False


async def agent_update_download(body: AgentUpdateRequest):
    """下载并应用 Agent 框架更新（一体化端点）

    流程：下载 → 校验 → 解压 → 替换 → 重启 gateway
    不需要重启壳（Electron/pywebview 继续运行）

    返回 SSE 流式进度。
    """
    if not body.url:
        raise HTTPException(status_code=400, detail="缺少下载地址")

    if not body.url.endswith(".tar.gz"):
        raise HTTPException(status_code=400, detail="Agent 框架更新仅支持 .tar.gz 格式")

    async def sse_stream():
        try:
            # 1. 下载 + 校验
            tar_path = await agent_download_and_verify(
                version=body.version,
                url=body.url,
                sha256=body.sha256,
                mirror_url=body.mirror_url,
            )
            yield f"data: {json.dumps(get_progress())}\n\n"

            # 2. 应用更新
            result = await agent_apply_update(body.version, tar_path)
            yield f"data: {json.dumps(get_progress())}\n\n"

            # 3. 触发 gateway 重启（不是整个应用重启）
            try:
                from vermes_cli.shutdown_signal import restart_event
                restart_event.set()
            except ImportError:
                # 没有 restart_event，走 shutdown
                try:
                    from vermes_cli.shutdown_signal import shutdown_event
                    shutdown_event.set()
                except Exception:
                    pass

        except Exception as e:
            _log.exception(f"[AgentUpdate] 更新失败: {e}")
            await _set_progress(
                status=UpdateStatus.ERROR,
                error=str(e),
                message=f"Agent 框架更新失败: {e}",
            )
            yield f"data: {json.dumps(get_progress())}\n\n"

    return StreamingResponse(
        sse_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def agent_get_backups():
    """列出 Agent 框架的可用备份版本"""
    return {
        "ok": True,
        "backups": agent_list_backups(),
        "current_version": get_agent_version(),
        "type": "agent",
    }


async def agent_update_rollback(body: AgentRollbackRequest):
    """回滚 Agent 框架到指定版本"""
    try:
        result = agent_rollback(body.version)
        # 触发 gateway 重启
        try:
            from vermes_cli.shutdown_signal import restart_event
            restart_event.set()
        except ImportError:
            try:
                from vermes_cli.shutdown_signal import shutdown_event
                shutdown_event.set()
            except Exception:
                pass
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        _log.exception(f"[AgentUpdate] 回滚失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── registration ───────────────────────────────────────────────

def register_to(app):
    """Register update routes on the FastAPI app."""
    # ── 应用更新（壳更新，下载 DMG/EXE）
    app.add_api_route(
        "/api/update/download", update_download, methods=["POST"], name="update_download"
    )
    app.add_api_route(
        "/api/update/progress", update_progress_sse, methods=["GET"], name="update_progress_sse"
    )
    app.add_api_route(
        "/api/update/apply", update_apply, methods=["POST"], name="update_apply"
    )
    app.add_api_route(
        "/api/update/backups", get_backups, methods=["GET"], name="get_backups"
    )
    app.add_api_route(
        "/api/update/rollback", update_rollback, methods=["POST"], name="update_rollback"
    )

    # ── Agent 框架更新（脑更新，下载 tar.gz，不重启壳）
    app.add_api_route(
        "/api/agent/check", agent_check_update, methods=["GET"], name="agent_check_update"
    )
    app.add_api_route(
        "/api/agent/update", agent_update_download, methods=["POST"], name="agent_update_download"
    )
    app.add_api_route(
        "/api/agent/backups", agent_get_backups, methods=["GET"], name="agent_get_backups"
    )
    app.add_api_route(
        "/api/agent/rollback", agent_update_rollback, methods=["POST"], name="agent_update_rollback"
    )


blueprint = None  # no APIRouter; uses register_to(app) pattern
