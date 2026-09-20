# D8 明文 Key 修复 · MiMo 交叉审计 · 2026-09-20

> **被审**：`b93a6e4cbb` fix(security): remove plaintext provider keys from config.yaml  
> **审计**：MiMo · 基于本机实测 + 源码对照  
> **QClaw 报告**：`reports/qclaw/audit-plaintext-provider-key_20260920.md`

---

## 总判定

| QClaw 五点 | MiMo 判定 |
|---|---|
| 1 安全性（key 不进日志） | ✅ 通过 |
| 2 一致性（无第三处 os.getenv 漏网） | ❌ **有缺口**：`runtime_provider.py` named-custom 路径仍 `os.getenv(key_env)` |
| 3 迁移幂等 | ⚠️ 脚本本身幂等，但**扫描范围不全**（见下） |
| 4 前端 mask | ✅ 依赖 `key_env`，理论零影响 |
| 5 存量已迁 | ❌ **providers 段已迁；auxiliary / custom_providers 仍明文** |

---

## 关键发现 1：迁移脚本漏扫两段（P1）

`scripts/migrate_plaintext_provider_keys.py` 只处理 **`providers` 映射**。

本机 `~/.vermes/config.yaml` 实测（已脱敏）：

| 路径 | 状态 |
|---|---|
| `providers.*.api_key` | ✅ 已清；各 entry 有 `key_env: *_API_KEY` |
| **`auxiliary.vision.api_key`** | ❌ **非空（len=52）** — agnes 视觉 key 仍明文 |
| **`custom_providers[].api_key`** | ❌ **多条非空**（len=42 为 ant-ling 真凭证；len=4 疑似 ollama/local 占位） |

因此 QClaw「config.yaml 无任何明文 api_key」**仅对 `providers` 段成立**。  
`migrate --dry-run` 输出 `no inline provider keys found`，是因为脚本根本没进 auxiliary / custom_providers。

**建议**：扩展迁移脚本扫描 `providers` + `auxiliary.*` + `custom_providers[]`；占位符（`ollama`/`local`/空）只剥离不写 .env。

---

## 关键发现 2：consistency 缺口（P1）

`agent/auxiliary_client.py` named-custom 分支已改 `get_env_value`（✅）。

但 **`vermes_cli/runtime_provider.py:677`** 仍为：

```python
os.getenv(str(custom_provider.get("key_env", "") or "").strip(), "").strip()
```

未走 `_resolve_env_key` / `get_env_value`。  
→ 另一条 custom provider 解析路径在 **desktop uvicorn 不注入 dotenv** 时会拿不到 key（与 QClaw 自己点出的 agnes 断供风险同构）。

**建议**：`:677` 改为 `_resolve_env_key(...)`；并 grep 确认无其它 `os.getenv(key_env-like)`。

---

## 关键发现 3：测试

- 目标套件最终一次完整跑：**124 passed**（含 `test_provider_add_no_wipe` + `test_runtime_provider_resolution`）。
- 过程中偶发 **2 个 qwen oauth** 失败（token 被 env 泄漏/`qwen-oauth` 判定），不稳定，疑似环境串扰；与 `_stamp_preset` 无关（该批未再复现）。
- QClaw 称 `_stamp_preset` pre-existing：本轮终跑未再出现，可另开观察。

---

## 其它观察（非本轮范围）

- `frontend` Settings 对 provider 的 mask 仍可依赖 `key_env`，与后端一致。
- `get_env_value`：`os.environ` 优先，再读 `~/.vermes/.env` — 符合「密钥只进 .env」方向。
- `_resolve_env_key` 无 logger 打 key；`auxiliary_client` 缺 key 只 warn **provider 名** — 安全项通过。

---

## 请 QClaw / 董董处置

| # | 项 | 建议 |
|---|---|---|
| 1 | 扩展 migrate 覆盖 `auxiliary.*` + `custom_providers[]` | 必做；迁移后重跑 dry-run 应出现「已剥离」而非假 clean |
| 2 | `runtime_provider.py:677` → `_resolve_env_key` | 必做（半小时） |
| 3 | 清理后复验：config 无非空 `api_key`；agnes/scnet/ant-ling 运行时仍能解析 | 复审计 |
| 4 | qwen oauth 偶发失败 | 观察，勿与 D8 绑死 |
| 5 | 系统钥匙串（D8 原议题） | 仍后置；先把「config 明文清零」做实 |

— MiMo 交叉审计 · 未改 QClaw 代码 · 待指令后开工
