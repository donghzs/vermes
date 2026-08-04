# Phase 3 设计：变体隔离（Variant Isolation）

> 乐高式改造第三站。Phase 1 建了 `governance.hash`（已就位但零消费方），
> Phase 2/2.5 外置了工具 processor + inline 执行。Phase 3 把 hash 变成
> **变体身份键**：processor 改版时旧版按 hash 归档，可查、可比、可回滚。

## 1. 目标与非目标

### 目标
- **变体共存**：同一 processor ID 的多个版本在磁盘上共存，按 hash 区分。
- **确定性回滚**：指定 hash 恢复任意旧版（不限上一版——`.bak` 只能做到上一版）。
- **审计可见**：小白能在 UI 里看到"这个 processor 改过几次、每次改了什么、谁改的、何时"。
- **零核心改动**：loader / ToolRegistry / watcher / dispatch 全部不改——变体是
  被动归档，只有 `processor.yaml` 是活跃入口。

### 非目标
- 不做分支/合并（不是 git）。
- 不做变体级 A/B 流量切分（那是 Phase 4 闭环串联的事）。
- 不做变体级模型亲和（同上）。
- 不给内置 processor 建变体（冻结包只读；用户 override 走用户路径，有变体）。

## 2. 现状基线（实测事实）

### 2.1 hash 已就位但零消费方
```
agent/prompt_processor_loader.py:286  compute_manifest_hash(data) → sha256
  - 递归 key 排序 + safe_dump + LF 归一 + sha256
  - governance.hash 自身被排除（hash 不能含自身）
  - 每个 processor 加载时 hash 已从 "auto" 解析为真实 sha256
```
全仓 grep `compute_manifest_hash` 消费方：只有两个 loader 各自的解析期赋值，
**没有任何地方读 hash 做分支/选择/归档**。Phase 3 是它的第一个真实消费者。

### 2.2 现有 .bak 机制（单版回滚）
- `tools/approval.py` 在写入 processor YAML 前，对旧内容创建 `.bak` 快照。
- `self_modify_rollback`(chat.py:2027) 用 `backup_path` 恢复 `.bak`。
- **局限**：`.bak` 只存上一版；改两次就丢第一次；无法按 hash 检索/对比。

### 2.3 审批流已覆盖 processor_hot_path
- `approval.py:674-681`：`~/.vermes/processors/` 下的 YAML → `processor_hot_path`
  → risk_tier 从 manifest 的 `governance.risk_tier` 读取（Phase 1）。
- `_resolve_processor_tier`（Phase 2.5 P1 修过）：inline processor 强制钳 L2。

### 2.4 watcher 已支持热加载
- `prompt_processor_loader.py` watcher：变更时 `invalidate_cache()` +
  `register_tool_processors()`（Phase 2 P1 修过），无需重启。
- **但**：watcher 递归监听 `~/.vermes/processors/`，如果 `variants/` 子目录也
  被监听，variant 归档会误触发 re-registration。

## 3. 设计

### 3.1 磁盘布局
```
~/.vermes/processors/<id>/
  processor.yaml              ← 活跃变体（loader 只读这个，零改动）
  variants/
    _registry.json            ← 变体注册表（元数据，非内容）
    sha256_<hash>.yaml        ← 归档变体（完整 YAML 副本）
    sha256_<hash>.yaml
    ...
```

**设计约束**：
- `processor.yaml` 是唯一活跃入口 → loader/watcher/ToolRegistry 全不改。
- `variants/` 是被动归档 → watcher 必须忽略它（否则归档触发 re-register）。
- `_registry.json` 是元数据（时间戳/作者/pin/摘要），不是变体内容本身。

### 3.2 变体生命周期

```
            ┌─────────────────────────────────────────────┐
            │  approval flow writes processor.yaml        │
            │                                             │
  old.yaml ─┼─► snapshot old content to variants/<h>.yaml │
            │  write new content to processor.yaml        │
            │  update _registry.json                      │
            └─────────────────────────────────────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
       list variants    diff vs active   rollback
       (GET)            (GET diff)      (POST)
```

1. **Birth**：首次写入 `processor.yaml` → hash 计算 → 无 variants。
2. **Supersede**：approval 流程写新内容前：
   - 旧 `processor.yaml` 内容 → 复制到 `variants/<old_hash>.yaml`
   - 新内容 → 写 `processor.yaml`
   - `_registry.json` 追加：`{hash, superseded_at, author, note, pinned}`
3. **Rollback**：用户选一个旧 hash 恢复：
   - 当前 `processor.yaml` → 归档到 `variants/<current_hash>.yaml`
   - 目标 `variants/<target_hash>.yaml` → 复制到 `processor.yaml`
   - **此操作本身走审批流**（processor_hot_path，风险档从 manifest 读）
4. **GC**：超过 max_variants 时，最老的**非 pinned** 变体被删除。

### 3.3 _registry.json 格式
```json
{
  "processor_id": "my_processor",
  "active_hash": "sha256:abc123...",
  "variants": [
    {
      "hash": "sha256:abc123...",
      "created_at": "2026-08-04T12:00:00",
      "superseded_at": null,
      "author": "user|aegis|system",
      "note": "initial version",
      "pinned": false
    },
    {
      "hash": "sha256:def456...",
      "created_at": "2026-08-04T11:00:00",
      "superseded_at": "2026-08-04T12:00:00",
      "author": "aegis",
      "note": "tightened risk_tier L2→L1",
      "pinned": true
    }
  ]
}
```

### 3.4 watcher 集成
- watcher 的文件遍历跳过 `variants/` 子目录（加一个 `if "variants" in parts: continue`）。
- variant 归档写 `variants/<hash>.yaml` 不触发 re-register。
- 只有 `processor.yaml` 变更触发（已有逻辑）。
- rollback 后 `processor.yaml` 被改写 → watcher 自然触发 re-register（正确行为）。

### 3.5 审批集成
- **Supersede（写新版）**：已有审批流（processor_hot_path + risk_tier），Phase 3 只在
  写入前**多加一步归档**——不改审批闸门，不改 risk_tier 逻辑。
- **Rollback（恢复旧版）**：走同一条审批流——`target_path` 是 `processor.yaml`，
  `new_content` 是旧版 YAML。`_resolve_processor_tier` 对旧版也做 inline 检测
  （Phase 2.5 P1 的 `is_inline_processor_content` 对 incoming 内容生效）。
- **变体删除**：只删 `variants/<hash>.yaml` + registry 条目，不碰 `processor.yaml`
  → 不走审批（归档文件不是运行态输入）。

### 3.6 API 端点
```
GET  /api/evolution/processors/{id}/variants
     → {variants: [{hash, created_at, superseded_at, author, note, pinned, active}]}

GET  /api/evolution/processors/{id}/variants/{hash}/diff
     → {diff: "unified diff text", active_hash: "sha256:...", target_hash: "sha256:..."}

POST /api/evolution/processors/{id}/rollback
     body: {hash: "sha256:..."}
     → 走审批流 → 恢复 → {ok: true, new_active_hash: "..."}

POST /api/evolution/processors/{id}/variants/{hash}/pin
     → {ok: true, pinned: true}

DELETE /api/evolution/processors/{id}/variants/{hash}
     → {ok: true}（pinned 的不可删）
```

全部加入 `_PUBLIC_API_PATHS`（与 EvolutionPanel 一致，免 token）。

### 3.7 GC 策略
- `config.yaml` 新增 `evolution.max_variants_per_processor`（默认 10）。
- 超限时删最老的非 pinned 变体。
- pinned 变体永不被 GC（用户标记"这个版本我要留"）。
- GC 在 supersede 后自动跑一次（无需定时器）。

## 4. 拍板点

### P3-① 变体存储
- **A（推荐）**：on-disk `variants/<hash>.yaml` + `_registry.json`。简单、可肉眼看、
  无 DB 依赖、与 YAML-only 设计一致。小白可以直接 `cat variants/<hash>.yaml` 检查。
- B：SQLite-backed。查询快但引入 DB 依赖、不可肉眼检查、与"乐高=可序列化"背离。

### P3-② 活跃选择
- **A（推荐）**：单一 `processor.yaml` 为活跃入口，variants 为被动归档。loader/watcher/
  ToolRegistry 零改动。rollback = 复制旧版到 processor.yaml。
- B：manifest.json 指针 + 多活跃变体 + loader 按 hash 选。复杂、要改 loader 核心。

### P3-③ 变体创建触发
- **A（推荐）**：approval 写入前自动归档旧版。不漏、不需用户手动"存为变体"。
  每次改 processor YAML 自动留下痕迹。
- B：显式 `POST .../variants` 命令才归档。用户会忘、会漏。

### P3-④ 回滚治理
- **A（推荐）**：rollback 走审批流（processor_hot_path + risk_tier + inline 钳 L2）。
  旧版可能有已知缺陷/安全档位放宽，恢复它 = 重新引入那些风险，必须人工确认。
  与现有 `.bak` rollback 一致（也走审批）。
- B：rollback 绕过审批（trusted operation）。危险——旧版可能 risk_tier L1
  绕过 inline L2 钳档。

### P3-⑤ GC 策略
- **A（推荐）**：`max_variants_per_processor`（默认 10）+ LRU 驱逐 + pinned 豁免。
  简单、可预测、磁盘不涨。
- B：不 GC（永久保留）。磁盘可能涨（但 processor YAML 很小，也可接受）。
- C：时间基（30 天后删）。比 LRU 复杂且可能删掉刚改的。

### P3-⑥ API 范围
- **A（推荐）**：list + diff + rollback + pin + delete 五个端点全做。小白需要看 diff
  才能决定回滚到哪个版本；需要 pin 防止 GC 删重要版本。
- B：只做 list + rollback。够用但 diff/pin 是体验提升，不做小白只能"盲回滚"。

## 5. 验证清单

- [ ] variant 自动归档：改 processor.yaml → 旧版进 `variants/<hash>.yaml` + registry 更新。
- [ ] rollback 恢复：POST rollback → processor.yaml 恢复为旧版 → watcher 触发 re-register。
- [ ] rollback 走审批：inline 旧版恢复仍钳 L2（is_inline_processor_content 对 incoming 生效）。
- [ ] watcher 忽略 variants/：写 variants/ 不触发 re-register。
- [ ] GC：超 max_variants 删最老非 pinned。
- [ ] pinned 不被 GC：标记 pinned 后即使最老也不删。
- [ ] no-mock 真实回归：variant 归档 → rollback → re-register 全链路真实跑通。
- [ ] EvolutionPanel 集成：前端能 list variants + 看 diff + 一键 rollback。
- [ ] _PUBLIC_API_PATHS 补全新端点（避免重蹈 401 bug）。
