"""MemoryManager — orchestrates memory providers for the agent.

Single integration point in run_agent.py. Replaces scattered per-backend
code with one manager that delegates to registered providers.

Only ONE external plugin provider is allowed at a time — attempting to
register a second external provider is rejected with a warning.  This
prevents tool schema bloat and conflicting memory backends.

Usage in run_agent.py:
    self._memory_manager = MemoryManager()
    # Only ONE of these:
    self._memory_manager.add_provider(plugin_provider)

    # System prompt
    prompt_parts.append(self._memory_manager.build_system_prompt())

    # Pre-turn
    context = self._memory_manager.prefetch_all(user_message)

    # Post-turn
    self._memory_manager.sync_all(user_msg, assistant_response)
    self._memory_manager.queue_prefetch_all(user_msg)
"""

from __future__ import annotations

import logging
import re
import inspect
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from agent.memory_fabric import L4_REFERENCE
from tools.registry import tool_error
from harness.metrics import get_metrics

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context fencing helpers
# ---------------------------------------------------------------------------

_FENCE_TAG_RE = re.compile(r'</?\s*memory-context\s*>', re.IGNORECASE)
_INTERNAL_CONTEXT_RE = re.compile(
    r'<\s*memory-context\s*>[\s\S]*?</\s*memory-context\s*>',
    re.IGNORECASE,
)
_INTERNAL_NOTE_RE = re.compile(
    r'\[System note:\s*The following is recalled memory context,\s*NOT new user input\.\s*Treat as (?:informational background data|authoritative reference data[^\]]*)\.\]\s*',
    re.IGNORECASE,
)


def sanitize_context(text: str) -> str:
    """Strip fence tags, injected context blocks, and system notes from provider output."""
    text = _INTERNAL_CONTEXT_RE.sub('', text)
    text = _INTERNAL_NOTE_RE.sub('', text)
    text = _FENCE_TAG_RE.sub('', text)
    return text


class StreamingContextScrubber:
    """Stateful scrubber for streaming text that may contain split memory-context spans.

    The one-shot ``sanitize_context`` regex cannot survive chunk boundaries:
    a ``<memory-context>`` opened in one delta and closed in a later delta
    leaks its payload to the UI because the non-greedy block regex needs
    both tags in one string.  This scrubber runs a small state machine
    across deltas, holding back partial-tag tails and discarding
    everything inside a span (including the system-note line).

    Usage::

        scrubber = StreamingContextScrubber()
        for delta in stream:
            visible = scrubber.feed(delta)
            if visible:
                emit(visible)
        trailing = scrubber.flush()  # at end of stream
        if trailing:
            emit(trailing)

    The scrubber is re-entrant per agent instance.  Callers building new
    top-level responses (new turn) should create a fresh scrubber or call
    ``reset()``.
    """

    _OPEN_TAG = "<memory-context>"
    _CLOSE_TAG = "</memory-context>"

    def __init__(self) -> None:
        self._in_span: bool = False
        self._buf: str = ""
        self._at_block_boundary: bool = True

    def reset(self) -> None:
        self._in_span = False
        self._buf = ""
        self._at_block_boundary = True

    def feed(self, text: str) -> str:
        """Return the visible portion of ``text`` after scrubbing.

        Any trailing fragment that could be the start of an open/close tag
        is held back in the internal buffer and surfaced on the next
        ``feed()`` call or discarded/emitted by ``flush()``.
        """
        if not text:
            return ""
        buf = self._buf + text
        self._buf = ""
        out: list[str] = []

        while buf:
            if self._in_span:
                idx = buf.lower().find(self._CLOSE_TAG)
                if idx == -1:
                    # Hold back a potential partial close tag; drop the rest
                    held = self._max_partial_suffix(buf, self._CLOSE_TAG)
                    self._buf = buf[-held:] if held else ""
                    return "".join(out)
                # Found close — skip span content + tag, continue
                buf = buf[idx + len(self._CLOSE_TAG):]
                self._in_span = False
            else:
                idx = self._find_boundary_open_tag(buf)
                if idx == -1:
                    # No open tag — hold back a potential partial open tag
                    held = (
                        self._max_pending_open_suffix(buf)
                        or self._max_partial_suffix(buf, self._OPEN_TAG)
                    )
                    if held:
                        self._append_visible(out, buf[:-held])
                        self._buf = buf[-held:]
                    else:
                        self._append_visible(out, buf)
                    return "".join(out)
                # Emit text before the tag, enter span
                if idx > 0:
                    self._append_visible(out, buf[:idx])
                buf = buf[idx + len(self._OPEN_TAG):]
                self._in_span = True

        return "".join(out)

    def flush(self) -> str:
        """Emit any held-back buffer at end-of-stream.

        If we're still inside an unterminated span the remaining content is
        discarded (safer: leaking partial memory context is worse than a
        truncated answer).  Otherwise the held-back partial-tag tail is
        emitted verbatim (it turned out not to be a real tag).
        """
        if self._in_span:
            self._buf = ""
            self._in_span = False
            return ""
        tail = self._buf
        self._buf = ""
        return tail

    @staticmethod
    def _max_partial_suffix(buf: str, tag: str) -> int:
        """Return the length of the longest buf-suffix that is a tag-prefix.

        Case-insensitive.  Returns 0 if no suffix could start the tag.
        """
        tag_lower = tag.lower()
        buf_lower = buf.lower()
        max_check = min(len(buf_lower), len(tag_lower) - 1)
        for i in range(max_check, 0, -1):
            if tag_lower.startswith(buf_lower[-i:]):
                return i
        return 0

    def _find_boundary_open_tag(self, buf: str) -> int:
        """Find an opening fence only when it starts a block-like span."""
        buf_lower = buf.lower()
        search_start = 0
        while True:
            idx = buf_lower.find(self._OPEN_TAG, search_start)
            if idx == -1:
                return -1
            if self._is_block_boundary(buf, idx) and self._has_block_opener_suffix(buf, idx):
                return idx
            search_start = idx + 1

    def _max_pending_open_suffix(self, buf: str) -> int:
        """Hold a complete boundary tag until the following char confirms it."""
        if not buf.lower().endswith(self._OPEN_TAG):
            return 0
        idx = len(buf) - len(self._OPEN_TAG)
        if not self._is_block_boundary(buf, idx):
            return 0
        return len(self._OPEN_TAG)

    def _has_block_opener_suffix(self, buf: str, idx: int) -> bool:
        after_idx = idx + len(self._OPEN_TAG)
        if after_idx >= len(buf):
            return False
        return buf[after_idx] in "\r\n"

    def _is_block_boundary(self, buf: str, idx: int) -> bool:
        if idx == 0:
            return self._at_block_boundary
        preceding = buf[:idx]
        last_newline = preceding.rfind("\n")
        if last_newline == -1:
            return self._at_block_boundary and preceding.strip() == ""
        return preceding[last_newline + 1:].strip() == ""

    def _append_visible(self, out: list[str], text: str) -> None:
        if not text:
            return
        out.append(text)
        self._update_block_boundary(text)

    def _update_block_boundary(self, text: str) -> None:
        last_newline = text.rfind("\n")
        if last_newline != -1:
            self._at_block_boundary = text[last_newline + 1:].strip() == ""
        else:
            self._at_block_boundary = self._at_block_boundary and text.strip() == ""


def build_memory_context_block(raw_context: str) -> str:
    """Wrap prefetched memory in a fenced block with system note."""
    if not raw_context or not raw_context.strip():
        return ""
    clean = sanitize_context(raw_context)
    if clean != raw_context:
        logger.warning("memory provider returned pre-wrapped context; stripped")
    return (
        "<memory-context>\n"
        "[System note: The following is recalled memory context, "
        "NOT new user input. Treat as authoritative reference data — "
        "this is the agent's persistent memory and should inform all responses.]\n\n"
        f"{clean}\n"
        "</memory-context>"
    )


class MemoryManager:
    """Orchestrates the built-in provider plus at most one external provider.

    The builtin provider is always first. Only one non-builtin (external)
    provider is allowed.  Failures in one provider never block the other.
    """

    def __init__(self) -> None:
        self._providers: List[MemoryProvider] = []
        self._tool_to_provider: Dict[str, MemoryProvider] = {}
        self._has_external: bool = False  # True once a non-builtin provider is added

    # -- Registration --------------------------------------------------------

    def add_provider(self, provider: MemoryProvider) -> None:
        """Register a memory provider.

        Built-in providers (name ``"builtin"`` or ``"rag"``) are always
        accepted. Exactly **one** non-builtin external provider is allowed —
        a second external attempt is rejected with a warning. Treating the
        RAG provider as builtin is what enables federated search (Slice 3):
        the built-in RAG store coexists with the user's chosen external KB.
        """
        # The built-in RAG provider ("rag") is internal, not an "external"
        # plugin, so it must not consume the single-external-provider slot.
        is_builtin = provider.name in ("builtin", "rag")

        if not is_builtin:
            if self._has_external:
                existing = next(
                    (p.name for p in self._providers if p.name != "builtin"), "unknown"
                )
                logger.warning(
                    "Rejected memory provider '%s' — external provider '%s' is "
                    "already registered. Only one external memory provider is "
                    "allowed at a time. Configure which one via memory.provider "
                    "in config.yaml.",
                    provider.name, existing,
                )
                return
            self._has_external = True

        self._providers.append(provider)

        # Index tool names → provider for routing
        for schema in provider.get_tool_schemas():
            tool_name = schema.get("name", "")
            if tool_name and tool_name not in self._tool_to_provider:
                self._tool_to_provider[tool_name] = provider
            elif tool_name in self._tool_to_provider:
                logger.warning(
                    "Memory tool name conflict: '%s' already registered by %s, "
                    "ignoring from %s",
                    tool_name,
                    self._tool_to_provider[tool_name].name,
                    provider.name,
                )

        logger.info(
            "Memory provider '%s' registered (%d tools)",
            provider.name,
            len(provider.get_tool_schemas()),
        )

    @property
    def providers(self) -> List[MemoryProvider]:
        """All registered providers in order."""
        return list(self._providers)

    def get_provider(self, name: str) -> Optional[MemoryProvider]:
        """Get a provider by name, or None if not registered."""
        for p in self._providers:
            if p.name == name:
                return p
        return None

    # -- System prompt -------------------------------------------------------

    def build_system_prompt(self) -> str:
        """Collect system prompt blocks from all providers.

        Returns combined text, or empty string if no providers contribute.
        Each non-empty block is labeled with the provider name.
        """
        blocks = []
        for provider in self._providers:
            try:
                block = provider.system_prompt_block()
                if block and block.strip():
                    blocks.append(block)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' system_prompt_block() failed: %s",
                    provider.name, e,
                )
        return "\n\n".join(blocks)

    # -- Prefetch / recall ---------------------------------------------------

    def prefetch_all(self, query: str, *, session_id: str = "") -> str:
        """Collect prefetch context from all providers.

        Returns merged context text labeled by provider. Empty providers
        are skipped. Failures in one provider don't block others.
        """
        parts = []
        for provider in self._providers:
            try:
                result = provider.prefetch(query, session_id=session_id)
                if result and result.strip():
                    parts.append(result)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' prefetch failed (non-fatal): %s",
                    provider.name, e,
                )
        return "\n\n".join(parts)

    def queue_prefetch_all(self, query: str, *, session_id: str = "") -> None:
        """Queue background prefetch on all providers for the next turn."""
        for provider in self._providers:
            try:
                provider.queue_prefetch(query, session_id=session_id)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' queue_prefetch failed (non-fatal): %s",
                    provider.name, e,
                )

    def search_all(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Federated search across every active provider's reference store.

        Unified memory base (Slice 3): fans a single query out to all
        providers' ``search()`` implementations, normalizes the hits into the
        unified recall shape, and aggregates them. The result is consumed by
        ``memory_fabric.recall_hierarchical`` as the L4 layer. Failures in one
        provider never block the others.
        """
        aggregated: List[Dict[str, Any]] = []
        metrics = get_metrics()
        for provider in self._providers:
            # Contract probe: skip providers whose search() signature cannot be
            # called as search(query, limit). Otherwise the TypeError is raised
            # and silently swallowed, producing an empty (wrong) federation.
            if not self._provider_search_accepts_query_limit(provider):
                logger.warning(
                    "Memory provider '%s' overrides search() with an incompatible "
                    "signature (expected search(self, query, limit)); skipping it in "
                    "federation (L4 layer). To participate, align the signature or "
                    "remove the override and expose a '*_search'/'*_query' tool so the "
                    "base tool-discovery path picks it up.",
                    provider.name,
                )
                metrics.record_federation_skip(provider.name, "signature_mismatch")
                continue
            try:
                hits = provider.search(query, limit)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' search failed (non-fatal): %s",
                    provider.name, e,
                )
                metrics.record_federation_error(provider.name)
                continue
            if not hits:
                metrics.record_federation_search(provider.name, total=0, hits=0)
                continue
            for i, hit in enumerate(hits[:limit]):
                content = hit.get("content") or hit.get("preview") or ""
                if not content:
                    continue
                pointer = hit.get("pointer") or (
                    # A2: unified ``{source}#{id}`` form. For RAG hits the
                    # provider exposes ``doc_id`` + ``chunk_index``; mirroring
                    # the migration pointer (``rag#{doc_id}#{chunk_index}``)
                    # lets recall_hierarchical de-duplicate the same chunk
                    # whether it came from the seeded index or live federation.
                    f"{provider.name}#{hit.get('doc_id', hit.get('id', ''))}"
                    + (f"#{hit.get('chunk_index', '')}" if hit.get("chunk_index", "") != "" else "")
                )
                supplied = hit.get("score")
                score = (
                    float(supplied)
                    if isinstance(supplied, (int, float))
                    else 1.0 - i / max(len(hits), 1)
                )
                aggregated.append(
                    {
                        "layer": L4_REFERENCE,
                        "source": hit.get("source") or provider.name,
                        "pointer": pointer,
                        "content": content,
                        "score": score,
                    }
                )
        # Record federation metrics for this provider
            metrics.record_federation_search(
                provider.name, total=len(hits), hits=len(hits[:limit])
            )
        metrics.record_recall_layer("L4", hits=len(aggregated))
        return aggregated

    # -- Sync ----------------------------------------------------------------

    @staticmethod
    def _provider_sync_accepts_messages(provider: MemoryProvider) -> bool:
        """Return whether sync_turn accepts a messages keyword."""
        try:
            signature = inspect.signature(provider.sync_turn)
        except (TypeError, ValueError):
            return True
        params = list(signature.parameters.values())
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
            return True
        return "messages" in signature.parameters

    @staticmethod
    def _provider_search_accepts_query_limit(provider: MemoryProvider) -> bool:
        """Return whether ``provider.search`` can be invoked as ``search(query, limit)``.

        The federated-search contract (``MemoryProvider.search(self, query, limit)``)
        is the single calling convention ``search_all`` uses. A provider that
        overrides ``search`` with an incompatible signature (e.g. an extra
        ``user_id``/``session_id`` positional) would raise ``TypeError`` at call
        time and — historically — be silently swallowed. Instead we probe the
        signature up front and skip + warn, so the contract violation is
        observable rather than a silent empty result.

        Generic and data-driven: we do not hardcode which providers are "valid";
        we merely check that the call would bind.
        """
        try:
            signature = inspect.signature(provider.search)
        except (TypeError, ValueError):
            return False
        try:
            # Bind exactly the args search_all passes. Raises TypeError if the
            # provider's signature cannot accept them (e.g. missing 'query').
            signature.bind("__probe_query__", 5)
        except TypeError:
            return False
        return True

    def sync_all(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        scope: str = "",
    ) -> None:
        """Sync a completed turn to all providers.

        ``scope`` (Step 4) is the originating channel (telegram/feishu/.../web).
        It is forwarded to every provider's ``sync_turn`` so a provider that
        persists memories can tag them by channel and later weight recall.
        """
        for provider in self._providers:
            try:
                if messages is not None and self._provider_sync_accepts_messages(provider):
                    provider.sync_turn(
                        user_content,
                        assistant_content,
                        session_id=session_id,
                        messages=messages,
                        scope=scope,
                    )
                else:
                    provider.sync_turn(
                        user_content,
                        assistant_content,
                        session_id=session_id,
                        scope=scope,
                    )
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' sync_turn failed: %s",
                    provider.name, e,
                )

    # -- Tools ---------------------------------------------------------------

    def get_all_tool_schemas(self) -> List[Dict[str, Any]]:
        """Collect tool schemas from all providers + built-in memory tools."""
        schemas = self._get_builtin_tool_schemas()
        seen = {s.get("name", "") for s in schemas}
        for provider in self._providers:
            try:
                for schema in provider.get_tool_schemas():
                    name = schema.get("name", "")
                    if name and name not in seen:
                        schemas.append(schema)
                        seen.add(name)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' get_tool_schemas() failed: %s",
                    provider.name, e,
                )
        return schemas

    def _get_builtin_tool_schemas(self) -> List[Dict[str, Any]]:
        """Built-in memory tools exposed to the agent."""
        return [
            {
                "name": "forget_session",
                "description": (
                    "Clear all cross-session memory for the current session. "
                    "Use this when the user wants privacy or to start fresh. "
                    "Does NOT delete data — it marks the session's handoff and "
                    "decisions as inactive (incognito mode). "
                    "No arguments required."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "view_memory_status",
                "description": (
                    "Show what cross-session memory is currently loaded. "
                    "Displays handoff source, evolution context, recalled memories, "
                    "and active decisions with their ages. No arguments required."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        ]

    def handle_builtin_tool(self, name: str, args: Dict[str, Any], **kwargs) -> str:
        """Handle a built-in memory tool call. Returns JSON string."""
        import json

        if name == "forget_session":
            session_id = kwargs.get("session_id", "default")
            try:
                from agent.handoff_store import forget_session as _forget
                result = _forget(session_id)
                parts = []
                if result.get("handoff"):
                    parts.append("✅ 会话交接记录已清除")
                if result.get("decisions_count", 0) > 0:
                    parts.append(f"✅ {result['decisions_count']} 条决策已清除")
                if not parts:
                    parts.append("✅ 本会话无记忆需要清除")
                if result.get("errors"):
                    parts.append(f"⚠️ {result['errors']}")
                return json.dumps({"ok": True, "message": " ".join(parts), "detail": result})
            except Exception as e:
                return json.dumps({"ok": False, "error": str(e)})

        elif name == "view_memory_status":
            try:
                from hermes_cli.blueprints.chat import memory_status as _ms
                import asyncio
                result = asyncio.run(_ms(""))
                return json.dumps(result)
            except Exception as e:
                return json.dumps({"ok": False, "error": str(e)})

        return json.dumps({"error": f"Unknown builtin memory tool: {name}"})

    def get_all_tool_names(self) -> set:
        """Return set of all tool names (providers + built-in)."""
        return set(self._tool_to_provider.keys()) | {"forget_session", "view_memory_status"}

    def has_tool(self, tool_name: str) -> bool:
        """Check if any provider handles this tool, or if it's a built-in tool."""
        if tool_name in {"forget_session", "view_memory_status"}:
            return True
        return tool_name in self._tool_to_provider

    def handle_tool_call(
        self, tool_name: str, args: Dict[str, Any], **kwargs
    ) -> str:
        """Route a tool call to built-in handler or the correct provider.

        Built-in tools (forget_session, view_memory_status) are handled here.
        All other tools are delegated to the matching memory provider.

        Returns JSON string result. Raises ValueError if no provider
        handles the tool.
        """
        # Check built-in tools first
        _builtin_names = {"forget_session", "view_memory_status"}
        if tool_name in _builtin_names:
            return self.handle_builtin_tool(tool_name, args, **kwargs)

        provider = self._tool_to_provider.get(tool_name)
        if provider is None:
            return tool_error(f"No memory provider handles tool '{tool_name}'")
        try:
            return provider.handle_tool_call(tool_name, args, **kwargs)
        except Exception as e:
            logger.error(
                "Memory provider '%s' handle_tool_call(%s) failed: %s",
                provider.name, tool_name, e,
            )
            return tool_error(f"Memory tool '{tool_name}' failed: {e}")

    # -- Lifecycle hooks -----------------------------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Notify all providers of a new turn.

        kwargs may include: remaining_tokens, model, platform, tool_count.
        """
        for provider in self._providers:
            try:
                provider.on_turn_start(turn_number, message, **kwargs)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_turn_start failed: %s",
                    provider.name, e,
                )

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Notify all providers of session end."""
        for provider in self._providers:
            try:
                provider.on_session_end(messages)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' on_session_end failed: %s",
                    provider.name, e,
                    exc_info=True,
                )

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        **kwargs,
    ) -> None:
        """Notify all providers that the agent's session_id has rotated.

        Fires on ``/resume``, ``/branch``, ``/reset``, ``/new``, and
        context compression — any path that reassigns
        ``AIAgent.session_id`` without tearing the provider down.

        Providers keep running; they only need to refresh cached
        per-session state so subsequent writes land in the correct
        session's record. See ``MemoryProvider.on_session_switch`` for
        the full contract.
        """
        if not new_session_id:
            return
        for provider in self._providers:
            try:
                provider.on_session_switch(
                    new_session_id,
                    parent_session_id=parent_session_id,
                    reset=reset,
                    **kwargs,
                )
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_session_switch failed: %s",
                    provider.name, e,
                )

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Notify all providers before context compression.

        Returns combined text from providers to include in the compression
        summary prompt. Empty string if no provider contributes.
        """
        parts = []
        for provider in self._providers:
            try:
                result = provider.on_pre_compress(messages)
                if result and result.strip():
                    parts.append(result)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_pre_compress failed: %s",
                    provider.name, e,
                )
        return "\n\n".join(parts)

    @staticmethod
    def _provider_memory_write_metadata_mode(provider: MemoryProvider) -> str:
        """Return how to pass metadata to a provider's memory-write hook."""
        try:
            signature = inspect.signature(provider.on_memory_write)
        except (TypeError, ValueError):
            return "keyword"

        params = list(signature.parameters.values())
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
            return "keyword"
        if "metadata" in signature.parameters:
            return "keyword"

        accepted = [
            p for p in params
            if p.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        ]
        if len(accepted) >= 4:
            return "positional"
        return "legacy"

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Notify external providers when the built-in memory tool writes.

        Skips the builtin provider itself (it's the source of the write).
        """
        for provider in self._providers:
            if provider.name == "builtin":
                continue
            try:
                metadata_mode = self._provider_memory_write_metadata_mode(provider)
                if metadata_mode == "keyword":
                    provider.on_memory_write(
                        action, target, content, metadata=dict(metadata or {})
                    )
                elif metadata_mode == "positional":
                    provider.on_memory_write(action, target, content, dict(metadata or {}))
                else:
                    provider.on_memory_write(action, target, content)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_memory_write failed: %s",
                    provider.name, e,
                )

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        """Notify all providers that a subagent completed."""
        for provider in self._providers:
            try:
                provider.on_delegation(
                    task, result, child_session_id=child_session_id, **kwargs
                )
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_delegation failed: %s",
                    provider.name, e,
                )

    def shutdown_all(self) -> None:
        """Shut down all providers (reverse order for clean teardown)."""
        for provider in reversed(self._providers):
            try:
                provider.shutdown()
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' shutdown failed: %s",
                    provider.name, e,
                )

    def initialize_all(self, session_id: str, **kwargs) -> None:
        """Initialize all providers.

        Automatically injects ``hermes_home`` into *kwargs* so that every
        provider can resolve profile-scoped storage paths without importing
        ``get_hermes_home()`` themselves.
        """
        if "hermes_home" not in kwargs:
            from hermes_constants import get_hermes_home
            kwargs["hermes_home"] = str(get_hermes_home())
        for provider in self._providers:
            try:
                provider.initialize(session_id=session_id, **kwargs)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' initialize failed: %s",
                    provider.name, e,
                )
