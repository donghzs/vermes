"""
迁移脚本：2.0.3 → 2.0.5

v2.0.5 主要变更：
1. 新增配额系统（quota 字段）
2. 新增消息编辑功能（edit 字段）
3. 新增多模型对比（compare 字段）
4. 新增消息时间戳显示（timestamp 字段）
5. 更新系统 v2（update_manager）
"""

import logging

logger = logging.getLogger(__name__)
import json
import sqlite3
from pathlib import Path


def migrate(VERMES_home: str):
    """从 v2.0.3 迁移到 v2.0.5"""
    home = Path(VERMES_home)
    migrated = []
    errors = []

    # ── 1. 升级 config.yaml ──────────────────────────────────────
    config_file = home / "config.yaml"
    if config_file.exists():
        try:
            import ruamel.yaml


            yaml = ruamel.yaml.YAML()
            with open(config_file) as f:
                config = yaml.load(f)

            # 添加 quota 相关配置（如果不存在）
            if "quota" not in config:
                config["quota"] = {
                    "enabled": True,
                    "daily_limit": 500,
                    "warning_threshold": 50
                }

            # 添加 update 配置（如果不存在）
            if "update" not in config:
                config["update"] = {
                    "auto_check": True,
                    "channel": "stable"
                }

            with open(config_file, "w") as f:
                yaml.dump(config, f)

            migrated.append("config.yaml")
        except Exception as e:
            errors.append(f"config.yaml: {e}")

    # ── 2. 升级 state.db schema ──────────────────────────────────
    db_file = home / "state.db"
    if db_file.exists():
        try:
            db = sqlite3.connect(str(db_file))
            cursor = db.cursor()

            # 检查 messages 表结构
            cursor.execute("PRAGMA table_info(messages)")
            columns = {row[1] for row in cursor.fetchall()}

            # 添加 edit_count 列（消息编辑次数）
            if "edit_count" not in columns:
                cursor.execute("ALTER TABLE messages ADD COLUMN edit_count INTEGER DEFAULT 0")
                migrated.append("messages.edit_count")

            # 添加 edited_at 列（最后编辑时间）
            if "edited_at" not in columns:
                cursor.execute("ALTER TABLE messages ADD COLUMN edited_at REAL")
                migrated.append("messages.edited_at")

            # 添加 compare_model 列（多模型对比的模型名）
            if "compare_model" not in columns:
                cursor.execute("ALTER TABLE messages ADD COLUMN compare_model TEXT")
                migrated.append("messages.compare_model")

            # 创建 quota_usage 表（配额使用记录）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS quota_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    used INTEGER DEFAULT 0,
                    limit INTEGER DEFAULT 500,
                    updated_at REAL
                )
            """)
            migrated.append("quota_usage table")

            # 创建 update_history 表（更新历史）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS update_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL,
                    applied_at REAL,
                    success BOOLEAN DEFAULT 1,
                    error TEXT
                )
            """)
            migrated.append("update_history table")

            db.commit()
            db.close()
            migrated.append("state.db")
        except Exception as e:
            errors.append(f"state.db: {e}")

    # ── 3. 升级 .env 文件 ────────────────────────────────────────
    env_file = home / ".env"
    if env_file.exists():
        try:
            content = env_file.read_text(encoding="utf-8")
            lines = content.splitlines()

            # 添加 VERMES_DATA_VERSION（如果不存在）
            if not any(line.startswith("VERMES_DATA_VERSION=") for line in lines):
                lines.append("VERMES_DATA_VERSION=2.0.5")
                env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                migrated.append(".env")
        except Exception as e:
            errors.append(f".env: {e}")

    # ── 4. 更新数据版本标记 ──────────────────────────────────────
    version_file = home / "data_version.json"
    version_data = {
        "version": "2.0.5",
        "migrated_from": "2.0.3",
        "migrated_at": __import__("time").time(),
        "migrated_items": migrated,
        "errors": errors
    }
    version_file.write_text(json.dumps(version_data, indent=2), encoding="utf-8")

    if errors:
        logger.info(f"[Migration] 2.0.3 → 2.0.5 部分完成，错误: {errors}")
    else:
        logger.info(f"[Migration] 2.0.3 → 2.0.5 迁移完成，共 {len(migrated)} 项")

    return len(errors) == 0
