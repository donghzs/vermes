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
    ('hermes_cli/web_dist', 'hermes_cli/web_dist'),
    ('hermes_cli/blueprints', 'hermes_cli/blueprints'),
    ('locales', 'locales'),
    ('skills', 'skills'),
    ('plugins', 'plugins'),
    ('tools', 'tools'),
    ('cron', 'cron'),
    ('agent', 'agent'),
    ('acp_adapter', 'acp_adapter'),
    ('acp_registry', 'acp_registry'),
    ('gateway', 'gateway'),
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
    ('hermes_cli/scholarforge', 'hermes_cli/scholarforge'),
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
    else:
        print(f"[Vermes] Skipping missing data path: {src}")

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
    # Harness layer (B1/B2/B3 — imported by tools.browser_tool via recoverable_tool)
    'harness', 'harness.recoverable', 'harness.stability', 'harness.constraints',
    'run_agent', 'hermes_constants', 'model_tools',
    'agent', 'agent.process_bootstrap', 'agent.iteration_budget',
    'agent.error_classifier', 'agent.prompt_builder',
    'agent.model_metadata', 'agent.prompt_caching',
    'agent.display', 'agent.message_sanitization',
    'agent.tool_dispatch_helpers', 'agent.tool_guardrails',
    'agent.trajectory', 'agent.memory_manager',
    'agent.think_scrubber', 'agent.retry_utils',
    'agent.browser_provider', 'agent.browser_registry',

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
    ['hermes_cli/gui_app.py'],
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

a.datas = [d for d in a.datas if not any(
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
