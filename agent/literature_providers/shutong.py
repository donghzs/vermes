"""书童图书馆 (shutong) 专用文献源适配器。

shutong 是一个第三方中文文献代理网关（知网/万方/维普等），基于 EmpireCMS。
其检索链路（真机抓包实证，2026-07-24）：

  1. EmpireCMS 表单登录（卡号+密码 → 会话 cookie）
  2. 登录态访问 ``/l77.php`` → JS 跳转 ``api88.wenxian.shop/token?sign_token=<JWT>``
  3. 跟随 token 302 → 动态分配的 CNKI KNS8 镜像（地址按会话动态分配，运行时发现）
  4. 镜像上 ``POST /kns8s/brief/grid`` 标准知网检索 → 解析 ``table.result-table-list``

全流程纯 HTTP（httpx）自动完成，无需浏览器、无需用户从 DevTools 抓 URL。
通用逻辑见 :mod:`agent.literature_providers.kns8_login_provider`（本类仅声明差异）。
镜像首页弹滑块验证码，但检索 API 不设验证码，故绕开首页直连 grid。

合规：仅用于用户自有合法 shutong 凭证；仅做已购权限内检索，不破解验证码。
"""

from __future__ import annotations

from typing import Any, Dict

from agent.literature_providers.kns8_login_provider import (
    Kns8TempLoginProvider,
    _parse_shutong_grid,
)

# 保留模块级别名，兼容既有测试 import 路径。
__all__ = ["ShutongProvider", "_parse_shutong_grid"]


class ShutongProvider(Kns8TempLoginProvider):
    """书童图书馆适配器：EmpireCMS 登录 + ``/l77.php`` SSO → KNS8 镜像检索。

    仅覆写默认配置；检索逻辑全部继承自 :class:`Kns8TempLoginProvider`。
    """

    _DEFAULT_SSO_PATH = "/l77.php"
    _DEFAULT_SSO_REFERER = "/zhongwenku/"
    _DEFAULT_SSO_MODE = "token_then_redirect"
    _DEFAULT_ECMSFROM = "/zhongwenku/"
    _DEFAULT_CHANNEL_GATE = False
    _DEFAULT_SOURCE_TAG = "shutong"

    def __init__(self, definition: Dict[str, Any]):
        super().__init__(definition)
