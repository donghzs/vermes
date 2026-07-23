"""
Host API bridge — 模块通过此文件访问宿主(Vermes)的能力
加载器在 register_to() 之前会注入实际实现
"""
from __future__ import annotations
from typing import Any, Optional

# 这些会被 module_loader 在加载时注入
_resolve_model_provider = None
_get_chat_credentials = None
PROVIDERS = None
_get_hermes_home = None
_registry = None


def _inject(host_module):
    """由 module_loader 调用，注入宿主函数"""
    global _resolve_model_provider, _get_chat_credentials, PROVIDERS, _get_hermes_home, _registry
    _resolve_model_provider = getattr(host_module, 'resolve_model_provider', None)
    _get_chat_credentials = getattr(host_module, 'get_chat_credentials', None)
    PROVIDERS = getattr(host_module, 'PROVIDERS', None)
    _get_hermes_home = getattr(host_module, 'get_hermes_home', None)
    _registry = getattr(host_module, 'registry', None)


def resolve_model_provider(provider_name: str = "", model_name: str = ""):
    """解析 provider/model → (base_url, api_key, model)"""
    if _resolve_model_provider:
        return _resolve_model_provider(provider_name, model_name)
    raise RuntimeError("Host API not injected: resolve_model_provider")


def get_chat_credentials() -> tuple[str, str, str]:
    """返回 (base_url, api_key, default_model)"""
    if _get_chat_credentials:
        return _get_chat_credentials()
    raise RuntimeError("Host API not injected: get_chat_credentials")


def get_providers():
    """返回 provider 列表"""
    if PROVIDERS:
        return PROVIDERS
    return {}


def get_hermes_home():
    """返回 hermes home 目录 Path"""
    if _get_hermes_home:
        return _get_hermes_home()
    from pathlib import Path
    import os
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def get_registry():
    """返回工具注册表"""
    if _registry:
        return _registry
    raise RuntimeError("Host API not injected: registry")


class ProxyRegistry:
    """代理 registry — 让 tools.py 的 registry.register() 转发到宿主"""
    def register(self, **kwargs):
        real = get_registry()
        if real:
            real.register(**kwargs)
        else:
            import logging
            logging.getLogger("scholarforge").warning("registry not injected, tool not registered: %s", kwargs.get('name'))


# tools.py 导入的 registry 用代理替代
registry = ProxyRegistry()
