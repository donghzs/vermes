# Vermes 多会话并行评估报告

**评估时间：** 2026-06-06 18:51  
**评估范围：** 多会话并行的后端支持 + 前端改造量

---

## 现状确认

### 后端：基础设施到位

_AgentCache 架构：
```
key = "{provider}:{model}:{session_id}"   ← 每个 session 独立 agent
threading.Lock()                          ← 线程安全
run_in_executor(None, run_sync)           ← 不阻塞 event loop
maxsize=20                                ← 20 个并发 agent 实例
```

结论：后端不需要大改。

### 前端：全局单布尔锁死所有会话

**位置：** `frontend/src/stores/chat.js:45`
```javascript
const loading = ref(false)   // ← 全局单布尔，一个会话锁住所有
```

**影响：** 
- 会话 A 加载中 → `loading.value = true`
- 会话 B 发消息 → `if (loading.value) return` → 被拒绝
- 用户无法并行使用多个会话

---

## 改造清单

| # | 问题 | 位置 | 代码量 | 工作量 | 严重度 |
|---|------|------|--------|--------|--------|
| 1 | loading 全局单布尔 | chat.js:45 | ~20 行 | 2h | 🔴 高 |
| 2 | SQLite 并发写 | evolution_manager.py | ~3 行 | 30min | 🟡 中 |
| 3 | 流式无 session 隔离 | state.py | ~15 行 | 1h | 🟡 中 |

**总计：** ~43 行代码，~4 小时

---

## 改造方案

### 1. 前端 loading per-session

```javascript
// 改前（chat.js:45）
const loading = ref(false)

// 改后
const sessionLoading = ref({})  // { sessionId: boolean }
const isSessionLoading = (sid) => sessionLoading.value[sid] || false
const setSessionLoading = (sid, v) => { sessionLoading.value[sid] = v }
```

### 2. SQLite WAL 模式

```python
# 改前（evolution_manager.py）
conn = sqlite3.connect(db_path)

# 改后
conn = sqlite3.connect(db_path)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
```

### 3. 流式 session 隔离

```python
# 在 WebSocket 连接中维护 per-session 状态
_sessions: Dict[str, Dict] = {}  # { session_id: { state... } }
```

---

## 依赖关系

```
P3 跨会话涌现
  ↓ 需要多会话基础设施
P4 多会话并行（本评估）
  ↓ 需要
前端 per-session loading
SQLite WAL 模式
流式 session 隔离
```

P4 是 P3 的基础设施，建议先做 P4。

---

## 结论

- ✅ 后端架构基本支持多会话（_AgentCache LRU 20 实例）
- ❌ 前端 loading 全局单布尔是唯一阻塞
- ✅ 改造量仅 ~43 行代码
- ⏱ 预计 4 小时完成
