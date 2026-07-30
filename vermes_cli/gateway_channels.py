"""
Gateway channel schema — defines the credential fields each platform needs.

Used by the /api/gateway/channels API to render the frontend toggle/config UI.
Each entry describes:
  - key: platform value (matches Platform enum)
  - label: display name (Chinese where appropriate)
  - icon: emoji
  - category: grouping for UI ("国内" / "国际" / "技术")
  - fields: list of credential fields the user must fill in
  - tutorial: short step-by-step text shown below the form
  - apply_url: official URL to register/create the bot
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ChannelField:
    """A single credential field for a platform."""
    key: str           # config.yaml extra key or special token/api_key
    label: str         # Chinese display label
    placeholder: str   # placeholder text
    required: bool = True
    secret: bool = True
    env_key: str = ""  # corresponding .env variable name
    storage: str = "extra"  # "extra" (config.yaml platforms.X.extra) or "token" or "api_key"


@dataclass
class ChannelSchema:
    """Full schema for a single platform channel."""
    key: str
    label: str
    icon: str
    category: str  # "国内" / "国际" / "技术"
    fields: List[ChannelField] = field(default_factory=list)
    tutorial: str = ""
    apply_url: str = ""
    note: str = ""  # extra hint shown below tutorial


def _all_schemas() -> List[ChannelSchema]:
    return [

    # ═══════════════ 国内平台 ═══════════════

    ChannelSchema(
        key="feishu", label="飞书", icon="🪽", category="国内",
        apply_url="https://open.feishu.cn/app",
        fields=[
            ChannelField(key="app_id", label="App ID", placeholder="cli_xxx", env_key="FEISHU_APP_ID"),
            ChannelField(key="app_secret", label="App Secret", placeholder="xxx", env_key="FEISHU_APP_SECRET"),
            ChannelField(key="encrypt_key", label="Encrypt Key", placeholder="可选，用于事件加密", required=False, env_key="FEISHU_ENCRYPT_KEY"),
            ChannelField(key="verification_token", label="Verification Token", placeholder="可选，事件校验", required=False, env_key="FEISHU_VERIFICATION_TOKEN"),
        ],
        tutorial=(
            "1. 打开飞书开放平台 → 创建企业自建应用\n"
            "2. 复制 App ID 和 App Secret 填入上方\n"
            "3. 权限管理 → 开通: im:message, im:chat, im:resource\n"
            "4. 事件订阅 → 选择长连接(WebSocket)模式\n"
            "5. 授权方式：\n"
            "   - 方式A（推荐）：首次私聊会收到配对码，\n"
            "     在终端运行 `vermes pairing approve feishu <配对码>`\n"
            "   - 方式B：设置 FEISHU_ALLOW_ALL_USERS=true，组织内所有人可用\n"
            "6. 保存后点击「启动网关」即可在飞书中与 Agent 对话"
        ),
        note="推荐 WebSocket 长连接模式，无需公网回调地址；私聊默认需要配对",
    ),

    ChannelSchema(
        key="dingtalk", label="钉钉", icon="💬", category="国内",
        apply_url="https://open-dev.dingtalk.com/",
        fields=[
            ChannelField(key="client_id", label="Client ID (AppKey)", placeholder="dingxxx", env_key="DINGTALK_CLIENT_ID"),
            ChannelField(key="client_secret", label="Client Secret (AppSecret)", placeholder="xxx", env_key="DINGTALK_CLIENT_SECRET"),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="DINGTALK_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. 打开钉钉开发者后台 → 创建应用\n"
            "2. 复制 AppKey 和 AppSecret 填入上方\n"
            "3. 权限管理 → 开通: 企业内机器人发送消息、接收消息\n"
            "4. 事件订阅 → Stream 模式（无需公网回调）\n"
            "5. 授权方式：\n"
            "   - 方式A（推荐）：留空 allowed_users，首次私聊收到配对码，\n"
            "     在终端运行 `vermes pairing approve dingtalk <配对码>`\n"
            "   - 方式B：填写 allowed_users（钉钉用户 ID）直接授权\n"
            "   - 方式C（公开）：设置 DINGTALK_ALLOW_ALL_USERS=true\n"
            "6. 保存后点击「启动网关」即可在钉钉中与 Agent 对话"
        ),
        note="推荐 Stream 模式，无需公网服务器；私聊默认需要配对",
    ),

    ChannelSchema(
        key="wecom", label="企业微信", icon="💬", category="国内",
        apply_url="https://work.weixin.qq.com/wework_admin/frame#apps",
        fields=[
            ChannelField(key="bot_id", label="Bot ID", placeholder="企业微信机器人 ID"),
            ChannelField(key="bot_token", label="Bot Token", placeholder="Webhook 机器人的 Token", required=False),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="WECOM_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. 企业微信管理后台 → 应用管理 → 自建应用\n"
            "2. 创建机器人 → 复制 Bot ID 填入上方\n"
            "3. 配置回调地址或使用 API 拉取模式\n"
            "4. 授权方式：\n"
            "   - 方式A（推荐）：首次私聊收到配对码，终端运行\n"
            "     `vermes pairing approve wecom <配对码>`\n"
            "   - 方式B：填写 allowed_users（企业微信用户 ID）直接授权\n"
            "   - 方式C（公开）：设置 WECOM_ALLOW_ALL_USERS=true\n"
            "5. 保存后点击「启动网关」"
        ),
        note="私聊默认需要配对",
    ),

    ChannelSchema(
        key="weixin", label="微信客服", icon="💬", category="国内",
        apply_url="https://mp.weixin.qq.com/",
        fields=[
            ChannelField(key="account_id", label="公众号 Account ID", placeholder="gh_xxx"),
            ChannelField(key="token", label="Token", placeholder="公众号 Token", storage="token"),
            ChannelField(key="encoding_aes_key", label="EncodingAESKey", placeholder="消息加解密密钥", required=False),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="WEIXIN_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. 微信公众平台 → 设置与开发 → 基本配置\n"
            "2. 复制 AppID 作为 Account ID、自定义 Token\n"
            "3. 配置服务器回调地址（需要公网 IP）\n"
            "4. 授权方式：\n"
            "   - 方式A（推荐）：首次消息收到配对码，终端运行\n"
            "     `vermes pairing approve weixin <配对码>`\n"
            "   - 方式B：填写 allowed_users（微信 OpenID）直接授权\n"
            "   - 方式C（公开）：设置 WEIXIN_ALLOW_ALL_USERS=true\n"
            "5. 保存后点击「启动网关」"
        ),
        note="需要公网服务器配置回调地址；默认需要配对",
    ),

    ChannelSchema(
        key="qqbot", label="QQ 机器人", icon="💬", category="国内",
        apply_url="https://q.qq.com",
        fields=[
            ChannelField(key="app_id", label="App ID", placeholder="QQ 机器人 AppID", env_key="QQ_APP_ID"),
            ChannelField(key="client_secret", label="Client Secret", placeholder="QQ 机器人 Secret", env_key="QQ_CLIENT_SECRET"),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="QQ_ALLOWED_USERS"),
            ChannelField(key="sandbox", label="沙箱模式", placeholder="true/false", required=False, secret=False),
        ],
        tutorial=(
            "1. 打开 QQ 机器人平台 → 创建机器人\n"
            "2. 复制 App ID 和 Client Secret 填入上方\n"
            "3. 开发设置 → 配置 WebSocket 回调\n"
            "4. 授权方式：\n"
            "   - 方式A（推荐）：留空 allowed_users，首次私聊会收到配对码，\n"
            "     在终端运行 `vermes pairing approve qqbot <配对码>` 即可\n"
            "   - 方式B：填写 allowed_users（QQ 用户 ID），直接授权无需配对\n"
            "   - 方式C（公开）：设置 QQ_ALLOW_ALL_USERS=true，所有人可用\n"
            "5. 保存后点击「启动网关」即可在 QQ 中与 Agent 对话"
        ),
        note="私聊默认需要配对；群聊通过 QQ_GROUP_ALLOWED_USERS 授权",
    ),

    ChannelSchema(
        key="yuanbao", label="腾讯元宝", icon="🤖", category="国内",
        apply_url="https://yuanbao.tencent.com/",
        fields=[
            ChannelField(key="app_id", label="App ID", placeholder="元宝 AppID", env_key="YUANBAO_APP_ID"),
            ChannelField(key="app_secret", label="App Secret", placeholder="元宝 AppSecret", env_key="YUANBAO_APP_SECRET"),
            ChannelField(key="bot_id", label="Bot ID", placeholder="可选，机器人 ID", required=False),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="YUANBAO_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. 腾讯元宝开放平台 → 创建应用\n"
            "2. 复制 App ID 和 App Secret 填入上方\n"
            "3. 配置机器人回调\n"
            "4. 授权方式：\n"
            "   - 方式A（推荐）：首次私聊收到配对码，终端运行\n"
            "     `vermes pairing approve yuanbao <配对码>`\n"
            "   - 方式B：填写 allowed_users 直接授权\n"
            "   - 方式C（公开）：设置 YUANBAO_ALLOW_ALL_USERS=true\n"
            "5. 保存后点击「启动网关」"
        ),
        note="私聊默认需要配对",
    ),

    # ═══════════════ 国际平台 ═══════════════

    ChannelSchema(
        key="telegram", label="Telegram", icon="📱", category="国际",
        apply_url="https://t.me/BotFather",
        fields=[
            ChannelField(key="token", label="Bot Token", placeholder="123456:ABC-DEF...", storage="token", env_key="TELEGRAM_BOT_TOKEN"),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则所有人可用", required=False, secret=False, env_key="TELEGRAM_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. 在 Telegram 中搜索 @BotFather → 发送 /newbot\n"
            "2. 按提示设置名称 → 复制 Bot Token 填入上方\n"
            "3. （可选）搜索 @userinfobot 获取你的 User ID\n"
            "4. 授权方式：\n"
            "   - 方式A（推荐）：留空 allowed_users，首次私聊收到配对码，\n"
            "     在终端运行 `vermes pairing approve telegram <配对码>`\n"
            "   - 方式B：填写 allowed_users（User ID）直接授权\n"
            "   - 方式C（公开）：设置 TELEGRAM_ALLOW_ALL_USERS=true，所有人可用\n"
            "5. 保存后点击「启动网关」即可在 Telegram 中与 Agent 对话"
        ),
        note="最简单的接入方式，3 分钟完成；私聊默认需要配对",
    ),

    ChannelSchema(
        key="discord", label="Discord", icon="💬", category="国际",
        apply_url="https://discord.com/developers/applications",
        fields=[
            ChannelField(key="token", label="Bot Token", placeholder="MTk4NjIy...", storage="token", env_key="DISCORD_BOT_TOKEN"),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则所有人可用", required=False, secret=False, env_key="DISCORD_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. Discord Developer Portal → New Application → Bot\n"
            "2. 复制 Bot Token 填入上方\n"
            "3. 开启 Message Content Intent + Server Members Intent\n"
            "4. OAuth2 → 复制邀请链接 → 邀请机器人到你的服务器\n"
            "5. 授权方式：\n"
            "   - 方式A（推荐）：留空 allowed_users，首次私聊收到配对码，\n"
            "     在终端运行 `vermes pairing approve discord <配对码>`\n"
            "   - 方式B：填写 allowed_users（Discord User ID）直接授权\n"
            "   - 方式C（公开）：设置 DISCORD_ALLOW_ALL_USERS=true\n"
            "6. 保存后点击「启动网关」即可在 Discord 中与 Agent 对话"
        ),
        note="私聊默认需要配对",
    ),

    ChannelSchema(
        key="slack", label="Slack", icon="💼", category="国际",
        apply_url="https://api.slack.com/apps",
        fields=[
            ChannelField(key="bot_token", label="Bot Token (xoxb-)", placeholder="xoxb-xxx", env_key="SLACK_BOT_TOKEN"),
            ChannelField(key="app_token", label="App Token (xapp-)", placeholder="xapp-xxx", required=False, env_key="SLACK_APP_TOKEN"),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="SLACK_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. Slack API → Create New App → From scratch\n"
            "2. OAuth & Permissions → 添加 scope: chat:write, app_mentions:read\n"
            "3. Install App → 复制 Bot Token (xoxb-)\n"
            "4. （可选）Socket Mode → 生成 App Token (xapp-)\n"
            "5. Event Subscriptions → 订阅 app_mention, message.im\n"
            "6. 授权方式：\n"
            "   - 方式A（推荐）：留空，首次私聊收到配对码，\n"
            "     在终端运行 `vermes pairing approve slack <配对码>`\n"
            "   - 方式B：填写 allowed_users（Slack User ID）直接授权\n"
            "   - 方式C（公开）：设置 SLACK_ALLOW_ALL_USERS=true\n"
            "7. 保存后点击「启动网关」"
        ),
        note="私聊默认需要配对",
    ),

    ChannelSchema(
        key="whatsapp", label="WhatsApp", icon="📱", category="国际",
        apply_url="https://wa.me/",
        fields=[
            ChannelField(key="phone", label="手机号", placeholder="关联 WhatsApp 的手机号", required=False, secret=False),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="WHATSAPP_ALLOWED_USERS"),
        ],
        tutorial=(
            "WhatsApp 通过桥接服务接入，无需创建 Bot。\n"
            "1. 确保 WhatsApp Web 已在浏览器登录\n"
            "2. 配置桥接服务（如 Baileys/whatsapp-web.js）\n"
            "3. 授权方式：\n"
            "   - 方式A：留空，首次消息收到配对码，\n"
            "     在终端运行 `vermes pairing approve whatsapp <配对码>`\n"
            "   - 方式B：填写 allowed_users（手机号）直接授权\n"
            "   - 方式C（公开）：设置 WHATSAPP_ALLOW_ALL_USERS=true\n"
            "4. 保存后点击「启动网关」"
        ),
        note="需要额外配置 WhatsApp 桥接服务；默认需要配对",
    ),

    ChannelSchema(
        key="signal", label="Signal", icon="📡", category="国际",
        apply_url="https://signal.org/",
        fields=[
            ChannelField(key="http_url", label="Signal CLI REST API 地址", placeholder="http://localhost:8080", secret=False),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="SIGNAL_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. 安装 signal-cli-rest-api（Docker 推荐）\n"
            "2. 注册并验证 Signal 号码\n"
            "3. 将 REST API 地址填入上方\n"
            "4. 授权方式：\n"
            "   - 方式A：留空，首次消息收到配对码，\n"
            "     在终端运行 `vermes pairing approve signal <配对码>`\n"
            "   - 方式B：填写 allowed_users（Signal 号码）直接授权\n"
            "   - 方式C（公开）：设置 SIGNAL_ALLOW_ALL_USERS=true\n"
            "5. 保存后点击「启动网关」"
        ),
        note="需要自建 Signal CLI REST API 服务；默认需要配对",
    ),

    ChannelSchema(
        key="matrix", label="Matrix", icon="💬", category="国际",
        apply_url="https://matrix.org/",
        fields=[
            ChannelField(key="homeserver", label="Homeserver URL", placeholder="https://matrix.org", secret=False, env_key="MATRIX_HOMESERVER"),
            ChannelField(key="user_id", label="User ID", placeholder="@bot:matrix.org", secret=False, env_key="MATRIX_USER_ID"),
            ChannelField(key="access_token", label="Access Token", placeholder="syt_xxx", env_key="MATRIX_ACCESS_TOKEN"),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="MATRIX_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. 在 Matrix 服务器注册账号\n"
            "2. 设置 → Help & About → Access Token → 复制\n"
            "3. 将 Homeserver、User ID、Access Token 填入上方\n"
            "4. 授权方式：\n"
            "   - 方式A：留空，首次消息收到配对码，\n"
            "     在终端运行 `vermes pairing approve matrix <配对码>`\n"
            "   - 方式B：填写 allowed_users（Matrix User ID）直接授权\n"
            "   - 方式C（公开）：设置 MATRIX_ALLOW_ALL_USERS=true\n"
            "5. 保存后点击「启动网关」"
        ),
        note="默认需要配对",
    ),

    ChannelSchema(
        key="mattermost", label="Mattermost", icon="💬", category="国际",
        apply_url="https://mattermost.com/",
        fields=[
            ChannelField(key="url", label="Mattermost URL", placeholder="https://your-team.mattermost.com", secret=False, env_key="MATTERMOST_URL"),
            ChannelField(key="token", label="Bot Token", placeholder="xxx", storage="token", env_key="MATTERMOST_TOKEN"),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="MATTERMOST_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. Mattermost → Integrations → Bot Accounts → Create\n"
            "2. 复制 Bot Token 填入上方\n"
            "3. 将服务器 URL 填入上方\n"
            "4. 授权方式：\n"
            "   - 方式A：留空，首次消息收到配对码，\n"
            "     在终端运行 `vermes pairing approve mattermost <配对码>`\n"
            "   - 方式B：填写 allowed_users 直接授权\n"
            "   - 方式C（公开）：设置 MATTERMOST_ALLOW_ALL_USERS=true\n"
            "5. 保存后点击「启动网关」"
        ),
        note="默认需要配对",
    ),

    ChannelSchema(
        key="email", label="邮件", icon="📧", category="国际",
        apply_url="",
        fields=[
            ChannelField(key="address", label="邮箱地址", placeholder="bot@example.com", secret=False),
            ChannelField(key="imap_server", label="IMAP 服务器", placeholder="imap.gmail.com:993", required=False, secret=False),
            ChannelField(key="smtp_server", label="SMTP 服务器", placeholder="smtp.gmail.com:587", required=False, secret=False),
            ChannelField(key="password", label="邮箱密码/应用密码", placeholder="xxx"),
            ChannelField(key="allowed_users", label="允许的发件人", placeholder="逗号分隔邮箱地址，留空则需配对", required=False, secret=False, env_key="EMAIL_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. 创建专用邮箱账号\n"
            "2. 配置 IMAP/SMTP 服务器地址\n"
            "3. 填入邮箱地址和密码\n"
            "4. 授权方式：\n"
            "   - 方式A：留空，首次邮件收到配对码回复，\n"
            "     在终端运行 `vermes pairing approve email <配对码>`\n"
            "   - 方式B：填写 allowed_users（发件人邮箱）直接授权\n"
            "   - 方式C（公开）：设置 EMAIL_ALLOW_ALL_USERS=true\n"
            "5. 保存后点击「启动网关」\n"
            "Agent 将自动回复收到的邮件"
        ),
        note="默认需要配对",
    ),

    ChannelSchema(
        key="sms", label="短信 (Twilio)", icon="📲", category="国际",
        apply_url="https://www.twilio.com/",
        fields=[
            ChannelField(key="account_sid", label="Account SID", placeholder="ACxxx", env_key="TWILIO_ACCOUNT_SID"),
            ChannelField(key="auth_token", label="Auth Token", placeholder="xxx", env_key="TWILIO_AUTH_TOKEN"),
            ChannelField(key="from_number", label="发信号码", placeholder="+1234567890", required=False, secret=False),
            ChannelField(key="allowed_users", label="允许的号码", placeholder="逗号分隔手机号，留空则需配对", required=False, secret=False, env_key="SMS_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. 注册 Twilio 账号 → 获取 Trial 号码\n"
            "2. 复制 Account SID 和 Auth Token 填入上方\n"
            "3. 授权方式：\n"
            "   - 方式A：留空，首次短信收到配对码回复，\n"
            "     在终端运行 `vermes pairing approve sms <配对码>`\n"
            "   - 方式B：填写 allowed_users（手机号）直接授权\n"
            "   - 方式C（公开）：设置 SMS_ALLOW_ALL_USERS=true\n"
            "4. 保存后点击「启动网关」"
        ),
        note="默认需要配对",
    ),

    ChannelSchema(
        key="bluebubbles", label="BlueBubbles (iMessage)", icon="💙", category="国际",
        apply_url="https://bluebubbles.app/",
        fields=[
            ChannelField(key="server_url", label="Server URL", placeholder="http://your-mac:1234", secret=False),
            ChannelField(key="password", label="Password", placeholder="BlueBubbles 服务器密码"),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="BLUEBUBBLES_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. 在 Mac 上安装 BlueBubbles Server\n"
            "2. 配置服务器地址和密码\n"
            "3. 将 Server URL 和 Password 填入上方\n"
            "4. 授权方式：\n"
            "   - 方式A：留空，首次消息收到配对码，\n"
            "     在终端运行 `vermes pairing approve bluebubbles <配对码>`\n"
            "   - 方式B：填写 allowed_users 直接授权\n"
            "   - 方式C（公开）：设置 BLUEBUBBLES_ALLOW_ALL_USERS=true\n"
            "5. 保存后点击「启动网关」"
        ),
        note="需要一台始终在线的 Mac 运行 BlueBubbles Server；默认需要配对",
    ),

    ChannelSchema(
        key="line", label="LINE", icon="💬", category="国际",
        apply_url="https://developers.line.biz/",
        fields=[
            ChannelField(key="channel_access_token", label="Channel Access Token", placeholder="xxx", env_key="LINE_CHANNEL_ACCESS_TOKEN"),
            ChannelField(key="channel_secret", label="Channel Secret", placeholder="xxx", env_key="LINE_CHANNEL_SECRET"),
            ChannelField(key="allow_all_users", label="公开模式", placeholder="true/false", required=False, secret=False),
        ],
        tutorial=(
            "1. LINE Developers → Create Provider → Create Messaging API Channel\n"
            "2. 复制 Channel Access Token 和 Channel Secret 填入上方\n"
            "3. Webhook → 设置回调 URL（需要公网）\n"
            "4. 授权方式：\n"
            "   - 方式A：LINE 默认公开，好友即可对话（无需配对）\n"
            "   - 方式B：设置 allow_all_users=false 则需配对\n"
            "5. 保存后点击「启动网关」"
        ),
        note="LINE 默认公开模式，好友即可对话",
    ),

    ChannelSchema(
        key="irc", label="IRC", icon="📡", category="国际",
        apply_url="",
        fields=[
            ChannelField(key="server", label="服务器地址", placeholder="irc.libera.chat", required=False, secret=False, env_key="IRC_SERVER"),
            ChannelField(key="channel", label="频道", placeholder="#my-channel", required=False, secret=False, env_key="IRC_CHANNEL"),
            ChannelField(key="nick", label="昵称", placeholder="VermesBot", required=False, secret=False, env_key="IRC_NICKNAME"),
            ChannelField(key="server_password", label="服务器密码", placeholder="可选", required=False, env_key="IRC_SERVER_PASSWORD"),
        ],
        tutorial=(
            "1. 选择 IRC 网络（如 libera.chat）\n"
            "2. 设置昵称和频道\n"
            "3. 授权方式：\n"
            "   - 方式A：留空，首次消息收到配对码，\n"
            "     在终端运行 `vermes pairing approve irc <配对码>`\n"
            "   - 方式B：填写 allowed_users 直接授权\n"
            "   - 方式C（公开）：设置 IRC_ALLOW_ALL_USERS=true\n"
            "4. 保存后点击「启动网关」"
        ),
        note="IRC 默认公开模式（JOIN 即可见消息），设置 allowed_users 后需配对",
    ),

    ChannelSchema(
        key="twitch", label="Twitch", icon="🎮", category="国际",
        apply_url="https://twitch.tv/",
        fields=[
            ChannelField(key="nick", label="昵称", placeholder="Bot 昵称", env_key="IRC_NICK"),
            ChannelField(key="password", label="OAuth Token", placeholder="oauth:xxx"),
        ],
        tutorial=(
            "1. 用 Bot 账号登录 Twitch\n"
            "2. 访问 tmi.twitch.tv 生成 OAuth Token\n"
            "3. 将昵称和 OAuth Token 填入上方\n"
            "4. 授权方式：\n"
            "   - 方式A：留空，首次消息收到配对码，\n"
            "     在终端运行 `vermes pairing approve twitch <配对码>`\n"
            "   - 方式B：填写 allowed_users 直接授权\n"
            "   - 方式C（公开）：设置 TWITCH_ALLOW_ALL_USERS=true\n"
            "5. 保存后点击「启动网关」"
        ),
        note="Twitch 使用 IRC 协议，凭据格式与 IRC 相同；默认需要配对",
    ),

    ChannelSchema(
        key="zalo", label="Zalo", icon="💬", category="国际",
        apply_url="https://developers.zalo.me/",
        fields=[
            ChannelField(key="access_token", label="Access Token", placeholder="xxx", env_key="ZALO_ACCESS_TOKEN"),
            ChannelField(key="secret_key", label="Secret Key", placeholder="xxx", env_key="ZALO_SECRET_KEY"),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="ZALO_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. Zalo Developers → 创建 Official Account\n"
            "2. 复制 Access Token 和 Secret Key 填入上方\n"
            "3. 配置 Webhook 回调\n"
            "4. 授权方式：\n"
            "   - 方式A：留空，首次消息收到配对码，\n"
            "     在终端运行 `vermes pairing approve zalo <配对码>`\n"
            "   - 方式B：填写 allowed_users 直接授权\n"
            "   - 方式C（公开）：设置 ZALO_ALLOW_ALL_USERS=true\n"
            "5. 保存后点击「启动网关」"
        ),
        note="默认需要配对",
    ),

    ChannelSchema(
        key="nostr", label="Nostr", icon="🟣", category="国际",
        apply_url="https://nostr.com/",
        fields=[
            ChannelField(key="private_key", label="Private Key (nsec)", placeholder="nsec1xxx", env_key="NOSTR_PRIVATE_KEY"),
            ChannelField(key="relay", label="Relay 地址", placeholder="wss://relay.damus.io", required=False, secret=False),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔 npub，留空则需配对", required=False, secret=False, env_key="NOSTR_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. 生成 Nostr 密钥对（nsec 开头）\n"
            "2. 将 Private Key 填入上方\n"
            "3. （可选）设置 Relay 地址\n"
            "4. 授权方式：\n"
            "   - 方式A：留空，首次消息收到配对码，\n"
            "     在终端运行 `vermes pairing approve nostr <配对码>`\n"
            "   - 方式B：填写 allowed_users（npub）直接授权\n"
            "   - 方式C（公开）：设置 NOSTR_ALLOW_ALL_USERS=true\n"
            "5. 保存后点击「启动网关」"
        ),
        note="默认需要配对",
    ),

    ChannelSchema(
        key="synology_chat", label="Synology Chat", icon="💾", category="国际",
        apply_url="https://www.synology.com/",
        fields=[
            ChannelField(key="incoming_url", label="Incoming Webhook URL", placeholder="https://nas/chat/webapi/...", secret=False, env_key="SYNOLOGY_CHAT_INCOMING_URL"),
            ChannelField(key="allowed_users", label="允许的用户 ID", placeholder="逗号分隔，留空则需配对", required=False, secret=False, env_key="SYNOLOGY_CHAT_ALLOWED_USERS"),
        ],
        tutorial=(
            "1. Synology Chat → 创建 Integration → Incoming Webhook\n"
            "2. 复制 Webhook URL 填入上方\n"
            "3. 授权方式：\n"
            "   - 方式A：留空，首次消息收到配对码，\n"
            "     在终端运行 `vermes pairing approve synology_chat <配对码>`\n"
            "   - 方式B：填写 allowed_users 直接授权\n"
            "   - 方式C（公开）：设置 SYNOLOGY_CHAT_ALLOW_ALL_USERS=true\n"
            "4. 保存后点击「启动网关」"
        ),
        note="默认需要配对",
    ),

    # ═══════════════ 技术平台 ═══════════════

    ChannelSchema(
        key="homeassistant", label="Home Assistant", icon="🏠", category="技术",
        apply_url="https://www.home-assistant.io/",
        fields=[
            ChannelField(key="token", label="Long-Lived Access Token", placeholder="xxx", storage="token"),
            ChannelField(key="url", label="Home Assistant URL", placeholder="http://homeassistant.local:8123", required=False, secret=False),
        ],
        tutorial=(
            "1. Home Assistant → Profile → Long-Lived Access Tokens → Create\n"
            "2. 复制 Token 填入上方\n"
            "3. 将 HA URL 填入上方\n"
            "4. 保存后点击「启动网关」\n"
            "HA 事件由系统生成，Token 已验证身份，无需配对"
        ),
        note="技术平台，无需配对（Token 认证）",
    ),

    ChannelSchema(
        key="webhook", label="Webhook", icon="🔗", category="技术",
        apply_url="",
        fields=[
            ChannelField(key="secret", label="Webhook Secret", placeholder="自定义密钥", required=False),
            ChannelField(key="port", label="监听端口", placeholder="8080", required=False, secret=False),
        ],
        tutorial=(
            "1. 设置 Webhook 密钥（用于验证请求）\n"
            "2. （可选）设置监听端口\n"
            "3. 保存后点击「启动网关」\n"
            "4. 向 http://your-server:port/webhook 发送 POST 请求\n"
            "Webhook 通过 HMAC 签名验证，无需配对"
        ),
        note="技术平台，无需配对（HMAC 签名验证）",
    ),

    ChannelSchema(
        key="api_server", label="API Server", icon="🌐", category="技术",
        apply_url="",
        fields=[
            ChannelField(key="key", label="API Key", placeholder="自定义 API Key", required=False),
            ChannelField(key="port", label="监听端口", placeholder="8080", required=False, secret=False),
        ],
        tutorial=(
            "1. 设置 API Key（用于鉴权）\n"
            "2. （可选）设置监听端口\n"
            "3. 保存后点击「启动网关」\n"
            "4. 通过 OpenAI 兼容 API 调用: POST /v1/chat/completions\n"
            "API Server 通过 API Key 鉴权，无需配对"
        ),
        note="技术平台，无需配对（API Key 鉴权）；提供 OpenAI 兼容接口",
    ),

    ]


# Build lookup index
SCHEMAS: dict[str, ChannelSchema] = {s.key: s for s in _all_schemas()}


def get_channel_schema(platform_key: str) -> Optional[ChannelSchema]:
    return SCHEMAS.get(platform_key)


def get_all_channel_schemas() -> List[ChannelSchema]:
    return _all_schemas()
