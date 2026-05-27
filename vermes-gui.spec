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
    ('locales', 'locales'),
    ('skills', 'skills'),
    ('plugins', 'plugins'),
    ('tools', 'tools'),
    ('README.md', '.'),
    ('BRAND.md', '.'),
    ('LICENSE', '.'),
]:
    if os.path.exists(src):
        datas.append((src, dst))
    else:
        print(f"[Vermes] Skipping missing data path: {src}")

# Hidden imports
hiddenimports = [
    # Core
    'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
    'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan', 'uvicorn.lifespan.on',

    # HTTP
    'httpx', 'httpx._transports', 'httpx._transports.default',

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
    'gateway', 'gateway.status', 'gateway.config',
    'run_agent',

    # Provider backends
    'openai', 'anthropic',

    # Web server
    'multipart', 'starlette', 'starlette.responses',
    'starlette.routing', 'starlette.middleware',
    'starlette.middleware.cors', 'starlette.staticfiles',
    'starlette.websockets',

    # Utils
    'proxy_tools',
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
    console=False,  # <-- Windowed mode (no terminal)
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
            'CFBundleShortVersionString': '2.0.1',
            'CFBundleVersion': '2.0.1',
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
