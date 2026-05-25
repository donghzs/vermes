# -*- mode: python ; coding: utf-8 -*-
"""
Vermes PyInstaller spec for Windows/macOS

Usage:
  pyinstaller vermes.spec
  # Output: dist/vermes/ (folder) or dist/vermes.exe (onefile)

For release builds, use --clean to rebuild from scratch:
  pyinstaller --clean vermes.spec
"""

import sys
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect all data files from hermes_cli (web_dist, locales, etc.)
datas = [
    ('hermes_cli/web_dist', 'hermes_cli/web_dist'),
    ('locales', 'locales'),
    ('skills', 'skills'),
    ('plugins', 'plugins'),
    ('tools', 'tools'),
    ('README.md', '.'),
    ('BRAND.md', '.'),
    ('LICENSE', '.'),
]

# Hidden imports that PyInstaller might miss
hiddenimports = [
    # Core
    'uvicorn.logging',
    'uvicorn.loops',
    'uvicorn.loops.auto',
    'uvicorn.protocols',
    'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan',
    'uvicorn.lifespan.on',

    # HTTP clients
    'httpx',
    'httpx._transports',
    'httpx._transports.default',
    'httpx_socks',

    # Async
    'anyio',
    'anyio._backends',
    'anyio._backends._asyncio',

    # Rich
    'rich',
    'rich.console',
    'rich.progress',

    # Prompt toolkit
    'prompt_toolkit',
    'prompt_toolkit.input',
    'prompt_toolkit.output',

    # YAML
    'yaml',
    'ruamel.yaml',

    # Crypto
    'jwt',

    # Cron
    'croniter',

    # Provider backends (lazy loaded)
    'openai',
    'anthropic',
    'google.generativeai',

    # TTS
    'edge_tts',

    # Tools
    'psutil',
    'dotenv',
    'tenacity',
    'pydantic',
    'jinja2',

    # Web server
    'multipart',
    'starlette',
    'starlette.responses',
    'starlette.routing',
    'starlette.middleware',
    'starlette.middleware.cors',
    'starlette.staticfiles',
    'starlette.websockets',

    # CLI dependencies (required by main.py)
    'prompt_toolkit',
    'prompt_toolkit.shortcuts',
    'prompt_toolkit.layout',
    'prompt_toolkit.widgets',
    'prompt_toolkit.application',
    'prompt_toolkit.key_binding',
    'prompt_toolkit.styles',
    'prompt_toolkit.filters',
    'prompt_toolkit.formatted_text',
    'prompt_toolkit.history',
    'prompt_toolkit.completion',
]

# Platform-specific hidden imports
if sys.platform == 'win32':
    hiddenimports.extend([
        'pywin32',
        'win32api',
        'win32con',
        'win32process',
        'pywintypes',
    ])
elif sys.platform == 'darwin':
    hiddenimports.extend([
        # macOS-specific if needed
    ])

# Main entry point
a = Analysis(
    ['hermes_cli/main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude unused heavy packages
        'tkinter',
        'unittest',
        'test',
        'tests',
        'pytest',
        'debugpy',
        'IPython',
        'jupyter',
        'notebook',
        'sphinx',
        'docutils',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Filter out unnecessary files
def should_include(p):
    """Filter out tests, __pycache__, and other unnecessary files"""
    p_str = str(p).replace('\\', '/')
    exclude_patterns = [
        '/test', '/tests', '/__pycache__', '/.git', '/.venv',
        '/node_modules', '.pyc', '.pyo',
    ]
    return not any(ex in p_str for ex in exclude_patterns)

a.datas = [d for d in a.datas if should_include(d[0])]
a.binaries = [b for b in a.binaries if should_include(b[0])]

# Include missing C extension modules on Windows
if sys.platform == 'win32':
    import os
    python_dlls = os.path.join(sys.base_prefix, 'DLLs')
    for pyd_file in ['_socket.pyd']:
        pyd_path = os.path.join(python_dlls, pyd_file)
        if os.path.exists(pyd_path):
            a.binaries.append((pyd_file, pyd_path, 'BINARY'))
            print(f"  Added binary: {pyd_file}")

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Create folder-based bundle (faster startup, easier debugging)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='vermes',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Keep console for CLI output
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='packaging/vermes.ico' if sys.platform == 'win32' else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='vermes',
)

# macOS: Create .app bundle
if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='Vermes.app',
        icon='packaging/vermes.icns',
        bundle_identifier='cn.vermes.agent',
        info_plist={
            'CFBundleShortVersionString': '1.1.0',
            'CFBundleVersion': '1.1.0',
            'CFBundleDisplayName': 'Vermes',
            'CFBundleName': 'Vermes',
            'CFBundleIdentifier': 'cn.vermes.agent',
            'NSHighResolutionCapable': True,
            'LSMinimumSystemVersion': '10.15.0',
            'NSPrincipalClass': 'NSApplication',
        },
    )
