# Vermes 数据迁移脚本

迁移脚本在应用更新后、首次启动时执行，用于升级用户数据格式。

## 命名规则

```
{from_version}_to_{to_version}.py
```

例如：`2.0.3_to_2.0.5.py` 表示从 v2.0.3 升级到 v2.0.5 的迁移。

## 脚本结构

```python
def migrate(VERMES_home: str):
    """迁移入口函数

    Args:
        VERMES_home: 用户数据目录路径 (~/.vermes)
    """
    from pathlib import Path
    import json

    home = Path(VERMES_home)

    # 示例：升级 config.yaml 格式
    config_file = home / "config.yaml"
    if config_file.exists():
        import ruamel.yaml
        yaml = ruamel.yaml.YAML()
        with open(config_file) as f:
            config = yaml.load(f)

        # 添加新字段
        if "new_field" not in config:
            config["new_field"] = "default_value"

        with open(config_file, "w") as f:
            yaml.dump(config, f)

    # 示例：升级 state.db schema
    import sqlite3
    db = sqlite3.connect(str(home / "state.db"))
    cursor = db.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS new_table (
            id INTEGER PRIMARY KEY,
            data TEXT
        )
    """)
    db.commit()
    db.close()
```

## 注意事项

1. 迁移脚本应该是**幂等的**（多次执行结果相同）
2. 使用 `IF NOT EXISTS`、`ADD COLUMN IF NOT EXISTS` 等防御性 SQL
3. 先检查文件/表是否存在再修改
4. 迁移失败不会阻塞应用启动（但会记录错误日志）
5. 测试迁移脚本时，先备份 `~/.vermes/` 目录
