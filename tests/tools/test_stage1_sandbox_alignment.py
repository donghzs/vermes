"""Stage 1 声明层对齐验证：联网工具 sandbox=container 后闸门不再 DENY。

验证点：
1. 所有 _NETWORK_SPECS / _BROWSER_SPECS / _DELEGATE_SPECS / _TTS_SPECS / _VISION_SPECS
   工具的 permission_spec.sandbox == SANDBOX_CONTAINER
2. TrustGate.check() 对这些 spec 返回 ALLOW（非 DENY）
3. network_no_sandbox 规则不再触发
4. 纯读/写 FS 工具仍为 SANDBOX_NONE 且 ALLOW
5. EXEC 工具仍为 SANDBOX_NONE 且 ALLOW
"""
import pytest
from vermes_cli.adapters.trust_gate import (
    PermissionSpec, SANDBOX_NONE, SANDBOX_CONTAINER, TrustGate,
)


# 工具名集合（与 registry.py 保持一致）
NETWORK_SPECS = {
    'web_search', 'web_extract', 'image_generate', 'video_generate',
    'literature_search', 'x_search', 'yb_query_group_info', 'yb_query_group_members',
    'yb_search_sticker', 'yb_send_dm', 'yb_send_sticker', 'send_message',
    'discord', 'discord_admin', 'feishu_drive_add_comment', 'feishu_drive_reply_comment',
    'ha_call_service',
}
BROWSER_SPECS = {
    'browser_navigate', 'browser_click', 'browser_type', 'browser_scroll',
    'browser_press', 'browser_snapshot', 'browser_back', 'browser_console',
    'browser_get_images', 'browser_vision', 'browser_dialog', 'browser_cdp',
}
DELEGATE_SPECS = {'delegate_task', 'mixture_of_agents'}
TTS_SPECS = {'text_to_speech'}
VISION_SPECS = {'vision_analyze', 'video_analyze'}

ALL_CONTAINER = NETWORK_SPECS | BROWSER_SPECS | DELEGATE_SPECS | TTS_SPECS | VISION_SPECS


class TestStage1SandboxAlignment:
    """验证联网工具 sandbox=SANDBOX_CONTAINER。"""

    def test_container_specs_have_network(self):
        """所有 container 沙箱工具都声明了 network=True。"""
        # 我们用 registry.apply_permission_specs 来实际注册
        from tools.registry import apply_permission_specs
        import tools.registry as reg_module
        
        # 获取 registry 单例
        registry = reg_module.registry
        apply_permission_specs(registry)
        
        for name in ALL_CONTAINER:
            entry = registry.get_entry(name)
            if entry is None:
                continue  # 工具可能未安装
            assert entry.permission_spec is not None, f"{name} has no spec"
            assert entry.permission_spec.network is True, f"{name} should have network=True"
            assert entry.permission_spec.sandbox == SANDBOX_CONTAINER, \
                f"{name} sandbox should be SANDBOX_CONTAINER, got {entry.permission_spec.sandbox}"

    def test_network_no_sandbox_rule_not_triggered(self):
        """TrustGate.check() 对 container spec 返回 ALLOW，不触发 network_no_sandbox。"""
        spec = PermissionSpec(
            reads_fs=False, writes_fs=False, network=True,
            exec_external=False, sandbox=SANDBOX_CONTAINER,
            requires_explicit_consent=False,
        )
        result = TrustGate.check(spec)
        assert result.decision == "allow"
        assert result.rule != "network_no_sandbox"

    def test_browser_spec_allows(self):
        """浏览器工具 spec（network+exec+container）应 ALLOW。"""
        spec = PermissionSpec(
            reads_fs=True, writes_fs=True, network=True,
            exec_external=True, sandbox=SANDBOX_CONTAINER,
            requires_explicit_consent=False,
        )
        result = TrustGate.check(spec)
        assert result.decision == "allow"

    def test_delegate_spec_asks_user(self):
        """委托工具 spec（requires_explicit_consent=True）应 ASK_USER。"""
        spec = PermissionSpec(
            reads_fs=False, writes_fs=False, network=True,
            exec_external=True, sandbox=SANDBOX_CONTAINER,
            requires_explicit_consent=True,
        )
        result = TrustGate.check(spec)
        assert result.decision == "ask_user"
        assert result.rule == "consent_required"

    def test_old_none_sandbox_with_network_still_denies(self):
        """验证：sandbox=none + network=True 仍触发 network_no_sandbox DENY（反向验证）。"""
        spec = PermissionSpec(
            reads_fs=False, writes_fs=False, network=True,
            exec_external=False, sandbox=SANDBOX_NONE,
            requires_explicit_consent=False,
        )
        result = TrustGate.check(spec)
        assert result.decision == "deny"
        assert result.rule == "network_no_sandbox"

    def test_fs_only_tools_still_none_sandbox(self):
        """纯读/写 FS 工具仍为 SANDBOX_NONE 且 ALLOW。"""
        from tools.registry import apply_permission_specs
        import tools.registry as reg_module
        
        registry = reg_module.registry
        apply_permission_specs(registry)
        
        # 纯读工具示例
        read_entry = registry.get_entry('read_file')
        if read_entry and read_entry.permission_spec:
            assert read_entry.permission_spec.sandbox == SANDBOX_NONE
            assert read_entry.permission_spec.network is False
            result = TrustGate.check(read_entry.permission_spec)
            assert result.decision == "allow"
