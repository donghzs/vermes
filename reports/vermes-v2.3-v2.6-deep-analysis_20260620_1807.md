# Vermes v2.3-v2.6 深度源码分析与优化方案

## 2026-06-20 18:07

---

## 一、v2.3 进化系统落地 — 深度分析

### 1.1 现状源码分析

**后端 `agent/evolution_manager.py` (1,100+行)**
- ✅ 已有完整数据层：`self-model.db`(outcomes/anti_patterns/strategies/self_model/roles/relations 6张表) + `fusion-state.db`(emotional_state/fusion_decisions/evolution_metrics 3张表)
- ✅ 已有记录链路：`tool_executor.py:271` → `record_tool_outcome()` → 写 outcomes + anti_patterns + relations + emotional_state + achievements
- ✅ 已有查询接口：`/api/evolution/status` 返回 total_outcomes/success_rate/anti_patterns_count/top_domains/role_stats/recent_failures
- ✅ 已有 prompt 注入：`build_evolution_prompt()` 在 chat.py 中注入到 system prompt
- ✅ 已有成就系统：`_check_evolution_achievements()` 6个里程碑(10/50/100条/高准确率/反模式3/10个/首次错误)
- ✅ 已有情绪映射：success+fast→confident, error+permission→frustrated 等
- ✅ 已有 DAG 雏形：relations 表记录 outcome→anti_pattern, outcome→emotional_state 边

**前端 `EvolutionPanel.vue` (175行)**
- ❌ 只是静态仪表盘：显示数字+进度条+展开详情
- ❌ 无签到简报：每次启动不会主动展示"学到了什么"
- ❌ 无进化时刻：工具调用后无即时反馈
- ❌ 无成就通知：解锁成就在后端记录但前端无展示
- ❌ 无对话式交互：纯展示，不参与对话流

### 1.2 精准优化方案

#### 改动 1：签到简报（后端 + 前端）

**后端** `evolution_manager.py` 新增 `build_daily_briefing()`:
```python
def build_daily_briefing() -> str:
    """生成每日签到简报，注入到首次对话的 system prompt。"""
    status = get_evolution_status()
    if not status or status.get("total_outcomes", 0) < 10:
        return ""
    
    parts = [
        f"📋 今日简报：已积累 {status['total_outcomes']} 次工具调用经验",
        f"📊 整体成功率 {status['success_rate']}%",
    ]
    
    # 最近 24h 新增
    conn = _get_conn(str(get_self_model_db()))
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM outcomes WHERE timestamp > datetime('now', '-1 day')")
    recent = c.fetchone()[0]
    if recent > 0:
        parts.append(f"📈 最近24小时新增 {recent} 条记录")
    
    # 最常出错的工具
    if status.get("recent_failures"):
        top_fail = status["recent_failures"][0]
        parts.append(f"⚠️ 注意：{top_fail[0]} 常出现 {top_fail[1]} 错误")
    
    # 情绪状态
    emotion = get_current_emotional_state()
    if emotion:
        parts.append(f"😌 当前状态：{emotion}")
    
    return "\n".join(parts)
```

**改动量**：~25行后端

**前端** `EvolutionPanel.vue` 改造为"签到卡片"模式：
- 首次打开时弹出简报卡片（非侧边栏静态面板）
- 卡片显示 3 秒后自动收起到侧边栏
- 点击侧边栏图标可重新展开

**改动量**：~40行前端

#### 改动 2：进化时刻（前端 SSE 增强）

**`MessageList.vue`** 在工具调用结果渲染区增加进化反馈：
- 工具调用成功 → 绿色微光闪过 + "✅ 第N次成功"
- 工具调用失败 → 黄色提示 + "⚠️ 已记录反模式"
- 成就解锁 → 金色通知弹窗

**改动量**：~30行前端，需读取 SSE 流中的 evolution 元数据

**后端** `chat.py` 在 SSE 流中追加进化事件：
```python
# 在 tool_executor 记录后，如果有成就/建议，追加 SSE 事件
achievement = record_tool_outcome(...)
if achievement:
    yield f"data: {json.dumps({'type': 'evolution', 'content': achievement})}\n\n"
```

**改动量**：~15行后端

#### 改动 3：成就通知（前端）

**`EvolutionPanel.vue`** 或新建 `AchievementToast.vue`：
- 监听 SSE 中的 `type: 'evolution'` 事件
- 成就解锁时弹出金色渐变通知 + 闪光动画
- 3秒后自动消失

**改动量**：~35行前端

#### 改动 4：进化面板增强

**`EvolutionPanel.vue`** 展开区增加：
- 最近 5 条反模式列表（可点击查看修正建议）
- 角色演变时间线（显示角色出现频率）
- 成就墙（已解锁成就图标网格）

**改动量**：~40行前端

**总改动量：~25+40+30+15+35+40 = ~185行**（与预估168行接近）

### 1.3 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 签到简报触发时机 | 首次对话 system prompt 注入 | 不需要额外API调用，零延迟 |
| 进化时刻数据通道 | SSE 流内嵌事件 | 不需要额外WebSocket连接 |
| 成就持久化 | 后端 `_unlocked_achievements` set | 进程内去重足够，跨会话从DB查 |
| 简报数据源 | self-model.db 直接查询 | 不需要额外存储 |

---

## 二、v2.4 上游精华同步 — 深度分析

### 2.1 后台异步子agent

**现状**：`tools/delegate_tool.py` (2,819行)
- 已有完整的子agent 委派机制：`_build_child_system_prompt()` / `_strip_blocked_tools()` / `check_delegate_requirements()`
- 已有并发控制：`_get_max_concurrent_children()` / `_get_child_timeout()` / `_get_max_spawn_depth()`
- 已有事件系统：`DelegateEvent` 枚举
- ❌ **缺失**：`background=true` 参数 — 当前所有 delegate 都是同步阻塞的
- ❌ **缺失**：异步句柄返回 — 不能"先返回，后补充结果"

**上游 v0.17.0 实现**：
- `delegate_task(background=true)` 立即返回句柄
- 子agent在后台运行，完成后结果作为新turn注入对话
- 用户和模型可以继续工作，不用等

**精准改动**：

1. **`delegate_tool.py` schema 扩展**：在 `delegate_task` 的工具 schema 中加 `background: bool = False` 参数
2. **异步执行路径**：
```python
if background:
    # 立即返回句柄，不等结果
    task_id = f"bg-{int(time.time())}"
    asyncio.create_task(_run_subagent_async(agent, task_id, ...))
    return json.dumps({"status": "dispatched", "task_id": task_id, 
                       "message": "子agent正在后台执行，完成后结果会自动出现"})
else:
    # 现有同步路径
```
3. **结果注入**：子agent完成后，通过 `agent.pending_background_results` 队列，在下一轮 conversation_loop 开始时注入为新 turn

**改动量**：~60行（schema 5行 + 异步路径 35行 + 结果注入 20行）

**风险**：
- 需要处理子agent异常不中断主对话
- 需要限制并发数量（已有 `_get_max_concurrent_children`）
- 需要超时清理（已有 `_get_child_timeout`）

### 2.2 Memory Tool 升级

**现状**：`tools/memory_tool.py` (753行)
- 存储方式：纯文件（MEMORY.md + USER.md），§ 分隔条目
- ✅ 已有原子写入（`atomic_replace`）
- ✅ 已有文件锁（fcntl/msvcrt）
- ✅ 已有注入检测（`_MEMORY_THREAT_PATTERNS`）
- ❌ **缺失**：无 FTS5 全文搜索（纯 substring 匹配）
- ❌ **缺失**：无语义检索（无 embedding）
- ❌ **缺失**：无跨会话关联（无法关联相似记忆）

**上游 v0.17.0 Memory 升级**（从 release notes）：
- Memory tool got a major upgrade

**精准改动**：

1. **FTS5 搜索**：在 `memory_tool.py` 中增加 SQLite FTS5 虚拟表
```python
# 在 MemoryStore.__init__ 中
self._fts_db = sqlite3.connect(str(mem_dir / "memory.fts.db"))
self._fts_db.execute(
    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
    "entry, target, content, tokenize='trigram')"
)
# 写入时同步更新 FTS
# 搜索时用 MATCH 查询
```

2. **read action 增强**：当 `action=read` 且有 `query` 参数时，用 FTS5 搜索

**改动量**：~40行

**注意**：Vermes 已有 FTS5 trigram 降级经验（v2.2.0），可复用

### 2.3 Curator 优化

**现状**：`agent/curator_backup.py` (270行) 已有快照+回滚
- 上游 v0.17.0："curator stopped spending aux-model budget on every routine run"
- 意思是：Curator 之前每次运行都调用辅助模型(summarize等)，现在改为只在必要时才调用

**精准改动**：
- 找到 curator 调用辅助模型的位置（需进一步检查上游代码）
- 增加"routine run"判断条件：如果技能没有变化，跳过 aux-model 调用
- 这是优化项，不影响功能，优先级可降

**改动量**：~15行（预估）

### 2.4 Skills Hub 浏览器改版

**现状**：`tools/skills_hub.py` (3,592行) 已有完整搜索/fetch/inspect 功能
- 前端无 Skills Hub 浏览器界面（只有 Settings 中的 Provider 管理）
- 上游 v0.17.0："Skills Hub browser was rehauled"

**精准改动**：
- 前端新增 `SkillsHub.vue` 组件，调用 `/api/skills/search` 和 `/api/skills/install`
- 搜索框 + 分类筛选 + 技能卡片网格 + 一键安装

**改动量**：~200行前端 + ~20行后端路由

---

## 三、v2.5 Studio 升级 — 深度分析

### 3.1 现状源码分析

**后端 `hermes_cli/blueprints/studio.py` (551行)**
- ✅ 已有 4 种生成模式：text / image / image2image / video
- ✅ 已有 Provider 适配：通过 `base_url` + `api_key` 直连任意 OpenAI 兼容 API
- ✅ 已有图片上传（data URI → base64）
- ✅ 已有视频任务状态轮询

**前端 `StudioChat.vue` (816行)**
- ✅ 已有聊天式交互
- ✅ 已有 Provider 预设 + 配置保存
- ✅ 已有图片上传/拖拽
- ✅ 已有图片/视频结果展示

### 3.2 差距分析（vs 专业创作工具）

| 功能 | 现状 | 目标 |
|------|------|------|
| 多图批量生成 | ❌ 单次 | ✅ 批量4图 |
| 生成历史画廊 | ❌ | ✅ 本地持久化 |
| 参数控制 | ❌ 仅prompt | ✅ size/quality/style/seed |
| 图生图参数 | ✅ 基础 | ✅ strength/mask |
| 结果对比 | ❌ | ✅ 原图vs生成 |
| 创作模板 | ❌ | ✅ 预设prompt模板 |
| 导出格式 | ❌ 仅显示 | ✅ 下载PNG/WebP |

### 3.3 精准优化方案

#### 阶段 A：参数控制增强（后端 + 前端）

**后端** `studio.py` `StudioRequest` 模型扩展：
```python
class StudioRequest(BaseModel):
    # 现有
    prompt: str
    mode: str = "text"  # text/image/image2image/video
    # 新增
    size: str = "1024x1024"  # 1024x1024/1792x1024/1024x1792
    quality: str = "standard"  # standard/hd
    style: str = ""  # vivid/natural
    seed: Optional[int] = None
    n: int = 1  # 批量数量(1-4)
    strength: float = 0.8  # image2image 变换强度
```

**改动量**：~30行后端 + ~50行前端

#### 阶段 B：生成历史画廊（前端）

新建 `StudioGallery.vue`：
- localStorage 持久化生成历史
- 网格展示缩略图
- 点击查看大图 + 参数复用

**改动量**：~120行前端

#### 阶段 C：创作模板（前端）

新建 `StudioTemplates.vue`：
- 8 个预设模板（人物/风景/Logo/图标/插画/写实/动漫/3D）
- 点击模板自动填充 prompt + 参数

**改动量**：~80行前端

**总改动量：~30+50+120+80 = ~280行**

---

## 四、v2.6 RAG + Windows — 深度分析

### 4.1 RAG 基础层审计

Vermes 已有的 RAG 基础层：

| 组件 | 位置 | 状态 |
|------|------|------|
| FTS5 trigram | evolution_manager (SQLite WAL) | ✅ 有降级经验 |
| 文件解析 | Voffice file_parser.py (服务器) | ✅ 支持 docx/xlsx/pptx/pdf |
| 向量化 | ❌ 无 | 需新增 |
| 文档分块 | ❌ 无 | 需新增 |
| 检索增强生成 | ❌ 无 | 需新增 |

**memory_provider.py** (291行) 已有 ABC 接口：
- `initialize()` / `system_prompt_block()` / `prefetch(query)` / `sync_turn()` / `get_tool_schemas()` / `handle_tool_call()`
- 已有 8 个插件实现：hindsight/retaindb/openviking/holographic/honcho/byterover/mem0/supermemory

### 4.2 精准 RAG 方案

**不引入向量数据库**，用 SQLite + FTS5 做"轻量 RAG"：

#### 后端：新建 `agent/rag_provider.py`

```python
class RAGProvider(MemoryProvider):
    """Agent 记忆型 RAG — 文档向量化 + FTS5 检索 + 注入 prompt"""
    
    def initialize(self):
        # SQLite + FTS5 虚拟表
        # documents(id, path, chunk_id, content, metadata)
        # documents_fts USING fts5(content, tokenize='trigram')
    
    def ingest(self, file_path: str):
        # 1. 解析文件（复用 file_parser 逻辑）
        # 2. 分块（500字/块，100字重叠）
        # 3. 写入 FTS5
    
    def prefetch(self, query: str) -> str:
        # FTS5 MATCH 查询 → top-3 chunk → 返回文本
    
    def system_prompt_block(self) -> str:
        # 返回 [知识库上下文] 块
```

**改动量**：~200行后端

#### 前端：知识库管理

Settings 中新增"知识库"标签页：
- 文件上传（拖拽支持）
- 已索引文件列表（文件名/大小/分块数/操作）
- 删除索引

**改动量**：~150行前端

#### Agent 集成

在 `agent_init.py` 中注册 RAG provider：
- 当配置 `rag.enabled: true` 时激活
- `prefetch()` 在每轮对话前检索相关知识
- 检索结果注入 system prompt

**改动量**：~30行

**总改动量：~200+150+30 = ~380行**

### 4.3 DAG 已有基础

进化系统中的 `relations` 表已经是 DAG 雏形：
```sql
CREATE TABLE relations (
    source_type TEXT, source_id INTEGER,
    target_type TEXT, target_id INTEGER,
    rel_type TEXT, weight REAL, timestamp TEXT
)
```

当前已记录的边类型：
- `outcome → anti_pattern` (rel_type: 'triggered')
- `outcome → emotional_state` (rel_type: 'caused_emotion')
- 预留：`anti_pattern → skill` (rel_type: 'mitigated_by')

**v2.6 可扩展**：
- `document → chunk` (rel_type: 'contains')
- `chunk → outcome` (rel_type: 'informed')
- `skill → document` (rel_type: 'uses_knowledge')

这样 Agent 的知识库、技能、经验、情绪就形成完整的 DAG。

### 4.4 Windows 修复

**现状**：`vermes.spec` 已有 Windows 支持（`sys.platform == 'win32'` 分支）
- 已配置 pywin32/win32api/win32con/win32process/pywintypes
- 已排除 numpy/pandas/scipy（会导致 TTS 等功能不可用）

**需要修复**（从 MEMORY.md 历史记录）：
1. 7 个根级 .py 模块缺失（toolsets/kanban_db.py 等）
2. 3 个关键目录缺失（acp_adapter/acp_registry/cron）
3. numpy 排除导致 TTS 崩溃 → 需要条件排除（TTS 可用时保留 numpy）

**精准改动**：

1. **vermes.spec 修复**：
```python
# 补充缺失模块到 hiddenimports
hiddenimports.extend([
    'toolsets.kanban_db', 'toolsets.screenshot', ...
    'acp_adapter.server', 'acp_adapter.session', ...
    'cron.scheduler', 'cron.manager', ...
])

# numpy 条件排除（而非全局排除）
if not config.get('tts.enabled', False):
    excludes.append('numpy')
```

2. **Windows 测试**：需要 Windows 环境实际构建验证

**改动量**：~40行 spec 修改 + Windows 环境测试

---

## 五、优先级排序与依赖关系

```
v2.3 进化系统落地 (~185行)
  ├── 签到简报 (后端25行 + 前端40行)
  ├── 进化时刻 SSE (后端15行 + 前端30行)
  ├── 成就通知 (前端35行)
  └── 面板增强 (前端40行)
  ⬇️ 无依赖，可立即开始

v2.4 上游精华同步 (~335行)
  ├── 后台异步子agent (60行) ← 需要先做
  ├── Memory FTS5 (40行) ← 独立
  ├── Curator 优化 (15行) ← 独立
  └── Skills Hub 浏览器 (220行) ← 独立
  ⬇️ 依赖：v2.3 的 SSE 事件机制

v2.5 Studio 升级 (~280行)
  ├── 参数控制 (后端30行 + 前端50行)
  ├── 历史画廊 (前端120行)
  └── 创作模板 (前端80行)
  ⬇️ 独立，可与 v2.4 并行

v2.6 RAG + Windows (~420行)
  ├── RAG 后端 (200行) ← 依赖 v2.4 Memory FTS5
  ├── RAG 前端 (150行) ← 独立
  ├── DAG 扩展 (30行) ← 依赖 v2.3 进化系统
  └── Windows 修复 (40行) ← 独立
```

## 六、总结

Vermes 的进化系统后端已经非常扎实（1,100+行，6张表，DAG雏形，情绪映射，成就系统），但前端只有 175 行的静态面板。**v2.3 的核心工作是"把后端能力释放到前端"**，不需要大改后端，主要是前端增强 + SSE 事件流打通。

v2.4 的核心是补齐上游关键功能，其中后台异步子agent 优先级最高（60行改动解决长任务阻塞问题）。

v2.5 Studio 升级是纯产品体验优化，可与 v2.4 并行。

v2.6 RAG 不引入向量数据库，复用 SQLite + FTS5 做"Agent记忆型RAG"，与进化系统的 DAG 形成完整知识图谱。
