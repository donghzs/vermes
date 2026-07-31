# -*- mode: python ; coding: utf-8 -*-
"""
Vermes Backend (Headless) — Electron 使用的后端专用构建脚本。
不含 pywebview / pyobjc / GUI 依赖，体积更小。
Usage: pyinstaller vermes-backend.spec
"""
import sys
import os

block_cipher = None

# spec 文件所在目录：datas / 入口必须用绝对路径，否则在 WinRM 等 cwd 非仓库根
# 的环境下 os.path.exists() 全部失败 -> 依赖被静默跳过 -> 打出的 exe 缺 gateway/ 等。
# 注意：PyInstaller 控制台脚本调用时 spec 内 __file__ 可能未定义，故从 sys.argv
# 解析 spec 路径（必含 .spec 参数），跨平台稳健，不依赖 cwd。
_spec_arg = next((a for a in sys.argv if a.endswith('.spec')), None)
spec_dir = os.path.dirname(os.path.abspath(_spec_arg)) if _spec_arg else os.getcwd()

# Collect data files — 与 vermes-gui.spec 共用路径
datas = []
for src, dst in [
    ('vermes_cli/web_dist', 'vermes_cli/web_dist'),
    ('vermes_cli/blueprints', 'vermes_cli/blueprints'),
    ('vermes_cli/scholarforge', 'vermes_cli/scholarforge'),
    ('locales', 'locales'),
    ('skills', 'skills'),
    ('plugins', 'plugins'),
    ('tools', 'tools'),
    ('cron', 'cron'),
    ('agent', 'agent'),
    ('acp_adapter', 'acp_adapter'),
    ('acp_registry', 'acp_registry'),
    ('gateway', 'gateway'),
    ('harness', 'harness'),
    ('vermes_constants.py', '.'),
    ('model_tools.py', '.'),
    ('run_agent.py', '.'),
    ('toolsets.py', '.'),
    ('toolset_distributions.py', '.'),
    ('utils.py', '.'),
    ('vermes_bootstrap.py', '.'),
    ('vermes_logging.py', '.'),
    ('vermes_state.py', '.'),
    ('vermes_time.py', '.'),
    ('vermes_cli/__init__.py', 'vermes_cli'),
    ('README.md', '.'),
    ('BRAND.md', '.'),
    ('LICENSE', '.'),
]:
    _abs_src = src if os.path.isabs(src) else os.path.join(spec_dir, src)
    if os.path.exists(_abs_src):
        datas.append((_abs_src, dst))
    else:
        print(f"[Vermes Backend] Skipping missing data path: {src}")

# Vector backend (A-1): bundle sqlite-vec native lib as binary resource.
# Shared-object suffix differs per platform: .dll (Windows), .dylib (macOS), .so (Linux).
try:
    import sqlite_vec as _sv
    _vec_dir = os.path.dirname(_sv.__file__)
    _vec_dylib = None
    for _ext in ('vec0.dll', 'vec0.dylib', 'vec0.so'):
        _cand = os.path.join(_vec_dir, _ext)
        if os.path.exists(_cand):
            _vec_dylib = _cand
            break
    if _vec_dylib:
        datas.append((_vec_dylib, 'sqlite_vec'))
        print(f"[Vermes Backend] Bundled sqlite-vec: {_vec_dylib}")
    else:
        print("[Vermes Backend] sqlite-vec native lib not found — vector backend disabled in package")
except ImportError:
    print("[Vermes Backend] sqlite-vec not installed — vector backend disabled in package")

# Hidden imports — core server only (no pywebview/pyobjc)
hiddenimports = [
    # Windows UTF-8 bootstrap (fix GBK UnicodeEncodeError + subprocess decode)
    'vermes_bootstrap',
    # Core uvicorn
    'uvicorn', 'uvicorn.__main__', 'uvicorn.main', 'uvicorn.config',
    'uvicorn.server', 'uvicorn.workers', 'uvicorn.importer',
    'uvicorn.logging', 'uvicorn._subprocess', 'uvicorn._compat', 'uvicorn._types',
    'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.loops.asyncio', 'uvicorn.loops.uvloop',
    'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.http.httptools_impl',
    'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
    'uvicorn.protocols.websockets.wsproto_impl', 'uvicorn.protocols.websockets.websockets_impl',
    'uvicorn.lifespan', 'uvicorn.lifespan.on', 'uvicorn.lifespan.off',
    'uvicorn.middleware.asgi2', 'uvicorn.middleware.message_logger',
    'uvicorn.middleware.proxy_headers', 'uvicorn.middleware.wsgi',
    'fastapi', 'starlette',

    # HTTP
    'httpx', 'httpx._transports', 'httpx._transports.default',
    'requests', 'urllib3',

    # Async
    'anyio', 'anyio._backends', 'anyio._backends._asyncio',

    # Rich / CLI
    'rich', 'rich.console', 'rich.progress',
    'prompt_toolkit', 'prompt_toolkit.input', 'prompt_toolkit.output',

    # Core deps
    'yaml', 'ruamel.yaml', 'jwt', 'croniter', 'dotenv',
    'psutil', 'tenacity', 'pydantic', 'jinja2',
    'psutil', 'edge_tts',

    # SSL certs for bundled binary
    'certifi',

    # Web server modules
    'vermes_cli.web_server', 'vermes_cli.gateway',
    'vermes_cli.blueprints', 'vermes_cli.blueprints.chat',
    'vermes_cli.blueprints.config', 'vermes_cli.blueprints.dashboard',
    'vermes_cli.blueprints.helpers', 'vermes_cli.blueprints.models',
    'vermes_cli.blueprints.providers', 'vermes_cli.blueprints.quota',
    'vermes_cli.blueprints.session', 'vermes_cli.blueprints.state',
    'vermes_cli.blueprints.wechat', 'vermes_cli.blueprints.cron_jobs',
    'vermes_cli.blueprints.update', 'vermes_cli.blueprints.skills_tools',
    'vermes_cli.blueprints.analytics', 'vermes_cli.blueprints.status',
    'vermes_cli.blueprints.profiles', 'vermes_cli.blueprints.oauth',
    'vermes_cli.update_manager',
    'vermes_cli.shutdown_signal',
    'vermes_cli.win_adapter',
    'gateway', 'gateway.status', 'gateway.config', 'gateway.session_context',
    'gateway.gateway_utils',
    'gateway.slash_handlers', 'gateway.slash_handlers._common',
    'gateway.slash_handlers.capability_handlers',
    'gateway.slash_handlers.config_handlers',
    'gateway.slash_handlers.session_handlers',
    'gateway.slash_handlers.system_handlers',
    # Mixin modules
    'gateway.telegram_topics_mixin',
    'gateway.voice_mixin',
    'gateway.goal_mixin',
    'gateway.kanban_mixin',
    'gateway.slash_commands_mixin',
    'gateway.session_mixin',
    # slash_handlers 子包替代旧 mixin 名
    'gateway.slash_handlers.session_handlers',
    'gateway.slash_handlers.config_handlers',
    'gateway.slash_handlers.system_handlers',
    'gateway.slash_handlers.capability_handlers',
    'gateway.auth_mixin',
    'gateway.config_loader_mixin',
    'gateway.watcher_mixin',
    'run_agent', 'vermes_constants', 'model_tools',
    'agent', 'agent.process_bootstrap', 'agent.iteration_budget',
    'agent.error_classifier', 'agent.prompt_builder',
    'agent.model_metadata', 'agent.prompt_caching',
    'agent.display', 'agent.message_sanitization',
    'agent.tool_dispatch_helpers', 'agent.tool_guardrails',
    'agent.trajectory', 'agent.memory_manager',
    'agent.think_scrubber', 'agent.retry_utils',
    'agent.browser_provider', 'agent.browser_registry',
    'agent.agent_init',
    'agent.copilot_acp_client',
    'agent.continuity_facade',
    'agent.pipeline',
    'agent.metrics',

    # ScholarForge modules
    'vermes_cli.scholarforge', 'vermes_cli.scholarforge.tools',
    'vermes_cli.scholarforge.blueprint', 'vermes_cli.scholarforge.database',
    'vermes_cli.scholarforge.scoring', 'vermes_cli.scholarforge.quality',
    'vermes_cli.scholarforge.plagcheck', 'vermes_cli.scholarforge.rag',
    'vermes_cli.scholarforge.citation_provider', 'vermes_cli.scholarforge.citation_verifier',
    'vermes_cli.scholarforge.validators', 'vermes_cli.scholarforge.style_profile',
    'vermes_cli.scholarforge.storm_adapter', 'vermes_cli.scholarforge.cnki_fetcher',
    'vermes_cli.scholarforge.baidu_scholar_fetcher',
    'vermes_cli.scholarforge.search',
    'vermes_cli.scholarforge.export', 'vermes_cli.scholarforge.export.full',
    'vermes_cli.scholarforge.export.latex', 'vermes_cli.scholarforge.export.pdf_css',
    # Harness layer (B1/B2/B3)
    'harness', 'harness.recoverable', 'harness.stability', 'harness.constraints',

    # Provider backends
    'openai', 'anthropic',

    # Web server
    'multipart', 'starlette', 'starlette.responses',
    'starlette.routing', 'starlette.middleware',
    'starlette.middleware.cors', 'starlette.staticfiles',
    'starlette.websockets',
    'websockets',

    # Utils
    'toolsets',
    'toolset_distributions',
    # Vector backend (A-1): sqlite-vec
    'sqlite_vec',

    # ── 平台渠道依赖（全量打包，即配即用）──
    # 飞书/Lark
    'lark_oapi', 'lark_oapi.api', 'lark_oapi.api.application', 'lark_oapi.api.application.v6',
    'lark_oapi.api.im', 'lark_oapi.api.im.v1', 'lark_oapi.core', 'lark_oapi.core.const',
    'lark_oapi.core.model', 'lark_oapi.event', 'lark_oapi.event.callback',
    'lark_oapi.event.callback.model', 'lark_oapi.event.dispatcher_handler',
    'lark_oapi.ws',
    'qrcode',
    # Telegram
    'telegram', 'telegram.ext', 'telegram.request', 'telegram._utils',
    'telegram.error', 'telegram.constants', 'telegram.helpers',
    # Discord
    'discord', 'discord.ext', 'discord.utils', 'discord.app_commands',
    # Slack
    'slack_bolt', 'slack_bolt.adapter', 'slack_bolt.adapter.asgi', 'slack_sdk',
    # DingTalk
    'dingtalk_stream', 'alibabacloud_dingtalk',
    # WeCom (企业微信)
    'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.backends',
    'cryptography.hazmat.primitives', 'cryptography.hazmat.primitives.ciphers',
    # Weixin (微信公众号)
    # cryptography 已包含
    # Matrix
    'mautrix', 'mautrix.client', 'mautrix.types', 'mautrix.crypto',
    'mautrix.crypto.attachments', 'mautrix.util',
    # QQ Bot (不依赖外部 PyPI 包，适配器自包含)
    # 元宝
    # websockets 已包含
    # Nostr
    'coincurve',
    # Common HTTP
    'httpx', 'aiohttp', 'aiohttp_socks', 'websockets',
    # 音频处理
    'mutagen', 'mutagen.oggopus',
    # 其他
    'pilk', 'nacl', 'nacl.secret', 'markdown', 'brotlicffi', 'Markdown',
]

# Platform specific
if sys.platform == 'win32':
    hiddenimports.extend(['pywin32', 'win32api', 'win32con'])

# ── collect_all for platform channel packages with native/extension deps ──
from PyInstaller.utils.hooks import collect_all, collect_submodules

_extra_datas = []
_extra_binaries = []
_extra_hidden = []

for _pkg in [
    'lark_oapi', 'qrcode',
    # Telegram
    'telegram',
    # Discord
    'discord',
    # Slack
    'slack_bolt', 'slack_sdk',
    # DingTalk
    'dingtalk_stream', 'alibabacloud_dingtalk',
    # WeCom / Weixin
    'cryptography',
    # Matrix
    'mautrix',
    # QQ Bot (自包含，无外部依赖)
    # Nostr
    'coincurve',
    # 音频
    'mutagen',
    # 其他
    'pilk', 'nacl', 'brotlicffi', 'aiohttp_socks',
    'markdown',
]:
    try:
        _d, _b, _h = collect_all(_pkg)
        _extra_datas.extend(_d)
        _extra_binaries.extend(_b)
        _extra_hidden.extend(_h)
        print(f"[Vermes Backend] collect_all({_pkg}) OK")
    except Exception as _e:
        print(f"[Vermes Backend] collect_all({_pkg}) SKIP: {_e}")

hiddenimports.extend(_extra_hidden)

a = Analysis(
    [os.path.join(spec_dir, 'backend_main.py')],
    pathex=[spec_dir],
    binaries=_extra_binaries,
    datas=datas + _extra_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=['runtime_hook_no_ensurepip.py'],
    excludes=[
        'tkinter', 'test', 'tests', 'pytest',
        'debugpy', 'IPython', 'jupyter', 'notebook', 'sphinx',
        'ensurepip', '_ensurepip',
        # ML deps — too large
        'torch', 'torchvision', 'torchaudio', 'torch.distributed',
        'scipy', 'scipy.spatial', 'scipy.special', 'scipy.io',
        'sklearn', 'sklearn.neighbors', 'sklearn.linear_model',
        'pandas', 'pandas.io',
        'datasets', 'diffusers', 'accelerate', 'peft',
        'bitsandbytes', 'xformers', 'sentencepiece',
        'transformers', 'triton',
        'PIL', 'PIL.ImageFilter',
        'fsspec', 'sqlalchemy',
        'dateutil',
        'huggingface_hub',
        'google', 'protobuf',
        # GUI deps — not needed for headless backend
        'webview', 'bottle',
        'pyobjc', 'pyobjc.core', 'pyobjc.framework',
        'AppKit', 'Cocoa', 'Foundation', 'objc',
        'WebKit', 'Quartz', 'UniformTypeIdentifiers',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Filter out test/cache/.venv paths — but preserve sqlite-vec dylib (A-1)
_vec_keep = lambda d: 'vec0' in str(d).replace('\\', '/')
a.datas = [d for d in a.datas if _vec_keep(d[0]) or not any(
    ex in str(d[0]).replace('\\', '/')
    for ex in ['/test', '/tests', '/__pycache__', '/.git', '/.venv', '/node_modules']
)]
a.binaries = [b for b in a.binaries if not any(
    ex in str(b[0]).replace('\\', '/')
    for ex in ['/test', '/__pycache__', '/.git', '/.venv']
)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='vermes',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 后台运行，不弹窗口
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False,
    name='vermes',
)
