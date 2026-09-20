# 交叉审计：WorkBuddy 审 mimo M1–M4（`9bbf2f1ece`）

- 审计方：WorkBuddy
- 被审计对象：`9bbf2f1ece feat(p0a-mimo): M1–M4`（12 文件 +798 / −48）
- 方法：**全部实测复跑**，不采信回执描述。审计脚本/命令见每条「证据」。
- 结论：**1 条 P1（实证）、3 条 P2、4 项通过**。无虚构项，无越界改动。

---

## 总表

| ID | 判定 | 关键结论 |
|---|---|---|
| M1 | ✅ 通过 | 脚本可跑，数字复现一致 |
| M2 | ✅ 通过（**且修正了工单板**） | 版本 2.5.0 实测属实，工单板的 2.4.9 才是错的 |
| M3 | ✅ 通过 | 两 lane `if: false` 确认；js-tests 依赖齐备，不会误伤 |
| M4 | ⚠️ **P1 ×1 + P2 ×3** | 读侧单一口径 ✅；写路径会**丢 config.yaml 全部注释**（实证 21→0） |

---

## M1 · 分歧度量脚本

**✅ 通过。**

```
$ .venv/bin/python scripts/diverge_metrics.py ~/.hermes/hermes-agent .
  browser_*: 上游 19 / Vermes 12
  kanban_*:  上游 14 / Vermes 9
  toolsets.py 0.09 / model_tools.py 0.12 / utils.py 0.15 / context_compressor.py 0.09
  runtime 静默失败: 上游≈243 / Vermes≈936
```
复跑输出与 `reports/diverge-baseline-20260920.md`、与回执三方一致。

> 一处可忽略差异：回执写「近 90 天新增 3 行 / 3 提交」，本次实跑为 **5 行 / 4 提交**（`detail={'adds': 5, 'commits_touched': 4}`）。
> 原因应是我这次跑的 HEAD 比 mimo 跑基线时更新，属时间漂移，**不是错报**。

---

## M2 · UPSTREAM_SYNC.md 版本数字

**✅ 通过，且 mimo 是对的、工单板是错的。**

```
$ cat version.txt                        → 2.5.0
$ grep '"version"' package.json          → 2.5.0
$ grep '"version"' frontend/package.json → 2.5.0
$ grep '"version"' electron/package.json → 2.5.0
```
四处一致 = **2.5.0**。工单板原文写 2.4.9 → **已订正**（本次一并提交）。
文档里标了「实测 2026-09-20 + 复现命令」，并把旧 0.18/v2.3 列为废弃，符合 E5/E11 的取证口径。

---

## M3 · CI lane

**✅ 通过，未误伤。**

| 检查 | 结果 |
|---|---|
| `rg "if: false" tests-os.yml install-e2e.yml` | 两条占位确认（含注释说明「何时删」） |
| `js-tests.yml` 依赖是否齐备 | `frontend/package.json`：`"test": "vitest run"`、`vitest ^4.1.10`、`package-lock.json` 存在（184KB，2026-09-18）→ **能真跑** |
| 是否重复已有 lane | 未重复 `uv-lockfile-check.yml`；workflows 从 14 → 17 |

一个诚实提醒：`js-tests` 是三条里**唯一真跑**的，它会在 PR 上真实失败（若前端测试崩）。这是应该的，不是缺陷——只是别误以为三条都还是占位。

---

## M4 · home channel GUI + HTTP 面

### 通过的部分

| 项 | 证据 |
|---|---|
| 读侧单一口径 ✅ | `vermes_cli/gateway_channels.py:621 read_home_channel` → `:625` 只调 `resolve_home_channel_chat_id`；无第二套解析 |
| 写/读路径同源 ✅ | 写侧 `os.environ["VERMES_HOME"] \|\| ~/.vermes`；读侧 `vermes_cli/config.py:361 get_vermes_home()` **同样先读 `VERMES_HOME`**（:370）→ 不会「写 A 读 B」 |
| blueprint + 前端接口一致 ✅ | `GET/PUT /gateway/channels/{key}/home-channel` ↔ `api.js getGatewayChannelHome / putGatewayChannelHome` 路径对得上；`_schema_to_dict` 增 `home_channel` 字段 |
| 测试真绿 ✅ | `test_m4_home_channel_gui.py + test_home_channel_resolution.py` **20 passed**（见下方环境坑） |

> **环境坑（会影响复跑，非代码问题）**：WorkBuddy/沙箱下 pytest 默认 tmpdir（`/private/var/...`）与 `/tmp`（→`/private/tmp`）都会被 shim 拦 mkdir，报
> `PermissionError: EEXIST: file already exists, mkdir '/private/tmp/pytest-of-root'`。**这会让测试假红。**
> 可用配方：`BT=~/wb-tmp/bt-$RANDOM; TMPDIR=~/wb-tmp .venv/bin/python -m pytest <paths> -q -p no:xdist -o addopts="" --basetemp=$BT`
> （`--basetemp` 目录若已存在同样报错，必须每次新鲜。）

### P1 · `write_home_channel` 会抹掉 config.yaml 的全部注释（实证）

`vermes_cli/gateway_channels.py:690` 用 `yaml.dump` **全量重写** config.yaml。实测：

```
原文：681 行，其中注释行 21
写入 platforms.telegram.home_channel 后：661 行，注释行 0
```

损失不可逆（用户手写注释、排版、空行、键顺序全部重组），且发生在**第一次 GUI 保存**时。
config.yaml 里存着 provider api key 等配置，被重写后虽能解析，但diff 会是一整片重写，Code Review 与排障都会被污染。

**建议修法（按优先级）**：
1. `ruamel.yaml` round-trip —— 本机已装（实测 `0.18.17`），保留注释/顺序，改动最小；
2. 仓库已有 `from utils import atomic_yaml_write`（`gateway/platforms/yuanbao.py:1590` 在用）——至少用它替代裸 `write_text`，先解决原子性；
3. 降级方案：只定位并替换 `platforms.<key>.home_channel` 区块的行区间，其余字节原样不动。

### P2 · 写入非原子

现状是直接 `cfg_path.write_text(...)`。写到一半进程被杀 → config.yaml 截断损坏。
同 files_utils 生态里已有 `atomic_yaml_write`，无需手写 `tmp + os.replace`。

### P2 · `except Exception: pass` 造成「返回成功但实际没落盘」

```python
try:
    save_env_value(env_key, chat_id)
except Exception:
    pass
```
但函数末尾 `return read_home_channel(key)` —— 读的是**刚 set 过的 `os.environ`**，所以即使 `.env` 写失败，返回值依然显示新值，HTTP 返回 `{"ok": true}`。
这是「静默失败」，而 M1 恰恰在 measuring 这类静默失败（Vermes runtime ≈936）——**自己的补丁又引入了一处**，口径上应当一致对待。

补充：`managed_error`（`vermes_cli/config.py:298`）只 `logger.warning` **不抛异常**，所以 managed 模式下 `save_env_value` 会静默 `return`，连上面的 `except` 都不会触发。功能上仍能成立（config.yaml 那一腿写了），但 UI 会失真。

**建议**：让 `read_home_channel` 的返回带 `persisted` 状态，或把 `save_env_value` 的失败显式冒泡给 HTTP 层返回 5xx。

### P3 · 列表接口 N 次配置加载

`_schema_to_dict`（blueprint `:243`）对每个 schema 都调一次 `read_home_channel` → 每次走 `_load_gateway_config()`。
 channel schema 约 30 个 → 一次列表请求读 30 遍 config.yaml。量级不大（本地文件），但打列表接口时值得合并成一次加载后透传。

---

## 边界与红线

| 检查 | 结果 |
|---|---|
| 是否碰 `vermes_state.py` / `gateway/` / `cron/` | ✅ 未碰（`gateway_channels.py` 在 `vermes_cli/`，blueprint 是其 HTTP 壳） |
| 是否 push | ✅ 未 push |
| §4 跨界登记 | ✅ 已登记 blueprint HTTP 面 + A7 留给 W 侧 |

---

## 待办归属

| 项 | 归属 | 说明 |
|---|---|---|
| P1 yaml 注释丢失 | **mimo**（文件在其足迹内） | WorkBuddy 不改 `vermes_cli/` |
| P2 原子写 / P2 静默失败 / P3 N 次加载 | **mimo** | 同上 |
| A7 首条 DM 自动设定 | **WorkBuddy**（W 侧，落 `gateway/`） | W4 已建 `gateway/notices.py` 去重域，auto-set 若要写在同一个通知决策点上，口径一致；**本轮未做，待拍板** |
| 工单板 2.4.9 → 2.5.0 | WorkBuddy | 已随本次审计订正 |

---

# 返工点验（`e697aa9066`，2026-09-20 09:50）

针对上面提的 P1 / P2 ×2 / P3 逐条复验，**外加变异测试**。全部实跑，不采信回执。

## 结论总表

| 项 | 处置 | 点验结论 |
|---|---|---|
| P1 注释被 `yaml.dump` 抹掉 | 改用 `utils.atomic_roundtrip_yaml_update` | ✅ **关闭** |
| P2 非原子写 | 同上 helper | ✅ **关闭** |
| P2 吞 `save_env_value` 仍报 ok | 返回 `ok=false` + `env_error`，失败时不同步 `os.environ` | ✅ **关闭** |
| P3 列表 N 次读盘 | `read_home_channel(key, config=)`，blueprint 复用 `_load_config_yaml()` | ✅ **关闭** |
| — | — | ⚠️ **新发现：P1 只关了一半**，见下 |

## 逐条证据

**helper 是真存在的**（不是自造函数名）：`utils.py:203 def atomic_roundtrip_yaml_update`，
内部 `ruamel.yaml.YAML(typ="rt")` + `preserve_quotes=True` + `tempfile.mkstemp` + `os.replace`（见 `:221-250`）。
调用点 `vermes_cli/gateway_channels.py:676`。

**新增两条是「真行为测试」，不是读源码自证**：
- `test_config_yaml_preserves_comments`：预写 `# user comment keep me` / `# inner note` → 调 `write_home_channel` → **回读文件**断言注释仍在。
- `test_env_write_failure_is_not_silent_ok`：monkeypatch `save_env_value` 抛 `OSError` → 断言 `ok=False`、`env_error` 非空，且 `os.environ` **没被写成假成功值**。

**复跑**：`test_m4_home_channel_gui.py + test_home_channel_resolution.py` **22 passed**（与回执一致）。
连同 W 侧回归一起跑：`tests/vermes_cli/test_m4_home_channel_gui.py + test_home_channel_resolution.py + tests/gateway/test_notices_dedup.py + tests/gateway/test_email.py` = **97 passed**。

**变异测试 ×2（证明新断言不是恒真）**：

| 变异 | 结果 |
|---|---|
| A：把 helper 换回旧的 `safe_load → yaml.dump` | `test_config_yaml_preserves_comments` 红，`AssertionError: '# user comment keep me' not found in 'platforms:\n feishu:\n…'` —— **失败原因正当**（确实是被抹了） |
| B：把 `env_error` 改回吞掉、`os.environ` 无条件同步 | `test_env_write_failure_is_not_silent_ok` 红（`True is not false`） |

两个探针均已回退，`grep MUTATION-PROBE` 零残留，工作树干净。

**P3 落实路径正确**：`_schema_to_dict` 传入的 `config_data` 来自 `_load_config_yaml()`（`:253`），
是全量 config dict，结构与 `config_home_channel_chat_id()` 的期望一致（`:170/:194` 本来就按 `platforms.<key>` 取），**不会因传 dict 而读空**。

**mimo 主动披露的 P3 残留属实且定性准确**：
`blueprints/gateway_channels.py:61-67 _save_config_yaml` 仍是 `safe_load → mutate → yaml.dump`。
`git log -S"_save_config_yaml"` → 引入于 `9304923c5c feat(brand): Vermes 彻底 fork 品牌化`，**早于 M4**，确属既有代码。

---

## ⚠️ 新发现：P1 只关了一半（同一 root cause 的第二条写路径）

`_save_config_yaml`（`:61-67`）被三个端点调用，全部是 GUI 常用操作：

| 端点 | 触发场景 |
|---|---|
| `PUT /gateway/channels/{key}`（`:337`） | **保存平台凭据** |
| `DELETE /gateway/channels/{key}`（`:374`） | 清除凭据 |
| `POST /gateway/channels/{key}/toggle`（`:399`） | **启用/禁用开关** |

这三处走的还是 `safe_load → yaml.dump` 全量重写 → **同样会把 config.yaml 的 21 行注释抹掉**。

也就是说，用户实际路径是：
```
GUI 设 home channel   → 注释保留 ✅（新 helper）
GUI 改任一平台凭据/开关 → 注释照样全丢 ❌（旧路径）
```
只修 home-channel 这一条腿，**P1 的破坏面并未消除**，只是从「每次 GUI 保存」变成「每次改凭据/开关」。

**建议（新工单 M5，归 mimo，文件仍在其足迹内）**：把 `_save_config_yaml` 也换成 ruamel round-trip。
难点（也是我看它比 M4 难的地方）：这三处要改的是**任意多个嵌套键**（`platforms.<key>.token/api_key/extra.*`、顶层旧段清理、`enabled`），
不是单一 dotted key —— `atomic_roundtrip_yaml_update` 的单键签名不够用，需要在 `utils.py` 加一个 `CommentedMap` 级别的
`atomic_roundtrip_yaml_write_whole(path, mutate_fn)`：读入 CommentedMap → 交给回调原地改 → 原子写回。
另 `save_channel` 里有 `plat_data.pop(...)` / `extra.clear()` —— 这些在 CommentedMap 上是**支持注释保留的**，改造可行性没问题。

> 本轮**不做**：`vermes_cli/` 是 mimo 足迹，我不越界改。已在工单板 §3 登记为 M5 待领。
