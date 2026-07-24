"""文献云图书馆 (wenx / ccki 等同族) 文献源适配器。

与 shutong 同构：EmpireCMS 卡密登录 → 某 SSO 入口跳转到动态 KNS8 镜像 →
标准知网检索。差异仅在入口形式：

- SSO 入口为 ``/cs00.php``（及同族 ``/csNN.php``），**直接 302** 到 KNS8 镜像
  （shutong 则是 ``/l77.php`` JS 跳 → api88 token → 302）。
- 该账号的知网频道**需购买群组开通**：未开通时入口会停在门户域的
  ``/e/member/buygroup/``，此时给出友好错误而非静默失败（``channel_gate``）。

通用检索逻辑见 :mod:`agent.literature_providers.kns8_login_provider`。
本类仅声明入口差异与频道开通标记。登录字段（username + enews 等）由注册时
``_looks_like_empirecms`` 自动补齐，无需在此重复。

合规：仅用于用户自有合法凭证；仅做已购权限内检索，不破解验证码。
"""

from __future__ import annotations

from typing import Any, Dict

from agent.literature_providers.kns8_login_provider import Kns8TempLoginProvider


class WenxProvider(Kns8TempLoginProvider):
    """文献云图书馆适配器：EmpireCMS 登录 + ``/cs00.php`` 直接 302 → KNS8 镜像。

    频道开通后即可端到端检索；未开通时给出"需开通群组"的友好错误。
    """

    _DEFAULT_SSO_PATH = "/cs00.php"
    _DEFAULT_SSO_REFERER = "/"
    _DEFAULT_SSO_MODE = "direct_302"
    _DEFAULT_ECMSFROM = "/"
    _DEFAULT_CHANNEL_GATE = True
    _DEFAULT_SOURCE_TAG = "wenx"

    def __init__(self, definition: Dict[str, Any]):
        super().__init__(definition)
