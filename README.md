# Vermes 🚀

> 你的 AI，即刻启程 — 开箱即用的中文 AI Agent

Vermes **fork 自 [Nous Research · Hermes Agent](https://github.com/NousResearch/hermes-agent)**（MIT），
在**同一引擎**上做中文本地化 + 自研 Electron 桌面 GUI + 学术垂直（ScholarForge）。
与 [EKKOLearnAI · Hermes Studio](https://github.com/EKKOLearnAI/hermes-studio)（WebUI，BSL-1.1）
是**同源异 GUI 的平行路线**：引擎同根，壳我们自己写。

> 一句话：**引擎 fork 自 Nous Research（MIT），GUI 与中文体验由我们重写**——不是「纯国产原创」，也不受 EKKO WebUI 的 BSL-1.1 约束。

## ✨ 特性

- 🧠 **自进化 Agent** — 从经验中学习，创建和改进技能
- 🛠 **技能可扩展** — 仓内 `optional-skills/` 约 80+ 可选技能（文档/搜索/邮件/天气/代码…），装到 `~/.vermes/skills/` 即用
- 🏪 **技能商店** — 随时通过 Skillhub 扩展能力
- 🌐 **多模型支持** — DeepSeek、Qwen、OpenAI、Ollama...
- 💻 **全平台** — macOS / Windows / Linux
- 🇨🇳 **中文优先** — 默认中文交互，预置国内模型

## 📦 安装

### macOS / Linux
```bash
curl -fsSL https://install.vbit.top | bash
```

### pip
```bash
pip install vermes
vermes setup
vermes
```

## 🚀 快速开始

1. `vermes setup` — 选择模型和提供商（推荐 DeepSeek）
2. `vermes` — 开始对话！

## 🏪 技能商店

```bash
vermes skills search 天气   # 搜索技能
vermes skills install weather  # 安装技能
```

或直接在对话中让 Vermes 帮你安装。

## 📁 配置

| 文件 | 路径 | 说明 |
|------|------|------|
| 配置 | `~/.vermes/config.yaml` | 模型、工具、Agent 设置 |
| 环境 | `~/.vermes/.env` | API Keys（安全存储） |
| 技能 | `~/.vermes/skills/` | 自定义技能目录 |
| 人格 | `~/.vermes/SOUL.md` | Agent 个性定义 |

## 🙏 致谢

- [Nous Research · Hermes Agent](https://github.com/NousResearch/hermes-agent) — 引擎上游（MIT）
- [QClaw / Skillhub](https://skillhub.cn) — 技能生态

## 📄 许可

MIT License（全栈）。**不**从 EKKOLearnAI/hermes-web-ui 搬前端代码（该仓为 BSL-1.1）。
