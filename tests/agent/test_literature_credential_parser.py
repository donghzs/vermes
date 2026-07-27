"""文献凭证块识别 / 表单登录 provider 测试。"""
from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from agent.literature_custom_store import (
    parse_literature_credential_block,
    register_source_from_credential_block,
)
from agent.literature_providers.custom import CustomHttpProvider


SHUTONG_BLOCK = """卡号：83219570
密码：335779
【使用方法如下】
1.建议用搜狗或谷歌浏览器，复制网址http://3.shutong2.com/ 到浏览器直接输入卡号密码、登录即可。"""


def test_parse_shutong_block():
    p = parse_literature_credential_block(SHUTONG_BLOCK)
    assert p["ok"] is True
    assert p["user"] == "83219570"
    assert p["password"] == "335779"
    assert p["url"] == "http://3.shutong2.com"
    assert p["detected_auth"] == "form"
    # 域名作为默认名称
    assert "shutong2.com" in p["label"]


def test_parse_apikey_block():
    text = "名称：我校图书馆\n网关地址：https://lib.example.edu\nAPI Key：abc-123-xyz"
    p = parse_literature_credential_block(text)
    assert p["ok"] is True
    assert p["api_key"] == "abc-123-xyz"
    assert p["url"] == "https://lib.example.edu"
    assert p["detected_auth"] == "bearer"
    assert p["label"] == "我校图书馆"


def test_parse_user_password_only_warns():
    text = "账号：foo\n密码：bar"
    p = parse_literature_credential_block(text)
    # 没有网址 → 仍识别为 form，但给出 warning
    assert p["user"] == "foo"
    assert p["password"] == "bar"
    assert p["detected_auth"] == "form"
    assert any("网址" in w for w in p["warnings"])


def test_parse_empty():
    assert parse_literature_credential_block("").get("ok") is False
    assert parse_literature_credential_block("随便一段无凭证文字").get("ok") is False


def test_parse_label_from_url_when_no_label():
    text = "卡号：111\n密码：222\n网址：http://lib.foo.cn/path"
    p = parse_literature_credential_block(text)
    assert p["url"] == "http://lib.foo.cn/path"
    assert "foo.cn" in p["label"]


def test_form_provider_search_success(monkeypatch):
    defn = {
        "id": "shutong_test", "label": "test", "base_url": "http://x",
        "auth_scheme": "form", "login_url": "http://x/login",
        "login_user_field": "user", "login_password_field": "password",
        "search_url": "http://x/search", "method": "GET", "query_param": "q",
        "fields": [
            {"key": "LIT_SHUTONG_TEST_USER", "kind": "user", "label": "账号", "secret": False},
            {"key": "LIT_SHUTONG_TEST_PASSWORD", "kind": "password", "label": "密码", "secret": True},
            {"key": "LIT_SHUTONG_TEST_BASE_URL", "kind": "base_url", "label": "网关", "secret": False},
        ],
    }
    os.environ["LIT_SHUTONG_TEST_USER"] = "83219570"
    os.environ["LIT_SHUTONG_TEST_PASSWORD"] = "335779"
    os.environ["LIT_SHUTONG_TEST_BASE_URL"] = "http://x"
    try:
        # 模拟登录→检索：返回一条命中
        fake = {"ok": True, "data": {"results": [
            {"title": "深度学习综述", "authors": ["张三"], "year": 2020, "journal": "AI"}
        ]}}
        monkeypatch.setattr(
            "agent.literature_providers.custom.http_login_then_search",
            lambda **kw: fake,
        )
        prov = CustomHttpProvider(defn)
        assert prov.is_available() is True
        res = prov.search("深度学习", limit=10)
        assert res["success"] is True
        papers = res["data"]["papers"]
        assert len(papers) == 1
        assert papers[0]["title"] == "深度学习综述"
        assert papers[0]["source"] == "shutong_test"
    finally:
        for k in ("LIT_SHUTONG_TEST_USER", "LIT_SHUTONG_TEST_PASSWORD", "LIT_SHUTONG_TEST_BASE_URL"):
            os.environ.pop(k, None)


def test_form_provider_search_login_failure(monkeypatch):
    defn = {
        "id": "shutong_test2", "label": "t2", "base_url": "http://x",
        "auth_scheme": "form", "login_url": "http://x/login",
        "login_user_field": "user", "login_password_field": "password",
        "search_url": "http://x/search",
        "fields": [
            {"key": "LIT_SHUTONG_T2_USER", "kind": "user", "label": "账号", "secret": False},
            {"key": "LIT_SHUTONG_T2_PASSWORD", "kind": "password", "label": "密码", "secret": True},
        ],
    }
    os.environ["LIT_SHUTONG_T2_USER"] = "u"
    os.environ["LIT_SHUTONG_T2_PASSWORD"] = "p"
    try:
        monkeypatch.setattr(
            "agent.literature_providers.custom.http_login_then_search",
            lambda **kw: {"ok": False, "error": "401 登录失败"},
        )
        prov = CustomHttpProvider(defn)
        res = prov.search("x")
        assert res["success"] is False
        assert "401" in res["error"]
    finally:
        for k in ("LIT_SHUTONG_T2_USER", "LIT_SHUTONG_T2_PASSWORD"):
            os.environ.pop(k, None)


def test_register_source_persist_false_creates_definition(tmp_path, monkeypatch):
    # 把自定义源存储重定向到临时目录，避免污染 ~/.vermes
    monkeypatch.setattr(
        "agent.literature_custom_store._resolve_store_path",
        lambda: tmp_path / "literature_custom_sources.json",
    )
    res = register_source_from_credential_block(SHUTONG_BLOCK, persist_credentials=False)
    assert res["success"] is True
    assert res["auth_scheme"] == "form"
    # 未落盘凭证时不应写出任何 LIT_ 环境变量
    assert not any(k.startswith("LIT_") for k in os.environ)
    # 源定义已写入临时文件
    assert (tmp_path / "literature_custom_sources.json").exists()
    import json
    data = json.loads((tmp_path / "literature_custom_sources.json").read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["auth_scheme"] == "form"
    # EmpireCMS 真机：登录脚本是 /e/member/doaction.php，字段名是 username
    assert data[0]["login_url"] == "http://3.shutong2.com/e/member/doaction.php"
    assert data[0]["login_user_field"] == "username"
