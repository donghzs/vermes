# Vermes v2.0.7 使用说明

## 快速开始

1. **下载安装**: [vbit.top/vermes](https://vbit.top/vermes/#downloads) 下载对应平台安装包
2. **登录**: 微信扫码登录，自动领取免费体验额度
3. **对话**: 输入问题即可开始，支持图片附件

## 核心功能

### 🤖 多模型切换
- 顶部模型选择器切换 DeepSeek/MiMo/Qwen/OpenAI 等 20+ 模型
- 支持搜索和最近使用置顶
- 免费模型：MiMo（无限聊）、DeepSeek v4 Flash（低配额消耗）

### 🧠 进化系统（NEW）
Vermes 会自动学习你的使用习惯：

- **进化指示器**: 侧边栏底部显示 `🧠 87% · 612 条`，随时了解进化状态
- **每日简报**: 首次打开时展示进化报告（成功率、情绪、反模式）
- **进化时刻**: 工具执行时自动注入建议（"上次这个操作失败过，我换个方法"）
- **成就系统**: 达到里程碑自动解锁（第一步→初露锋芒→百次积累→精准执行→善于学习→经验丰富→失败是成功之母）

### 🔌 API 接入（NEW）
让外部系统调用 Agent：

```bash
# 基础调用
curl -X POST http://localhost:9119/api/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"task":"检查磁盘空间"}'

# 定时任务（每天 9 点生成日报）
0 9 * * * curl -s http://localhost:9119/api/agent/run \
  -d '{"task":"生成昨日工作日报","session_id":"daily-report"}'

# 指定模型
curl -X POST http://localhost:9119/api/agent/run \
  -d '{"task":"分析这段代码","model":"deepseek-v4-flash"}'
```

**参数说明**:
| 参数 | 必填 | 说明 |
|------|------|------|
| task | ✅ | 任务描述 |
| session_id | ❌ | 会话 ID（默认 "api-default"，相同 ID 复用 agent） |
| model | ❌ | 模型名（默认使用配置的默认模型） |
| provider | ❌ | 提供商名（自动推断） |

**返回格式**:
```json
{"ok": true, "session_id": "cron-1", "response": "磁盘剩余 45GB (23%)"}
```

**使用场景**:
- CI/CD 代码审查
- 监控系统故障诊断
- 飞书/企微 webhook 回调
- cron 定时报告

### 🔍 代码智能（NEW）
- **LSP 诊断**: 写代码时自动检查错误，精确到行:列
- **浏览器自动化**: Agent 可以打开网页、截图、填写表单
- 支持 20+ 编程语言（Python/JS/Go/Rust/Java 等）

### ⚡ 快捷键
| 快捷键 | 功能 |
|--------|------|
| `Cmd/Ctrl + K` | 聚焦输入框 |
| `Cmd/Ctrl + N` | 新建会话 |
| `Cmd/Ctrl + B` | 切换侧边栏 |
| `Cmd/Ctrl + ,` | 打开设置 |
| `Escape` | 停止生成 |
| `Shift + Enter` | 多行输入 |

### 💬 消息操作
- **复制**: hover 消息后点击 📋
- **重新生成**: hover 最后一条 AI 消息后点击 🔄
- **编辑**: hover 用户消息后点击 ✏️
- **代码复制**: hover 代码块右上角点击"复制"

## 设置页

### 模型设置
- 添加/删除 API 提供商
- 同步模型列表
- 设置默认模型

### API 接入（关于页）
- 查看 curl 示例
- 复制 curl 命令
- 一键测试 API

### 存储用量
- 查看对话记录、记忆文件、技能缓存占用

## 常见问题

**Q: 免费额度用完了怎么办？**
A: 微信扫码登录自动领取 Token，或在设置页添加自己的 API Key

**Q: 如何使用本地模型？**
A: 安装 Ollama，在设置页选择"本地模型"提供商

**Q: API 如何在服务器上使用？**
A: 启动 Vermes 后，`http://localhost:9119/api/agent/run` 即可调用

**Q: 进化系统会泄露隐私吗？**
A: 进化数据存储在本地 SQLite，不会上传到任何服务器
