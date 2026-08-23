"""Central registry for all Vermes-agent tools.

Each tool file calls ``registry.register()`` at module level to declare its
schema, handler, toolset membership, and availability check.  ``model_tools.py``
queries the registry instead of maintaining its own parallel data structures.

Import chain (circular-import safe):
    tools/registry.py  (no imports from model_tools or tool files)
           ^
    tools/*.py  (import from tools.registry at module level)
           ^
    model_tools.py  (imports tools.registry + all tool modules)
           ^
    run_agent.py, cli.py, batch_runner.py, etc.
"""

import ast
import importlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def _is_registry_register_call(node: ast.AST) -> bool:
    """Return True when *node* is a ``registry.register(...)`` call expression."""
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "register"
        and isinstance(func.value, ast.Name)
        and func.value.id == "registry"
    )


def _module_registers_tools(module_path: Path) -> bool:
    """Return True when the module contains a top-level ``registry.register(...)`` call.

    Only inspects module-body statements so that helper modules which happen
    to call ``registry.register()`` inside a function are not picked up.
    """
    try:
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(module_path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False

    return any(_is_registry_register_call(stmt) for stmt in tree.body)


def discover_builtin_tools(tools_dir: Optional[Path] = None) -> List[str]:
    """Import built-in self-registering tool modules and return their module names."""
    tools_path = Path(tools_dir) if tools_dir is not None else Path(__file__).resolve().parent
    module_names = [
        f"tools.{path.stem}"
        for path in sorted(tools_path.glob("*.py"))
        if path.name not in {"__init__.py", "registry.py", "mcp_tool.py"}
        and _module_registers_tools(path)
    ]

    imported: List[str] = []
    for mod_name in module_names:
        try:
            importlib.import_module(mod_name)
            imported.append(mod_name)
        except Exception as e:
            logger.warning("Could not import tool module %s: %s", mod_name, e)
    return imported


# ------------------------------------------------------------------
# Phase 1.1: 非联网工具批量挂 PermissionSpec
# 集中声明，不动各工具文件。按工具实际行为分类。
# ------------------------------------------------------------------

# 纯读 FS + 无网络（安全）
_SAFE_READ_SPECS = {
    'read_file', 'search_files',              # file_tools 读操作
    'memory',                                  # memory_tool
    'session_search',                          # session_search_tool
    'todo',                                    # todo_tool (读+写都在一个入口)
    'kanban_list', 'kanban_show',              # kanban 读
    'process',                                 # process_registry
    'skills_list', 'skill_view',               # skills_tool 读
    'present_files',                           # present_files_tool
    'clarify',                                 # clarify_tool
    'thumbs', 'submit_correction',             # feedback_tool
    'feishu_doc_read',                         # feishu_doc 读
    'feishu_drive_list_comments', 'feishu_drive_list_comment_replies',  # feishu_drive 读
    'ha_get_state', 'ha_list_entities', 'ha_list_services',  # homeassistant 读
}

# 写 FS + 无网络
_SAFE_WRITE_SPECS = {
    'write_file', 'patch',                     # file_tools 写
    'kanban_create', 'kanban_complete', 'kanban_block', 'kanban_unblock',  # kanban 写
    'kanban_comment', 'kanban_link', 'kanban_heartbeat',  # kanban 其他
    'self_modify',                             # self_modify_tool
    'cronjob',                                 # cronjob_tools
    'skill_manage',                            # skill_manager 写
}

# 有网络（需声明 network=True）
_NETWORK_SPECS = {
    'web_search', 'web_extract',               # web_tools
    'image_generate',                          # image_generation_tool
    'video_generate',                          # video_generation_tool
    'literature_search',                       # literature_search_tool
    'x_search',                                # x_search_tool
    'yb_query_group_info', 'yb_query_group_members',          # yuanbao 读
    'yb_search_sticker', 'yb_send_dm', 'yb_send_sticker',     # yuanbao 写
    'send_message',                            # send_message_tool
    'discord', 'discord_admin',                # discord_tool
    'feishu_drive_add_comment', 'feishu_drive_reply_comment', # feishu_drive 写
    'ha_call_service',                         # homeassistant 写
}

# 执行外部命令
_EXEC_SPECS = {
    'terminal',                                # terminal_tool
    'execute_code',                            # code_execution_tool
    'computer_use',                            # computer_use_tool
}

# 浏览器（有网络 + exec）
_BROWSER_SPECS = {
    'browser_navigate', 'browser_click', 'browser_type',
    'browser_scroll', 'browser_press', 'browser_snapshot',
    'browser_back', 'browser_console', 'browser_get_images',
    'browser_vision', 'browser_dialog', 'browser_cdp',
}

# MCP / 委托（需显式授权）
_DELEGATE_SPECS = {
    'delegate_task', 'mixture_of_agents',
}

# TTS / 语音
_TTS_SPECS = {
    'text_to_speech',
}

# 视频分析
_VISION_SPECS = {
    'vision_analyze', 'video_analyze',
}


def apply_permission_specs(registry_instance=None):
    """Phase 1.1: 为已注册工具批量挂 PermissionSpec。
    
    在 discover_builtin_tools() 之后调用。
    未在此处声明的工具保持 permission_spec=None，走兜底默认 ALLOW。
    """
    if registry_instance is None:
        registry_instance = registry
    
    try:
        from vermes_cli.adapters.trust_gate import (
            PermissionSpec, SANDBOX_NONE, SANDBOX_CONTAINER,
        )
    except Exception as exc:
        logger.warning("Cannot apply permission specs: %s", exc)
        return 0
    
    # spec 定义
    specs = {
        # 纯读：低权，无网络
        **{name: PermissionSpec(
            reads_fs=True, writes_fs=False, network=False,
            exec_external=False, sandbox=SANDBOX_NONE,
            requires_explicit_consent=False,
        ) for name in _SAFE_READ_SPECS},
        
        # 写 FS：低权，无网络
        **{name: PermissionSpec(
            reads_fs=True, writes_fs=True, network=False,
            exec_external=False, sandbox=SANDBOX_NONE,
            requires_explicit_consent=False,
        ) for name in _SAFE_WRITE_SPECS},
        
        # 联网工具：声明 sandbox=container（逻辑沙箱）
        # HTTP API 工具通过 httpx 调用，不执行外部命令，"沙箱" = 代码自身约束
        # 浏览器工具依赖 Chrome 自带站点隔离，不自建沙箱
        # 委托/TTS/视觉同理
        # Stage 1 声明层对齐：让 TrustGate 不再空转 DENY（network_no_sandbox），
        # 闸门命中率数据真实化，为 Stage 2 OS 级沙箱铺路
        **{name: PermissionSpec(
            reads_fs=False, writes_fs=False, network=True,
            exec_external=False, sandbox=SANDBOX_CONTAINER,
            requires_explicit_consent=False,
        ) for name in _NETWORK_SPECS},
        
        # 执行外部命令
        **{name: PermissionSpec(
            reads_fs=True, writes_fs=True, network=False,
            exec_external=True, sandbox=SANDBOX_NONE,
            requires_explicit_consent=False,
        ) for name in _EXEC_SPECS},
        
        # 浏览器：有网络 + exec，Chrome 自带沙箱
        **{name: PermissionSpec(
            reads_fs=True, writes_fs=True, network=True,
            exec_external=True, sandbox=SANDBOX_CONTAINER,
            requires_explicit_consent=False,
        ) for name in _BROWSER_SPECS},
        
        # 委托/MCP：需显式授权
        **{name: PermissionSpec(
            reads_fs=False, writes_fs=False, network=True,
            exec_external=True, sandbox=SANDBOX_CONTAINER,
            requires_explicit_consent=True,
        ) for name in _DELEGATE_SPECS},
        
        # TTS：有网络（调 API）
        **{name: PermissionSpec(
            reads_fs=False, writes_fs=False, network=True,
            exec_external=False, sandbox=SANDBOX_CONTAINER,
            requires_explicit_consent=False,
        ) for name in _TTS_SPECS},
        
        # 视觉：有网络
        **{name: PermissionSpec(
            reads_fs=True, writes_fs=False, network=True,
            exec_external=False, sandbox=SANDBOX_CONTAINER,
            requires_explicit_consent=False,
        ) for name in _VISION_SPECS},
    }
    
    applied = 0
    for name, spec in specs.items():
        entry = registry_instance.get_entry(name)
        if entry is not None:
            entry.permission_spec = spec
            applied += 1
    
    logger.info("apply_permission_specs: %d/%d tools got specs", applied, len(specs))
    return applied


class ToolEntry:
    """Metadata for a single registered tool."""

    __slots__ = (
        "name", "toolset", "schema", "handler", "check_fn",
        "requires_env", "is_async", "description", "emoji",
        "max_result_size_chars", "dynamic_schema_overrides",
        "verify_fn", "permission_spec",
    )

    def __init__(self, name, toolset, schema, handler, check_fn,
                 requires_env, is_async, description, emoji,
                 max_result_size_chars=None, dynamic_schema_overrides=None,
                 verify_fn=None, permission_spec=None):
        self.name = name
        self.toolset = toolset
        self.schema = schema
        self.handler = handler
        self.check_fn = check_fn
        self.requires_env = requires_env
        self.is_async = is_async
        self.description = description
        self.emoji = emoji
        self.max_result_size_chars = max_result_size_chars
        # Optional zero-arg callable returning a dict of schema overrides
        # applied at get_definitions() time. Use for fields that depend on
        # runtime config (e.g. delegate_task's description must reflect the
        # user's current delegation.max_concurrent_children / max_spawn_depth
        # so the model isn't told the wrong limits). The callable is invoked
        # on every get_definitions() call; results are merged shallow on top
        # of the base schema before the {"type": "function", ...} wrap.
        self.dynamic_schema_overrides = dynamic_schema_overrides
        # Optional post-execution verifier callable.
        # Signature: verify_fn(function_name: str, function_args: dict,
        #                      function_result: str, is_error: bool) -> tuple[bool, str]
        # Returns (ok, reason). ok=True = outcome verified.
        # ok=False = outcome NOT verified (DB write failed, API returned error, etc).
        # fail-open: if verify_fn raises, treated as (True, "verifier error: ...").
        # None = no verifier for this tool (default, backwards compatible).
        self.verify_fn = verify_fn
        # Optional permission declaration for the A1 dispatch trust gate.
        # None = undeclared → dispatch applies the conservative cli_native
        # default (low-priv ALLOW) so existing 273 tools stay zero-regression.
        self.permission_spec = permission_spec


# ---------------------------------------------------------------------------
# check_fn TTL cache
#
# check_fn callables like tools/terminal_tool.check_terminal_requirements
# probe external state (Docker daemon, Modal SDK install, playwright binary
# availability). For a long-lived CLI or gateway process, calling them on
# every get_definitions() is pure waste — external state changes on human
# timescales. Cache results for ~30 s so env-var flips via ``vermes tools``
# or live credential file changes propagate within a turn or two without
# requiring any explicit invalidation.
# ---------------------------------------------------------------------------

_CHECK_FN_TTL_SECONDS = 30.0
_check_fn_cache: Dict[Callable, tuple[float, bool]] = {}
_check_fn_cache_lock = threading.Lock()


def _check_fn_cached(fn: Callable) -> bool:
    """Return bool(fn()), TTL-cached across calls. Swallows exceptions as False."""
    now = time.monotonic()
    with _check_fn_cache_lock:
        cached = _check_fn_cache.get(fn)
        if cached is not None:
            ts, value = cached
            if now - ts < _CHECK_FN_TTL_SECONDS:
                return value
    try:
        value = bool(fn())
    except Exception:
        value = False
    with _check_fn_cache_lock:
        _check_fn_cache[fn] = (now, value)
    return value


def invalidate_check_fn_cache() -> None:
    """Drop all cached ``check_fn`` results. Call after config changes that
    affect tool availability (e.g. ``vermes tools enable``)."""
    with _check_fn_cache_lock:
        _check_fn_cache.clear()


class ToolRegistry:
    """Singleton registry that collects tool schemas + handlers from tool files."""

    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}
        self._toolset_checks: Dict[str, Callable] = {}
        self._toolset_aliases: Dict[str, str] = {}
        # MCP dynamic refresh can mutate the registry while other threads are
        # reading tool metadata, so keep mutations serialized and readers on
        # stable snapshots.
        self._lock = threading.RLock()
        # Monotonically-increasing generation counter. Bumped on every
        # mutation (register / deregister / register_toolset_alias / MCP
        # refresh). External callers (e.g. get_tool_definitions) can memoize
        # against it: a cache entry keyed on the generation is valid for as
        # long as the generation hasn't changed.
        self._generation: int = 0
        # A1: 主执行点统一信任闸门模式。
        # fail_open（默认，观测期）：记录命中率 + 告警但继续执行，273 工具零回归。
        # fail_closed（VERMES_DISPATCH_GATE_MODE=fail_closed 或运行时设置）：
        #   阻断非 ALLOW 决策（DENY / ASK_USER）。观测期结束、数据达标后切换。
        self.dispatch_gate_mode = os.environ.get("VERMES_DISPATCH_GATE_MODE", "fail_open")
        # Phase 1.4: 未声明工具（plugin / 未来新增工具）的兜底策略。
        # allow（默认，观测期）：替换为 cli_native 低权信任 → 100% ALLOW（零回归）。
        # deny：**不替换** → TrustGate.check(None) 以 rule=undeclared_deny 判 DENY，
        #   让闸门自带的 deny-unless-declared 真正生效（需配合 fail_closed 才阻断）。
        # Phase 1.1 已为 69/69 内置工具挂 spec，故 deny 仅影响未声明的 plugin/未来工具。
        self.undeclared_tool_policy = os.environ.get(
            "VERMES_UNDECLARED_TOOL_POLICY", "allow"
        )

    def set_undeclared_tool_policy(self, policy: str) -> None:
        """Phase 1.4: 运行时切换未声明工具兜底策略。policy ∈ {"allow", "deny"}。

        默认 allow 保留观测期零回归；deny 激活闸门 deny-unless-declared。
        """
        if policy not in ("allow", "deny"):
            raise ValueError(
                f"undeclared_tool_policy must be 'allow' or 'deny', got {policy!r}"
            )
        self.undeclared_tool_policy = policy

    def set_dispatch_gate_mode(self, mode: str) -> None:
        """Phase 3.4: 运行时切换统一信任闸门模式。mode ∈ {"fail_open", "fail_closed", "observe"}。

        - fail_open（默认，观测期）：记录 NON-ALLOW 命中并告警，但继续执行，保证 273 工具零回归。
        - observe（观测增强态）：与 fail_open 行为一致（记录 + 告警 + 执行），
          但语义上明确"仅观测、不隐含放行基线"——用于观测期数据收集/告警调优，
          与 fail_open 的可观测性区分由调用方日志/指标层体现。
        - fail_closed：阻断非 ALLOW 决策（DENY / ASK_USER）。
        三者均通过本方法或 env VERMES_DISPATCH_GATE_MODE 切换，构成统一三模式开关。
        """
        if mode not in ("fail_open", "fail_closed", "observe"):
            raise ValueError(
                f"dispatch_gate_mode must be one of "
                f"'fail_open'/'fail_closed'/'observe', got {mode!r}"
            )
        self.dispatch_gate_mode = mode

    def _snapshot_state(self) -> tuple[List[ToolEntry], Dict[str, Callable]]:
        """Return a coherent snapshot of registry entries and toolset checks."""
        with self._lock:
            return list(self._tools.values()), dict(self._toolset_checks)

    def _snapshot_entries(self) -> List[ToolEntry]:
        """Return a stable snapshot of registered tool entries."""
        return self._snapshot_state()[0]

    def _snapshot_toolset_checks(self) -> Dict[str, Callable]:
        """Return a stable snapshot of toolset availability checks."""
        return self._snapshot_state()[1]

    def _evaluate_toolset_check(self, toolset: str, check: Callable | None) -> bool:
        """Run a toolset check, treating missing or failing checks as unavailable/available."""
        if not check:
            return True
        try:
            return bool(check())
        except Exception:
            logger.debug("Toolset %s check raised; marking unavailable", toolset)
            return False

    def get_entry(self, name: str) -> Optional[ToolEntry]:
        """Return a registered tool entry by name, or None."""
        with self._lock:
            return self._tools.get(name)

    def _suggest_module_for_tool(self, tool_name: str) -> Optional[str]:
        """P3: 工具未注册时查 catalog，返回安装提示。"""
        try:
            from agent.module_catalog import (
                load_catalog,
                catalog_modules,
                find_module_for_tool,
                is_module_installed,
            )
            # P7 远程优先：远程官方 catalog → bundled → 用户缓存 → 空
            mods = catalog_modules(load_catalog())
            mod = find_module_for_tool(tool_name, mods)
            if mod is None:
                return None
            installed = is_module_installed(mod.name)
            if installed:
                # 已安装但工具未注册——可能是加载问题
                return (
                    f"工具 {tool_name} 属于已安装模块 {mod.name}，"
                    f"但工具未注册。尝试重启 Vermes 或运行 reload_module_tools('{mod.name}')。"
                )
            return (
                f"工具 {tool_name} 属于可插拔模块 {mod.name}（{mod.display_name}）。"
                f"安装命令: vermes module install --release {mod.name}"
            )
        except Exception:
            return None

    def _ensure_module_for_tool(self, tool_name: str) -> tuple[bool, str]:
        """P3/P1 联动（Phase 3.1 主演练路径）：工具未注册但 catalog 中有模块提供时，
        自动安装该模块代码包（sha256 校验 + 安全解压）→ 热重载工具 → 使工具可被分发。

        返回 (ok, message)。失败原因（网络/校验/加载）经 message 透出，绝不抛异常；
        调用方据此决定是否回退到「Unknown tool + hint」文案。

        安全约束（沿用 module_catalog 既有原语，不新增信任面）：
        - download_file 内含 sha256 供应链校验，不匹配则拒绝安装；
        - safe_extract 逐条拒绝绝对路径 / '..' 穿越 / 越界符号链接；
        - 仅当 manifest 解析 + reload_module_tools 成功，工具才进入 registry。
        """
        try:
            from agent.module_catalog import (
                load_catalog,
                catalog_modules,
                find_module_for_tool,
                is_module_installed,
                ensure_module_ready_sync,
            )
            mods = catalog_modules(load_catalog())
            mod = find_module_for_tool(tool_name, mods)
            if mod is None:
                return False, f"catalog 中无模块提供工具 {tool_name!r}"
            if is_module_installed(mod.name):
                # 已安装但未注册——可能是加载失败，尝试热重载而非重装
                try:
                    from agent.module_loader import reload_module_tools
                    res = reload_module_tools(mod.name)
                    if res.get("ok"):
                        return True, f"重新加载已安装模块 {mod.name}"
                    return False, f"模块 {mod.name} 已安装但热重载失败：{res.get('error')}"
                except Exception as _e:  # noqa: BLE001
                    return False, f"模块 {mod.name} 已安装但热重载异常：{_e}"
            # 未安装 → 按需下载安装 + 热重载（sync 包装，dispatch 是非 async 上下文）
            ok, msg = ensure_module_ready_sync(mod.name, auto_install=True)
            if not ok:
                return False, msg or f"安装模块 {mod.name} 失败"
            return True, f"已安装模块 {mod.name}"
        except Exception as _e:  # noqa: BLE001
            logger.warning("ensure_module_for_tool failed for %s: %s", tool_name, _e)
            return False, f"自动安装模块失败：{_e}"

    def get_registered_toolset_names(self) -> List[str]:
        """Return sorted unique toolset names present in the registry."""
        return sorted({entry.toolset for entry in self._snapshot_entries()})

    def get_tool_names_for_toolset(self, toolset: str) -> List[str]:
        """Return sorted tool names registered under a given toolset."""
        return sorted(
            entry.name for entry in self._snapshot_entries()
            if entry.toolset == toolset
        )

    def register_toolset_alias(self, alias: str, toolset: str) -> None:
        """Register an explicit alias for a canonical toolset name."""
        with self._lock:
            existing = self._toolset_aliases.get(alias)
            if existing and existing != toolset:
                logger.warning(
                    "Toolset alias collision: '%s' (%s) overwritten by %s",
                    alias, existing, toolset,
                )
            self._toolset_aliases[alias] = toolset
            self._generation += 1

    def get_registered_toolset_aliases(self) -> Dict[str, str]:
        """Return a snapshot of ``{alias: canonical_toolset}`` mappings."""
        with self._lock:
            return dict(self._toolset_aliases)

    def get_toolset_alias_target(self, alias: str) -> Optional[str]:
        """Return the canonical toolset name for an alias, or None."""
        with self._lock:
            return self._toolset_aliases.get(alias)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        check_fn: Callable = None,
        requires_env: list = None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "",
        max_result_size_chars: int | float | None = None,
        dynamic_schema_overrides: Callable = None,
        verify_fn: Callable = None,
        permission_spec=None,
        override: bool = False,
    ):
        """Register a tool.  Called at module-import time by each tool file.

        ``override=True`` is an explicit opt-in for plugins that intend to
        replace an existing built-in tool implementation (e.g. swap the
        default browser tool for a headed-Chrome CDP backend). Without it,
        registrations that would shadow an existing tool from a different
        toolset are rejected to prevent accidental overwrites.
        """
        with self._lock:
            existing = self._tools.get(name)
            if existing and existing.toolset != toolset:
                # Allow MCP-to-MCP overwrites (legitimate: server refresh,
                # or two MCP servers with overlapping tool names).
                both_mcp = (
                    existing.toolset.startswith("mcp-")
                    and toolset.startswith("mcp-")
                )
                if both_mcp:
                    logger.debug(
                        "Tool '%s': MCP toolset '%s' overwriting MCP toolset '%s'",
                        name, toolset, existing.toolset,
                    )
                elif override:
                    # Explicit plugin opt-in: replace the existing tool.
                    # Logged at INFO so the override is auditable in agent.log.
                    logger.info(
                        "Tool '%s': toolset '%s' overriding existing toolset '%s' "
                        "(override=True opt-in)",
                        name, toolset, existing.toolset,
                    )
                else:
                    # Reject shadowing — prevent plugins/MCP from overwriting
                    # built-in tools or vice versa.
                    logger.error(
                        "Tool registration REJECTED: '%s' (toolset '%s') would "
                        "shadow existing tool from toolset '%s'. Pass "
                        "override=True to register() if the replacement is "
                        "intentional, or deregister the existing tool first.",
                        name, toolset, existing.toolset,
                    )
                    return
            self._tools[name] = ToolEntry(
                name=name,
                toolset=toolset,
                schema=schema,
                handler=handler,
                check_fn=check_fn,
                requires_env=requires_env or [],
                is_async=is_async,
                description=description or schema.get("description", ""),
                emoji=emoji,
                max_result_size_chars=max_result_size_chars,
                dynamic_schema_overrides=dynamic_schema_overrides,
                verify_fn=verify_fn,
                permission_spec=permission_spec,
            )
            if check_fn and toolset not in self._toolset_checks:
                self._toolset_checks[toolset] = check_fn
            self._generation += 1

    def deregister(self, name: str) -> None:
        """Remove a tool from the registry.

        Also cleans up the toolset check if no other tools remain in the
        same toolset.  Used by MCP dynamic tool discovery to nuke-and-repave
        when a server sends ``notifications/tools/list_changed``.
        """
        with self._lock:
            entry = self._tools.pop(name, None)
            if entry is None:
                return
            # Drop the toolset check and aliases if this was the last tool in
            # that toolset.
            toolset_still_exists = any(
                e.toolset == entry.toolset for e in self._tools.values()
            )
            if not toolset_still_exists:
                self._toolset_checks.pop(entry.toolset, None)
                self._toolset_aliases = {
                    alias: target
                    for alias, target in self._toolset_aliases.items()
                    if target != entry.toolset
                }
            self._generation += 1
        logger.debug("Deregistered tool: %s", name)

    # ------------------------------------------------------------------
    # Schema retrieval
    # ------------------------------------------------------------------

    def get_definitions(self, tool_names: Set[str], quiet: bool = False) -> List[dict]:
        """Return OpenAI-format tool schemas for the requested tool names.

        Only tools whose ``check_fn()`` returns True (or have no check_fn)
        are included. ``check_fn()`` results are cached for ~30 s via
        :func:`_check_fn_cached` to amortize repeat probes (check_terminal_
        requirements probes modal/docker, browser checks probe playwright,
        etc.); TTL chosen so env-var changes (``vermes tools enable foo``)
        still take effect in near-real-time without forcing a full cache
        flush on every call.
        """
        result = []
        # Per-call cache on top of the 30 s TTL — handles repeat probes of the
        # same check_fn within one definitions pass without re-reading the
        # TTL clock.
        check_results: Dict[Callable, bool] = {}
        entries_by_name = {entry.name: entry for entry in self._snapshot_entries()}
        for name in sorted(tool_names):
            entry = entries_by_name.get(name)
            if not entry:
                continue
            if entry.check_fn:
                if entry.check_fn not in check_results:
                    check_results[entry.check_fn] = _check_fn_cached(entry.check_fn)
                if not check_results[entry.check_fn]:
                    if not quiet:
                        logger.debug("Tool %s unavailable (check failed)", name)
                    continue
            # Ensure schema always has a "name" field — use entry.name as fallback
            schema_with_name = {**entry.schema, "name": entry.name}
            # Apply runtime-dynamic overrides (e.g. delegate_task description
            # depends on current delegation.max_concurrent_children /
            # max_spawn_depth). Caller side (model_tools.get_tool_definitions)
            # already keys its memo on config.yaml mtime + size, so changes
            # to delegation.* in config invalidate the cache automatically.
            if entry.dynamic_schema_overrides is not None:
                try:
                    overrides = entry.dynamic_schema_overrides()
                    if isinstance(overrides, dict):
                        schema_with_name.update(overrides)
                except Exception as exc:
                    logger.warning(
                        "dynamic_schema_overrides for tool %s raised %s; "
                        "using static schema",
                        name, exc,
                    )
            result.append({"type": "function", "function": schema_with_name})
        return result

    # ------------------------------------------------------------------
    # A1: 统一信任闸门评估（提级到主执行点）
    # ------------------------------------------------------------------

    # 放行常量。**故意用字面量**而非 `from ...trust_gate import ALLOW`：
    # dispatch 是热路径且必须 fail-open —— 若在 dispatch 里 import 这个符号，
    # trust_gate 不可用时会在 _evaluate_dispatch_gate 的降级逻辑生效**之前**
    # 抛 ImportError，直接击穿 fail-open 把 273 个工具全打死。
    # 漂移防护：tests/test_registry_dispatch_gate.py 断言本字面量 == trust_gate.ALLOW。
    _GATE_ALLOW = "allow"

    def _evaluate_dispatch_gate(self, entry, kwargs):
        """对单个工具调用 TrustGate.check，返回 (decision, reason, rule)。

        fail-open 安全网：闸门模块不可用时降级放行（仍记录 gate_unavailable），
        绝不因闸门故障阻断 273 工具。
        """
        try:
            from vermes_cli.adapters.trust_gate import (
                TrustGate, PermissionSpec, SANDBOX_NONE, SANDBOX_CONTAINER,
            )
        except Exception as exc:  # pragma: no cover - 仅依赖缺失场景
            logger.warning("TrustGate 模块不可用，dispatch 降级放行: %s", exc)
            return "allow", "trust_gate unavailable (fail-open)", "gate_unavailable"
        ctx = kwargs.get("ctx")
        spec = entry.permission_spec
        if spec is None:
            # Phase 1.4: 兜底策略可配。
            if self.undeclared_tool_policy == "deny":
                # 保持 None → TrustGate.check(None) 以 rule=undeclared_deny 判 DENY。
                # 不在此替换，否则会盖掉闸门自带的 deny-unless-declared。
                pass
            else:
                # allow（默认）：未声明工具走 cli_native 低权信任
                # （观测期基线 = 100% ALLOW；与 §15.3 一致）。
                spec = PermissionSpec(
                    reads_fs=True, writes_fs=True, network=False,
                    exec_external=True, sandbox=SANDBOX_NONE,
                    requires_explicit_consent=False,
                )
        result = TrustGate.check(spec, ctx)
        return result.decision, result.reason, result.rule

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def dispatch(self, name: str, args: dict, **kwargs) -> str:
        """Execute a tool handler by name.

        * Async handlers are bridged automatically via ``_run_async()``.
        * All exceptions are caught and returned as ``{"error": "..."}``
          for consistent error format.
        * P3: Unknown tool → 查 catalog 提示安装可插拔模块。
        """
        entry = self.get_entry(name)
        if not entry:
            # P3: 查 catalog 是否有模块提供此工具
            hint = self._suggest_module_for_tool(name)
            # Phase 3.1: 若开启自动安装（默认关，fail-open 零回归），先尝试一次性
            # 安装缺失模块并热重载，再重试分发；失败则回退到上面的 hint 文案。
            if hint and os.environ.get("VERMES_AUTO_INSTALL_MODULE", "").strip().lower() in {
                "1", "true", "yes", "on"
            }:
                ok, msg = self._ensure_module_for_tool(name)
                if ok:
                    entry = self.get_entry(name)
                    if entry:
                        # 模块已就绪，继续走下方正常分发（含信任闸门）
                        logger.info("auto-installed module for tool %s, retrying dispatch", name)
                    else:
                        return json.dumps({
                            "error": f"Unknown tool: {name}",
                            "hint": hint,
                            "auto_install": msg,
                        }, ensure_ascii=False)
                else:
                    return json.dumps({
                        "error": f"Unknown tool: {name}",
                        "hint": hint,
                        "auto_install": msg,
                    }, ensure_ascii=False)
            if hint:
                return json.dumps({
                    "error": f"Unknown tool: {name}",
                    "hint": hint,
                }, ensure_ascii=False)
            return json.dumps({"error": f"Unknown tool: {name}"})

        # ---- A1: 统一信任闸门（提级到主执行点，对齐 Codex exec_policy）----
        # fail-open（默认，观测期）：记录命中率 + 告警，但继续执行，保证 273 工具零回归。
        # fail-closed：阻断非 ALLOW 决策（DENY / ASK_USER）。
        gate_decision, gate_reason, gate_rule = self._evaluate_dispatch_gate(entry, kwargs)
        if gate_decision != self._GATE_ALLOW:
            if self.dispatch_gate_mode == "fail_closed":
                # 阻断非 ALLOW 决策（Phase 3.4：三模式之一）。
                logger.warning(
                    "Dispatch gate BLOCKED (fail-closed): tool=%s decision=%s "
                    "rule=%s reason=%s", name, gate_decision, gate_rule, gate_reason,
                )
                return json.dumps({
                    "error": "permission denied by dispatch gate",
                    "tool": name,
                    "gate": gate_decision,
                    "reason": gate_reason,
                }, ensure_ascii=False)
            # fail_open 与 observe 均记录 + 告警 + 执行（Phase 3.4 统一语义）
            logger.warning(
                "Dispatch gate NON-ALLOW (fail-open/observe, executing anyway): "
                "tool=%s decision=%s rule=%s reason=%s",
                name, gate_decision, gate_rule, gate_reason,
            )
        try:
            # ── A4 otel: span around the actual handler execution (fail-open) ──
            from agent.observability import span

            with span(f"tool.dispatch:{name}", attributes={
                "tool": name,
                "gate": gate_decision,
                "async": entry.is_async,
            }) as _sp:
                if entry.is_async:
                    from model_tools import _run_async
                    _result = _run_async(entry.handler(args, **kwargs))
                else:
                    _result = entry.handler(args, **kwargs)
                if _sp is not None and hasattr(_sp, "set_attribute"):
                    try:
                        _sp.set_attribute("result_len", len(_result) if isinstance(_result, str) else -1)
                    except Exception:
                        pass
                return _result
        except Exception as e:
            logger.exception("Tool %s dispatch error: %s", name, e)
            # Route through the sanitizer so framing tokens / CDATA / fences
            # in exception strings don't reach the model as structural noise.
            # See model_tools._sanitize_tool_error for rationale.
            raw = f"Tool execution failed: {type(e).__name__}: {e}"
            try:
                from model_tools import _sanitize_tool_error
                sanitized = _sanitize_tool_error(raw)
            except Exception:
                sanitized = raw  # defensive: never let the sanitizer block error propagation
            return json.dumps({"error": sanitized})

    # ------------------------------------------------------------------
    # Dispatch precedence (single source of truth)
    # ------------------------------------------------------------------

    def resolve_dispatch_mechanism(self, agent: Any, tool_name: str) -> str:
        """Return which mechanism owns *tool_name*.

        Single source of truth for tool dispatch precedence. Mirrors the
        agent loop (``agent/tool_executor.py``) and the plugin API
        (``vermes_cli/plugins.py``). Keeping the order here prevents the two
        entry points from drifting apart (the fork-risk called out in the
        system audit).

        Precedence:
            1. ``"context_engine"`` — engine-specific tools (``lcm_grep`` ...)
            2. ``"memory"``         — memory-provider tools (``hindsight_retain`` ...)
            3. ``"registry"``       — everything else (central tool registry)

        ``agent`` is the parent agent (or ``None`` in gateway mode). The
        ``is True`` checks guard against ``MagicMock`` in tests where
        ``has_tool`` returns a truthy non-bool.
        """
        if agent is not None:
            ce_names = getattr(agent, "_context_engine_tool_names", None)
            if ce_names and tool_name in ce_names:
                return "context_engine"
            mem_mgr = getattr(agent, "_memory_manager", None)
            if mem_mgr is not None and mem_mgr.has_tool(tool_name) is True:
                return "memory"
        return "registry"

    # ------------------------------------------------------------------
    # Query helpers  (replace redundant dicts in model_tools.py)
    # ------------------------------------------------------------------

    def get_max_result_size(self, name: str, default: int | float | None = None) -> int | float:
        """Return per-tool max result size, or *default* (or global default)."""
        entry = self.get_entry(name)
        if entry and entry.max_result_size_chars is not None:
            return entry.max_result_size_chars
        if default is not None:
            return default
        from tools.budget_config import DEFAULT_RESULT_SIZE_CHARS
        return DEFAULT_RESULT_SIZE_CHARS

    def get_all_tool_names(self) -> List[str]:
        """Return sorted list of all registered tool names."""
        return sorted(entry.name for entry in self._snapshot_entries())

    def get_schema(self, name: str) -> Optional[dict]:
        """Return a tool's raw schema dict, bypassing check_fn filtering.

        Useful for token estimation and introspection where availability
        doesn't matter — only the schema content does.
        """
        entry = self.get_entry(name)
        return entry.schema if entry else None

    def get_toolset_for_tool(self, name: str) -> Optional[str]:
        """Return the toolset a tool belongs to, or None."""
        entry = self.get_entry(name)
        return entry.toolset if entry else None

    def get_emoji(self, name: str, default: str = "⚡") -> str:
        """Return the emoji for a tool, or *default* if unset."""
        entry = self.get_entry(name)
        return (entry.emoji if entry and entry.emoji else default)

    def get_tool_to_toolset_map(self) -> Dict[str, str]:
        """Return ``{tool_name: toolset_name}`` for every registered tool."""
        return {entry.name: entry.toolset for entry in self._snapshot_entries()}

    def is_toolset_available(self, toolset: str) -> bool:
        """Check if a toolset's requirements are met.

        Returns False (rather than crashing) when the check function raises
        an unexpected exception (e.g. network error, missing import, bad config).
        """
        with self._lock:
            check = self._toolset_checks.get(toolset)
        return self._evaluate_toolset_check(toolset, check)

    def check_toolset_requirements(self) -> Dict[str, bool]:
        """Return ``{toolset: available_bool}`` for every toolset."""
        entries, toolset_checks = self._snapshot_state()
        toolsets = sorted({entry.toolset for entry in entries})
        return {
            toolset: self._evaluate_toolset_check(toolset, toolset_checks.get(toolset))
            for toolset in toolsets
        }

    def get_available_toolsets(self) -> Dict[str, dict]:
        """Return toolset metadata for UI display."""
        toolsets: Dict[str, dict] = {}
        entries, toolset_checks = self._snapshot_state()
        for entry in entries:
            ts = entry.toolset
            if ts not in toolsets:
                toolsets[ts] = {
                    "available": self._evaluate_toolset_check(
                        ts, toolset_checks.get(ts)
                    ),
                    "tools": [],
                    "description": "",
                    "requirements": [],
                }
            toolsets[ts]["tools"].append(entry.name)
            if entry.requires_env:
                for env in entry.requires_env:
                    if env not in toolsets[ts]["requirements"]:
                        toolsets[ts]["requirements"].append(env)
        return toolsets

    def get_toolset_requirements(self) -> Dict[str, dict]:
        """Build a TOOLSET_REQUIREMENTS-compatible dict for backward compat."""
        result: Dict[str, dict] = {}
        entries, toolset_checks = self._snapshot_state()
        for entry in entries:
            ts = entry.toolset
            if ts not in result:
                result[ts] = {
                    "name": ts,
                    "env_vars": [],
                    "check_fn": toolset_checks.get(ts),
                    "setup_url": None,
                    "tools": [],
                }
            if entry.name not in result[ts]["tools"]:
                result[ts]["tools"].append(entry.name)
            for env in entry.requires_env:
                if env not in result[ts]["env_vars"]:
                    result[ts]["env_vars"].append(env)
        return result

    def check_tool_availability(self, quiet: bool = False):
        """Return (available_toolsets, unavailable_info) like the old function."""
        available = []
        unavailable = []
        seen = set()
        entries, toolset_checks = self._snapshot_state()
        for entry in entries:
            ts = entry.toolset
            if ts in seen:
                continue
            seen.add(ts)
            if self._evaluate_toolset_check(ts, toolset_checks.get(ts)):
                available.append(ts)
            else:
                unavailable.append({
                    "name": ts,
                    "env_vars": entry.requires_env,
                    "tools": [e.name for e in entries if e.toolset == ts],
                })
        return available, unavailable


# Module-level singleton
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Helpers for tool response serialization
# ---------------------------------------------------------------------------
# Every tool handler must return a JSON string.  These helpers eliminate the
# boilerplate ``json.dumps({"error": msg}, ensure_ascii=False)`` that appears
# hundreds of times across tool files.
#
# Usage:
#   from tools.registry import registry, tool_error, tool_result
#
#   return tool_error("something went wrong")
#   return tool_error("not found", code=404)
#   return tool_result(success=True, data=payload)
#   return tool_result(items)            # pass a dict directly


def tool_error(message, **extra) -> str:
    """Return a JSON error string for tool handlers.

    >>> tool_error("file not found")
    '{"error": "file not found"}'
    >>> tool_error("bad input", success=False)
    '{"error": "bad input", "success": false}'
    """
    result = {"error": str(message)}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(data=None, **kwargs) -> str:
    """Return a JSON result string for tool handlers.

    Accepts a dict positional arg *or* keyword arguments (not both):

    >>> tool_result(success=True, count=42)
    '{"success": true, "count": 42}'
    >>> tool_result({"key": "value"})
    '{"key": "value"}'
    """
    if data is not None:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(kwargs, ensure_ascii=False)
