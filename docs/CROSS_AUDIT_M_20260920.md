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
