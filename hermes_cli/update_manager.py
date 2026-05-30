"""
Vermes 自更新管理器

完整的应用更新系统，支持：
- 异步流式下载 + SSE 实时进度
- SHA256 校验和验证
- 原子替换（复制→验证→rename）
- 旧版本备份 + 用户数据备份
- 一键回滚到历史版本
- 数据迁移框架
- 跨平台支持（macOS / Windows）
"""

import asyncio
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ── 常量 ────────────────────────────────────────────────────────────────

HERMES_HOME = Path(os.environ.get("VERMES_HOME", os.path.expanduser("~/.vermes")))
UPDATE_DIR = HERMES_HOME / "update"
STAGING_DIR = UPDATE_DIR / "staging"
PENDING_FILE = UPDATE_DIR / "pending.json"
BACKUP_DIR = HERMES_HOME / "backup"
USER_DATA_BACKUP = BACKUP_DIR / "user-data-backup.tar.gz"
MAX_BACKUPS = 3  # 保留最近 3 个版本

# 用户数据目录列表（更新时需要备份）
USER_DATA_DIRS = [
    "sessions",
    "skills",
    "memories",
    "logs",
    "webview_data",
    "plugins",
    "update",  # 更新相关数据
    "backup",  # 备份目录
]
USER_DATA_FILES = [
    "config.yaml",
    ".env",
    "auth.json",
]

class UpdateStatus(str, Enum):
    IDLE = "idle"
    DOWNLOADING = "downloading"
    EXTRACTING = "extracting"
    VERIFYING = "verifying"
    BACKING_UP = "backing_up"
    APPLYING = "applying"
    MIGRATING = "migrating"
    DONE = "done"
    ERROR = "error"


@dataclass
class UpdateProgress:
    status: UpdateStatus = UpdateStatus.IDLE
    progress: float = 0.0  # 0-100
    message: str = ""
    error: str = ""
    version: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed_bps: float = 0.0  # bytes per second
    eta_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "progress": round(self.progress, 1),
            "message": self.message,
            "error": self.error,
            "version": self.version,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "speed_bps": round(self.speed_bps, 0),
            "eta_seconds": round(self.eta_seconds, 0),
        }


# 全局进度状态（供 SSE 端点读取）
_current_progress = UpdateProgress()
_progress_event = asyncio.Event()
_progress_lock = asyncio.Lock()


def get_progress() -> Dict[str, Any]:
    return _current_progress.to_dict()


async def _set_progress(**kwargs):
    async with _progress_lock:
        global _current_progress
        for k, v in kwargs.items():
            setattr(_current_progress, k, v)
        _progress_event.set()


async def _reset_progress():
    async with _progress_lock:
        global _current_progress
        _current_progress = UpdateProgress()
        _progress_event.clear()


# ── 校验和验证 ──────────────────────────────────────────────────────────

def compute_sha256(file_path: str) -> str:
    """计算文件的 SHA256 校验和"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_checksum(file_path: str, expected_sha256: str) -> bool:
    """验证文件的 SHA256 校验和"""
    if not expected_sha256:
        return True  # 没有校验和则跳过验证
    actual = compute_sha256(file_path)
    return actual.lower() == expected_sha256.lower()


# ── 备份管理 ─────────────────────────────────────────────────────────────

def get_app_path() -> Optional[Path]:
    """获取当前应用路径"""
    system = platform.system()
    if system == "Darwin":
        # macOS: 检查 /Applications/Vermes.app 或运行目录
        app_path = Path("/Applications/Vermes.app")
        if app_path.exists():
            return app_path
        # PyInstaller 打包后的路径
        if getattr(sys, 'frozen', False):
            return Path(sys.executable).parent.parent.parent
    elif system == "Windows":
        # Windows: exe 所在目录
        if getattr(sys, 'frozen', False):
            return Path(sys.executable).parent
    return None


def get_current_version() -> str:
    """获取当前版本号"""
    try:
        # 尝试从 version.json 读取
        if getattr(sys, 'frozen', False):
            if platform.system() == "Darwin":
                version_file = Path(sys.executable).parent.parent / "Resources" / "version.json"
            else:
                version_file = Path(sys.executable).parent / "version.json"
            if version_file.exists():
                data = json.loads(version_file.read_text(encoding="utf-8"))
                return data.get("version", "unknown")
    except Exception:
        pass
    return "unknown"


def list_backups() -> List[Dict[str, Any]]:
    """列出所有可用的备份"""
    backups = []
    if not BACKUP_DIR.exists():
        return backups

    for item in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if item.is_dir() and item.name.startswith("v"):
            meta_file = item / "backup.json"
            meta = {}
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            backups.append({
                "version": item.name.lstrip("v"),
                "path": str(item),
                "timestamp": meta.get("timestamp", item.stat().st_mtime),
                "platform": meta.get("platform", "unknown"),
                "size_bytes": sum(
                    f.stat().st_size for f in item.rglob("*") if f.is_file()
                ),
            })

    return backups


def backup_current_version(version: str) -> Optional[str]:
    """备份当前版本到 ~/.vermes/backup/v{version}/

    Returns: 备份路径，失败返回 None
    """
    app_path = get_app_path()
    if not app_path or not app_path.exists():
        return None

    backup_path = BACKUP_DIR / f"v{version}"
    if backup_path.exists():
        shutil.rmtree(backup_path)

    backup_path.mkdir(parents=True, exist_ok=True)

    try:
        if platform.system() == "Darwin" and app_path.suffix == ".app":
            # macOS: 复制 .app bundle
            dst = backup_path / app_path.name
            shutil.copytree(app_path, dst)
        elif platform.system() == "Windows":
            # Windows: 复制 exe 目录
            for item in app_path.iterdir():
                if item.is_dir():
                    shutil.copytree(item, backup_path / item.name)
                else:
                    shutil.copy2(item, backup_path / item.name)
        else:
            return None

        # 写入备份元数据
        meta = {
            "version": version,
            "timestamp": time.time(),
            "platform": platform.system(),
            "app_path": str(app_path),
        }
        (backup_path / "backup.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        return str(backup_path)

    except Exception as e:
        _log.error(f"[Update] 备份失败: {e}")
        shutil.rmtree(backup_path, ignore_errors=True)
        return None


def backup_user_data() -> Optional[str]:
    """备份用户数据到 ~/.vermes/backup/user-data-backup.tar.gz

    Returns: 备份文件路径，失败返回 None
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(str(USER_DATA_BACKUP), "w:gz") as tar:
            # 备份目录
            for dir_name in USER_DATA_DIRS:
                dir_path = HERMES_HOME / dir_name
                if dir_path.exists():
                    tar.add(str(dir_path), arcname=dir_name)

            # 备份文件
            for file_name in USER_DATA_FILES:
                file_path = HERMES_HOME / file_name
                if file_path.exists():
                    tar.add(str(file_path), arcname=file_name)

        return str(USER_DATA_BACKUP)

    except Exception as e:
        _log.error(f"[Update] 用户数据备份失败: {e}")
        return None


def cleanup_old_backups():
    """清理旧备份，只保留最近 MAX_BACKUPS 个版本"""
    backups = list_backups()
    if len(backups) <= MAX_BACKUPS:
        return

    # 按时间排序，删除最旧的
    backups.sort(key=lambda b: b["timestamp"])
    for backup in backups[:-MAX_BACKUPS]:
        backup_path = Path(backup["path"])
        if backup_path.exists():
            shutil.rmtree(backup_path, ignore_errors=True)


# ── 异步下载（httpx 流式 + 进度）───────────────────────────────────────

async def download_with_progress(
    url: str,
    dest_path: str,
    expected_sha256: str = "",
    timeout: float = 600.0,
) -> str:
    """异步流式下载文件，实时更新进度

    Args:
        url: 下载地址
        dest_path: 目标文件路径
        expected_sha256: 预期 SHA256（可选）
        timeout: 超时秒数

    Returns:
        下载文件的 SHA256

    Raises:
        Exception: 下载失败或校验不匹配
    """
    import httpx

    await _set_progress(
        status=UpdateStatus.DOWNLOADING,
        progress=0,
        message="正在连接...",
    )

    # SSL 配置（兼容旧服务器）
    ssl_verify = True
    try:
        import certifi
        ssl_context = certifi.where()
    except Exception:
        ssl_verify = False

    downloaded = 0
    start_time = time.time()
    sha256_hash = hashlib.sha256()

    async with httpx.AsyncClient(
        verify=ssl_verify,
        timeout=httpx.Timeout(timeout, connect=30, read=60),
        follow_redirects=True,
        limits=httpx.Limits(max_connections=5),
    ) as client:
        async with client.stream(
            "GET", url,
            headers={"User-Agent": "Vermes-Updater/2.0"},
        ) as response:
            response.raise_for_status()

            total = int(response.headers.get("content-length", 0))
            await _set_progress(
                total_bytes=total,
                message=f"开始下载 ({total / 1024 / 1024:.1f} MB)..." if total else "开始下载...",
            )

            with open(dest_path, "wb") as f:
                async for chunk in response.aiter_bytes(65536):
                    f.write(chunk)
                    sha256_hash.update(chunk)
                    downloaded += len(chunk)

                    # 更新进度
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    eta = (total - downloaded) / speed if speed > 0 and total > 0 else 0
                    progress = (downloaded / total * 100) if total > 0 else 0

                    await _set_progress(
                        progress=min(progress, 99.9),
                        downloaded_bytes=downloaded,
                        speed_bps=speed,
                        eta_seconds=eta,
                        message=f"已下载 {downloaded / 1024 / 1024:.1f} MB"
                                + (f" / {total / 1024 / 1024:.1f} MB" if total else "")
                                + f" ({speed / 1024 / 1024:.1f} MB/s)",
                    )

    actual_sha256 = sha256_hash.hexdigest()

    # 校验和验证
    if expected_sha256:
        await _set_progress(
            status=UpdateStatus.VERIFYING,
            message="正在验证校验和...",
        )
        if actual_sha256.lower() != expected_sha256.lower():
            os.remove(dest_path)
            raise ValueError(
                f"SHA256 校验失败！\n"
                f"预期: {expected_sha256}\n"
                f"实际: {actual_sha256}\n"
                f"文件可能被篡改或下载不完整。"
            )

    return actual_sha256


# ── 解压 ─────────────────────────────────────────────────────────────────

async def extract_to_staging(archive_path: str) -> str:
    """解压 DMG/ZIP 到 staging 目录

    Returns:
        staging 目录路径
    """
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    is_dmg = archive_path.endswith(".dmg")
    is_zip = archive_path.endswith(".zip")

    await _set_progress(
        status=UpdateStatus.EXTRACTING,
        message="正在解压...",
        progress=99.5,
    )

    if is_dmg:
        mount_point = str(UPDATE_DIR / "mount")
        os.makedirs(mount_point, exist_ok=True)
        try:
            r = subprocess.run(
                ["hdiutil", "attach", archive_path, "-mountpoint", mount_point, "-nobrowse", "-quiet"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode != 0:
                raise RuntimeError(f"挂载 DMG 失败: {r.stderr}")

            # 找 .app 目录
            app_found = False
            for item in os.listdir(mount_point):
                if item.endswith(".app"):
                    src = os.path.join(mount_point, item)
                    dst = os.path.join(STAGING_DIR, item)
                    shutil.copytree(src, dst)
                    app_found = True
                    break

            if not app_found:
                raise RuntimeError("DMG 中未找到 .app 目录")

        finally:
            subprocess.run(
                ["hdiutil", "detach", mount_point, "-quiet"],
                capture_output=True, timeout=30,
            )

    elif is_zip:
        import zipfile
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(STAGING_DIR)

    else:
        raise ValueError("不支持的文件格式（仅支持 .dmg 和 .zip）")

    return str(STAGING_DIR)


# ── 原子替换 ─────────────────────────────────────────────────────────────

def atomic_replace_macos(staging_app: Path, target_app: Path) -> bool:
    """macOS 原子替换 .app bundle

    流程：
    1. 复制到 target.new
    2. 验证关键文件存在
    3. rename target → target.old
    4. rename target.new → target
    5. 删除 target.old

    Returns:
        True 成功，False 失败（已自动回滚）
    """
    temp_target = target_app.parent / (target_app.name + ".new")
    old_target = target_app.parent / (target_app.name + ".old")

    try:
        # 1. 复制到临时位置
        if temp_target.exists():
            shutil.rmtree(temp_target)
        shutil.copytree(staging_app, temp_target)

        # 2. 验证关键文件
        main_exe = temp_target / "Contents" / "MacOS" / "vermes"
        if not main_exe.exists():
            raise RuntimeError("无效的 .app bundle：缺少 MacOS/vermes")

        info_plist = temp_target / "Contents" / "Info.plist"
        if not info_plist.exists():
            raise RuntimeError("无效的 .app bundle：缺少 Info.plist")

        # 3. 原子切换
        if target_app.exists():
            os.rename(str(target_app), str(old_target))
        os.rename(str(temp_target), str(target_app))

        # 4. 清理旧版本
        if old_target.exists():
            shutil.rmtree(old_target, ignore_errors=True)

        return True

    except Exception as e:
        # 回滚
        if temp_target.exists():
            shutil.rmtree(temp_target, ignore_errors=True)
        if old_target.exists() and not target_app.exists():
            os.rename(str(old_target), str(target_app))
        raise


def atomic_replace_windows(staging_dir: Path, target_dir: Path) -> bool:
    """Windows 原子替换

    Windows 不支持 rename 覆盖目录，使用分步替换：
    1. 复制到 target.new
    2. 验证关键文件
    3. 重命名 target → target.old
    4. 重命名 target.new → target
    5. 后台删除 target.old

    Returns:
        True 成功
    """
    temp_target = target_dir.parent / (target_dir.name + ".new")
    old_target = target_dir.parent / (target_dir.name + ".old")

    try:
        # 1. 复制到临时位置
        if temp_target.exists():
            shutil.rmtree(temp_target)
        shutil.copytree(staging_dir, temp_target)

        # 2. 验证关键文件
        main_exe = temp_target / "Vermes.exe"
        if not main_exe.exists():
            raise RuntimeError("无效的更新包：缺少 Vermes.exe")

        # 3. 原子切换
        if target_dir.exists():
            os.rename(str(target_dir), str(old_target))
        os.rename(str(temp_target), str(target_dir))

        # 4. 后台清理旧版本（不阻塞启动）
        if old_target.exists():
            import threading
            def _cleanup():
                try:
                    shutil.rmtree(old_target)
                except Exception:
                    pass
            threading.Thread(target=_cleanup, daemon=True).start()

        return True

    except Exception as e:
        # 回滚
        if temp_target.exists():
            shutil.rmtree(temp_target, ignore_errors=True)
        if old_target.exists() and not target_dir.exists():
            os.rename(str(old_target), str(target_dir))
        raise


# ── 数据迁移 ─────────────────────────────────────────────────────────────

def get_data_version() -> str:
    """获取当前数据版本（存储在 state.db 或 config.yaml 中）"""
    version_file = HERMES_HOME / "data_version.json"
    if version_file.exists():
        try:
            data = json.loads(version_file.read_text(encoding="utf-8"))
            return data.get("version", "0.0.0")
        except Exception:
            pass
    return "0.0.0"


def set_data_version(version: str):
    """设置当前数据版本"""
    version_file = HERMES_HOME / "data_version.json"
    version_file.write_text(
        json.dumps({"version": version, "updated_at": time.time()}, indent=2),
        encoding="utf-8",
    )


def check_data_compatibility(min_data_version: str) -> bool:
    """检查当前数据是否兼容新版本

    Returns:
        True 兼容，False 不兼容
    """
    if not min_data_version:
        return True  # 没有最低版本要求

    current = get_data_version()
    if current == "0.0.0":
        return True  # 首次安装，没有数据版本

    # 比较版本号
    def parse_version(v: str) -> tuple:
        parts = v.split(".")
        return tuple(int(p) for p in parts[:3] if p.isdigit())

    try:
        return parse_version(current) >= parse_version(min_data_version)
    except Exception:
        return True  # 解析失败，假设兼容


async def run_migrations(from_version: str, to_version: str):
    """运行数据迁移脚本

    迁移脚本位于 app bundle 的 migrations/ 目录，命名格式：
    {from_version}_to_{to_version}.py
    """
    if getattr(sys, 'frozen', False):
        if platform.system() == "Darwin":
            migrations_dir = Path(sys.executable).parent.parent / "Resources" / "migrations"
        else:
            migrations_dir = Path(sys.executable).parent / "migrations"
    else:
        migrations_dir = Path(__file__).parent.parent / "migrations"

    if not migrations_dir.exists():
        return

    # 查找匹配的迁移脚本
    migration_file = migrations_dir / f"{from_version}_to_{to_version}.py"
    if not migration_file.exists():
        return

    await _set_progress(
        status=UpdateStatus.MIGRATING,
        message=f"正在迁移数据 ({from_version} → {to_version})...",
    )

    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("migration", str(migration_file))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, "migrate"):
            module.migrate(str(HERMES_HOME))
            _log.info(f"[Update] 数据迁移完成: {from_version} → {to_version}")
    except Exception as e:
        _log.error(f"[Update] 数据迁移失败: {e}")
        # 迁移失败不阻塞更新，但记录错误


# ── 回滚 ─────────────────────────────────────────────────────────────────

def rollback_to_version(version: str) -> bool:
    """回滚到指定版本

    Returns:
        True 成功
    """
    backup_path = BACKUP_DIR / f"v{version}"
    if not backup_path.exists():
        raise FileNotFoundError(f"备份 v{version} 不存在")

    app_path = get_app_path()
    if not app_path:
        raise RuntimeError("无法确定应用路径")

    staging_path = UPDATE_DIR / "staging"
    if staging_path.exists():
        shutil.rmtree(staging_path)

    # 找到备份中的 .app 或 exe 目录
    if platform.system() == "Darwin":
        backup_app = None
        for item in backup_path.iterdir():
            if item.is_dir() and item.name.endswith(".app"):
                backup_app = item
                break
        if not backup_app:
            raise RuntimeError(f"备份 v{version} 中未找到 .app")

        # 写入 pending.json 让下次启动时应用
        pending = {
            "version": version,
            "staging_path": str(backup_path),
            "platform": platform.system(),
            "timestamp": time.time(),
            "is_rollback": True,
        }
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        PENDING_FILE.write_text(json.dumps(pending, indent=2), encoding="utf-8")

    elif platform.system() == "Windows":
        # 写入 pending.json
        pending = {
            "version": version,
            "staging_path": str(backup_path),
            "platform": platform.system(),
            "timestamp": time.time(),
            "is_rollback": True,
        }
        UPDATE_DIR.mkdir(parents=True, exist_ok=True)
        PENDING_FILE.write_text(json.dumps(pending, indent=2), encoding="utf-8")

    return True


# ── 启动时应用更新（gui_app 调用）────────────────────────────────────────

def apply_pending_update() -> Optional[str]:
    """启动时检查并应用待处理的更新

    Returns:
        更新的版本号，无更新返回 None
    """
    if not PENDING_FILE.exists():
        return None

    try:
        pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        _log.error(f"[Update] 读取 pending.json 失败: {e}")
        PENDING_FILE.unlink(missing_ok=True)
        return None

    version = pending.get("version", "unknown")
    staging_path = Path(pending.get("staging_path", ""))
    is_rollback = pending.get("is_rollback", False)

    if not staging_path.exists():
        _log.error(f"[Update] staging 目录不存在: {staging_path}")
        PENDING_FILE.unlink(missing_ok=True)
        return None

    action = "回滚" if is_rollback else "更新"
    print(f"[Vermes] 发现待应用{action} v{version}，正在应用...")

    try:
        app_path = get_app_path()
        if not app_path:
            raise RuntimeError("无法确定应用路径")

        if platform.system() == "Darwin":
            # 找到 staging 中的 .app
            staging_app = None
            for item in staging_path.iterdir():
                if item.is_dir() and item.name.endswith(".app"):
                    staging_app = item
                    break
            if not staging_app:
                raise RuntimeError("staging 中未找到 .app")

            # 原子替换
            atomic_replace_macos(staging_app, app_path)

            # 重启前等待旧进程释放端口
            print(f"[Vermes] ✅ {action}到 v{version}，正在重启...")
            _cleanup_after_apply()
            # 给旧进程时间释放端口（uvicorn 优雅关闭需要 ~1s）
            time.sleep(2)
            subprocess.Popen(["open", str(app_path)])
            import sys
            sys.exit(0)

        elif platform.system() == "Windows":
            # 原子替换
            atomic_replace_windows(staging_path, app_path)

            # 重启前等待旧进程释放端口
            print(f"[Vermes] ✅ {action}到 v{version}，正在重启...")
            _cleanup_after_apply()
            time.sleep(2)
            subprocess.Popen([str(app_path / "Vermes.exe")])
            import sys
            sys.exit(0)

    except Exception as e:
        print(f"[Vermes] ❌ {action}失败: {e}")
        _log.error(f"[Update] {action}失败: {e}")
        PENDING_FILE.unlink(missing_ok=True)
        return None

    return version


def _cleanup_after_apply():
    """更新应用后的清理"""
    try:
        PENDING_FILE.unlink(missing_ok=True)
        if STAGING_DIR.exists():
            shutil.rmtree(STAGING_DIR, ignore_errors=True)
        cleanup_old_backups()
    except Exception:
        pass


# ── 日志 ─────────────────────────────────────────────────────────────────

import sys

try:
    from hermes_cli.log_utils import get_logger
    _log = get_logger("update")
except Exception:
    import logging
    _log = logging.getLogger("update")
    _log.setLevel(logging.INFO)
