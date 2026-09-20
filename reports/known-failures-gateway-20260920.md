# `tests/gateway` 已知失败隔离清单（2026-09-20）

- 基线：`b7305707d4`（A1/A2 已提交后）
- 全量结果：**13 failed / 5820 passed / 13 skipped / 4 xfailed / 1 xpassed**（1614.88s，27 分钟）
- **对照实验（2026-09-20 08:15 补做，结论已升级为「已证明」）**：
  `git checkout eaa63411a2 -- gateway/{gateway_utils,message_handler_mixin}.py cron/scheduler.py`（回退到 A1/A2 **之前**的源码）后复跑同一批 13 条 → **同样 13 failed**。
  → **这 13 条与 A1/A2 改动无关，属 pre-existing。**（此前仅靠「失败原因 + 零引用」定性，现已用对照实验坐实。）
- 用途：避免下次真回归被这 13 条噪声淹没。

---

## 修复进度（2026-09-20 09:25，WorkBuddy W1–W4 收尾）

**13 条 → 剩 6 条**，其中 7 条是**真修**（不是隔离、不是改测试期望蒙混）：

| 类别 | 原条数 | 处置 | commit / 落点 |
|---|---|---|---|
| B 品牌大小写（irc） | 2 | ✅ 真修：测试期望 `VERMES_`→`Vermes_`（5 处）+ 源码注释 `:414` 订正 | `f89435a43c` |
| E reconnect | 4 | ✅ 真修：**两个真 bug**（孤儿 dict 致 circuit breaker 永不触发 + 不可重试平台死分支无限重试） | `f89435a43c` |
| G email self-message | 1 | ✅ 真修：`gateway/platforms/email.py:437` 两侧归一化后比较 | 本轮（W3） |
| A 环境缺依赖（voice） | 2 | ⏸ 未动（噪声，需补装 `davey`/`discord` 或声明可选 skip） | — |
| C 中文化遗留 | 1 | ⏸ 未动（`test_resume_command.py:88` 期望英文） | — |
| D 工具集期望漂移 | 1 | ⏸ 未动（`test_api_server_toolset.py:129`） | — |
| F runner 启动降级 | 2 | ⏸ 未动（噪声，无 adapter 可加载） | — |

> 注：A/F 共 4 条为环境噪声，C/D 为测试期望过时——均需单独决策，**不在本轮 W1–W4 范围**。
> 本轮同时新增 A3 提示去重（`gateway/notices.py`），与失败清单无关。

---

## 分类总表

| 类别 | 条数 | 性质 | 处置建议 |
|---|---|---|---|
| A 环境缺依赖 | 3 | 噪声（本机未装可选依赖） | 隔离 |
| B 品牌重命名遗留（Hermes→Vermes） | 2 | **真问题**，重命名 sweep 没扫干净 | 独立修，不隔离 |
| C 中文化遗留 | 1 | 真问题（测试期望英文文案） | 独立修，不隔离 |
| D 工具集期望漂移 | 1 | 真问题（测试期望列表过时） | 独立修，不隔离 |
| E reconnect 断言 | 4 | 待判定 | 先判定再定 |
| F runner 启动降级 | 2 | 噪声（无 adapter 可加载） | 隔离 |
| G **self-message 过滤** | 1 | **⚠️ 疑真 bug，不是噪声** | **先人工判定，禁止隔离** |

---

## 明细

### A · 环境缺依赖（3，噪声）

| 测试 | 失败原因 |
|---|---|
| `test_voice_command.py::TestVoiceReception::test_on_packet_dave_known_user_decrypt_ok` | `No module named 'davey'`（DAVE 解密未装） |
| `test_voice_command.py::TestVoiceReception::test_on_packet_dave_unencrypted_error_passthrough` | 同上 + `No module named 'discord.utils'` |
| `test_runner_startup_failures.py::test_runner_degrades_gracefully_when_all_adapters_missing` | `no adapter available for telegram; no adapter available for discord` |

> 根因：测试环境未声明可选依赖。要么补装，要么在 CI 里声明 `davey`/`discord` 为可选并 skip。

### B · 品牌重命名遗留（2，**真问题**）

```
tests/gateway/test_irc_adapter.py:223   assert 'Vermes_' == 'VERMES_'
tests/gateway/test_irc_adapter.py:379   assert 'Vermes_' == 'VERMES_'
```

IRC nick collision 生成的 nick 前缀是 `VERMES_`（旧品牌大写），测试期望 `Vermes_`。→ **Hermes→Vermes 重命名 sweep 没扫干净**，建议全仓搜 `VERMES_` / `HERMES_` 大写残留。

### C · 中文化遗留（1，真问题）

```
tests/gateway/test_resume_command.py:88
  assert 'Research' in '未找到已命名的会话。\n使用 `/title 我的会话` ...'
```
测试期望英文输出，实际已是中文。→ 改测试期望，或补 i18n 开关。

### D · 工具集期望漂移（1，真问题）

```
tests/gateway/test_api_server_toolset.py:129
  assert ['google_meet...', 'terminal', 'web'] == ['terminal', 'web']
```
实际列表多出 `google_meet` 等。→ 测试期望列表过时（或新增平台未同步测试）。

### E · reconnect 断言（4，待判定）

`test_platform_reconnect.py` :255 / :294 / :339 / :472
- `assert <Platform.TELEGRAM> not in {...}`（重试队列残留）
- `assert 1 == 2`（attempts）
- `assert 1 == 10`（circuit breaker 阈值）

> 未逐条定位根因。**不能默认当噪声**——reconnect 行为回归正是这类测试要抓的。建议单独排期判定。

### F · runner 启动降级（2，噪声）

| 测试 | 失败原因 |
|---|---|
| `test_runner_fatal_adapter.py:64` | `assert False is True`（nonretryable startup conflict 未触发 clean exit） |
| 见上 `test_runner_startup_failures.py` | 同 A 类 |

### G · ⚠️ self-message 过滤（1，**疑真 bug，禁止隔离**）

```
tests/gateway/test_email.py::TestDispatchMessage::test_self_message_filtered
  adapter._message_handler.assert_not_called()
  AssertionError: Expected 'mock' to not have been called. Called 1 times.
  → MessageEvent(source=SessionSource(platform=EMAIL, chat_id='Vermes@test.com',
      user_id='Vermes@test.com', ...))
```

自发自收消息**未被过滤**，handler 被真的调用了一次。`chat_id` 与 `user_id` 相同（`Vermes@test.com`），本应命中 self 过滤。
→ 这可能是**真实的过滤失效**（或断言口径变更），**不得作为噪声隔离**，需人工判定后再处置。

---

## 隔离机制（三个选项，需拍板——未改任何配置）

| 选项 | 做法 | 风险 |
|---|---|---|
| 1（推荐） | 只在**本地/CI 基线对比**时用命令行 `--deselect` 跑，不改仓库配置 | 无持久化影响；但每次要带长命令 |
| 2 | 写进 `pytest.ini` / `setup.cfg` 的 `addopts --deselect …` | ⚠️ 全局静默跳过，**会掩盖真回归**；且 E 类（4 条）尚未判定 |
| 3 | 给每条加 `@pytest.mark.xfail(strict=True)` | 需改既有测试文件；`strict=True` 在修复后会报 XPASS 提醒清理，比 deselect 安全 |

**我的建议**：先只做「选项 1 + 本文档」，并把 B/C/D/G 四类（共 5 条）列为**必修项**而非噪声；E 类判定后再决定是否纳入隔离。G 类**永远不隔离**。

复现命令：

```bash
cd ~/projects/vermes-electron
BT=$(mktemp -d /tmp/vtest.XXXXXX)   # 沙箱下必须指定 basetemp，否则 tmpdir 被拦
.venv/bin/python -m pytest tests/gateway -p no:xdist -o addopts="" --basetemp="$BT" -q
```

---

## 独立待办（不属本清单，但同源暴露）

1. **品牌重命名 sweep 收尾**：全仓搜大写残留 `VERMES_` / `HERMES_`（IRC 已暴露 2 处，可能还有）。
2. **测试环境声明**：`davey` / `discord` 等可选依赖未声明，导致 3 条环境性失败。
3. **E 类 4 条 reconnect 断言**：根因待定位，不要默认当噪声。
