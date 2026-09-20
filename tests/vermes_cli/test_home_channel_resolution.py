"""P0-A — home channel 单一口径契约测试。

守卫的不变量：**任何判定「该平台是否设置了 home channel」的代码，都不得只读
环境变量。** 历史上两处 env-only 旁路（`message_handler_mixin.py` 的新会话提示、
`cron/scheduler.py` 的投递解析）会漏掉只存在于 config 里的 home channel ——
前者每新会话误报一次，后者静默丢投递。

解析口径（单一入口 `gateway.gateway_utils.resolve_home_channel_chat_id`）：
env → legacy env → config（GatewayConfig 对象或 config.yaml 字典）。
"""
from __future__ import annotations

import os
import unittest

# 测试显式清/恢复：conftest 只过滤凭据型变量，HOME_CHANNEL 不在其列，
# 开发机 .env 里若已设值会让断言假绿。
_ENV_VARS = (
    "TELEGRAM_HOME_CHANNEL",
    "FEISHU_HOME_CHANNEL",
    "QQBOT_HOME_CHANNEL",
    "QQ_HOME_CHANNEL",
)


class _Home:
    """HomeChannel 替身 —— 解析器只消费 .chat_id。"""

    def __init__(self, chat_id: str):
        self.chat_id = chat_id


class _Cfg:
    """GatewayConfig 替身 —— 只暴露 get_home_channel(platform)。"""

    def __init__(self, mapping: dict):
        self._mapping = mapping

    def get_home_channel(self, platform):
        return self._mapping.get(getattr(platform, "value", str(platform)))


class _EnvGuard:
    def __enter__(self):
        self._saved = {k: os.environ.pop(k, None) for k in _ENV_VARS}
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


class HomeChannelResolutionTests(unittest.TestCase):
    """解析器本身的四条口径。"""

    def _resolve(self):
        from gateway.gateway_utils import resolve_home_channel_chat_id
        return resolve_home_channel_chat_id

    def test_env_only(self):
        with _EnvGuard():
            os.environ["FEISHU_HOME_CHANNEL"] = "oc_env_1"
            self.assertEqual(self._resolve()("feishu"), "oc_env_1")

    def test_config_only_dict(self):
        """只写 config.yaml 时必须认 —— 这是被 env-only 漏掉的那条路。"""
        with _EnvGuard():
            cfg = {"platforms": {"feishu": {"home_channel": {"chat_id": "oc_cfg_1"}}}}
            self.assertEqual(self._resolve()("feishu", cfg), "oc_cfg_1")

    def test_config_only_object(self):
        with _EnvGuard():
            cfg = _Cfg({"feishu": _Home("oc_obj_1")})
            self.assertEqual(self._resolve()("feishu", cfg), "oc_obj_1")

    def test_real_gateway_config_object(self):
        """/sethome 同步进内存的就是这种对象形态，必须真能取到值。"""
        with _EnvGuard():
            from gateway.config import (
                GatewayConfig,
                HomeChannel,
                Platform,
                PlatformConfig,
            )
            cfg = GatewayConfig(platforms={
                Platform.FEISHU: PlatformConfig(
                    enabled=True,
                    home_channel=HomeChannel(
                        platform=Platform.FEISHU,
                        chat_id="oc_real_1",
                        name="Home",
                    ),
                )
            })
            self.assertEqual(self._resolve()("feishu", cfg), "oc_real_1")

    def test_env_wins_over_config(self):
        """显式 env 覆盖仍优先，与上游一致，保证不是回归改动。"""
        with _EnvGuard():
            os.environ["FEISHU_HOME_CHANNEL"] = "oc_env_2"
            cfg = {"platforms": {"feishu": {"home_channel": {"chat_id": "oc_cfg_2"}}}}
            self.assertEqual(self._resolve()("feishu", cfg), "oc_env_2")

    def test_legacy_env_fallback(self):
        with _EnvGuard():
            os.environ["QQ_HOME_CHANNEL"] = "qq_legacy"
            self.assertEqual(self._resolve()("qqbot"), "qq_legacy")

    def test_nothing_set_returns_empty(self):
        with _EnvGuard():
            self.assertEqual(self._resolve()("feishu", {}), "")


class CronDeliveryFallbackTests(unittest.TestCase):
    """A2 — cron 投递不得再静默丢弃 config-only 的 home channel。"""

    def _cron_chat_id(self):
        from cron.scheduler import _get_home_target_chat_id
        return _get_home_target_chat_id

    def _patch_config(self, cfg: dict):
        import cron.scheduler as sched
        original = sched.load_config
        sched.load_config = lambda: cfg
        return sched, original

    def test_cron_falls_back_to_config(self):
        with _EnvGuard():
            fn = self._cron_chat_id()
            sched, original = self._patch_config(
                {"platforms": {"feishu": {"home_channel": {"chat_id": "oc_cron_1"}}}}
            )
            try:
                self.assertEqual(fn("feishu"), "oc_cron_1")
            finally:
                sched.load_config = original

    def test_cron_env_still_wins(self):
        with _EnvGuard():
            fn = self._cron_chat_id()
            os.environ["FEISHU_HOME_CHANNEL"] = "oc_cron_env"
            sched, original = self._patch_config(
                {"platforms": {"feishu": {"home_channel": {"chat_id": "oc_cron_cfg"}}}}
            )
            try:
                self.assertEqual(fn("feishu"), "oc_cron_env")
            finally:
                sched.load_config = original

    def test_cron_nothing_set_returns_empty(self):
        with _EnvGuard():
            fn = self._cron_chat_id()
            sched, original = self._patch_config({})
            try:
                self.assertEqual(fn("feishu"), "")
            finally:
                sched.load_config = original


class NoEnvOnlyBypassTests(unittest.TestCase):
    """契约守卫（弱）：两个历史旁路点必须已接入共享解析器。

    这是导入/源码级守卫而非行为测试 —— 真行为验证要构造完整 turn 流水线，
    留给 P1 契约测试补齐。此处只保证「不会悄悄退回 env-only」。
    """

    def test_notice_prompt_module_imports_shared_resolver(self):
        from gateway import message_handler_mixin as mixin
        self.assertTrue(hasattr(mixin, "resolve_home_channel_chat_id"))

    def test_cron_resolver_has_config_leg(self):
        import inspect
        from cron.scheduler import _get_home_target_chat_id
        self.assertIn(
            "config_home_channel_chat_id",
            inspect.getsource(_get_home_target_chat_id),
        )


if __name__ == "__main__":
    unittest.main()
