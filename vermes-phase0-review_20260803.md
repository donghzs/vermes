# Phase 0 设计评审（对照 GLM5.2 规约做源码级核验）

> 2026-08-03，评审对象：`~/.qclaw/workspace/phase0-design-spec_20260803.md`
> 方法：每条可证伪断言都去源码 `agent/module_loader.py` / `tools/registry.py` / `tools/approval.py` / `vermes_cli/scholarforge/tools.py` / `tools/self_modify_tool.py` 逐条核对。

## 一、结论

规约方向正确——**重定向到 `~/.vermes/modules/` 热路径、不碰冻结包** 这条核心决策成立，且和「部署铁律」（冻结包改源码+重建才生效）完全一致。但落地前有三处必须修正，否则会在 reload 第一步就踩空。

| # | 规约断言 | 源码真相 | 判定 |
|---|---------|---------|------|
| 1 | `registry.deregister()` 已有 | `tools/registry.py:307` 确有 `def deregister`；`get_tool_names_for_toolset` 在 `:201` | ✅ 成立 |
| 2 | 模块工具 toolset 用 `module_{name}` 前缀（如 `module_scholarforge`） | ScholarForge 实际注册 `toolset="scholarforge"`（bare name，`scholarforge/tools.py:3462` 等 12 处） | ❌ 假设错误 |
| 3 | `register_modules` 记录模块→toolset 映射 | `module_loader.py:286-346` 只调 `mod.register_tools(host_api)`，**不记录**映射；无 `_module_tools` 字典 | ⚠️ 缺失（reload 找旧工具的依据） |
| 4 | `is_config_level_target` / `_CONFIG_LEVEL_SUFFIXES` 现状 | `approval.py:612-642` 确为 `.yaml/.yml/.json/.toml/.ini/.env` | ✅ 成立（安全黑洞真实） |
| 5 | `self_modify_tool` 当前接受任意 `target_path` | `self_modify_tool.py:86-104` 仅 `required: target_path+content`，**无路径限制** | ✅ 成立（决策点 2 为真） |
| 6 | `web_server.py:2908-2910` 是注入点 | `:2908` import、`2909` `HostAPI()`、`2910` `register_modules(app, _host_api)` | ✅ 成立（`_set_app_ref` 插入此处正确） |
| 7 | `_set_app_ref`/`_get_host_api`/`_host_api_ref` 将在 module_loader 新增 | 源码全仓 grep 无这些符号 → 确为净新增 | ✅ 一致（规划项） |

## 二、三处必须修正（编码前）

### 修正 1：toolset 命名约定（决策点 1 的真实答案）

规约假设 `toolset == f"module_{name}"`，但 ScholarForge 用 `toolset="scholarforge"`。**规约给的 A/B/C 三方案都不够稳**：
- A（强制 `module_{name}` 前缀）：要改 ScholarForge 现有代码，破坏兼容；
- B（manifest 加 `toolset_prefix` 字段）：多一处易漂移的声明；
- C（自动包装）：要改 `HostAPI.register_tool` 注入逻辑，侵入面大。

**推荐「注册时记录」法（优于 A/B/C）**：
在 `register_modules` 每次 `mod.register_tools(host_api)` 前后，快照 `registry` 的工具名集合，把 `module_name -> set(tool_names)` 存进模块级 `_module_tool_names: Dict[str, Set[str]]`。`reload_module_tools(name)` 直接 `deregister` 这个**精确名单**，不猜前缀、不碰 manifest、对任何模块（含 `scholarforge`）通吃。这就是把"找旧工具"的真相来源从"约定"换成"注册时实测"。

### 修正 2：self_modify 的 target_path 限制（决策点 2）

当前 `self_modify_tool` 对路径零限制（决策点 2 为真）。规约给两选项：X 禁止写冻结包 / Y 不限制只热加载热路径。

**推荐 Y + "需重建提示"**，不要选 X：
- 选 X 会退化现有「改核心框架 → 写文件 → 重建 DMG」能力，是回归；
- 选 Y：`apply_change` 总是写文件；仅当 `_is_module_hot_path(target)` 才 reload；
- 关键补强：**当 target 在冻结包内且不在热路径时，在 `change_ledger` 写一条"此改动需重建 DMG 才生效"通知**。今天的真实坑是"AI 改完以为生效实际没生效"的无声落差——把它变成显式提示就堵住了，且不破坏任何既有行为。
- 冻结包写保护留给 sandbox/权限层，不要在这一层硬拒。

### 修正 3：reload 期间的在途工具调用（规约漏写）

`registry` 已有 `_generation` 计数器（`registry.py:167,305,330` 每次变更 +1）。reload 必须 `+=1`，且**工具分发器要按 generation 重新读取**——否则某 Agent 正在调用该模块工具时，`deregister`+`re-exec` 会让在途调用 `KeyError`/`TypeError`。Phase 0 至少要做到：reload 前 `bump_generation`、reload 后对 `dispatch` 加一条"读最新 _tools 快照"的约束（或串行化：reload 期间短暂阻塞新工具调用）。这是 P0 上线必踩的并发坑，规约§4.6 完全没提。

### 修正 3.1：rollback 也要触发 reload

规约§5.3 说 rollback 后"不自动 reload，下次重启生效"。但 Phase 0 的卖点就是"写完立刻用"——rollback 也应对称：restore 文件后，若 target 在热路径，自动 `reload_module_tools` 回到旧版。否则 rollback 了 Agent 还在跑新版，体验割裂。建议把 reload 抽成 `apply_change`/`rollback_change` 共用的 `_hot_reload_if_needed(target)`。

## 三、审批判据第三类（classify_component_swap）——确认成立

- `module_hot_path` → L1（可逆、单模块、有 `.bak`+24h 撤回兜底）与既有治理层一致；
- 但**不要**让 `module_hot_path` 受 `_source_modify_always_confirm()` 约束（那是给冻结包 .py 用的，套上会让所有模块改动强制 L2，架空 P0 意义）；
- 规约§4.4 的 `approve_privileged_action` 片段方向对，注意 `yolo_exempt=True` 仅对 L1 可逆桶成立，不要外溢到任何不可逆动作（守住 T1/T6 铁律）。

## 四、两个决策点的拍板建议

1. **toolset 命名**：不采用 A/B/C，改「注册时记录 `module_name -> tool_names`」法（见修正 1）。零破坏、零新约定、精确可靠。
2. **self_modify target_path**：采用 Y + 重建提示（见修正 2），不硬禁冻结包写入。

## 五、编码顺序微调（相对规约§六）

| 步骤 | 文件 | 改动 |
|---|---|---|
| 0 | `tools/registry.py` | 暴露 `snapshot_tool_names()` 或在 module_loader 自行 diff（修正 1 依据） |
| 1 | `agent/module_loader.py` | 新增 `_module_tool_names` 记录 + `_set_app_ref`/`_get_host_api` + `reload_module_tools`（用修正 1 法找旧工具、修正 3 bump generation）+ `_is_module_hot_path`/`_extract_module_name` |
| 2 | `vermes_cli/web_server.py` | `:2910` 后加 `_set_app_ref(app, _host_api)` |
| 3 | `agent/emergent_change.py` | `apply_change` Step 3c 热路径 reload；抽 `_hot_reload_if_needed` 供 rollback 共用（修正 3.1） |
| 4 | `tools/approval.py` | `classify_component_swap` 第三类；`approve_privileged_action` 加 `module_hot_path` 分支（不受 source_modify_always_confirm 约束） |
| 5 | `agent/emergent_change.py` / `change_ledger` | target 在冻结包且不在热路径时写"需重建"通知（修正 2） |

验收同规约§七，但 test_reload 模块建议用**第三方模块**（`~/.vermes/modules/test_reload/`），不走内置 ScholarForge，避免 bundle/override 路径歧义。
