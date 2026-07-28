# c5 (G6) 定稿设计 —— 聊天图片隐形老化淘汰（纯前端 IDB，无后端端点、无用户 UI）

> 重写于 2026-07-28 19:xx，经团队代码核验修订。
> 前稿（全量双写 + `/api/gui/images` 服务端副本）已否决；本稿为**终态实施方案**，
> 所有开放问题已闭合（§5 投票结果），待实施。

## 0. 背景与前提（含代码级证据）

### 0.1 G6 原始立论已弱化
G6 最初针对"图片每次启动被 `clearStorageData` 全清"活 bug。但 **c1 (G0/G2)** 已止血：
版本戳门控 + 永不清 indexdb → 头号丢失原因消除。剩余丢失路径仅低频残留，
且桌面端 backend 与 IDB 同机同盘，双写=同盘冗余非真备份。

### 0.2 用户可视化存储管理 = 红线（不可破）
不得增加"存储已满 / 资料库 / 清理面板"等 UI。Vermes 小白定位，任何存储管理面都违背纪律。
→ 分层持久化若需用户手动标记"重要/临时"则否决（认知税过高）。

### 0.3 「聊天图 = 临时」有代码级硬证据（团队核验确认）
- `frontend/src/stores/chat.js:640`：发 API 请求时只保留**最近 5 张图**
  （`allMsgsFiltered.filter(... data:image ...).slice(-5)`），更老的图在 API 层被
  `replace(/!\[.*?\]\(data:image[^)]+\)/g, '[图片]')` 替换成占位——**老图对模型早就不存在**，
  只剩前端展示价值。淘汰它们不影响任何对话能力。
- 图片 key = `${uid()}-${idx}`，`uid()` = `crypto.randomUUID()`（`chat-session.js:412`）。
- `stripBase64FromContent` 正则 `BASE64_RE` 仅抓 `data:image` 内联 base64，
  **外链 `![](https://...)` 从不进 IDB**——现状已是"存 URL 不存字节"。

### 0.4 ScholarForge 图独立（团队核验确认）
`ScholarForgePanel.vue` 及 `frontend/src/components/scholar/*` 全模块
**零引用** `saveImage/chat-storage/vermes-images/loadImage`。
且 `saveImage/loadImage` 的调用方**仅 `chat-session.js`** → 图片库即聊天专用，
本方案不触碰 ScholarForge 资产。

## 1. 终态方案：聊天图片 IDB 隐形老化淘汰

### 1.1 核心策略
- **不新增后端端点**（砍掉 `/api/gui/images` 全家桶）
- 图片**仅存 IDB**（`vermes-images`），与现状一致
- **老化淘汰（age-based FIFO，非 LRU）**：按写入时间删最旧，双条件
  （≥90 天 **或** 总量 ≥ 500MB，取先到者）
- **零用户 UI**：不弹"存储已满"、不提供清理按钮、不显示占用
- 用户感知 = 无感知（类比手机微信缓存自动瘦身）

### 1.2 命名澄清：这是老化淘汰，不是 LRU
- 真 LRU 要求 `loadImage` 每次读回写访问时间 = 读放大写，且聊天场景"最近看过≠重要"
- 按**写入时间**删最旧更诚实也更便宜：用户粘贴完即定级，之后不再因"看过"续命
- 文档与代码注释统一称"老化淘汰 / age-based eviction"，不叫 LRU

### 1.3 前置必做改动：value 加时间戳（绕不开）
当前 `saveImage(key, base64Data)` 调 `store.put(base64Data, key)`——**value 是裸 base64 字符串，
无时间戳；key 是 `${UUID}-${idx}` 也无时间戳**。
→ 任何按时间淘汰都必须先做：
```js
// 新格式
store.put({ d: base64Data, t: Date.now() }, key)
// loadImage 兼容旧格式（typeof r === 'string' ? r : r?.d）
```
**这是淘汰的前提，稿子前版漏写，此处补齐。**

### 1.4 淘汰时机：启动后 idle 单次清扫（非 per-save）
- **不**在 `saveImage` 内每次粘贴都开游标估算 500MB（卡输入路径）
- 改为：`requestIdleCallback`（fallback `setTimeout`）在启动后跑**一次**游标遍历，
  同一游标顺带完成：① 字节求和 ② 超 90 天或超 500MB 的删最旧
- `saveImage` 保持零开销（只写，不查）
- 微信缓存清理也是后台批处理，不在发消息时干——同哲学

### 1.5 自动分层（无 UI）
| 图片来源 | 归类 | 存储策略 |
|---|---|---|
| 聊天粘贴/上传图 | 临时 | IDB + 老化淘汰 |
| ScholarForge / 文献图 | 重要 | 走各自模块既有存储（已核实独立，不归本方案） |
| 外链 URL 图 | 引用 | 现状已是只存 URL 不存字节（无动作） |

### 1.6 死引用降级（惰性，不清扫）
`loadImage(key)` 返回 null（IDB miss / 被老化淘汰）→ 渲染层降级占位 SVG。
**不做批量清扫** `_imageKeys` 死引用，读到时降级即可（惰性哲学）。

## 2. 实施改动点（诚实排期 ~60-80 行前端 + 测试）

### 2.1 `chat-storage.js`
- `IMAGE_STORE` value 格式迁移：`saveImage` 写 `{ d, t }`；`loadImage` 兼容旧字符串
- `IMAGE_EVICT_MAX_BYTES = 500 * 1024 * 1024`、`IMAGE_EVICT_MAX_AGE_DAYS = 90`
- 新增 `evictStaleImages()`：
  - `requestIdleCallback` 触发（fallback `setTimeout(…, 3000)`）
  - 单次 `openCursor` 遍历：累计 `byteSum += (rec.d||rec).length`；
    收集 `t < now - 90d` 或（遍历完且 `byteSum > MAX`）的最旧 N 条 → `store.delete`
  - best-effort，失败仅 `logger.warn`，不阻断
- `saveImage` 保持纯写（不调 evict）

### 2.2 渲染层（`chat-session.js:345` restoreImages）
`loadImage(key)` 返回 null → 渲染占位 SVG（"图片不可用"），不报错、不断裂消息流。

### 2.3 测试 `test_c5_image_evict.mjs`（从磁盘抽真实函数注入 mock IDB）
- 超 90 天 → 触发删除、不抛错
- 超 500MB（用短字符串模拟累加）→ 触发删除最旧
- 旧格式 value（裸字符串）`loadImage` 仍可读（兼容）
- `saveImage` 不触发游标（零开销断言）

## 3. 与既有体系关系
- **c1**：已止血"每次启动丢图"——本方案补"防膨胀"，构成图片治理终态
- **c2/c3/c4**：无关，本方案纯前端、独立小 commit
- **ScholarForge**：独立，不归本方案（§0.4 已核实）

## 4. 否决清单（前稿方案为何弃用，含诚实残余风险）

| 否决项 | 理由 |
|---|---|
| 全量双写 `/api/gui/images` 服务端副本 | ① 桌面端同机同盘=冗余非备份；② c1 已止血头号丢失，ROI 掉档；③ 新增后端端点+鉴权+路径穿越防护，回归面大 |
| 用户"存到资料库"按钮 / 空间管理面板 | 违背小白定位与"零用户存储 UI"红线，认知税过高 |
| 内容寻址 sha256 key 重构 | 改动消息结构 + 历史引用映射，收益不及成本（G0 已清历史图，无迁移价值） |
| 服务端 TTL 淘汰 | 仍依赖新增后端端点，同"全量双写"问题 |
| 真 LRU（按访问时间） | 读放大写 + 聊天场景"看过≠重要"，不如按写入时间删最旧诚实便宜 |
| 条目数上限（≤2000） | 截图体积方差大（0.5–2MB），2000 条可能 20MB 也可能 2GB；**字节才是真约束**，用 500MB 取代条目数 |
| 外链 URL 图"存 URL"改造（§5.2） | **伪需求**：`stripBase64FromContent` 正则只抓 `data:image` 内联 base64，外链从不进 IDB，现状已是只存 URL |
| per-save 检查体积 | 每次粘贴开游标估算卡输入路径；改 idle 单次清扫 |

### 4.1 已接受的残余风险（诚实反例，防翻案）
> **c1 的 IDB 自愈是"打不开 → 删库重建"**——图片库若真损坏，自愈本身就是全删。
> 这是"同盘副本仍有价值"的**唯一真场景**（防逻辑删除，不防物理盘坏）。
> 本方案仍不支持双写：该场景概率极低，且 c1 已将自愈收窄到"只删坏的那个库"而非全清。
> 但须明写：**双写在"防 IDB 逻辑损坏"维度并非零价值**，只是成本/概率权衡后选择不做。
> 记录此残余风险，比把双写写成"全无价值"更能防未来翻案。

## 5. 开放问题投票结果（已闭合）

| 问题 | 结论 |
|---|---|
| Q1 阈值形态 | **90 天 + 500MB 双条件**，启动 idle 单次清扫；**不要**条目数上限 |
| Q2 外链 URL 图 | **砍**——现状已是只存 URL，伪需求 |
| Q3 ScholarForge 图 | **已核实关闭**——零引用聊天图片库，独立 |
| Q4 独立 PR？ | **独立小 commit**，不并入 c1（c1 已 push，语义是"止血"，本方案是"防膨胀"，混了脏历史） |
| Q5 彻底砍 G6？ | **不砍**——IDB 无限增长是真问题（base64 比原图大约 1/3，重度用户一年轻松过 GB），60-80 行买断它值 |

## 6. 状态
- 本稿为**终态实施方案**，待独立 commit 实施（不并入 c1）
- 不新增后端端点、不新增用户 UI 为硬约束
- 前置改动（value 加时间戳）已明确，实施时先做
- Q2/Q3 已当场关闭；Q1/Q4/Q5 投票闭合
