"""通用「wrapper 型 CLI」运行环境解析（vendor-agnostic）。

背景（神魔堂 · 异构 agent 联邦的硬前提）
----------------------------------------
桌面版 AI agent 常把真实 CLI 包成一层 **shell wrapper**，其运行时依赖
（Node 解释器路径、真实入口脚本路径…）由**宿主 App 主进程**通过环境变量注入。
于是：宿主 App 自己 spawn → 有 env → 正常；第三方进程（Vermes）直接 spawn
该 wrapper → 缺 env → **秒退 exit 1**，表现为「登堂成功但一调用就挂」。

这类 CLI 不止一家（各厂 Electron 壳都在这么做），因此本模块**不认厂商**——
不写 `if vendor == ...`，只认**模式**：

    1. 判定命令是否为 wrapper 脚本（shebang + 体量小）
    2. 从 wrapper 源码解析它「要求、但当前环境缺失」的环境变量名
    3. 按**变量名语义** + **就近的 App 运行时配置**解析候选值
    4. 用 wrapper 自己的存在性校验（`-f` / `-x`）确认候选
    5. 解不出就保持原样 —— 绝不覆盖已有 env、绝不阻塞启动（fail-safe）

只加这一层，任意厂商的 wrapper CLI 都能被第三方进程正确拉起。

用法::

    from vermes_cli.adapters.cli_env import resolve_cli_env
    env = resolve_cli_env(command, env=os.environ.copy())
    subprocess.Popen([command, *args], env=env, ...)
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger("vermes.cli_env")

# wrapper 判定：有 shebang 的文本脚本，且体量小（真实入口在别处）
_WRAPPER_MAX_BYTES = 256 * 1024
_SHEBANG_RE = re.compile(r"^#!\s*(?:/usr/bin/env\s+)?(?:ba|z|da|k)?sh\b", re.M)

# 「要求该 env 非空」的常见写法（bash / sh / node / python wrapper 通吃）
_REQUIRED_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"\[\s*-z\s+\"?\$\{(\w+)(?::-[^}]*)?\}\"?\s*\]"),   # [ -z "${VAR:-}" ]
    re.compile(r"\[\s*-z\s+\"?\$(\w+)\"?\s*\]"),                    # [ -z "$VAR" ]
    re.compile(r"\$\{(\w+):\?[^}]*\}"),                             # ${VAR:?msg}
    re.compile(r"process\.env\.(\w+)\s*(?:\?\?|==|\|\|)"),          # process.env.VAR
    re.compile(r"os\.environ\[[\"'](\w+)[\"']\]"),                  # os.environ["VAR"]
    re.compile(r"os\.environ\.get\([\"'](\w+)[\"']"),               # os.environ.get("VAR"
)

# 变量名语义 token：用于按「形状」猜该填什么
_NODE_TOKENS = {"node", "nodejs", "binary", "bin", "runtime", "exec"}
_ENTRY_TOKENS = {"mjs", "cjs", "js", "entry", "script", "main", "cli", "index"}

_RESOLVE_CACHE: Dict[tuple, Dict[str, str]] = {}
_CACHE_LIMIT = 64


def _is_wrapper(path: Path) -> bool:
    """命令是否是可解析的 shell/node wrapper 脚本。"""
    try:
        if not path.is_file() or path.stat().st_size > _WRAPPER_MAX_BYTES:
            return False
        head = path.read_bytes()[:512]
        if b"\x00" in head:  # 二进制可执行文件，不是 wrapper
            return False
        text = head.decode("utf-8", errors="ignore")
        return bool(_SHEBANG_RE.search(text))
    except OSError:
        return False


def required_env_vars(path: Path) -> List[str]:
    """从 wrapper 源码解析「要求非空」的环境变量名（保持出现顺序、去重）。"""
    try:
        text = path.read_text("utf-8", errors="ignore")
    except OSError:
        return []
    found: List[str] = []
    seen = set()
    for pat in _REQUIRED_PATTERNS:
        for m in pat.finditer(text):
            name = m.group(1)
            if name and name not in seen:
                seen.add(name)
                found.append(name)
    return found


def _camel_split(s: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", s)


def _tokens(s: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _camel_split(s).lower()) if t]


_DIR_TOKENS = {"dir", "home", "root", "folder", "workspace", "statedir"}


def _usable_path(value: Any, tokens: Optional[set] = None) -> Optional[str]:
    """值是可用的路径才采纳。

    - 目录：仅当变量名像目录类（dir/home/root/…）时接受；
    - 文件：配置文件（.json/.yaml/脚本）只要求存在；可执行文件额外要求可执行位。
    """
    if not isinstance(value, str) or not value:
        return None
    if not (value.startswith("/") or value.startswith("~") or value.startswith("./")):
        return None
    p = Path(os.path.expanduser(value))
    tokens = tokens or set()
    try:
        if not p.exists():
            return None
        if p.is_dir():
            return str(p) if (tokens & _DIR_TOKENS) else None
        if not p.is_file():
            return None
        if (p.suffix in {".mjs", ".cjs", ".js", ".sh", ".json", ".yaml", ".yml", ".py"}
                or p.name == "node"):
            return str(p)
        return str(p) if os.access(str(p), os.X_OK) else None
    except OSError:
        return None


def _collect_runtime_jsons(cmd_path: Path) -> List[Path]:
    """就近收集可能的「App 运行时配置」JSON（不含 node_modules）。

    覆盖两类真实布局：① wrapper 同目录/上层的配置；② 家目录下隐藏配置目录
    （各厂都爱放 `~/.<app>/<app>.json` 或 `~/.config/<app>/*.json`）。
    """
    out: List[Path] = []
    app_tokens = _app_tokens(cmd_path)

    def _subdirs(base: Path) -> List[Path]:
        """逐项容错：任一子项无权限（如 ~/.ssh）不能拖垮整次扫描。"""
        try:
            with os.scandir(base) as it:
                entries = list(it)
        except OSError:
            return []
        res: List[Path] = []
        for e in entries:
            try:
                if e.is_dir():
                    res.append(Path(e.path))
            except OSError:
                continue
        return res

    def _jsons(d: Path, cap: int) -> List[Path]:
        try:
            return [f for f in sorted(d.glob("*.json"))[:cap]
                    if "node_modules" not in f.parts]
        except OSError:
            return []

    # ① wrapper 自身向上 4 层
    for parent in list(cmd_path.parents)[:4]:
        out.extend(_jsons(parent, 20))
    home = Path.home()
    # ② ~/.<app>/*.json 与 ~/.config/<app>/*.json
    #    只扫**与本 CLI 同名**的配置目录：既快（不必遍历 ~/.npm、~/.cache 等
    #    巨型目录），也避免把别家 App 的同名 key（nodeBinary…）错配给本 CLI。
    for base in (home / ".config", home):
        if not base.is_dir():
            continue
        for d in sorted(_subdirs(base)):
            name = d.name[1:] if d.name.startswith(".") else d.name
            if not d.name.startswith(".") and base == home:
                continue
            if app_tokens and not (set(_tokens(name)) & app_tokens):
                continue
            out.extend(_jsons(d, 10))
    return out[:60]


def _app_tokens(cmd_path: Path) -> set:
    """从 CLI 路径里提取「所属 App」的语义 token（vendor-agnostic）。

    例如 ``~/Library/Application Support/QClaw/openclaw/config/bin/openclaw``
    → {qclaw, openclaw}：据此定向到 ``~/.qclaw``、``~/.config/qclaw`` 等配置
    目录，既不写死厂商，也不会误配别家的运行时。
    """
    noise = {
        "library", "application", "support", "contents", "resources", "config",
        "bin", "users", "user", "home", "opt", "usr", "local", "app",
    }
    toks: set = set()
    for part in cmd_path.parts:
        p = part.lower()
        if p.endswith(".app"):
            p = p[:-4]
        for t in _tokens(p):
            if len(t) > 2 and t not in noise:
                toks.add(t)
    return toks


def _from_runtime_json(var: str, cmd_path: Path) -> Optional[str]:
    """变量名 token ⊇ 配置 key token 即视为命中（如 NODE_BINARY ↔ nodeBinary）。"""
    var_tokens = set(_tokens(var))
    best: Optional[tuple] = None  # (score, path)

    def _walk(obj: Any, depth: int = 0) -> None:
        nonlocal best
        if depth > 4 or not isinstance(obj, dict):
            return
        for key, val in obj.items():
            if isinstance(val, (dict, list)):
                _walk(val, depth + 1)
                continue
            key_tokens = _tokens(str(key))
            if not key_tokens or not set(key_tokens) <= var_tokens:
                continue
            usable = _usable_path(val, var_tokens)
            if usable is None:
                continue
            score = len(key_tokens)
            if best is None or score > best[0]:
                best = (score, usable)

    for cfg in _collect_runtime_jsons(cmd_path):
        try:
            _walk(json.loads(cfg.read_text("utf-8", errors="ignore")))
        except (OSError, ValueError):
            continue
    return best[1] if best else None


def _app_roots(cmd_path: Path) -> List[Path]:
    return list(cmd_path.parents)[:6]


def _by_name_shape(var: str, cmd_path: Path) -> Optional[str]:
    """按变量名语义猜：要 Node 解释器？还是要真实入口脚本？"""
    tokens = set(_tokens(var))
    cli_name = cmd_path.stem

    if tokens & _NODE_TOKENS:
        for root in _app_roots(cmd_path):
            for cand in (
                root / "node",
                root / "bin" / "node",
                root / "Contents" / "Resources" / "node" / "node",
                root / "node_modules" / ".bin" / "node",
            ):
                if _usable_path(str(cand), tokens):
                    return str(cand)
        for name in ("node", "nodejs"):
            found = shutil.which(name)
            if found and _usable_path(found, tokens):
                return found
        for cand in ("/opt/homebrew/bin/node", "/usr/local/bin/node"):
            if _usable_path(cand, tokens):
                return cand
        return None

    if tokens & _ENTRY_TOKENS:
        # 优先「与 CLI 同名」的入口（openclaw → openclaw.mjs）
        for root in _app_roots(cmd_path):
            for mod_dir in (root / "node_modules", root):
                if not mod_dir.is_dir():
                    continue
                direct = mod_dir / cli_name / f"{cli_name}.mjs"
                if _usable_path(str(direct), tokens):
                    return str(direct)
            try:
                hits = sorted((root / "node_modules").glob(f"*/{cli_name}.mjs"))[:1]
            except OSError:
                hits = []
            if hits and _usable_path(str(hits[0]), tokens):
                return str(hits[0])
        return None

    return None


_ENTRY_EXEC_RE = re.compile(r"^\s*exec\b.*$", re.M)
_SCRIPT_ENV_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]{3,})"),
    re.compile(r"process\.env\[[\"']([A-Z][A-Z0-9_]{3,})[\"']\]"),
    re.compile(r"[\"']([A-Z][A-Z0-9_]{3,})[\"']\s+in\s+process\.env"),
    re.compile(r"os\.environ(?:\.get\(|\[)[\"']([A-Z][A-Z0-9_]{3,})[\"']"),
)


def _entry_script(wrapper: Path, env: Dict[str, str]) -> Optional[Path]:
    """从 wrapper 的 ``exec`` 行反推真实入口脚本（如 ``*.mjs``）。

    入口脚本里往往还藏着另一类**可选但关键**的 env：宿主 App 用它指定
    profile / 配置目录 / 状态目录（有默认值 → wrapper 不会报错 → 静默走错
    profile，表现为「能起来但连不上自己的网关」）。只补齐必填 env 不够。
    """
    try:
        text = wrapper.read_text("utf-8", errors="ignore")
    except OSError:
        return None
    for line in _ENTRY_EXEC_RE.findall(text):
        for token in re.findall(r"\$\{(\w+)\}|\$(\w+)", line):
            name = token[0] or token[1]
            val = env.get(name) or ""
            if val.endswith((".mjs", ".cjs", ".js")) and Path(val).is_file():
                return Path(val)
    return None


def _env_vars_in_script(path: Path) -> List[str]:
    """扫描入口脚本读取的环境变量名（上限 2MB，避免巨型 bundle 拖慢）。"""
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return []
        text = path.read_text("utf-8", errors="ignore")
    except OSError:
        return []
    found: List[str] = []
    seen = set()
    for pat in _SCRIPT_ENV_PATTERNS:
        for m in pat.finditer(text):
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                found.append(name)
    return found[:200]


def _looks_like_runtime_var(vars_: Sequence[str]) -> bool:
    """变量名是否像「运行时路径依赖」（决定要不要为解析失败告警）。"""
    return any(set(_tokens(v)) & (_NODE_TOKENS | _ENTRY_TOKENS) for v in vars_)


def _resolve_var(var: str, cmd_path: Path) -> Optional[str]:
    return _from_runtime_json(var, cmd_path) or _by_name_shape(var, cmd_path)


def resolve_cli_env(
    command: str,
    env: Optional[Dict[str, str]] = None,
    *,
    extra_dirs: Optional[Iterable[str]] = None,
) -> Dict[str, str]:
    """为 wrapper 型 CLI 补齐缺失的运行时 env；非 wrapper 则原样返回。

    永不抛异常。已存在的变量一律不覆盖（尊重宿主/用户显式设置）。
    """
    base: Dict[str, str] = dict(os.environ if env is None else env)
    try:
        raw = (command or "").strip()
        if not raw:
            return base
        path: Optional[Path] = None
        if os.path.sep in raw:
            p = Path(os.path.expanduser(raw))
            if p.is_file():
                path = p
        if path is None:
            found = shutil.which(raw)
            if not found:
                for d in extra_dirs or ():
                    cand = Path(d) / raw
                    if cand.is_file():
                        found = str(cand)
                        break
            if found:
                path = Path(found)
        if path is None or not _is_wrapper(path):
            return base

        missing = [v for v in required_env_vars(path) if not base.get(v)]
        if not missing:
            return base

        try:
            mtime = int(path.stat().st_mtime)
        except OSError:
            mtime = 0
        cache_key = (str(path), mtime, tuple(missing))
        if cache_key in _RESOLVE_CACHE:
            base.update(_RESOLVE_CACHE[cache_key])
            return base

        injected: Dict[str, str] = {}
        for var in missing:
            value = _resolve_var(var, path)
            if value:
                injected[var] = value

        # 第二层：入口脚本里的「可选但关键」env —— profile / 配置目录 / 状态目录。
        # 这类变量有默认值，wrapper 不会报错，但会静默使用错误的默认 profile
        # （表现为「能起来，却连不上宿主 App 自己的网关/配置」）。
        entry = _entry_script(path, {**base, **injected})
        if entry is not None:
            app_tokens = _app_tokens(path)
            for var in _env_vars_in_script(entry):
                if base.get(var) or injected.get(var):
                    continue
                if app_tokens and not (set(_tokens(var)) & app_tokens):
                    continue  # 与本 App 无关的变量不碰
                value = _from_runtime_json(var, path)
                if value:
                    injected[var] = value

        if len(_RESOLVE_CACHE) >= _CACHE_LIMIT:
            _RESOLVE_CACHE.clear()
        _RESOLVE_CACHE[cache_key] = dict(injected)  # 负结果同样缓存，避免重复扫盘
        if injected:
            base.update(injected)
            logger.info(
                "[cli_env] 为 %s 补齐 wrapper 运行时 env: %s",
                path.name, ", ".join(sorted(injected)),
            )
        elif _looks_like_runtime_var(missing):
            # 只在「像是运行时路径依赖」时才告警——普通 shell 脚本常引用各类
            # 无关环境变量，逐条告警会淹没日志（噪声 ≠ 诊断）。
            logger.warning(
                "[cli_env] %s 是 wrapper 型 CLI，缺 env %s 但无法解析；"
                "该 agent 将沿用宿主 App 之外的裸调用（可能秒退）",
                path.name, ", ".join(missing),
            )
        else:
            logger.debug("[cli_env] %s 缺 env %s，非运行时路径依赖，跳过",
                         path.name, ", ".join(missing))
        return base
    except Exception as exc:  # noqa: BLE001 — 环境解析绝不能阻断启动
        logger.debug("[cli_env] resolve_cli_env 跳过（%s）", exc)
        return base
