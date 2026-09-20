# 明文 Provider Key 收口 — 交叉审计报告（交 MiMo）

> 提交：`b93a6e4cbb` fix(security): remove plaintext provider keys from config.yaml
> 执行：QClaw ｜ 时间：2026-09-20 20:0x GMT+8 ｜ 状态：待 MiMo 交叉审计

## 一、问题背景

`vermes_cli/blueprints/providers.py::add_provider()` 对「不在 PROVIDER_TEMPLATES 里的 provider」（如 agnes、scnet）会走一段分支：

```python
if not template and body.api_key:
    entry["api_key"] = body.api_key   # ← 明文 key 落盘 config.yaml
```

导致凭证**双写**：既进 `~/.vermes/.env`（save_env_value），又明文躺进 `~/.vermes/config.yaml`。备份/同步/误传会把密钥带走，违背「密钥只进 .env」规矩（issue #15803）。

## 二、改动清单（4 个文件 + 1 脚本）

### 1. `vermes_cli/blueprints/providers.py` — add_provider
- 删除 `entry["api_key"] = body.api_key` 明文分支。
- 补写 `entry["key_env"] = env_key`（此前从不写 key_env）。
- `entry.pop("api_key", None)` 清理遗留内联 key。
- 仅当 `body.base_url` 存在时才动 config；`body.api_key` 仍走 save_env_value 写 .env（不变）。

### 2. `vermes_cli/runtime_provider.py` — custom provider key 解析
- 新增 `_resolve_env_key(key_env)`：优先 `get_env_value`（os.environ OR ~/.vermes/.env 文件回读），异常兜底 `os.getenv`。
- 原 `resolved_api_key = os.getenv(key_env)` 改为 `_resolve_env_key(key_env)`。

### 3. `agent/auxiliary_client.py` — custom 分支 key 解析
- 原 `custom_key = os.getenv(custom_key_env)` 改为 `get_env_value(custom_key_env)`（带 try/except 兜底）。

### 4. `scripts/migrate_plaintext_provider_keys.py` — 一次性存量迁移
- 把 config.yaml 里现存明文 `api_key` 落 .env（缺失才写，已存在同名 env 用 env 为准），再剥离 `api_key`、确保 `key_env`。
- 占位符值（`ollama`/`local`）视为非机密：剥离但不写入 .env。
- round-trip YAML 保注释/顺序/引号。
- 支持 `--dry-run`。

### 5. 测试
- `test_provider_add_no_wipe.py`：+2（custom provider key 写 .env 不写明文；空 key 仍记录 key_env）。
- `test_runtime_provider_resolution.py`：+3（key_env 从 .env 文件回读；_resolve_env_key 空值兜底；named custom provider 用 file 解析）。

## 三、验证结果

| 项 | 结果 |
|---|---|
| 存量迁移 dry-run | ✅ agnes(env 已匹配→剥离)、scnet(真凭证→先落 .env)、local/ollama(占位符→剥离) |
| 存量迁移实跑 | ✅ config.yaml 无任何明文 api_key；SCNET_API_KEY 已进 .env |
| 运行时回读（删明文后，模拟不注入 os.environ） | ✅ AGNES/SCNET/OLLAMA/LOCAL 全部 FOUND |
| auxiliary_client custom 分支回读 | ✅ agnes client.api_key FOUND len=52 |
| 目标测试 | ✅ 9 passed（add_provider 5 + runtime_provider 4） |
| 广回归（api_key_providers + custom_provider_model_switch） | ✅ 183 passed / 1 failed（该失败为 pre-existing `_stamp_preset` 签名 bug） |
| py_compile 三文件 | ✅ 通过 |

### pre-existing 失败（与本次无关，已 stash 实证）
`_stamp_preset()` 签名不匹配：`e7b3aa9e7ba`（2026-08-22）引入，调用处只传 1 参、定义要 3 参（`resolved, preset_spec, preset_name`）。
- `test_runtime_provider_resolution.py` 16 例
- `test_api_key_providers.py` 1 例
- 共 17 例，均为 `TypeError: _stamp_preset() missing 2 required positional arguments`。
- **不在本次改动范围**，建议另立工单（一行修法：调用处补 `None, None` 或定义加默认值）。

## 四、请 MiMo 重点交叉审计的点

1. **安全性**：`get_env_value` 引入后是否在任何路径把 key 泄露到日志/异常（`_resolve_env_key` 无 logger，`auxiliary_client` 只 debug 级）。
2. **一致性**：是否还有第三处 custom provider 用 `os.getenv(key_env)` 没改到？建议 grep 全库 `os.getenv(` 且参数是 key_env/api_key_env 变量的调用点。
3. **迁移脚本幂等性**：重复跑 `migrate_plaintext_provider_keys.py` 是否安全（应 no-op）。
4. **前端交互**：Settings.vue 回显 mask 依赖 `pcfg.api_key || pcfg.key_env`，删明文后 key_env 仍在，理论上零影响——请确认无其他读 `providers.*.api_key` 明文的前端路径。
5. **存量迁移是否已在你环境执行**：QClaw 已在 `~/.vermes` 实跑（备份 `config.yaml.bak-20260920_200135`），审计时无需重跑，但可 review 脚本逻辑。

## 五、备份与回滚

- 实跑前已备份：`~/.vermes/config.yaml.bak-20260920_200135`、`~/.vermes/.env.bak-20260920_200135`。
- 回滚：`cp config.yaml.bak-* config.yaml && cp .env.bak-* .env`。

## 六、未提交说明

- 本次改动已本地 commit `b93a6e4cbb`，遵循 §12 冻结，**未 push**。
- `_stamp_preset` pre-existing bug 未动，留待另立工单。
