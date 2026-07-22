# -*- mode: python ; coding: utf-8 -*-
"""
Vermes Backend (Headless) — Electron 使用的后端专用构建脚本。
不含 pywebview / pyobjc / GUI 依赖，体积更小。
Usage: pyinstaller vermes-backend.spec
"""
import sys
import os

block_cipher = None

# Collect data files — 与 vermes-gui.spec 共用路径
datas = []
for src, dst in [
    ('hermes_cli/web_dist', 'hermes_cli/web_dist'),
    ('hermes_cli/blueprints', 'hermes_cli/blueprints'),
    ('hermes_cli/scholarforge', 'hermes_cli/scholarforge'),
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
    ('hermes_constants.py', '.'),
    ('model_tools.py', '.'),
    ('run_agent.py', '.'),
    ('toolsets.py', '.'),
    ('toolset_distributions.py', '.'),
    ('utils.py', '.'),
    ('hermes_bootstrap.py', '.'),
    ('hermes_logging.py', '.'),
    ('hermes_state.py', '.'),
    ('hermes_time.py', '.'),
    ('hermes_cli/__init__.py', 'hermes_cli'),
    ('README.md', '.'),
    ('BRAND.md', '.'),
    ('LICENSE', '.'),
]:
    if os.path.exists(src):
        datas.append((src, dst))
    else:
        print(f"[Vermes Backend] Skipping missing data path: {src}")

# Vector backend (A-1): bundle sqlite-vec dylib as binary resource
try:
    import sqlite_vec as _sv
    _vec_dylib = os.path.join(os.path.dirname(_sv.__file__), 'vec0.dylib')
    if not os.path.exists(_vec_dylib):
        _vec_dylib = os.path.join(os.path.dirname(_sv.__file__), 'vec0.so')
    if os.path.exists(_vec_dylib):
        datas.append((_vec_dylib, 'sqlite_vec'))
        print(f"[Vermes Backend] Bundled sqlite-vec: {_vec_dylib}")
    else:
        print("[Vermes Backend] sqlite-vec dylib not found — vector backend disabled in DMG")
except ImportError:
    print("[Vermes Backend] sqlite-vec not installed — vector backend disabled in DMG")

# Hidden imports — core server only (no pywebview/pyobjc)
hiddenimports = [
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
    'hermes_cli.web_server', 'hermes_cli.gateway',
    'hermes_cli.blueprints', 'hermes_cli.blueprints.chat',
    'hermes_cli.blueprints.config', 'hermes_cli.blueprints.dashboard',
    'hermes_cli.blueprints.helpers', 'hermes_cli.blueprints.models',
    'hermes_cli.blueprints.providers', 'hermes_cli.blueprints.quota',
    'hermes_cli.blueprints.session', 'hermes_cli.blueprints.state',
    'hermes_cli.blueprints.wechat', 'hermes_cli.blueprints.cron_jobs',
    'hermes_cli.blueprints.update', 'hermes_cli.blueprints.skills_tools',
    'hermes_cli.blueprints.analytics', 'hermes_cli.blueprints.status',
    'hermes_cli.blueprints.profiles', 'hermes_cli.blueprints.oauth',
    'hermes_cli.update_manager',
    'hermes_cli.shutdown_signal',
    'hermes_cli.win_adapter',
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
    'run_agent', 'hermes_constants', 'model_tools',
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

    # ScholarForge modules
    'hermes_cli.scholarforge', 'hermes_cli.scholarforge.tools',
    'hermes_cli.scholarforge.blueprint', 'hermes_cli.scholarforge.database',
    'hermes_cli.scholarforge.scoring', 'hermes_cli.scholarforge.quality',
    'hermes_cli.scholarforge.plagcheck', 'hermes_cli.scholarforge.rag',
    'hermes_cli.scholarforge.citation_provider', 'hermes_cli.scholarforge.citation_verifier',
    'hermes_cli.scholarforge.validators', 'hermes_cli.scholarforge.style_profile',
    'hermes_cli.scholarforge.storm_adapter', 'hermes_cli.scholarforge.cnki_fetcher',
    'hermes_cli.scholarforge.baidu_scholar_fetcher',
    'hermes_cli.scholarforge.search',
    'hermes_cli.scholarforge.export', 'hermes_cli.scholarforge.export.full',
    'hermes_cli.scholarforge.export.latex', 'hermes_cli.scholarforge.export.pdf_css',
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
]

# Platform specific
if sys.platform == 'win32':
    hiddenimports.extend(['pywin32', 'win32api', 'win32con'])

a = Analysis(
    ['backend_main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
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
    name='vermes-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 后台运行，不弹窗口
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False,
    name='vermes-backend',
)
