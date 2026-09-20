# 明文 Provider Key 收口 — MiMo 交叉审计报告

> 被审计提交：`b93a6e4cbb` fix(security): remove plaintext provider keys from config.yaml
> 当前 HEAD：`d88b05e009` ( = b93a6e4cbb + "fix(tests): _stamp_preset signature mismatch + qwen pool isolation" )
> 交叉审计：MiMo（WorkBuddy）｜ 时间：2026-09-21 ｜ 基准：commit 实读 + 实测 pytest + .env/config.yaml 实查

## 〇、结论（先给）

**PASS — 是优化不是退化，但 QClaw 原审计报告有 2 处数字失真 + 2 处未修干净的同类一致性缺口。**

- 核心安全修复（删明文 + `key_env` 指针 + 双路径 `.env` 回读）**实测通过、功能链路正常**。
- 存量迁移**正确、幂等、零丢失**：`config.yaml` 已无任何明文 `api_key`（26 个 provider 全部走 `key_env`），`SCNET_API_KEY` 已安全落 `.env`，备份 `config.yaml.bak-20260920_200135` 存在。
- QClaw 报告的「183 passed / 1 failed（_stamp_preset）」**不准确**：那两个文件实测 184 passed / 0 failed；commit message 称 17 个 `_stamp_preset` 失败，但当前 HEAD 已修复，4 个相关文件合计 **308 passed / 0 failed**。报告的「1 failed」是幻影。
- 无第三处会「真断」的自定义 provider key 解析；QClaw 点名的 `runtime_provider.py:677` `os.getenv(key_env)` 是**死代码**（不会真断），但属于「同类 bug 没修干净」的还有 `studio.py:604` / `model_switch.py:1525`。

## 一、报告声称 vs 实测

| 项 | QClaw 报告声称 | MiMo 实测 | 判定 |
|---|---|---|---|
| commit 文件数 / 行数 | 6 files, +282/−4 | `git show --stat` 一致 | ✅ 真实 |
| 改动内容（4 代码 + 1 脚本 + 测试） | 见原报告 | `git diff b93a6e4cbb^!` 逐处核对一致 | ✅ 真实 |
| 目标测试 | 9 passed | `test_provider_add_no_wipe.py` 5 passed；新增 2 例（写 .env 不写明文 / 空 key 仍记 key_env）通过 | ✅ 真实 |
| 广回归 | 183 passed / **1 failed**（_stamp_preset） | `test_api_key_providers.py` 173 passed + `test_custom_provider_model_switch.py` 11 passed = **184 passed / 0 failed** | 🟡 数字失真（失败数错，通过数差 1） |
| _stamp_preset 失败数 | commit msg 称 17（16+1）；报告称「留待另立工单未动」 | 当前 HEAD 已通过 `d88b05e9` 修复（`runtime_provider.py:71-74` 加默认值）；`test_runtime_provider_resolution.py` 119 passed（含原 16 例） | 🟡 报告已过时 |
| 全 4 相关文件回归 | — | **308 passed / 0 failed**（5+119+173+11） | ✅ 零回归 |
| config.yaml 无明文 | 已无明文 | 实查：26 provider，inline `api_key` 0 条，inline+无 key_env 0 条 | ✅ 真实 |
| 迁移脚本零丢失 | scnet 真凭证已落 .env | `.env` 含 `SCNET_API_KEY`/`AGNES_API_KEY`/`LOCAL_API_KEY`/`OLLAMA_API_KEY`（共 40 key） | ✅ 真实 |
| 迁移脚本可重跑 | 幂等 no-op | 导入点 `load_roundtrip_yaml`(utils.py:323)/`atomic_roundtrip_yaml_dump`(utils.py:339)/`load_env`(config.py:4865)/`save_env_value`(config.py:5088) 均存在；重跑对无明文项直接 skip | ✅ 幂等 |
| 安全面（key 不泄露日志） | `_resolve_env_key` 无 logger；auxiliary_client 仅 debug 级 | 实读：`_resolve_env_key` 无 logger；`auxiliary_client:3592-3606` 仅 `logger.warning` 且不涉及明文值；`get_env_value`(config.py:5269) 不打印值 | ✅ 真实 |
| 第三处 `os.getenv(key_env)` 漏改 | 建议 grep 全库 | 主线两处已改；`runtime_provider.py:677` 是死代码（见账 #3）；另有 `studio.py:604`/`model_switch.py:1525` 同类未修（见账 #1/#2） | 🟡 部分成立 |

## 二、实测命令（MiMo 复现，供 MiMo 自己再跑）

```bash
cd /Users/dongzusheng/Projects/vermes-electron
export TMPDIR=/tmp/vermes-pytest && mkdir -p "$TMPDIR"
# 注：WorkBuddy 沙箱的 brokered sitecustomize 会拦截 /tmp 下 pytest 临时目录 mkdir，
#     故用 --basetemp 指到工作区目录；或直接 sandbox 外跑。
.venv/bin/python -m pytest \
  tests/vermes_cli/test_provider_add_no_wipe.py \
  tests/vermes_cli/test_runtime_provider_resolution.py \
  tests/vermes_cli/test_api_key_providers.py \
  tests/vermes_cli/test_custom_provider_model_switch.py \
  -p no:xdist -o addopts="-m 'not integration'" --basetemp="$TMPDIR/b" -q
# 结果：308 passed, 0 failed
```

## 三、QClaw 做对了什么（诚实记录，非挑刺）

1. **删明文同时改解析函数**——这是本次修复的命门。只删 `entry["api_key"]=...` 而不同步把 `os.getenv` 改成 `get_env_value`，桌面 dev 直启（uvicorn 不注入 dotenv）必断。QClaw 两处都改了（`auxiliary_client.py:3602` + `runtime_provider.py:509`），方向正确。
2. **保留 legacy inline 回退**——`runtime_provider.py:510-512` 与 `:676` 在 `key_env` 解析不到时仍回退 `entry["api_key"]`，旧 config / 未迁移项不会崩。纵深设计合理。
3. **迁移脚本安全优先**——env 同名冲突时「不覆盖、留 inline、continue」，宁愿留明文也不丢凭证；占位符（ollama/local）剥离不写 `.env`；round-trip YAML 保注释。零丢失。
4. **测试覆盖到点**——3 个 runtime 测试专门覆盖「key 只在 .env 文件、不在 os.environ」这一核心场景，正是回归风险点。

## 四、待修账（MiMo 收口用，编号稳定）

| # | 严重度 | 位置 | 问题 | 状态 |
|---|---|---|---|---|
| 1 | 🟠 P2 一致性 | `vermes_cli/blueprints/studio.py:604` | `_resolve_key_entry` 用 `os.environ.get(key_env)`，无 `.env` 文件回读。创作工作室直连厂商场景在「dev uvicorn 不注入 dotenv」下解析不到 key——与本次修复针对的同类 bug 未修干净（桌面 App 因 dotenv 注入不受影响，仅 dev 路径）。 | 待修（建议改 `get_env_value`） |
| 2 | 🟠 P2 一致性 | `vermes_cli/model_switch.py:1525` | `api_key = os.environ.get(key_env)` 无 `.env` 回读。自定义端点 `/models` 自动发现会失败（仅影响模型列表枚举，不影响真实 API 调用）。 | 待修（建议改 `get_env_value`） |
| 3 | 🟡 P3 死代码/误导 | `vermes_cli/runtime_provider.py:677` | `os.getenv(str(custom_provider.get("key_env")...))` 是**死代码**：此处 `custom_provider` 是 `_get_named_custom_provider` 的返回 dict，根本不携带 `key_env` 字段，恒返回 `""`；且上一行 `:676` 已从返回 dict 的 `api_key`（= 已解析值）取到正确 key。不会真断，但易误导后续维护者以为这里是解析点。 | 待清（删除或改 `_resolve_env_key` 以与主线一致） |
| 4 | 🟡 P2 数字/文档纪律 | `reports/qclaw/audit-plaintext-provider-key_20260920.md` + commit message | 回归数字失真：报告「183/1」实测「184/0」；commit message 称 17 个 `_stamp_preset` 失败，但当前 HEAD 已修复。Vermes 一贯的数字失真问题再次复现，需修正报告而非仅认领。 | 待修正报告 |
| 5 | ✅ P1 已自然消除 | `runtime_provider.py:71-74` | `_stamp_preset` 签名 bug：QClaw 报告称「留待另立工单未动」，但当前 HEAD `d88b05e9` 已加默认值 `preset_spec=None, preset_name=None` 修复；`test_runtime_provider_resolution.py` 原 16 例 + `test_api_key_providers.py` 原 1 例全部转绿。 | 已消除（报告需更新状态） |
| 6 | 🟡 P3 收尾 | `reports/qclaw/audit-plaintext-provider-key_20260920.md` | 该报告仍为 **untracked**，且状态仍标「待 MiMo 交叉审计」。本交叉审计结论应回写或另立文件，并在 §12 冻结纪律下由董董决定是否提交。 | 待收尾 |

> 说明：`agent_init.py:814` 与 `chat_completion_helpers.py:1103` 也用 `os.getenv(key_env)` 算 fallback 提示 key，但二者都把结果作为 `explicit_api_key` 传给 `resolve_provider_client`，后者内部（`:3602`）会再用 `get_env_value` 解析，故**不构成回归**，不入账。

## 五、安全面复核（QClaw 点名项）

- `_resolve_env_key`（runtime_provider.py:74-89）：无 `logger`，异常兜底 `os.getenv`，不打印值。✅
- `auxiliary_client.py:3592-3606`：仅 `logger.warning`（no-key-required 分支），不涉及明文值。✅
- `get_env_value`（config.py:5269）：os.environ 优先 → `load_env()`（读 .env 文件）；不打印值。✅
- 未发现任何把 key 写进日志/异常的路径。✅

## 六、迁移脚本安全性复核

- 幂等：重跑时 provider 已无 `api_key`（除 env-conflict 保留项），循环 `if not raw_key: continue` 直接 skip → no-op。✅
- 零丢失：`env-conflict-skip` 分支在 `.env` 已有**不同**值时「不覆盖、留 inline、continue」，宁可留明文也不丢凭证。✅
- 导入点全部存在（见第一节），脚本可安全重跑。✅
- 备份：`~/.vermes/config.yaml.bak-20260920_200135` + `.env.bak-20260920_200135` 存在，回滚路径明确。✅

## 七、最终判定交付建议

1. **核心修复 + 迁移：放行**（优化非退化，功能链路正常，安全面干净）。
2. **账 #1/#2**：建议在合并前把 Studio / model_switch 的 key 解析也走 `get_env_value`，彻底消除「dev-uvicorn-无-dotenv」这一类隐患（董董踩过的真实场景）。
3. **账 #3**：顺手删掉 `runtime_provider.py:677` 的死代码或改 `_resolve_env_key`，消除维护误导。
4. **账 #4/#5/#6**：更新 QClaw 报告数字与状态，并把本报告/更新后的报告在 §12 冻结下由董董提交。
