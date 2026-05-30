# QClaw 工作接力 — 移动端 & Windows 版同步

## 当前状态总览

```
桌面版: QClaw 在 A11 上 PyInstaller 构建 → 我(Hermes)做 Inno Setup 安装包 → 发布
移动端: 公众号"胜比特"已连上轻量 Agent 服务 → 需更换完整引擎
```

---

## 一、桌面版 Windows 构建（待收尾）

**QClaw 已完成**：PyInstaller `vermes-gui.spec` 构建（dist/Vermes/Vermes.exe）

**上次问题已修复**：源码包已更新（加了 `toolsets.py`, `toolset_distributions.py`, `utils.py`, `hermes_bootstrap.py` 等缺失模块到 datas 和 hiddenimports）

**下一个关键步骤**：
```
1. 验证构建：dist/Vermes/Vermes.exe 存在
2. 注入 VC++ DLL（vcruntime140.dll, vcruntime140_1.dll, msvcp140.dll 等 6 个 → 根目录 + _internal 双份）
3. 编译 Inno Setup 安装包（绿色 V 图标）
   ISCC.exe packaging\vermes-inno-setup.iss
4. 安装包上传到 vbit: /var/www/html/vermes/downloads/
```

---

## 二、移动端 — 微信 Agent

### 已完成

| 项目 | 详情 |
|------|------|
| 公众号配置 | "胜比特"服务器配置已通过验证，URL=`https://vbit.top/wechat`，Token=`shengbiti2026` |
| 微信消息网关 v2 | 运行在 vbit:9221，DeepSeek 直连，每个用户独立 SQLite DB |
| 用户测试 | 回复正常（"帮我关注PS5降价"→"已经记录"） |
| Vermes 引擎 | 已安装到 `/opt/vermes/`，Python 3.11.15 venv，已测试通过 |
| v3 集成脚本 | `/opt/vbit/wechat_agent_v3.py` — 调 Vermes 引擎的完整版 |

### 待完成（优先级排序）

```
P0: 微信 v3 服务切换
    /opt/vermes/.venv/bin/pip install fastapi uvicorn aiohttp lxml
    pkill -f wechat_agent_service
    cd /opt/vbit && nohup /opt/vermes/.venv/bin/python wechat_agent_v3.py > /tmp/wechat-agent-v3.log 2>&1 &
    curl http://127.0.0.1:9221/health

P1: 验证完整引擎跑通
    发"帮我关注PS5降价" → 引擎应该调工具而不是纯聊天
    看日志确认工具调用正常

P2: cron 调度器实现
    引擎自带 cron 能力，但需要把用户的定时任务持久化并运行
    用户说"提醒我XX" → 引擎记cron → 到期推送

P3: 声音克隆 TTS
    火山引擎/MiniMax API 接入（3秒样本即可克隆）
    cron 推送走 TTS 合成语音 → 微信语音消息
```

### 重要限制

- **"胜比特"是订阅号**，不是服务号。订阅号：
  - 不能主动推送消息给用户（48小时外）
  - 回复必须在5秒内同步返回 XML
  - 没有客服消息 API
- 要解决主动推送，需要：iLink Bot（网关已有 weixin.py）或升级服务号
- 引擎跑在单台 2核4G 服务器上，并发用户数有限（~300 同时活跃）

### 关键账号

| 资源 | 值 |
|------|-----|
| 公众号 AppID | wxf417d09c7a92b87d |
| 公众号 Secret | REDACTED_WECHAT_MP_SECRET |
| vbit 服务器 | REDACTED_USER@REDACTED_SERVER_IP |
| DeepSeek API Key | REDACTED_API_KEY |
| Vermes 引擎位置 | `/opt/vermes/` (venv: `/opt/vermes/.venv/`) |
| WeChat Token | shengbiti2026 |
| 微信消息网关 | vbit:9221, nginx 路由 `/wechat` |

---

## 三、移动端产品方向

**不做"你问我答"的 chatbot，做"越来越懂你的私人助理"**

核心竞争力：
1. **个性化记忆** — 记住每个用户的偏好/习惯
2. **主动推送** — cron 任务监控（价格/天气/提醒）
3. **声音克隆** — 用喜欢的人的声音提醒
4. **不碰隐私数据** — 只爬公开信息（比价/天气/活动）

不做：医疗/金融/企业级深度场景、跨小程序协同、低代码平台

全媒体能力路线：
- V1 ✅ 文字对话（已完成）
- V2 图片/语音/位置识别（引擎已有，需接微信格式）
- V3 视频/文件处理（引擎已有）
- V4 声音克隆 TTS（接火山/MiniMax API）
- V5 主动推送系统（需解决订阅号限制）
