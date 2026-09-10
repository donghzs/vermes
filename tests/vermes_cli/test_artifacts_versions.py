# -*- coding: utf-8 -*-
"""A1 版本快照基建后端真实往返测试。

覆盖：写回前自动快照 / 无变化跳过 / /versions 列表 / /versions/{vid} 取内容 /
/revert 回退（含回退前快照）/ /diff 文本对比 / LRU 淘汰 / 大文件占位 / 二进制回退。

注意：产物文件必须落在 _is_safe_path 白名单根（真实 /tmp），沿用既有测试 tmp/ + basename 约定；
版本库本身指向临时文件（ver_db fixture），不污染真实 ~/.vermes/artifacts_versions.db。
"""
import sys
import os
import uuid

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

import pytest


@pytest.fixture
def ver_db(tmp_path, monkeypatch):
    """把版本库指向临时文件，避免污染真实 ~/.vermes/artifacts_versions.db。"""
    import vermes_cli.blueprints.artifacts as art
    db = tmp_path / "versions_test.db"
    monkeypatch.setattr(art, "_VERSIONS_DB_PATH", str(db))
    art._init_versions_db()
    yield art
    for ext in ("", "-wal", "-shm"):
        p = str(db) + ext
        if os.path.exists(p):
            os.unlink(p)


@pytest.fixture
def client(ver_db):
    import vermes_cli.web_server as ws
    from starlette.testclient import TestClient
    return TestClient(ws.app)


def _mk_artifact(ext: str) -> str:
    """在真实 /tmp 下创建（空）产物文件，返回绝对路径；调用方负责清理。"""
    p = f"/tmp/a1v_{uuid.uuid4().hex}{ext}"
    open(p, "w").close()
    return p


def _rel(p: str) -> str:
    return "tmp/" + os.path.basename(p)


def _write(client, rel, content: bytes, ctype: str = "text/plain"):
    return client.post(f"/api/v1/artifacts/{rel}/content",
                       content=content, headers={"Content-Type": ctype})


def test_auto_snapshot_text_write_and_revert(client):
    """空文件保存两次 → 2 个版本（最旧为覆盖前空状态）；回退到 v1 后文件变空且产生回退前快照。"""
    p = _mk_artifact(".md")
    try:
        rel = _rel(p)
        r = _write(client, rel, b"# draft\n")
        assert r.status_code == 200, r.text
        v = client.get(f"/api/v1/artifacts/{rel}/versions").json()
        assert len(v["versions"]) == 1
        assert v["versions"][0]["kind"] == "text"

        r = _write(client, rel, b"# draft\nsecond line\n")
        assert r.status_code == 200
        v = client.get(f"/api/v1/artifacts/{rel}/versions").json()
        assert len(v["versions"]) == 2

        # 最旧版本（version 1，DESC 列表末位）内容应为空（覆盖前状态）
        oldest = v["versions"][-1]
        assert oldest["version"] == 1
        g = client.get(f"/api/v1/artifacts/{rel}/versions/{oldest['id']}").json()
        assert g["content"] == ""

        # 回退到 v1（空）
        rev = client.post(f"/api/v1/artifacts/{rel}/versions/{oldest['id']}/revert")
        assert rev.status_code == 200, rev.text
        assert open(p, "r").read() == ""
        # 回退本身应产生一条「回退前快照」版本
        v = client.get(f"/api/v1/artifacts/{rel}/versions").json()
        assert len(v["versions"]) == 3
        assert "回退" in v["versions"][0]["note"]
    finally:
        os.unlink(p)


def test_skip_unchanged_no_version(client):
    """保存相同内容不记版本；保存不同内容才记一条（覆盖前状态）。"""
    p = _mk_artifact(".md")
    try:
        open(p, "w").write("A")  # 预置 A
        rel = _rel(p)
        r = _write(client, rel, b"A")
        assert r.status_code == 200
        assert len(client.get(f"/api/v1/artifacts/{rel}/versions").json()["versions"]) == 0

        r = _write(client, rel, b"B")
        assert r.status_code == 200
        v = client.get(f"/api/v1/artifacts/{rel}/versions").json()
        assert len(v["versions"]) == 1
        g = client.get(f"/api/v1/artifacts/{rel}/versions/{v['versions'][0]['id']}").json()
        assert g["content"] == "A"
    finally:
        os.unlink(p)


def test_lru_eviction(client, monkeypatch, ver_db):
    """单产物版本超上限（monkeypatch=3）时按 version 升序淘汰最旧，留存 3 条。"""
    monkeypatch.setattr(ver_db, "MAX_VERSIONS_PER_ARTIFACT", 3)
    p = _mk_artifact(".md")
    try:
        rel = _rel(p)
        for i in range(5):
            r = _write(client, rel, f"s{i}\n".encode())
            assert r.status_code == 200
        v = client.get(f"/api/v1/artifacts/{rel}/versions").json()
        assert len(v["versions"]) == 3, f"应淘汰到 3 条，实际 {len(v['versions'])}"
    finally:
        os.unlink(p)


def test_diff_text(client):
    """v2（覆盖前 line1/line2）与当前（line2 changed/line3）对比应可 diff 且含变更行。"""
    p = _mk_artifact(".md")
    try:
        rel = _rel(p)
        _write(client, rel, b"line1\nline2\n")
        _write(client, rel, b"line1\nline2 changed\nline3\n")
        v = client.get(f"/api/v1/artifacts/{rel}/versions").json()
        v2 = [x for x in v["versions"] if x["version"] == 2][0]
        d = client.get(f"/api/v1/artifacts/{rel}/versions/{v2['id']}/diff?base=current").json()
        assert d["diffable"] is True
        assert "line2 changed" in d["diff"]
        assert "line3" in d["diff"]
    finally:
        os.unlink(p)


def test_binary_xlsx_roundtrip(client):
    """xlsx 二进制回存记 binary 版本，取内容（base64）可还原并成功回退。"""
    import zipfile
    import base64
    p = f"/tmp/a1v_{uuid.uuid4().hex}.xlsx"
    try:
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
        rel = _rel(p)
        with open(p, "rb") as f:
            blob1 = f.read()
        # 首次写入与文件相同 → 不记版本；改内容再存 → 记 v1=blob1
        r = _write(client, rel, blob1, "application/octet-stream")
        assert r.status_code == 200
        blob2 = blob1 + b"EXTRA"
        r = _write(client, rel, blob2, "application/octet-stream")
        assert r.status_code == 200
        v = client.get(f"/api/v1/artifacts/{rel}/versions").json()
        assert len(v["versions"]) == 1
        assert v["versions"][0]["kind"] == "binary"
        g = client.get(f"/api/v1/artifacts/{rel}/versions/{v['versions'][0]['id']}").json()
        assert base64.b64decode(g["content"]) == blob1
        rev = client.post(f"/api/v1/artifacts/{rel}/versions/{v['versions'][0]['id']}/revert")
        assert rev.status_code == 200
        with open(p, "rb") as f:
            assert f.read() == blob1
    finally:
        if os.path.exists(p):
            os.unlink(p)


def test_oversize_skip(client, monkeypatch, ver_db):
    """超阈值文件仅记元信息占位（kind=oversize，不可回退，revert 返回 409）。"""
    monkeypatch.setattr(ver_db, "_MAX_VERSIONED_SIZE", 10)
    p = f"/tmp/a1v_{uuid.uuid4().hex}.bin"
    try:
        open(p, "wb").write(b"X" * 12)  # 预置 12 字节（>10）
        rel = _rel(p)
        r = _write(client, rel, b"Y" * 12, "application/octet-stream")
        assert r.status_code == 200
        v = client.get(f"/api/v1/artifacts/{rel}/versions").json()
        assert len(v["versions"]) == 1
        assert v["versions"][0]["kind"] == "oversize"
        assert v["versions"][0]["restorable"] is False
        rev = client.post(f"/api/v1/artifacts/{rel}/versions/{v['versions'][0]['id']}/revert")
        assert rev.status_code == 409
    finally:
        if os.path.exists(p):
            os.unlink(p)
