# 上下文生命周期通道修复报告

**日期**: 2026-06-03  
**项目**: vermes-electron  
**分支**: current (未提交)  
**类型**: 体验优化 — 消除对话上下文管理的黑盒感  

---

## 背景

后端压缩机制业界顶配（三层防御 + 结构化摘要），但用户对这一层零控制、零感知。关键问题是 `AIAgent` 的 `_emit_status` 和 `_emit_warning` 一直在执行，但因为后端 **没有把 `status_callback` 传给 agent**，这些事件全部被丢弃（`if self.status_callback:` 分支跳过）。

## 修改的四刀

### 1️⃣ 后端：打通 SSE 生命周期通道
**文件**: `hermes_cli/blueprints/chat.py` +749  
**改动**: +19 行

```python
def status_callback(event_type: str, message: str):
    """Route lifecycle/warn events from AIAgent to SSE stream."""
    event = {
        "type": "lifecycle" if event_type == "lifecycle" else "warn",
        "message": message,
    }
    loop = asyncio.get_running_loop()
    loop.call_soon_threadsafe(_delta_queue.put_nowait, event)
```

赋值：`agent.status_callback = status_callback`（line 845）

与 `tool_progress_handler`、`thinking_handler`、`stream_callback` 走完全相同的 `call_soon_threadsafe → _delta_queue.put_nowait` 路径。**零新增计算量，事件本来就已产生。**

### 2️⃣ 前端 SSE 协议层：注册 `onStatus`
**文件**: `frontend/src/services/api.js` +3 行

- `sendMessage` 新增 `onStatus` 参数
- SSE 循环中处理 `json.type === 'lifecycle'` 和 `json.type === 'warn'` 事件

### 3️⃣ 前端 Store：状态管理
**文件**: `frontend/src/stores/chat.js` +11 行

- 新增 `statusMessages: Ref<Array>` — 存储生命周期事件（带 id/type/message/timestamp）
- 新增 `lastTokenUsage: Ref<Object|null>` — 存储最后一次 token 统计
- `onStatus` callback: push 事件 → `scheduleScroll()`
- `onDone`: 清空 `statusMessages` + 保存 `lastTokenUsage`
- `onError`: 清空 `statusMessages`
- 两者均暴露在 store 的 return 中

### 4️⃣ 前端 UI 渲染
**文件**: `frontend/src/components/MessageList.vue` +30 行

**状态消息条**（消息列表与滚动按钮之间）：
- 循环渲染 `chat.statusMessages`
- 类型图标：`📦` (lifecycle) / `⚠️` (warn)
- 消息文本显示：「对话已压缩 (第3次)」等
- fade-in 动画（0.3s ease-out）

**Token 用量显示**（消息列表底部右侧）：
- 格式：`{prompt_tokens} / {completion_tokens} → {total_tokens} tokens`
- 仅在 `!chat.loading` 时显示
- 灰色等宽数字，不抢眼

---

## 修复前后对比

| 场景 | 之前 | 之后 |
|------|------|------|
| 对话压缩 | 静默，用户不知情 | 消息条显示 📦 对话已压缩 (第3次) |
| Token 用量 | 后端有数据，前端丢弃 | 底部显示 4.2K / 8.5K → 12.7K tokens |
| 长工具调用 | 看不到 | 能看到 lifecycle 状态 |
| 错误场景 | 状态信息被吞 | onError 清空 statusMessages |

## 影响评估

| 维度 | 评估 |
|------|------|
| **修复成本** | 4 文件，63 行净增 |
| **性能影响** | 零 — 事件已产生，改路由 |
| **体验提升** | 中 — 消除黑盒感 |
| **状态** | 代码已就位，**前端需 `vite build` + 部署** |
