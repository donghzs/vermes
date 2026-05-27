# Vermes 产品页构建指南

> **重要**：`/var/www/html/vermes/` 只放产品介绍+下载页，**绝不部署 Vue 聊天空壳**。铁律不可违反。

## 服务器路径

```
/var/www/html/vermes/
├── index.html          ← 产品页（纯静态 HTML，无构建工具）
├── version.json        ← 版本信息
└── downloads/          ← 安装包文件
    ├── Vermes-{version}-macos-arm64.dmg
    ├── Vermes-{version}-macos-arm64.zip
    ├── Vermes-v{version}-windows-x64.zip
    └── ...（旧版本保留，.spec/.py 等杂文件可清理）
```

## 设计规范

| 项目 | 规范 |
|------|------|
| 背景色 | `#0a0a0a`（暗色） |
| 卡片背景 | `#141414` |
| 主色 | `#22c55e`（绿色） |
| 文字色 | `#f5f5f5` |
| 辅助文字 | `#a3a3a3` |
| 边框 | `#1e1e1e` |
| 字体 | `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif` |
| 圆角 | 卡片 14px，按钮 12px |
| 响应式 | 用 `clamp()` 函数，不用 media query 做字号 |

## 页面结构（8 个卡片，2 列排版）

### Header（与首页 vbit.top 完全一致）
- 左侧：`Vbit` logo（绿色，链接到 `/`）
- 右侧：导航（首页 / **Vermes** / Voffice 喔工）+ 联系支持 hover 弹出二维码
- 二维码：微信公众号 + 飞书（图片路径 `/images/wechat_qrcode.jpg`、`/images/feishu_qrcode.jpg`）

### Hero 区
- 绿色渐变方块图标（V 字母）
- 标题：Vermes
- 副标题：开箱即用的 AI Agent 客户端 · 多模型接入 · 本地运行 · 隐私安全

### 功能亮点（4 个卡片，2 列）
1. 🤖 多模型接入 — DeepSeek、MiMo、Qwen、OpenAI 等 20+ 厂商一键切换
2. ⚡ 免费体验 — 微信扫码登录即送额度，MiMo V2.5 + DeepSeek V4 畅聊
3. 🔒 本地运行 — 桌面客户端运行，支持 Ollama 本地模型，数据不出本机
4. 🎯 开箱即用 — 下载安装扫码即用，无需配置环境，小白友好

### 下载安装（4 个卡片，2 列）
1. **macOS 安装包**（DMG，Apple 官方 logo，推荐标签）— Apple Silicon M1 及以上
2. **macOS 便携版**（ZIP，Apple 官方 logo）— Apple Silicon M1 及以上
3. **Windows 版**（ZIP，Windows 官方四色 logo）— Windows 10/11 x64
4. **GitHub 源码**（GitHub Octocat logo）— 开源 MIT License

### 安装说明
- macOS：DMG 拖入 Applications，首次提示"无法验证开发者"→ 系统设置 → 隐私与安全性 → 仍要打开
- Windows：ZIP 解压运行 Vermes.exe
- 免费体验：微信扫码登录即送额度，无需 API Key

### Footer（与首页完全一致）
- © 2026 Vbit.top
- 粤ICP备2026056664号（链接 beian.miit.gov.cn）
- 公安备案图标 + 粤公网安备44011402001304号（链接 beian.mps.gov.cn）

## 官方 Logo SVG

### Apple Logo（macOS 卡片）
```html
<svg viewBox="0 0 24 24" fill="#aaa"><path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/></svg>
```

### Windows Logo（四色窗口）
```html
<svg viewBox="0 0 24 24"><path d="M0 3.449L9.75 2.1v9.451H0m10.949-9.602L24 0v11.4H10.949M0 12.6h9.75v9.451L0 20.699M10.949 12.6H24V24l-12.9-1.801" fill="#00ADEF"/></svg>
```

### GitHub Logo（Octocat）
```html
<svg viewBox="0 0 24 24" fill="#aaa"><path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"/></svg>
```

## 更新流程

### 更新版本号
1. 修改下载卡片中的版本号（如 `1.1.4` → `1.2.0`）
2. 修改下载文件名中的版本号
3. 修改 `.dl-size` 中的文件大小
4. 修改底部版本信息
5. 更新 `version.json`
6. 上传新的安装包到 `/var/www/html/vermes/downloads/`
7. 部署新的 `index.html`

### 部署命令
```bash
# 写好 HTML 后上传
scp /tmp/vermes-product.html ubuntu@82.156.45.139:/tmp/vermes-product.html
ssh ubuntu@82.156.45.139 "cp /tmp/vermes-product.html /var/www/html/vermes/index.html"
```

### 灾难恢复
```bash
# 从备份恢复
scp ~/Projects/vermes/VERMES-PRODUCT-PAGE-BACKUP.html ubuntu@82.156.45.139:/tmp/vermes-product.html
ssh ubuntu@82.156.45.139 "cp /tmp/vermes-product.html /var/www/html/vermes/index.html"
```

## Nginx 配置（关键）

```nginx
# 下载目录（autoindex 列出文件）
location /vermes/downloads/ {
    alias /var/www/html/vermes/downloads/;
    autoindex on;
}

# 产品页（alias + try_files）
location /vermes/ {
    alias /var/www/html/vermes/;
    try_files $uri $uri/ /vermes/index.html;
}
```

⚠️ **注意**：`/vermes/` 用 `alias` 而非 `root`，所以 index.html 中的资源引用必须用相对路径 `./assets/` 或绝对路径 `/vermes/xxx`，**不能用** `/assets/`（会被 location / 处理，找不到文件）。

## 历史教训

1. **铁律**：`/vermes/` 只放产品介绍+下载页，绝不部署 Vue 聊天空壳
2. **不要用正则插入 HTML**：修改产品页时整体替换整个 section，不要在中间插入片段
3. **必须视觉验证**：每次修改后截图或浏览器预览，不能只靠 grep 检查
4. **SPA 资源路径**：如果误部署了 SPA，`./assets/` 相对路径才正确，`/assets/` 绝对路径会白屏
5. **不要相信之前的"成功"输出**：之前脚本说"✅ 已添加"但实际 HTML 已经坏了

## 备份文件

- 本地备份：`~/Projects/vermes/VERMES-PRODUCT-PAGE-BACKUP.html`
- 服务器：`/var/www/html/vermes/index.html`
- version.json：`/var/www/html/vermes/version.json`
