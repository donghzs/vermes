# Vermes Agent

Vermes 是基于 Vermes Agent 引擎的中文 AI Agent 分发版。

## 快速开始

```bash
vermes setup    # 初始化配置（选择模型和提供商）
vermes          # 启动对话
```

## 技能
- 运行时技能目录：`~/.vermes/skills/`（安装/更新时从仓内同步，也可由 Agent 自进化写入）
- 仓内 `skills/` 仅含 SOUL/结构目录，几乎无 `SKILL.md` 清单；可选技能库在 `optional-skills/`（约 80+，覆盖文档/搜索/邮件/天气/代码等，可按需安装）
- 支持 Skillhub 技能商店，随时扩展能力
- 自进化：Agent 可从经验中创建和改进技能

## 配置
- 配置文件：~/.vermes/config.yaml
- 技能目录：~/.vermes/skills/
- 环境变量：~/.vermes/.env

## Git Pre-commit Hook (安全检查)

所有仓库都配置了全局 pre-commit hook，**提交前自动检查敏感信息**。

**检查内容：**
- 服务器 IP 地址（如 `<server-ip>`）
- API Key（sk- 开头的长字符串）
- SSH 用户名（如 `<user>@`）
- 密码/Secret/Token 字段
- 私钥文件
- 微信 AppID

**如果 commit 被拒绝：**
1. 查看错误提示中的 **修复建议**
2. 用占位符替换真实值：
   - `<server-ip>` → 占位符
   - `<api-key>` 或 `your_api_key_here` → 占位符
   - `<user>@` → 占位符
   - 硬编码密码 → 环境变量 `os.environ['PASSWORD']`
3. 重新 `git add` 和 `git commit`

**示例：**
```bash
# ❌ 错误：包含真实值
scp file <user>@<server-ip>:/path/
api_key = "<api-key-prefix>..."

# ✅ 正确：使用占位符
scp file <user>@<server>:/path/
api_key = os.environ['API_KEY']
```

**跳过检查（不推荐）：** `git commit --no-verify`

## ⚖️ 血缘与许可证红线（2026-09-26）

- 引擎 fork 自 **NousResearch/hermes-agent（MIT）**；GUI 自研（Electron / Vue3+Tailwind，MIT）。
- **禁止**从 **EKKOLearnAI/hermes-web-ui / hermes-studio** 搬前端代码——**BSL-1.1**，商用有约束。
- 对外文案禁止「纯国产原创」「基于 Vermes Agent」（循环自证）；标准口径见 README。
- 借鉴只限引擎层（MIT）的接口思路；GUI 保持自研。

---

## 🔍 发现即刀 + 一并处理（2026-09-26）

- **发现问题当场修或当场登记**，不「留以后」——否则会忘。
- 一次审计/测试里扫出的多项，**同一批处理完再收工**；只留「有意递延」且写明触发条件。
- 基线对比一律用 `git worktree`，**绝不动工作树**（曾因 restore 写错路径污染）。
- 改动面的契约测必须含**同文件全量**（如 `test_feishu.py`/`test_qqbot.py`），不能只跑新测文件——否则回归漏网（L-037 `create_task` 教训）。
- 仓库内 `TMPDIR` 会造成假红；跑测用 `TMPDIR=/tmp`。

---

## 🔧 Git 工作流规范

Vermes 项目严格遵守 **分支工作流**，禁止直接在 main 上操作。

### 核心规则

| 规则 | 说明 |
|------|------|
| **1. 先建分支** | 任何改动，先 `git checkout -b feat/xxx` 或 `fix/xxx` |
| **2. 分支粒度** | 一个功能/一个修复 = 一个分支。不要堆在一起 |
| **3. 测试确认** | 分支上确认代码能跑、语法通过，再合 main |
| **4. review 大改动** | >20 个文件的改动，先在 issue 或群里讨论 |
| **5. 合入 main** | `git checkout main && git merge feat/xxx` |
| **6. 不 force push** | `main` 分支永远 `--force` 禁止。出问题开新分支修 |
| **7. 发布打 tag** | 发布时 `git tag vX.Y.Z && git push origin vX.Y.Z` |

### 分支命名

```
feat/xxx      — 新功能
fix/xxx       — 修 bug
chore/xxx     — 工具链/配置/文档
release/v*    — 发布分支
```

### 事故处理（如果 main 崩了）

1. `git log` 找最后一个干净的提交
2. `git checkout -b recovery/xxx <clean-hash>`
3. 在 recovery 分支重新 cherry-pick 需要的改动
4. 确认没问题后，用 recovery 分支覆盖 main
5. **不要在 main 上直接 reset --hard**

### 总结

> main 是产品，不是草稿纸。
> 每个分支解决的问题不超过一个。
