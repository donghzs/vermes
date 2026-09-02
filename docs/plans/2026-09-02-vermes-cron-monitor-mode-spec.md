# Vermes Cron monitor-mode 精准改动规格（④）

> 用户审阅结论：性价比最高的取长项。**初判改动量约 50–80 行；🔴 已上调为 ~200–250 行（路线图口径）**，见 §5 口径说明。
> 真源：`/Users/dongzusheng/Projects/vermes-electron`（HEAD `d634478778`），**只读不写，由用户 commit/push**
> 锚点实测：`cron/scheduler.py:1622` `skip_memory=True`
> **⚠️ 权威性（2026-09-02 通篇审计后加）**：本文件是 **④ 的可 apply 实现规格**，`Vermes-catchup-roadmap-final.md` 是**唯一权威**。冲突时以路线图为准（路线图「文档契约」第 2 条）。
> **同步状态**：已同步至路线图 **vFINAL.2**。本轮修订：① **行数口径统一**（补 `create_job` 参数链路、`_resolve_monitor_target`、API/UI 开关，并标注与路线图 ~200–250 行的换算关系）；② 修正 §4「新表靠 `_reconcile_columns()` 加列」的**概念错误**。**vFINAL.3 增量**：§ 尾「与 ⑮ 协同边界」更新为三条腿口径（job 级状态走 `cron_notepad`，阶段结论走 ⑮ 腿 B `project_handoff`，结论+依据+索引走腿 C）。

---

## 1. 动机

当前 cron 任务构造 agent 时一律 `skip_memory=True`（`cron/scheduler.py:1622`，注释「Cron system prompts would corrupt user representations」）。这有两个副作用：

1. **监控类任务用不上记忆**：「盯着一个 URL / 文件 / 指标，变化时通知我」这类 monitor 任务，本应加载用户长期记忆（知道「我关心什么阈值」「通知发哪」），却因 `skip_memory` 被剥光。
2. **无变化也白白烧 token**：monitor 任务多数轮次目标状态没变，却每轮都跑完整 LLM 调用。

目标：加一个 `monitor_mode` 作业字段。开启后——
- `skip_memory=False`（加载记忆，让 agent 理解「该关注什么」）
- 跑前先算目标状态 hash，**无变化直接短路跳过 LLM**
- 用 `cron_notepad` 表存小状态，不污染 session 记忆

---

## 2. 改动 1：`skip_memory` 条件化（`cron/scheduler.py:1622`）

**现状（1622 行，硬编码）**：
```python
            skip_memory=True,  # Cron system prompts would corrupt user representations
```

**改为**：
```python
            # monitor_mode：监控类作业需加载用户记忆（理解关注点/通知目标），
            # 且由下方 hash 短路决定是否需要真正调用 LLM。
            skip_memory=not bool(job.get("monitor_mode")),
```

> 仅这一行就恢复了「monitor 任务能加载记忆」。其余 cron 作业行为不变（`monitor_mode` 缺省 = False → `skip_memory=True`，与现状一致，零回归）。

---

## 3. 改动 2：hash 对比短路（在 `cron/scheduler.py:1595` AIAgent 构造之前插入）

**插入位置**：`agent = AIAgent(`（`scheduler.py:1595`）之前，约 1594 行之后。

**新增代码**：
```python
        # ── monitor-mode 短路：目标状态无变化则跳过 LLM，省 token ──
        _monitor_hash = None
        if job.get("monitor_mode"):
            try:
                _target = job.get("monitor_target") or prompt
                _monitor_hash = _compute_monitor_hash(_target)
                _prev = _get_monitor_hash(job_id)
                if _prev is not None and _prev == _monitor_hash:
                    logger.info(
                        "Job '%s': monitor target unchanged (hash match) — skipping LLM run",
                        job_id,
                    )
                    return  # 无变化，不烧 token；不写 notepad（状态未变）
                _store_monitor_hash(job_id, _monitor_hash)
            except Exception as _h_exc:
                logger.warning("Job '%s': monitor hash check failed (non-fatal): %s", job_id, _h_exc)
```

**配套 helper（放 `cron/scheduler.py` 顶部或 `cron/_monitor.py` 新文件）**：
```python
import hashlib

def _compute_monitor_hash(target: str) -> str:
    """对监控目标（URL 内容 / 文件内容 / 指标快照）取稳定 hash。
    调用方负责把 target 解析成可 hash 的字符串（fetch URL / 读文件 / 取指标）。
    这里只做归一化 + sha256。"""
    _norm = (target or "").strip()
    return hashlib.sha256(_norm.encode("utf-8", "ignore")).hexdigest()[:16]

def _get_monitor_hash(job_id: str):
    try:
        from vermes_state import SessionDB
        _db = SessionDB()
        try:
            return _db.get_cron_monitor_hash(job_id)
        finally:
            _db.close()
    except Exception:
        return None

def _store_monitor_hash(job_id: str, h: str):
    try:
        from vermes_state import SessionDB
        _db = SessionDB()
        try:
            _db.set_cron_monitor_hash(job_id, h)
        finally:
            _db.close()
    except Exception:
        pass
```

> 注：`_compute_monitor_hash` 的输入应是「已解析的目标状态字符串」，不是原始 URL。若 monitor 目标是 URL，调用方需先 fetch 页面文本再传入；若目标是本地文件，先读内容。建议 `monitor_target` 支持 `{"type":"url","value":"..."}` / `{"type":"file","value":"..."}` / `{"type":"cmd","value":"..."}` 三种，由 `_resolve_monitor_target()` 统一解析成字符串再 hash（约 15 行）。

---

## 4. 改动 3：SQLite 表（`vermes_state.py` SCHEMA_SQL）

按项目规矩，新表写进 `SCHEMA_SQL`（约 `vermes_state.py:498` 附近），用 `CREATE TABLE IF NOT EXISTS`；索引在 `_reconcile_columns()` 之后（`~988-991`）用 `CREATE INDEX IF NOT EXISTS`。启动自动建表/迁移。

```sql
-- ── Cron monitor-mode 小状态（不污染 session 记忆）──
CREATE TABLE IF NOT EXISTS cron_monitor_state (
    job_id          TEXT PRIMARY KEY,
    target_hash     TEXT,                      -- 上次目标状态 hash（短路判定用）
    updated_at      REAL
);

-- ── Cron notepad：monitor agent 跨轮持久的小笔记 ──
CREATE TABLE IF NOT EXISTS cron_notepad (
    job_id          TEXT NOT NULL,
    note_key        TEXT NOT NULL,             -- 任意业务 key，如 "last_alert_sent"
    note_value      TEXT,
    updated_at      REAL,
    PRIMARY KEY (job_id, note_key)
);
CREATE INDEX IF NOT EXISTS idx_cron_notepad_job ON cron_notepad(job_id);
```

**`SessionDB` 方法（新增，约 `vermes_state.py` 中部）**：
```python
    def get_cron_monitor_hash(self, job_id: str):
        row = self._conn.execute(
            "SELECT target_hash FROM cron_monitor_state WHERE job_id=?", (job_id,)
        ).fetchone()
        return row[0] if row else None

    def set_cron_monitor_hash(self, job_id: str, h: str):
        self._conn.execute(
            "INSERT INTO cron_monitor_state(job_id, target_hash, updated_at) "
            "VALUES(?,?,?) ON CONFLICT(job_id) DO UPDATE SET target_hash=excluded.target_hash, updated_at=excluded.updated_at",
            (job_id, h, time.time()),
        )
        self._conn.commit()

    def get_notepad(self, job_id: str, key: str):
        row = self._conn.execute(
            "SELECT note_value FROM cron_notepad WHERE job_id=? AND note_key=?", (job_id, key)
        ).fetchone()
        return row[0] if row else None

    def set_notepad(self, job_id: str, key: str, value: str):
        self._conn.execute(
            "INSERT INTO cron_notepad(job_id, note_key, note_value, updated_at) "
            "VALUES(?,?,?,?) ON CONFLICT(job_id, note_key) DO UPDATE SET note_value=excluded.note_value, updated_at=excluded.updated_at",
            (job_id, key, value, time.time()),
        )
        self._conn.commit()
```

> **⚠️ 概念修正（2026-09-02 通篇审计）**：旧稿写"上述 4 个方法若 `_reconcile_columns()` 已能自动加列，则新表也就绪"——**这是错的，建表与加列是两套机制**：
> - **建新表**：靠 `SCHEMA_SQL` 里的 `CREATE TABLE IF NOT EXISTS`（启动时 `executescript` 执行），**与 `_reconcile_columns()` 无关**；
> - **给已有表加列**：才靠 `_reconcile_columns()` 用 `PRAGMA table_info` 比对后自动 ALTER（本项目的便利机制，无需手写迁移）；
> - **索引**：引用新表/新列的索引一律放在 `_reconcile_columns()` 调用**之后**（`~988-991`）用 `CREATE INDEX IF NOT EXISTS` 建——否则历史库首轮初始化会因表/列尚不存在而中断（本文件 §4 的两条 `CREATE INDEX` 即须如此放置）。
>
> 建议实跑验证：建一个缺 `cron_monitor_state` 的旧库 → 新代码打开 → 确认表自动建出、查询可走、重开幂等。

---

## 5. 改动量汇总

| 改动 | 文件 | 行数 |
|---|---|---|
| `skip_memory` 条件化 | `cron/scheduler.py:1622` | 1（改） |
| hash 短路分支 | `cron/scheduler.py`（AIAgent 前插入） | ~18 |
| 3 个 helper | `cron/scheduler.py` 或 `cron/_monitor.py` | ~30 |
| **`_resolve_monitor_target()`**（url / file / cmd → 可 hash 字符串） | `cron/scheduler.py` 或 `cron/_monitor.py` | **~15**（🆕 旧稿汇总表漏列，正文 §3 注中提及但没计数，本轮补） |
| 2 张表 DDL | `vermes_state.py` SCHEMA_SQL | ~16 |
| 4 个 SessionDB 方法 | `vermes_state.py` | ~30 |
| **小计（本规格范围 = `scheduler.py` + `vermes_state.py` 核心逻辑）** | | **~110 行（净逻辑 ~60–80 行）** |
| **`create_job` 加 `monitor_mode` 参数 + 参数传递链** | `cron/jobs.py:509` 及调用方 | **~40–60**（🆕 旧稿汇总表漏列，本轮补） |
| **API / UI 暴露 `monitor_mode` 开关**（建作业表单 + 接口字段） | `vermes_cli/blueprints/*` + 前端 | **~50–80** |
| **合计（路线图口径）** | | **~200–250 行** |

> **口径说明（2026-09-02 加，务必先读）**：本规格正文聚焦 `scheduler.py` 的**核心逻辑**（小计 ~110 行）；路线图 §1 / §6 的 **~200–250 行**是**全链路口径**，含 `create_job` 参数链路与 API/UI 开关暴露。**二者不矛盾——本规格小计是路线图总数的子集**（路线图「文档契约」第 2 条）。
> 初判"50–80 行"指净逻辑代码、不含参数链路与 UI，**已废弃不用**，保留在此仅供溯源"当时为什么这么估"。
> 全部为**新增表/方法 + 一处条件化**，不改既有 cron 作业行为（`monitor_mode` 缺省 False 时与现状完全一致，**零回归**）。

---

## 6. 验收

- [ ] 普通 cron 作业（无 `monitor_mode`）行为不变：`skip_memory=True`，日志无 hash 短路。
- [ ] 建一个 `monitor_mode: true` + `monitor_target: {type:url, value:"https://example.com/feed"}` 的作业。
- [ ] 首次运行：hash 无前值 → 正常跑 LLM + 存 hash + 存 notepad。
- [ ] 目标未变二次运行：日志出现 `monitor target unchanged — skipping LLM run`，**不调用 LLM**（token 明显下降）。
- [ ] 目标变化第三次运行：hash 不一致 → 重新跑 LLM。
- [ ] 重开应用（旧库无 `cron_monitor_state` 表）→ 新代码自动建表，无崩溃（`_reconcile_columns` 活体验证）。

---

## 7. 与取长路线图对应

- 用户审阅把 Cron 记忆从 Tier1 调降到 Tier2，本规格即 Tier2 的首个落地项（编号 **④**）。
- 纯增量、复用 `vermes_state.py` 既有迁移机制、真源只读——符合三条纪律。
- 落地后可立刻给「盯盘/盯页/盯文件」类场景赋能，是 ROI 最高的单点取长。
- **与 ⑮ 文档记忆层的协同（2026-09-02 补；vFINAL.3 更新）**：monitor 任务的"上次输出 / 阈值 / 通知偏好"本质就是**长程状态**，与 ⑮ 要解决的"阶段结论跨会话留存"同源。④ 用 `cron_notepad` 表解决 **job 级小状态**；⑮（已改为**三条腿**：清僵尸 / 通用发射桥 / `docmemory` 索引插件，见路线图 §11.3）解决 **agent / 项目级大结论**。**二者不冲突但须划边界**：job 级瞬时状态走 `cron_notepad`（快、小、结构化）；需人读 / 需跨 agent 复用的**阶段结论走 ⑮ 腿 B 的 `project_handoff` 通用发射**（`agent/project_handoff.py` 表已现成），**结论+依据+索引走腿 C 文档**。**实现 ⑮ 时应回看本节，避免两套状态机制重复造轮子**（见路线图 §11.2 / §11.3）。
- **发布策略提醒（2026-09-02 决策）**：本项落地后**只本地 `git commit`、暂不 `git push` 远端**（路线图 §12）。
