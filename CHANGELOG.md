# Changelog

All notable changes to Vermes will be documented in this file.

## [2.5.2] - 2026-09-22

> 范围：`26416408b4`（2.5.1 发布点）→ `018b4e761a`。
> 性质：**正确性修复**，无新功能开关。

### 文件读取 · 单行截断不说谎（P0）

- `read_file` 对单行/长行文件会在 `_add_line_numbers` 阶段行内截断到 `max_line_length`，但 `truncated` 标志只看 `wc -l`（行数截断）→ **静默丢 ~96% 内容且报 `truncated=False`**
- `wc -l` 数的是换行符个数，无尾换行的文件末行被漏算（单行文件报 `total_lines=0`）
- 修：数行改 `awk 'END{print NR+0}'`；新增 `_has_oversized_lines` 把行内截断并入 `truncated`；hint 区分「offset 续读」vs「read_file_raw 读全文」
- 契约测试 `tests/tools/test_file_read_truncation.py`（5 条）

### 代码执行 · 解释器 ABI 精确匹配（P1）

- `_is_usable_python` 仅 `>=3.8` 闸门；frozen 打包态下候选系统 Python 版本不匹配（如 3.14 vs 打包的 3.11）会 import 原生 `.so` 崩溃（`ImportError: _PyModule_AddObjectRef`）
- 修：frozen 模式改为 `major.minor` 精确匹配；非 frozen（源码/venv）保持 `>=3.8` 宽松闸门
- 契约测试 `tests/tools/test_code_execution_abi.py`（4 条）

### UX 打扰治理（并行轨，已审计）

- onDelivery 唯一弹产物（tool_step 静默）；审批队列 FIFO 修复（多会话卡死真 bug）；任务区/审批/更新角标降噪；P0-5 intermediate 标记隔离中间产物

## [2.5.1] - 2026-09-21

> 范围：`10c780d2a2`（2.5.0 发布点）→ `350a25d8cc`，共 **68 个提交**。
> 性质：**安全收口 + 正确性修复 + 测试债清零**，无新功能开关。

### 安全 · Provider 凭据明文收口（P1）

- `add_provider` 不再把 `api_key` 明文落进 `config.yaml`，改记 `key_env` 指针
- 解析面回读 `.env`：`runtime_provider` 新增 `_resolve_env_key()`、`auxiliary_client` custom 分支、`studio._resolve_key_entry`、`model_switch` `/models` 发现 —— 四路统一走 `get_env_value`（`os.environ` OR `.env` 文件），修掉「桌面 dev 直启（uvicorn 不注入 dotenv）解析不到 key」
- 迁移脚本 `scripts/migrate_plaintext_provider_keys.py`：扫 `providers` + `custom_providers[]` + `auxiliary.*`，按 `base_url` 反查 `key_env`，round-trip 保注释；`'none'` 当占位符处理
- 存量实测：`~/.vermes/config.yaml` **26 个 provider / 0 条明文**；`scnet` 真凭证已落 `.env`（备份 `config.yaml.bak-20260920_200135`）

### 运行时正确性

- `_stamp_preset` 签名不匹配（A3 `e7b3aa9e7ba` 遗留）：17 个 `TypeError` 清零；并补传 9 处非 explicit 出口的 preset 变量，修复「加默认值后 preset 静默不生效」
- `_resolve_named_custom_runtime` 死代码 `os.getenv(key_env)` → `_resolve_env_key`
- 新增契约测试锁死 copilot-acp 非 explicit 路径的 preset stamp（原先零覆盖）

### 配置写入卫生

- 6 处 yaml 写路径改就地 round-trip，**消除注释抹除 + 默认值膨胀**（`config.yaml` / 凭据写路径 / `backup.py` 对齐上游后 38 failures → 0）

### 技能索引与路由

- skill-routing：纯渠道门 auto + `SkillRouter` prefetch + heat tie-breaker
- A′ 渠道硬门：messaging 渠道 auto 永不降级
- 技能索引 deny-list 细校 + GUI 开关；M7 token 阈值上线（`web_dist` 同步）

### Gateway / 渠道

- A7 first-DM 自动 home channel；元宝 comment-safe 写入
- W3 email 自过滤 / W4 持久通知去重

### 测试债

- 既有失败清零：backup 对齐（38→0）、telegram mock + profiles 品牌迁移（9）、gateway 启动分类/索引/resume/toolset（7）、`_stamp_preset`（17）

### 已知问题（带病发布，如实登记）

| # | 缺陷 | 状态 |
|---|---|---|
| 1 | Windows 包未构建，`version.json` 的 `windows.sha256` 为空 | 待远端构建回填 |
| 2 | gateway 全量 **13 failed**（环境缺依赖 3 / 品牌重命名遗留 2 / 中文化遗留 1 / 工具集漂移 1 / reconnect·email·runner 7） | 2026-09-20 台账定性为 pre-existing；**本次未复跑全量** |
| 3 | SQLite 3.50.4 WAL-reset 损坏 bug（内嵌运行时） | 未修 |
| 4 | M7 auto 死开关（阈值 20 KB vs 实测索引快照 131,834 字节） | 见决策 D3，未纳入本次 |

### 验证

| 套件 | 结果 |
|---|---|
| `test_runtime_provider_resolution` + `test_api_key_providers` | 292 passed |
| `test_runtime_presets_contract` | 9 passed |
| `test_provider_add_no_wipe` + `test_custom_provider_model_switch` + `test_auth_qwen_provider` | 45 passed |
| 4 文件广回归（含本次全部改动面） | 308 passed / 0 failed |

## [2.5.0] - 2026-09-19

> 补记：2.5.0 当时只更新了 `version.json`，**CHANGELOG 漏记**。以下摘要以 `version.json` 发布说明为准。

- 可信显形（U-P0）：聊天 Harness 状态灯 / Auto 路由头 / 交付摘要卡 / 失败可行动横幅 / 时间线 harness 核验徽标与筛选 / 全站空态 StateBlock + PrereqBanner / 通知中心 / 场景轻量状态条
- 工程门禁：tool_step 契约 harness 双路径、harness_status SSE+HTTP 账本、route_ledger + `GET /api/route/ledger`、cron monitor-mode、verify_fn 写回扩容、TrustGate 严格模式 fail-closed、stability 探针可配置
- 文案纠偏（E-P0-4）：技能 / TrustGate / 微信体验宣称对齐真源

## [2.4.9] - 2026-09-18

> 基于 tag `v2.4.8` 之后的本地/已合入改动。**本机先构建 + 冒烟通过后才对外发布**；tag / vbit.top 投放以冒烟结果为准。

### ScholarForge · 论文写作

- **统计结果 → 学术三线表**（`scholarforge_stats_table`，第 28 个工具）：粘贴 SPSS 表格或统计结论句 → GFM 三线表；纯规则、零 LLM、数字保真
- SPSS 表型扩展：成对样本 / 卡方 / 相关矩阵 / GLM 与重复测量 / 纵向键值对（非参数、KMO-Bartlett、Cronbach α）/ 回归分析 / ANCOVA / 逻辑回归 / 因子分析 / 正态性 / 莱文方差齐性
- 一致性校验修复与适用条件：F↔η² 公式（补 df_between）、t↔d 候选集、η²↔d 多组闸门、补齐 d↔r、p↔t 精确计算（无 scipy）、粘贴表格时校验真正生效
- 28 个工具名中文化 + 工具箱分组归位 + **新手模式**（「写论文的 8 步」）
- 兜底项目显形：未选项目时写回结果明确告知落到哪个项目
- `learn_style`：500 字门槛显形（100–499 字警告「会被自动套用」）+ 空行短段落不再崩溃

### 桌面端体验（流畅度 / 封装）

- SQLite 移出 FastAPI 事件环；聊天/看板 markdown 渲染缓存；渠道轮询去抖；Sidebar 会话 Map 索引
- 定时器与 watcher 泄漏修复；窗口隐藏时暂停纯 UI 轮询；渠道 WS 重连耗尽后转长跑重试
- 主包代码分割 + highlight.js 全量语言移出首屏；路由懒加载；three.js 按需
- 系统托盘 + 全局快捷键；窗口尺寸/位置记忆；设置页跨 tab 搜索
- 引导页「能力地图」；删除 3 个确凿死组件；清理 9 个零引用依赖

### 上手体验

- 创作工作室：修「点了没反应」；空态引导；自动带入设置里已配模型；注册 `studio` blueprint
- 论文页：环境自检条；项目下拉经 store 同步后端激活项目；跨会话记住当前项目

### 更新与清单

- `version.json` 支持 `downloads.*` 与 `mac.{arm64,x64}` 架构嵌套；`update.js` 宽松解析，避免 Mac 更新「弹窗有、下载空」
- mac 清单 sha256/size 与本机 `dist-electron` DMG 对齐

### 测试与工程

- vitest `setup.js`：Node 26 下 localStorage 兜底，新手模式等前端测试可跑
- 工具数守卫同步至 28（注册/logger/module.yaml/validation_coverage/benchmark）
- memory richness 测试补 `is_active`；literature_matrix 输入守卫与测试契约对齐
- registry 写路径测试补全 DB mock，避免假失败

## [2.4.5] - 2026-08-28

- 本地模型智能发现：通用本地/自定义端点 + 端口范围扫描 + base_url 自动补 /v1
- 修复 gateway 状态检测（gateway_state.json fallback + macOS ps 标志兼容）
- 修复内置 MCP 尊重 connect:lazy 字段，不再启动即烧 CPU
- 修复 Telegram 渠道构建漏包（改用 venv Python 重打）
- 右栏编辑撑满 + 产物路径蓝卡片 + 文件标签会话隔离
- 新增 p5.js 创意技能（headless 帧导出 + 导出管线文档）
- 左栏去冗余 + 会话文案统一（新建会话）
- 修复 scholarforge 三线表（LaTeX + docx）+ 负值回归
- 右栏 Office 文档能力增强（轻量可编辑 + pptx 静态预览）
- 修复产物右栏渲染与 Agent/UI 状态同步，新增树形任务流
- 工作流可视化 DAG 编辑器 + 触发器（cron/webhook）

## [v2.1.0] - 2026-06-11

### 架构重建（从 v2.0.9 重新出发）

从 v2.0.9 基线重建，跳过损坏的 d5d4b43 提交。包含其间的所有功能升级。

### 进化系统

- **进化闭环**: 桥+裁剪+覆盖 — 工具调用后自动记录到 self-model.db，失败根因分析，反模式学习
- **疲劳裁剪**: 桥就绪后自动裁剪中间轮次，保留最近 3 轮 + MEMORY.md 沉淀
- **HybridRetriever**: 记忆语义检索层（Phase 1）— embedding 存储/召回/排序
- **DAG 跨层边**: outcome → emotional_state 关联，预留 skill 关联
- **行为引导**: 全渠道对等（CLI/桌面端/飞书/Telegram/gateway），注入进化上下文
- **多轮对话疲劳修复**: 每轮刷新进化上下文，防止 agent 变敷衍
- **build_evolution_prompt() 公共函数**: 消除重复实现

### 基础设施

- **version.json** → 2.1.0
- **electron/version.txt** → 2.1.0
- **版本号运行时化**: 前端不再依赖编译时注入，从 /health 端点运行时读取
- **builtin-mcp 自动发现**: builtin-mcp/ + manifest.yaml 免除 config.yaml 配置
- **Agnes 模型插件**: 支持 agnes-2.0-flash / agnes-image / agnes-video
- **uv.lock 重新生成**: 匹配最新 pyproject.toml

### 安全与稳定性

- **三层保护**:
  - pre-commit: 敏感信息扫描 + py_compile 语法检查 + 文件大小异常检测
  - 启动预检: 关键文件语法检查，语法错误直接退出并报错
  - 崩溃看门狗: 10 秒健康检测，启动失败自动回滚备份版本
- **分支工作流规范**: AGENTS.md 记录 — 禁止直接在 main 上操作
- **`.gitignore` 加固**: archive/ + dist-electron/ 防止敏感信息提交
- **清理 archive/reports/** 中的敏感文件

### 清理与修复

- **重复端点清理**: POST /api/shutdown / DELETE /api/env 迁移至 blueprints
- **SimpleMode 清除**: 移除 bypass Agent 的快速回复路径
- **缩进修复**: conversation_loop.py break outside loop 回归修复
- **update.js await 兼容**: 移除 top-level await，兼容 Vite esbuild target
- **冲突标记清理**: cherry-pick 残留的 <<<<<<< HEAD 标记全部清除

### 代码规范

- 26 次提交，全部语法通过
- 294 个 agent/ .py 文件编译通过
- 16 个 Blueprint 全部注册
- **branch → merge → tag 工作流正式启用**

## [v2.0.7] - 2026-06-03

### 上下文生命周期通道（status_callback 全链路）

后端 AIAgent 的 `_emit_status` / `_emit_warning` 一直在执行，但因 `status_callback=None` 全部丢弃。本次打通 SSE 通道，让用户看到压缩、生命周期等事件。

- **chat.py**: 新增 `status_callback(event_type, message)` 路由 lifecycle/warn → `_delta_queue` → SSE，赋值 `agent.status_callback`
- **api.js**: 新增 `onStatus` 参数 + SSE 循环处理 `lifecycle` / `warn` 事件类型
- **chat.js**: 新增 `statusMessages` ref + `lastTokenUsage` ref，onDone 保存 token 用量 + 清空状态，onError 同步清空
- **MessageList.vue**: 📦/⚠️ 状态消息条（fade-in 动画）+ `4.2K / 8.5K → 12.7K tokens` 底部用量显示

### 安全审查 P0/P1

- **P0-1** Agent 缓存 LRU: OrderedDict maxsize=20 + `pop_for_session()` 联动清理 (`dfb52f0`)
- **P0-2** 删除 TRIAL_EXPIRY 硬编码: 前后端全链路移除，免费模型无限免费 (`baf8afe`)
- **P0-4** GitHub Token 泄露: 吊销 + git filter-branch 重写历史
- **P1-5** `/api/env/reveal` 从公开白名单移除，改 session token 鉴权
- **P1-6** PROVIDERS 添加 cloud/free/recommended 字段 + `/api/config/cloud-models` 端点 + 前端动态拉取

### 安全修复（预发布审计 — 5 项）

- **P0-1/P0-6**: 全局异常处理器不再返回 `str(exc)`，改为通用 `"Internal server error"`，异常详情仅记日志
- **P0-4**: HistoryPanel.vue 搜索高亮先 HTML 实体转义再注入 `<mark>` 标签，防 XSS
- **P1-6**: SSE 错误消息入口 `stripHtml()` 防御纵深，剥离 HTML 标签
- **P1-7**: OAuth 窗口添加导航域白名单（vbit.top / weixin.qq.com 等7个），限制 `will-navigate/will-redirect`

### UI/UX 8 项优化

1. **微信登录直弹二维码**: WelcomeGuide 选微信→自动触发 WechatLogin 扫码→监听 `wechat-login-success`→自动领取
2. **消息气泡视觉区分**: AI 气泡 `border-l-[3px] border-green-400` 绿色左边框
3. **输入框空会话视觉强化**: placeholder "问我任何问题…" + 边框加深 + 空会话绿边
4. **模型选择器搜索+置顶**: 搜索框 + 最近 3 个置顶（localStorage `vermes-recent-models`）
5. **工具调用折叠**: >5 个工具默认折叠，"+N 更多"展开按钮
6. **侧边栏时间线/搜索**: 已有完整实现（无需改动）
7. **Settings Provider 搜索**: `providerSearch` 过滤推荐/中文/国际/自定义四组
8. **键盘快捷键**: ⌘K 聚焦 / ⌘N 新建 / ⌘B 侧边栏 / ⌘, 设置 / Esc 停止

### 额外修复

- **storage DB 路径**: `sessions/sessions.db` → `state.db`
- **会话 lastActive**: 新增字段，侧边栏按最后活跃时间排序分组
- **Settings 添加自定义提供商**: 底部"+ 添加自定义提供商"按钮
- **blueprints/__init__.py**: 补充 `storage` 模块导入
- **blueprints/storage.py**: `utils.config` → `vermes_cli.config`

### 启动欢迎页（Splash Screen）

首次启动时后端需初始化（尤其 Windows 版 PyInstaller 解包耗时 3-10 秒），此前窗口不可见或白屏。本次:

- **splash.html**: 自包含深色主题欢迎页，Vermes Logo + 动画加载点 + 进度条，`file://` 协议毫秒级渲染
- **main.js**: 重构启动流程 — 窗口优先创建 + 立即加载 splash → `ready-to-show` 展示 → 后台 `startBackend()` → IPC 推送进度 → 就绪后 `loadURL(BACKEND_URL)` 无缝过渡
- **main.js**: 失败时显示错误状态（红色警示 + 详情说明），`重试`按钮触发 `runInitialization()`
- **preload.js**: 新增 `onSplashMessage` / `retryInit` IPC 通道
- **package.json**: `splash.html` 加入打包 `files` 列表

### 更新概要展示

- `update.js`: 读取 `version.json` changelog 数组 → join 为 releaseNotes 字符串
- `App.vue`: 更新提示条增加"更新内容"按钮，展开显示逐条概要

### 项目清理

- 删除 `vermes-desktop/`（废弃 Tauri，3.2 GB）
- 删除 `vermes/`（原始项目，1.1 GB）
- 清理 dist-electron/mac/ + dist/ 缓存（~1.0 GB）
- **总计回收 ~5.4 GB**
- 唯一项目: `vermes-electron/`

### 判定不修（7 项）

SSE Queue 背压（桌面 localhost）、SSE 断线重连、stopGeneration 竞态、配额上报失败、web_server.py/auth.py 过大（P2 重构）、preload IPC 偶发不可用、CLOUD_MODELS 远程签名

---

## [v2.0.6] - 2026-06-02

### Electron 壳方案

- 替代 pywebview，解决 A11 Windows WebView2 崩溃
- Electron 仅作壳加载本地 SPA + 启动 Python 后端（uvicorn 子进程）
- 微信 OAuth BrowserWindow 子窗口 + will-redirect 监听 + 3 秒延迟关闭
- 全量删除 pywebview 代码，WechatLogin.vue 统一为 IPC 路径

### Agnes AI 接入

- 替换 DeepSeek/MiMo 为默认免费体验模型
- 文本/图片/视频三模态均正常
- One-API 渠道 id=3 Active

### Bug 修复

- DMG 黑屏: session.py 缺少 Request 导入 + PyInstaller 改用 venv Python 3.11
- 消息不保存: API 白名单补全
- 滚动失效: overflow-hidden → overflow-y-auto + min-h-0
- 微信登录白屏根因: `loadURL("数字")` → 修复为正确 OAuth URL
- 聊天 500: 旧后端无免费体验逻辑 + API Key 为空

### web_server.py 重构

- 6240→2830 行（-55%），拆出 blueprints 目录

### Windows 版本

- NSIS 安装包 `Vermes Setup 2.0.6.exe`（117MB）
- Python 3.12 后端（3.13 崩溃回退）
- perMachine: true（修复 WinRM 非交互问题）
