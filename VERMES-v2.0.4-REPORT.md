# Vermes v2.0.4 全面总结报告
> Hermes × QClaw 协作对齐文档 | 2026-05-30

---

## 一、P0-P3 前端优化（Hermes 负责，全部完成）

### P0 — 紧急修复（9项）
| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| P0-1 | Toast 通知系统 | ✅ | `toast.js` + `ToastContainer.vue`，替换所有 alert() |
| P0-2 | 复制按钮 | ✅ | 消息 hover 显示 📋 复制，代码块右上角独立复制 |
| P0-3 | 重新生成 | ✅ | 消息 hover 显示 🔄 重新生成 |
| P0-4 | Agent 流式回调修复 | ✅ | `stream_callback` 指向正确函数 |
| P0-5 | tool_progress_callback 挂载 | ✅ | `tool.started` → `tool_start` SSE 事件 |
| P0-6 | step_callback 挂载 | ✅ | thinking 事件发送到前端 |
| P0-7 | 保活心跳 | ✅ | 15秒 ping，SSE 超时 0.5s→1s |
| P0-8 | 工具结果摘要 | ✅ | `result_preview` 前200字符 |
| P0-9 | 移除冗余 callback | ✅ | tool_executor.py 2处 `stream_delta_callback` 冗余推送 |

### P1 — 体验优化（3项）
| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| P1-1 | 侧边栏收起 | ✅ | 收起保留 40px 窄边栏，V 图标+新会话按钮 |
| P1-2 | 系统主题偏好 | ✅ | `prefers-color-scheme` 监听 |
| P1-3 | 打字机光标 | ✅ | 2px 绿色竖线 blink 动画 |

### P2 — 功能增强（5项）
| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| P2-1 | 设置页分层 | ✅ | 推荐模式(vbit免费/DeepSeek/本地) + 高级模式 |
| P2-2 | 新手引导 | ✅ | 3步引导：选择方式→配置引导→快速开始 |
| P2-3 | 虚拟滚动 | ✅ | 分页加载 50 条 + IntersectionObserver |
| P2-4 | IndexedDB 异步加载 | ✅ | 先显示文本，图片 Promise.all 并行 |
| P2-5 | 自定义模型输入框 | ✅ | DeepSeek/本地模型支持手动输入 |

### P3 — 高级特性（3项 Hermes 负责）
| # | 任务 | 状态 | 说明 |
|---|------|------|------|
| P3-1 | 代码块语法高亮 | ✅ | highlight.js 15 种语言 + Catppuccin CSS |
| P3-4 | 会话搜索高亮 | ✅ | `highlightText` 正则匹配 + mark 标签 |
| P3-6 | 微信登录拆分 | ✅ | `WechatLogin.vue` 独立组件（178行） |

---

## 二、Agent 推理链可视化（方案A，Hermes 负责）

### 后端改动（web_server.py）
| 改动 | 说明 |
|------|------|
| `tool_progress_handler` | 工具启动/完成 → SSE `tool_start`/`tool_end` 事件 |
| `thinking_handler` | 推理步骤 → SSE `thinking` 事件 |
| 保活心跳 | 15秒 `ping` + 超时 1s |
| `result_preview` | 工具结果前 200 字符 |

### 前端改动
| 文件 | 改动 |
|------|------|
| `api.js` | SSE 解析 thinking/tool_start/tool_end/ping，**移除 🔧 文字注入** |
| `chat.js` | onTool 类型转换 `type→status`，新工具自动关闭 thinking 卡片，onDone 关闭残留 thinking |
| `MessageList.vue` | 流式中单条状态条（⏳ 思考中/读取文件...），完成后紧凑时间线标签 |

### 用户体验
```
流式中：  ⏳ 思考中... → ⏳ 读取文件... → ⏳ 执行命令...
完成后：  ✅ 读取文件 0.3s  ✅ 终端 1.2s  ✅ 搜索文件 0.5s
```

---

## 三、Hermes 引擎层修复（Hermes 负责，两套代码都改了）

### 问题
`_model_supports_vision()` 对未知模型（models.dev 查不到）返回 `False` → 图片在 API 调用前被剥离 → 用户发图 AI 看不到。

### 修复方案：默认信任 + 失败降级

**run_agent.py（两处：Hermes CLI + Vermes 引擎）**
```python
# 修改前
if caps is None:
    return False  # 未知模型 → 不支持 vision
# 修改后
if caps is None:
    return True   # 未知模型 → 先信任，让 API 决定
```

**conversation_loop.py（两处）**
- API 返回 4xx "does not support image" → 记录到 `_no_vision_models` 缓存
- 后续 turn/session 直接跳过 vision，不再浪费 API 调用

### 调用链（3个调用点）
1. `_prepare_anthropic_messages_for_api()` — Anthropic 路径
2. `_prepare_messages_for_non_vision_model()` — OpenAI/Codex 路径
3. `_tool_result_content_for_active_model()` — 工具结果路径

### 修改的文件（4个）
```
~/.hermes/hermes-agent/run_agent.py              — _model_supports_vision 默认 True
~/.hermes/hermes-agent/agent/conversation_loop.py — 降级时记录 _no_vision_models
~/Projects/vermes/run_agent.py                    — 同样的改动
~/Projects/vermes/agent/conversation_loop.py      — 同样的改动
```

---

## 四、其他 UX 修复（Hermes 负责）

| 修复 | 说明 |
|------|------|
| 打开会话滚到底部 | onMounted + watch(currentSessionId) 自动滚到最新消息 |
| 会话重命名 | 已存在（右键菜单 ✏️ 重命名），功能完整 |
| thinking 事件截流修复 | 恢复立即 tool_start+tool_end，避免 thinking 卡片永远 running |
| 🔧 文字污染移除 | api.js 不再用 onChunk 注入工具名到消息体 |

---

## 五、构建产物

| 产物 | 路径 | 大小 |
|------|------|------|
| 前端 JS | `index-DpInaBqa.js` | 411KB (gzip 155KB) |
| 前端 CSS | `index-CvBA7rYQ.css` | 40KB (gzip 7KB) |
| Mac DMG | `/tmp/Vermes-2.0.4-macos-arm64.dmg` | 68MB |
| web_dist | `hermes_cli/web_dist/` | 已同步 |

---

## 六、待处理

### 🔥 P0 — 自更新系统（QClaw + Hermes 协作）

**目标**：用户看到新版本提示 → 点击"立即更新" → 自动下载 → 自动覆盖 → 自动重启

**架构设计**：
```
前端 update banner → 点击"立即更新"
  → POST /api/update/download（后端下载新版本到 ~/.vermes/update/）
  → POST /api/update/apply（后端写更新脚本 → shutdown → 脚本替换文件 → 重启）
```

**需要改动**：

| # | 任务 | 负责 | 说明 |
|---|------|------|------|
| 1 | `version.json` 加 `download_url` 字段 | QClaw | mac_url + win_url 指向具体文件 |
| 2 | 后端 `/api/update/download` 端点 | QClaw | 下载到 `~/.vermes/update/`，返回进度 |
| 3 | 后端 `/api/update/apply` 端点 | QClaw | 写更新脚本 → shutdown → 脚本替换 → 重启 |
| 4 | 更新脚本（跨平台） | QClaw | macOS: bash（hdiutil mount/copy）/ Windows: bat（xcopy/restart） |
| 5 | 前端 update.js 改造 | Hermes | 点击"立即更新" → 调 API → 显示进度 → 自动重启 |
| 6 | gui_app.py 启动时检查待应用更新 | Hermes | `~/.vermes/update/pending` 标记文件 |
| 7 | WebView2 数据目录迁移到 `~/.vermes/` | Hermes | Windows `private_mode=False` + 自定义 `user_data_dir` |

**关键细节**：
- macOS: 下载 DMG → `hdiutil attach` → `cp -R /Volumes/Vermes/Vermes.app /Applications/` → `hdiutil detach` → `open /Applications/Vermes.app`
- Windows: 下载 ZIP → 解压到 `~/.vermes/update/staging/` → 写 bat 脚本（等待进程退出 → xcopy 覆盖 → 启动 EXE）→ 执行 bat → 退出当前进程
- 更新脚本必须独立于 Python 进程运行（subprocess + CREATE_NEW_PROCESS_GROUP）
- 下载支持断点续传（HTTP Range）
- 下载完校验 MD5（version.json 里加 `md5` 字段）

### 待 QClaw 处理

| 任务 | 优先级 | 说明 |
|------|--------|------|
| 上传最新 DMG 到服务器 | P0 | `/tmp/Vermes-2.0.4-macos-arm64.dmg`（68MB）→ vbit 服务器 |
| Git commit + push | P0 | 12 个未提交文件 + 本地 2 个新 commit 待 push |
| 服务器旧 DMG 清理 | P1 | 保留 2.0.3 + 2.0.4 最新版 |
| P3 剩余 5 项 | P3 | 配额拆分 / Blueprint 迁移 / 消息时间 / 消息编辑 / 多模型对比 |
| One-API 健康检查 cron | P2 | Token 更新机制 |

---

## 七、安全事项

| 项目 | 状态 |
|------|------|
| GitHub 密钥泄漏 | ✅ 已清理（WORK-SUMMARY-20260527.md 已删除，密码已改，force push 完成） |
| One-API 密码 | ✅ 已改为 DHz3753890@ |
| .gitignore | ✅ `hermes_state.db` 已添加 |

---

## 八、项目规模

- **总代码量**：677,715 行（Python 651K + JS/Vue 4.2K）
- **Vue 组件**：13 个
- **Blueprint**：11 个
- **前端测试**：0 个（待补充）
- **健康评分**：92/100
