# -*- mode: python ; coding: utf-8 -*-
"""
Vermes GUI App (Windowed) - Opens native window, no browser needed.
Usage: pyinstaller vermes-gui.spec
"""

import sys
import os

block_cipher = None

# Collect data files
datas = []
for src, dst in [
    ('vermes_cli/web_dist', 'vermes_cli/web_dist'),
    ('vermes_cli/blueprints', 'vermes_cli/blueprints'),
    ('locales', 'locales'),
    ('skills', 'skills'),
    ('plugins', 'plugins'),
    ('tools', 'tools'),
    ('cron', 'cron'),
    ('agent', 'agent'),
    ('acp_adapter', 'acp_adapter'),
    ('acp_registry', 'acp_registry'),
    ('gateway', 'gateway'),
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
    ('vermes_cli/scholarforge', 'vermes_cli/scholarforge'),
    ('vermes_cli/experts_catalog.json', 'vermes_cli'),
    ('harness', 'harness'),
    ('README.md', '.'),
    ('BRAND.md', '.'),
    ('LICENSE', '.'),
]:
    if os.path.exists(src):
        datas.append((src, dst))

# Vector backend (A-1): bundle sqlite-vec dylib as binary resource
try:
    import sqlite_vec as _sv
    _vec_dylib = os.path.join(os.path.dirname(_sv.__file__), 'vec0.dylib')
    if not os.path.exists(_vec_dylib):
        _vec_dylib = os.path.join(os.path.dirname(_sv.__file__), 'vec0.so')
    if os.path.exists(_vec_dylib):
        datas.append((_vec_dylib, 'sqlite_vec'))
        print(f"[Vermes GUI] Bundled sqlite-vec: {_vec_dylib}")
    else:
        print("[Vermes GUI] sqlite-vec dylib not found — vector backend disabled in DMG")
except ImportError:
    print("[Vermes GUI] sqlite-vec not installed — vector backend disabled in DMG")

# Hidden imports
hiddenimports = [
    # Core uvicorn (all submodules)
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

    # Rich / UI
    'rich', 'rich.console', 'rich.progress',
    'prompt_toolkit', 'prompt_toolkit.input', 'prompt_toolkit.output',

    # pywebview (CRITICAL for GUI)
    'webview', 'webview.window',
    'bottle',
    # pyobjc (macOS native)
    'pyobjc', 'pyobjc.core', 'pyobjc.framework.Cocoa',
    'pyobjc.framework.WebKit',
    'pyobjc.framework.Quartz',
    'pyobjc.framework.UniformTypeIdentifiers',

    # Core deps
    'yaml', 'ruamel.yaml', 'jwt', 'croniter', 'dotenv',
    'psutil', 'tenacity', 'pydantic', 'jinja2',
    'psutil', 'edge_tts',

    # SSL certs (critical for PyInstaller bundle — no system CA bundle)
    'certifi',

    # Missing modules (PyInstaller动态检测不到的)
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
    # Gateway mixins + slash_handlers (split from run.py in 2.3.x)
    'gateway.gateway_utils',
    'gateway.slash_handlers', 'gateway.slash_handlers._common',
    'gateway.slash_handlers.capability_handlers',
    'gateway.slash_handlers.config_handlers',
    'gateway.slash_handlers.session_handlers',
    'gateway.slash_handlers.system_handlers',
    'gateway.telegram_topics_mixin',
    'gateway.voice_mixin',
    'gateway.goal_mixin',
    'gateway.kanban_mixin',
    'gateway.slash_commands_mixin',
    'gateway.session_mixin',
    'gateway.session_commands_mixin',
    'gateway.config_commands_mixin',
    'gateway.system_commands_mixin',
    'gateway.capability_commands_mixin',
    'gateway.auth_mixin',
    'gateway.config_loader_mixin',
    'gateway.watcher_mixin',
    # ScholarForge module (hot-pluggable ecosystem module)
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
    # Harness layer (B1/B2/B3 — imported by tools.browser_tool via recoverable_tool)
    'harness', 'harness.recoverable', 'harness.stability', 'harness.constraints',
    'run_agent', 'vermes_constants', 'model_tools',
    'agent', 'agent.process_bootstrap', 'agent.iteration_budget',
    'agent.error_classifier', 'agent.prompt_builder',
    'agent.model_metadata', 'agent.prompt_caching',
    'agent.display', 'agent.message_sanitization',
    'agent.tool_dispatch_helpers', 'agent.tool_guardrails',
    'agent.trajectory', 'agent.memory_manager',
    'agent.think_scrubber', 'agent.retry_utils',
    'agent.browser_provider', 'agent.browser_registry',
    'agent.continuity_facade',
    'agent.pipeline',

    # Provider backends
    'openai', 'anthropic',

    # Web server
    'multipart', 'starlette', 'starlette.responses',
    'starlette.routing', 'starlette.middleware',
    'starlette.middleware.cors', 'starlette.staticfiles',
    'starlette.websockets',
    'websockets',

    # Utils
    'proxy_tools',
    'toolsets',
    'toolset_distributions',
    # Vector backend (A-1): sqlite-vec
    'sqlite_vec',
]

# Platform specific
if sys.platform == 'win32':
    hiddenimports.extend(['pywin32', 'win32api', 'win32con'])
elif sys.platform == 'darwin':
    hiddenimports.extend([
        'AppKit', 'Cocoa', 'Foundation', 'objc',
        'WebKit', 'Quartz', 'UniformTypeIdentifiers',
    ])

a = Analysis(
    ['vermes_cli/gui_app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter', 'test', 'tests', 'pytest',
        'debugpy', 'IPython', 'jupyter', 'notebook', 'sphinx',
        # ML libraries - too large for desktop app
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
    name='Vermes',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # 弹出 CMD 窗口，实时查看日志
    icon='packaging/vermes.icns' if sys.platform == 'darwin' else 'packaging/vermes.ico',
)

coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True,
    name='Vermes',
)

# macOS .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='Vermes.app',
        icon='packaging/vermes.icns',
        bundle_identifier='cn.vermes.agent',
        info_plist={
            'CFBundleShortVersionString': '2.3.2',
            'CFBundleVersion': '2.3.2',
            'CFBundleDisplayName': 'Vermes',
            'CFBundleName': 'Vermes',
            'CFBundleIdentifier': 'cn.vermes.agent',
            'LSMinimumSystemVersion': '10.15.0',
            'NSHighResolutionCapable': True,
            # Show as regular app (not background agent)
            'LSUIElement': False,
            'NSPrincipalClass': 'NSApplication',
            # App is agent (no dock icon) = False means normal app
            'NSAppleEventsApplicationUsageDescription': 'Vermes needs to control other apps.',
        },
    )
