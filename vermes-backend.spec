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
    # ── vermes_cli 子目录（全部）──
    ('vermes_cli/web_dist', 'vermes_cli/web_dist'),
    ('vermes_cli/blueprints', 'vermes_cli/blueprints'),
    ('vermes_cli/scholarforge', 'vermes_cli/scholarforge'),
    ('vermes_cli/mfgcad', 'vermes_cli/mfgcad'),
    ('vermes_cli/cadir', 'vermes_cli/cadir'),
    ('vermes_cli/processors', 'vermes_cli/processors'),
    ('vermes_cli/adapters', 'vermes_cli/adapters'),
    ('vermes_cli/modules', 'vermes_cli/modules'),
    ('vermes_cli/proxy', 'vermes_cli/proxy'),
    # ⑭ 请神收尾：a2a 食谱目录（含 registry/*.yaml 数据文件，必须显式 datas，
    # 否则重打 DMG 后 load_all_recipes(RECIPES_DIR, recursive=True) 读空目录，登堂功能全废）
    ('vermes_cli/a2a', 'vermes_cli/a2a'),
    # ③ Bot Mode P1：房间/会话密钥/@mention 解析纯函数 helper 包
    ('vermes_cli/botmode', 'vermes_cli/botmode'),
    # P3-4 D7：domains/*.yaml 数据文件（collect_submodules 只收 .py 进 PYZ，
    # 不收数据文件，必须显式 datas 打包，否则重打 DMG 后 domain_for_brick 读空目录）
    ('vermes_cli/capabilities/domains', 'vermes_cli/capabilities/domains'),
    ('vermes_cli/experts_catalog.json', 'vermes_cli'),
    ('vermes_cli/__init__.py', 'vermes_cli'),
    ('vermes_cli/web_server.py', 'vermes_cli'),
    ('vermes_cli/banner.py', 'vermes_cli'),
    ('vermes_cli/update_manager.py', 'vermes_cli'),
    ('vermes_cli/win_adapter.py', 'vermes_cli'),
    # ── 功能模块目录（全部）──
    ('locales', 'locales'),
    ('skills', 'skills'),
    ('optional-skills', 'optional-skills'),
    ('plugins', 'plugins'),
    ('tools', 'tools'),
    ('cron', 'cron'),
    ('agent', 'agent'),
    ('acp_adapter', 'acp_adapter'),
    ('acp_registry', 'acp_registry'),
    ('gateway', 'gateway'),
    ('harness', 'harness'),
    ('builtin-mcp', 'builtin-mcp'),
    ('scripts', 'scripts'),
    ('assets', 'assets'),
    ('migrations', 'migrations'),
    ('datagen-config-examples', 'datagen-config-examples'),
    # ── 根级 .py 文件 ──
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
    # ── 品牌/文档 ──
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
    'vermes_cli.blueprints.storage', 'vermes_cli.blueprints.studio',
    'vermes_cli.blueprints.gateway_channels',
    'vermes_cli.blueprints._gateway_control',
    'vermes_cli.blueprints.agent_cache',
    'vermes_cli.blueprints.profiles', 'vermes_cli.blueprints.oauth',
    'vermes_cli.tools_config',
    'vermes_cli.plugins',
    'vermes_cli.commands',
    'vermes_cli.config',
    'vermes_cli.update_manager',
    'vermes_cli.shutdown_signal',
    'vermes_cli.win_adapter',
    # ⑭/③ 新增子包：静态 import 已覆盖（chat.py:40-43），补 hiddenimports 双保险
    'vermes_cli.a2a', 'vermes_cli.a2a.recipes', 'vermes_cli.a2a.recipes.loader',
    'vermes_cli.a2a.recipes.schema', 'vermes_cli.a2a.credentials', 'vermes_cli.a2a.transport',
    'vermes_cli.botmode', 'vermes_cli.botmode.core',
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

    # Emergence / self-learning pipeline modules, kept explicit as a belt-and-
    # braces measure for the activation path (Bug 2).
    #
    # NB: the old comment here claimed PyInstaller "never sees" imports written
    # inside functions / try-except blocks.  That is not true and it cost real
    # debugging time — modulegraph walks bytecode, so a nested `import x` is
    # traced just like a top-level one.  Control experiment on the shipped
    # bundle: agent/emergent_insight.py, curator.py, decision_tracker.py and
    # system_prompt.py are all imported only from inside function bodies and
    # are all absent from this list, yet all four are present in _internal/.
    #
    # What modulegraph genuinely cannot follow is a *computed* import —
    # importlib.import_module(some_variable), __import__(name), plugin
    # discovery by directory scan.  Those are the ones that must be declared
    # here.  Adding a statically-written module is harmless but not load-
    # bearing, so don't treat this list as the reason a module works.
    'tools', 'tools.approval',
    'utils',
    'agent.capability_evolver', 'agent.cluster_lifecycle',
    'agent.skill_extractor', 'agent.evolution_manager',
    'agent.memory_recall', 'agent.memory_fabric',
    'agent.memory_reflection', 'agent.hybrid_retriever',
    'agent.raw_event', 'agent.graph_sync',
    'agent.self_model', 'agent.relations',
    'agent.episodic', 'agent.event_time',
    'agent.learning', 'agent.domain_modules',
    'agent.memory_migration', 'agent.session_handoff',

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
    'vermes_cli.scholarforge.benchmark',
    'vermes_cli.scholarforge.validation_coverage',
    'vermes_cli.scholarforge.export', 'vermes_cli.scholarforge.export.full',
    'vermes_cli.scholarforge.export.latex', 'vermes_cli.scholarforge.export.pdf_css',
    # CAD-IR 契约建模模块（P2-4 契约重建走普通 import，须进 PYZ 才可 import）
    'vermes_cli.cadir', 'vermes_cli.cadir.tools',
    'vermes_cli.cadir.cad_ir_contract', 'vermes_cli.cadir.spur_gear',
    'vermes_cli.cadir.stl_verify', 'vermes_cli.cadir.verify_step',
    'vermes_cli.cadir._engine_runner',
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
    # Office preview (pptx) — static import in artifacts.py /preview endpoint
    'pptx',

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
    'pilk', 'nacl', 'nacl.secret', 'markdown', 'brotlicffi',
]

# Platform specific
if sys.platform == 'win32':
    hiddenimports.extend(['pywin32', 'win32api', 'win32con'])

# ── collect_all for platform channel packages with native/extension deps ──
from PyInstaller.utils.hooks import collect_all, collect_submodules

_extra_datas = []
_extra_binaries = []
_extra_hidden = []

# 核心包（缺失=致命，on_error='raise'）：缺了这些产物无法运行或核心功能崩溃
_CORE_PKGS = [
    # LLM SDK（延迟导入，必须 collect_all 才能收全子模块）
    'openai', 'anthropic',
    # WeCom / Weixin 加解密
    'cryptography',
    # 配置读写（atomic_yaml_write 依赖）
    'ruamel.yaml',
    # 表单解析（FastAPI Form() 依赖）
    'multipart',
    # 向量检索（RAG 后端）
    'sqlite_vec',
    # 文档解析（PDF/DOCX 上传解析 + ScholarForge 导出）
    # 注意：代码 import fitz（chat.py:293），fitz 是 pymupdf 的兼容壳包，两者都要收
    'pymupdf', 'fitz', 'docx', 'lxml',
    # Windows 进程树扫描（main.py 依赖，pyproject 核心依赖）
    'psutil',
    # PTY 桥接（dashboard Chat tab，pty_bridge.py 用 try/except 延迟 import，
    # 静态分析收不到 → 打包产物会报 PtyUnavailableError）
    'ptyprocess',
]

# 渠道包（可选依赖残缺不阻断，on_error='warn once'）：第三方渠道 SDK 可能带
# 残缺 adapter（如 lark_oapi.adapter.flask 需要未装的 flask），这些不应让整个构建失败
_CHANNEL_PKGS = [
    'lark_oapi', 'qrcode',
    # Telegram
    'telegram',
    # Discord
    'discord',
    # Slack
    'slack_bolt', 'slack_sdk',
    # DingTalk
    'dingtalk_stream', 'alibabacloud_dingtalk',
    # Matrix
    'mautrix',
    # Nostr
    'coincurve',
    # 音频
    'mutagen',
    # 其他
    'pilk', 'nacl', 'brotlicffi', 'aiohttp_socks',
    'markdown',
    # 数值计算（ScholarForge/AI 工具链依赖）。用 warn once 而非 raise：
    # numpy.f2py.tests 子包 import pytest（构建机未装），核心功能不依赖它，
    # warn once 会正常收集 numpy 核心、仅跳过 tests 子包
    'numpy',
    # 重试逻辑（渠道连接）
    'tenacity',
]

for _pkg in _CORE_PKGS:
    try:
        # 核心包缺失=致命，on_error='raise' 立即抛异常（防静默漏打包）
        _d, _b, _h = collect_all(_pkg, on_error="raise")
        _extra_datas.extend(_d)
        _extra_binaries.extend(_b)
        _extra_hidden.extend(_h)
        print(f"[Vermes Backend] collect_all({_pkg}) OK [core]")
    except Exception as _e:
        import sys
        print(f"[Vermes Backend] ❌ collect_all({_pkg}) FAILED: {_e}", file=sys.stderr)
        print(f"[Vermes Backend] ❌ {_pkg} will be MISSING from the build!", file=sys.stderr)
        raise

for _pkg in _CHANNEL_PKGS:
    try:
        # 渠道包残缺（如 lark_oapi 的可选 adapter）不阻断构建，on_error='warn once'
        _d, _b, _h = collect_all(_pkg, on_error="warn once")
        _extra_datas.extend(_d)
        _extra_binaries.extend(_b)
        _extra_hidden.extend(_h)
        print(f"[Vermes Backend] collect_all({_pkg}) OK [channel]")
    except Exception as _e:
        import sys
        print(f"[Vermes Backend] ⚠️ collect_all({_pkg}) partial: {_e}", file=sys.stderr)

hiddenimports.extend(_extra_hidden)

# ── L2 adapters（vermes_cli.adapters 不是 PyPI 包，collect_all 不适用）──
try:
    _adapters_sub = collect_submodules('vermes_cli.adapters')
    hiddenimports.extend(_adapters_sub)
    print(f"[Vermes Backend] collect_submodules(vermes_cli.adapters) → {_adapters_sub}")
except Exception as _e:
    print(f"[Vermes Backend] collect_submodules(vermes_cli.adapters) SKIP: {_e}")

# 能力网关/清单（P0-2/3/4：chat.py 与 doctor.py 依赖 vermes_cli.capabilities，
# 显式收录避免重打 DMG 后 ImportError）
try:
    _caps_sub = collect_submodules('vermes_cli.capabilities')
    hiddenimports.extend(_caps_sub)
    print(f"[Vermes Backend] collect_submodules(vermes_cli.capabilities) → {_caps_sub}")
except Exception as _e:
    print(f"[Vermes Backend] collect_submodules(vermes_cli.capabilities) SKIP: {_e}")

# 腿B：Vermes 作为 ACP server（acp_adapter import 第三方 acp 包）
try:
    _acp_sub = collect_submodules('acp')
    hiddenimports.extend(_acp_sub)
    print(f"[Vermes Backend] collect_submodules(acp) → {len(_acp_sub)} submodules")
except Exception as _e:
    print(f"[Vermes Backend] collect_submodules(acp) SKIP: {_e}")
try:
    _acpa_sub = collect_submodules('acp_adapter')
    hiddenimports.extend(_acpa_sub)
    print(f"[Vermes Backend] collect_submodules(acp_adapter) → {_acpa_sub}")
except Exception as _e:
    print(f"[Vermes Backend] collect_submodules(acp_adapter) SKIP: {_e}")

# experts_catalog.json 和 adapters 目录已在主 datas 列表中，无需追加

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
        'numba', 'llvmlite',  # JIT 编译器，120MB LLVM DLL，桌面端不需要
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
    for ex in ['/test', '/tests', '/__pycache__', '/.git', '/.venv', '/node_modules', '/pipeline_cache']
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
