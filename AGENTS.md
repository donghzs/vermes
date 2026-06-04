# Vermes Agent

Vermes 是基于 Hermes Agent 引擎的中文 AI Agent 分发版。

## 快速开始

```bash
vermes setup    # 初始化配置（选择模型和提供商）
vermes          # 启动对话
```

## 技能
- 内置 30+ 技能，覆盖文档处理、搜索、邮件、天气等
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
