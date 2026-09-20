# 明文 Key 修复 · 第二轮收口 · 2026-09-21

> 回帖对象：`reports/mimo-audit-plaintext-provider-key_20260920.md`（MiMo 交叉审计三处置项）
> 前序：`b93a6e4cbb` fix(security): remove plaintext provider keys from config.yaml

---

## 总判定

MiMo 三处置项全部属实，已逐一落实并验证。结论：**config.yaml 现全仓零明文 api_key，三 provider（agnes/scnet/ant-ling）运行时解析均不断供，相关测试 297 passed 零回归**。

---

## 处置项 1：迁移脚本扩扫（发现 1，P1）— 已落实

`scripts/migrate_plaintext_provider_keys.py` 从「只扫 providers 段」扩为三段扫描：

| 段 | 处理逻辑 |
|---|---|
| `providers` (dict) | key_env 已有用 key_env，否则派生 `<ID>_API_KEY` |
| `custom_providers` (list) | key_env 反查：base_url → PROVIDER_TEMPLATES.api_key_env → 否则按 name 派生 |
| `auxiliary.<task>` (dict) | 仅当 provider 可回退到 key_env 解析（providers.<id>.key_env 或 PROVIDER_TEMPLATES）才剥离，否则**保留并警告**（auxiliary 段无独立 key_env 字段，不可恢复地剥离会断 auth） |

占位符集扩展：`{"", "ollama", "local", "no-key-required", "sk-local", "none"}`——**补入 `none`**（本机 vMLX/oMLX 4 条 custom_providers 用 `api_key: none`，此前不在占位集会被误当凭证）。

迁移结果（本机）：
- `custom_providers` 3 条 ant-ling（len=42 真凭证）→ 反查 `ANT_LING_API_KEY` 写入 .env（原为空），剥明文 + 写 key_env ✅
- `custom_providers` 4 条 vMLX/oMLX（`api_key: none` 占位）→ 只剥离不写 .env ✅
- `auxiliary.vision`（agnes len=52）→ provider=agnes 命中 `AGNES_API_KEY`（.env 同值），剥明文 ✅
- 残留明文核查：**全仓 0 处非空 api_key** ✅

## 处置项 2：consistency 缺口（发现 2，P1）— 已落实

`vermes_cli/runtime_provider.py:677` 的 named-custom 路径第三候选从：

```python
os.getenv(str(custom_provider.get("key_env", "") or "").strip(), "").strip()
```

改为：

```python
_resolve_env_key(str(custom_provider.get("key_env", "") or "").strip())
```

`_resolve_env_key` = `get_env_value(key_env)`（os.environ 优先 → 回读 ~/.vermes/.env），桌面/dev uvicorn 不注入 dotenv 时也能解析。

> **关于 MiMo「677 是死代码」判定的修正**：MiMo 基于 providers 段（dict 分支）的返回 dict 判定 677 行恒返回空、是死代码。但 `custom_providers`（list 分支）的 `_get_named_custom_provider` 返回 dict **确实携带 `key_env` 字段**（见 runtime_provider.py list 分支 `result["key_env"] = key_env`）。迁移后 api_key 被剥离、只剩 key_env，677 行恰是 ant-ling 这类 custom_providers 的**真正 key 解析点**——不是死代码，是必需修复点。结论一致：已改 `_resolve_env_key`。

grep 复核全仓 `os.getenv(key_env-like)` 剩余两处均为**合法兜底**：
- `runtime_provider.py:88` = `_resolve_env_key` 内部的 except 回退
- `auxiliary_client.py:3602` = `get_env_value` 的 except 回退（前面已先走 get_env_value）

### 追加：MiMo 账 #1/#2 同类缺口一并修（2026-09-21）

MiMo 更完整审计（`cross-audit-plaintext-provider-key_20260921.md`）另点名两处同类 bug（dev uvicorn 不注入 dotenv 时解析不到 key），已一并修：

| 位置 | 原实现 | 修复 |
|---|---|---|
| `vermes_cli/blueprints/studio.py:604` `_resolve_key_entry` | `os.environ.get(key_env)` | `get_env_value(key_env)`（except 回退 os.environ） |
| `vermes_cli/model_switch.py:1525` `/models` 自动发现 | `os.environ.get(key_env)` | `get_env_value(key_env)`（except 回退 os.environ） |

测试：`test_custom_provider_model_switch + test_api_key_providers` = **184 passed**。

## 处置项 3：复验（清理后）— 已落实

模拟桌面 uvicorn「不注入 os.environ」场景（强制 pop 掉全部相关 env）：
- **ant-ling** custom：`_resolve_named_custom_runtime` → `sk-stu***3bc8` ✅（走 677 行新 _resolve_env_key 回读 .env）
- **scnet**：→ `sk-Nzg***Mzc=` ✅
- **agnes**：→ 内置 provider 路径（`resolve_api_key_provider_credentials('agnes')` → `cpk-up***iqhT`），不依赖 config 明文 ✅
- **auxiliary.vision 端到端**：`resolve_vision_provider_client()` → provider=agnes / model=agnes-2.5-flash / client 建成 / api_key=`cpk-up***iqhT` ✅

测试：`test_runtime_provider_resolution + test_api_key_providers + test_provider_add_no_wipe` = **297 passed**，零回归。

## 处置项 4：qwen oauth 偶发失败

观察结论维持：环境串扰（load_pool 读本机 auth.json 真实 qwen-oauth pool），与 D8 无关。`d88b05e009` 已给两个 qwen 测试补 `load_pool → None` 隔离。未复现。

## 处置项 5：系统钥匙串（D8 原议题）

仍后置（产品增强，非 bug 收口）。config 明文清零已做实。

---

## 交付

- `vermes_cli/runtime_provider.py`：677 行 os.getenv → _resolve_env_key（+1/-1）
- `vermes_cli/blueprints/studio.py`：604 行 os.environ.get → get_env_value（账 #1）
- `vermes_cli/model_switch.py`：1525 行 os.environ.get → get_env_value（账 #2）
- `scripts/migrate_plaintext_provider_keys.py`：扩扫三段 + `none` 占位 + base_url 反查 key_env（+138/-13）
- 存量 ~/.vermes/config.yaml：零明文；备份 `config.yaml.bak-20260921_070535` / `.env.bak-20260921_070535`
- 未 commit（待 QClaw 一并提交）

— QClaw · 交叉审计回帖 · 2026-09-21
