# 交接报告 · WorkBuddy → QClaw（2026-09-20）

> 本轮由 WorkBuddy 执行 **A7（首条 DM 自动设定 home channel）** + **M5-a（yuanbao 写路径保注释）**，
> 并点验了 mimo 的 **M5**。以下全部为实跑结果，工单板原文未删。

---

## 0. 盘面（交接时状态）

```
仓库：/Users/dongzusheng/projects/vermes-electron
HEAD：7458fd023d (main, fix/m5-config-yaml-roundtrip)  ← mimo 的 M5
ahead origin：32
未 push（红线，两边都守）：是
```

**本机路径坑（必读）**：`~/projects` 与 `~/Projects` 是同一物理目录的软链。

---

## 1. A7 · 首条 DM 自动设定 —— 已实现，16 测试全绿

### 1.1 干了什么

新平台第一次收到**已授权用户的私聊**时，自动把该会话设为 home channel，并回执告知。
目的：GUI-only 用户从不学 `/sethome`，A1/A2 修好"设了能被认"对他们无效。

### 1.2 改动文件

| 文件 | 改动 |
|---|---|
| `gateway/message_handler_mixin.py` | 新增 `_maybe_auto_set_home_channel()` + 两个副作用 helper；调用点由「直接 prompt」改为「auto-set 失败才 prompt」 |
| `gateway/gateway_utils.py` | 新增 `auto_set_home_channel_enabled()`（开关）、`env_home_channel_chat_id()`（廉价闸门） |
| `gateway/notices.py` | 新增 notice key `home_channel_autoset`（**独立于** `home_channel_missing`） |
| `tests/gateway/test_autoset_home_channel.py` | 新建，16 用例 |

### 1.3 三道闸门（安全性设计，QClaw 接续时请勿削弱）

1. **授权已在更上层完成** —— `_handle_message` 顶部的 `_is_user_authorized` 会先 return，
   能走到这里的必然是 allow-list 内的人。**A7 自己不做第二遍授权判断**
   （已有上述证据锚点，是刻意设计，不是遗漏）。
2. **仅 DM** —— `chat_type != "dm"` 直接跳过；`chat_type` 为空**不**当作 DM（有些 adapter 在群聊时不设置该字段，
   fail-open 就是 gate 2 要防的事）。bot 消息亦跳过。
3. **绝不覆盖既有 home channel** —— 用与 cron 投递同一个 `resolve_home_channel_chat_id`，两边口径不会打架。

### 1.4 我做的一个决策（请你复决，可推翻）

**默认开启**，用 `VERMES_AUTO_SET_HOME_CHANNEL=false` 关闭。

理由：能触发者已过 allow-list；DM-only 排除了「拉 bot 进群劫持通知目标」；A7 的意义就是让 GUI 用户零配置获益，
默认关闭等于没做。残余风险是「哪个授权用户先 DM 谁赢」，可用 `/sethome` 迁移。

**未支持 config.yaml 开关**（刻意）：GUI 目前无对应字段，加了用户也改不了，只有手编 YAML 的人会用 —— 与 env 同受众。
将来要加：在 `auto_set_home_channel_enabled()` 里 env 之后补 `_load_gateway_config().get("gateway", {}).get("auto_set_home_channel")`，**并同改 GUI**。

**解析不了的值 fail-closed** —— 拼错成 `flase` 会关闭而非开启（写用户配置文件这种副作用，猜错方向代价太大）。

### 1.5 顺手修的性能问题（A7 引入，但 W4 已有隐患）

`resolve_home_channel_chat_id` 的 config 分支会走 `_load_gateway_config()` = **读文件**。放在每消息路径上，
等于每条入站消息解析一次 config.yaml。加了 `env_home_channel_chat_id()` 廉价闸门：env 是最高优先级分支，
命中即等价于完整解析，**零 IO**。语义严格等价，不会引入分叉。

---

## 2. M5-a · yuanbao auto-sethome 保注释 —— 已实现，5 测试全绿

**这是被漏掉的第四条写路径**，且在 `gateway/`（我域），原本根本没被列进 M5 工单：

`gateway/platforms/yuanbao.py:1566 AutoSetHomeMiddleware` 用 `atomic_yaml_write`（原子但 `safe_load` → `yaml.dump` 全量重写），
会在**第一条入站消息**时抹掉用户 config.yaml 的全部注释 —— 而那恰恰是手工配置最可能还带笔记的时刻。

改为 `atomic_roundtrip_yaml_update(...)`。新增 `tests/gateway/test_yuanbao_autoset_home.py`（5 用例），
含真 middleware 驱动 + 回读文件断言三条注释全部存活 + 写失败不阻断 pipeline。

---

## 3. 点验 mimo 的 M5（`7458fd023d`）—— 通过

| 声称 | 我的实测 |
|---|---|
| 三个 helper 真实存在 | ✅ `utils.py:240 load_roundtrip_yaml` / `:256 atomic_roundtrip_yaml_dump` / `:282 atomic_roundtrip_yaml_mutate` |
| 真行为测试 | ✅ `tests/vermes_cli/test_m5_config_yaml_roundtrip.py` + M4 套件 + A1 契约测试 **28 passed** |
| `yaml.dump` 残留为 0 | ⚠️ **不成立**，见 §4 |

> 注意：`atomic_roundtrip_yaml_update` 被 mimo 顺手重构成了内部 `_mutate`，
> 我验证了它对**顶层无点 key** 仍正确（`keys[:-1]` 为空 → 直接作用于根 map），M5-a 依赖这点，已用测试锁住。

---

## 4. ⚠️ 未决项：M6 —— 注释抹除还有第 5、6 条路径

我用 Grep 工具**全仓复搜**（不是 shell grep，本次刻意避开第 7 次假阴性），排除 `dist/ build/ tests/ node_modules/` 后，非测试代码里仍在 `yaml.dump`/`safe_dump` 写 YAML 的点包括：

| 位置 | 是否在 M5 范围 | 备注 |
|---|---|---|
| `gateway/platforms/telegram.py:1225` | ❌ **未处理** | **最可疑**。原子写（tmp+fsync+replace）但 `_yaml.dump(config, f, sort_keys=False)`，`config_path` 疑似 `~/.vermes/config.yaml`（更新 dm_topics 的 thread_id）。**在 gateway/ 域** |
| `tui_gateway/server.py:686` | ❌ 未处理 | `yaml.safe_dump(cfg, f)` |
| `vermes_cli/blueprints/chat.py:3326` | ❌ 未处理 | `yaml.safe_dump(merged, ...)` |
| `vermes_cli/profiles.py:569` | ❌ 未处理 | `yaml.safe_dump(existing, f)` |
| `agent/memory_reflection.py:611` | ❌ 未处理 | 可能不是 config.yaml |
| `plugins/memory/holographic/__init__.py:144` | ❌ 未处理 | 可能不是 config.yaml |
| `migrations/2.0.3_to_2.0.5.py:53` | ⏸ 一次性迁移 | 可接受 |

**我停下来没继续判定的原因**：要逐条确认「写的是不是 `~/.vermes/config.yaml`」+ 「原 load 是否已是 round-trip」，
每条都要读上下文，时间不够。**我没有把这些标成已修复，也没有确认它们是真缺陷** —— 交给 QClaw。

建议起手 `gateway/platforms/telegram.py:1225`：它在 gateway/ 域、写法明显、且 dm_topics 更新是日常路径。

---

## 5. A7 里我**没做**的部分

**「取消入口」未实现**。路线图原文要求回执"带取消入口"，但仓库里只有 `/sethome`（迁移），没有 clear/unsethome。
我没有编一个不存在的命令写进回执文案（那是 UX 误导），当前回执给的是真实可用的 `/sethome`（可迁移）+ 关闭开关。

要做真取消：`gateway/slash_handlers/config_handlers.py:855` 的 `/sethome` handler 里加 `clear` 参数处理，
并补 i18n `gateway.set_home.*`。**属新工单，我没动。**

---

## 6. 给 QClaw 的下一步（按建议顺序）

| # | 动作 | 归属 | 为什么 |
|---|---|---|---|
| 1 | 逐条判定 §4 表格里 6 个疑似点的实际受害者范围（是否真写 config.yaml） | 建议 QClaw 先看 `telegram.py:1225` | 它在 gateway/ 域、写法明显、dm_topics 更新是日常路径；其余 5 个在 vermes_cli/ 可能需要再找 mimo |
| 2 | 若 1 成立，起 **M6** 工单统一收口 | 对应文件足迹的拥有者 | 不要让 P1 第 N 次沦为「名义关闭」 |
| 3 | A7「取消入口」`/sethome clear` | gateway/（原属我） | 路线图 A7 的收尾项 |
| 4 | 跑一次 broader regression（`tests/gateway` 子集） | QClaw | 我只跑了受影响的定向子集，**没跑全量** |
| 5 | push / 打 tag | **董董专属** | 红线：agent 不 push |

---

## 7. 验证配方（照抄，不要重新试错）

```bash
cd /Users/dongzusheng/projects/vermes-electron
BT=~/wb-tmp/bt-$RANDOM
TMPDIR=~/wb-tmp .venv/bin/python -m pytest <paths> -q -p no:xdist -o addopts="" --basetemp=$BT
```

- **必须 `TMPDIR=~/wb-tmp`**：沙箱拦 `/private/var` 与 `/tmp` 的 mkdir，默认 tmpdir 会让整批测试变 ERROR（看着像代码崩了）。
- **必须 `--basetemp` 指向不存在的目录**：指向已存在的目录也会报 EEXIST。
- **必须 `-p no:xdist -o addopts=""`**：否则 xdist scheduler 崩溃。
- **`git --no-pager`**：git 进 pager 会挂住导致 exit 137。
- **`.git/index.lock` 残留**：先 `ps aux | grep [g]it` 确认无进程，再 `rm -f .git/index.lock`。
- **否定性结论一律用 Grep 工具，不用 shell grep**（本轮第 7 次遇到 shell grep 假阴性：同一个文件 Grep 命中、shell grep 返回空）。

### 本轮测试证据汇总

| 套件 | 结果 |
|---|---|
| `test_autoset_home_channel.py` | 16 passed |
| `test_yuanbao_autoset_home.py` | 5 passed |
| `test_notices_dedup.py` | 15 passed |
| `test_m5_config_yaml_roundtrip.py` + `test_m4_home_channel_gui.py` + `test_home_channel_resolution.py` | 28 passed（mimo M5 点验） |
| mimo M5 测试 + M4 + A1 契约（合跑） | 28 passed |
| A7 + notices + yuanbao（合跑） | 36 passed |

### 变异测试记录（证明断言非恒真，全部已回退、零残留）

| 探针 | 复红数 | 命中的用例 |
|---|---|---|
| A7 变异 A：DM 闸门失效 | 2 | group 不可采纳、空 chat_type 不当 DM |
| A7 变异 B：允许覆盖既有 home | 1 | 既有 home 不被覆盖 |
| A7 变异 C：不记录去重标记 | 5 | 只采纳一次、首次采纳、部分成功、回执失败、notice key 隔离 |
| A7 变异 D：只写内存不落盘 | 10 | 含真落盘集成用例 |
| M5-a 变异：退回 `atomic_yaml_write` | 1 | `# my hand-written note` 被抹 |

---

## 8. 一句话交代

A7 和 M5-a 代码 + 测试已完成并附带 5 轮变异佐证，**尚未 commit**（交接时仍在工作树）。
mimo 的 M5 点验通过。遗留最大项：**注释抹除远不止 M4/M5 那几条**（§4），这是下一棒最该先看的地方。

> 工作树状态（交接时）：
> ```
>  M gateway/gateway_utils.py
>  M gateway/message_handler_mixin.py
>  M gateway/notices.py
>  M gateway/platforms/yuanbao.py
>  ?? tests/gateway/test_autoset_home_channel.py
>  ?? tests/gateway/test_yuanbao_autoset_home.py
>  ?? SKILLS_INDEX_OPTIMIZATION_SPEC_2026-09-20.md   ← 非本轮产物，勿动
> ```
