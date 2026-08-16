"""P6 重资产管理测试。

验证 install_module_asset / list_module_assets / ensure_assets_ready 的代码路径。
使用 mock 下载，不依赖真实网络。
"""
import json
import hashlib
import tarfile
import io
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from agent.module_catalog import (
    AssetSpec,
    CatalogModule,
    install_module_asset,
    list_module_assets,
    ensure_assets_ready,
    _resolve_asset_target,
    _is_asset_ready,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_modules():
    """构造含一个必选 + 一个可选资产的 catalog 模块。"""
    return [
        CatalogModule(
            name="testmod",
            display_name="测试模块",
            latest="1.0.0",
            code_asset="https://example.com/testmod-1.0.0.tar.gz",
            code_sha256="abc123",
            size_code=1000,
            assets=[
                AssetSpec(
                    id="engine-venv",
                    label="引擎运行时",
                    url="https://example.com/engine-venv-1.0.0.tar.gz",
                    sha256="sha_engine",
                    size=50_000_000,
                    target="engines/testmod",
                    optional=False,
                ),
                AssetSpec(
                    id="sample-data",
                    label="示例数据",
                    url="https://example.com/samples-1.0.0.tar.gz",
                    sha256="sha_samples",
                    size=5_000_000,
                    target="modules/testmod/assets",
                    optional=True,
                ),
            ],
        )
    ]


@pytest.fixture
def fake_tarball(tmp_path):
    """构造一个真实的小 tar.gz 用于 mock 下载。"""
    tar_path = tmp_path / "fake-asset.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        # 加一个文件
        info = tarfile.TarInfo(name="hello.txt")
        content = b"hello world"
        info.size = len(content)
        tf.addfile(info, io.BytesIO(content))
    data = tar_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    return data, sha


# ---------------------------------------------------------------------------
# _resolve_asset_target
# ---------------------------------------------------------------------------

def test_resolve_absolute_target():
    a = AssetSpec(id="x", target="/opt/vermes/engines/x")
    assert _resolve_asset_target(a) == Path("/opt/vermes/engines/x")


def test_resolve_relative_target():
    a = AssetSpec(id="x", target="engines/x")
    t = _resolve_asset_target(a)
    assert t.is_absolute()
    assert t.name == "x"


def test_resolve_no_target():
    a = AssetSpec(id="x")
    t = _resolve_asset_target(a)
    assert t.is_absolute()
    assert "engines" in str(t)


# ---------------------------------------------------------------------------
# _is_asset_ready
# ---------------------------------------------------------------------------

def test_asset_not_ready_empty_dir(tmp_path):
    a = AssetSpec(id="x", sha256="abc")
    assert not _is_asset_ready(a, tmp_path)


def test_asset_not_ready_no_dir(tmp_path):
    a = AssetSpec(id="x", sha256="abc")
    assert not _is_asset_ready(a, tmp_path / "nonexistent")


def test_asset_ready_with_marker(tmp_path):
    a = AssetSpec(id="x", sha256="abc123")
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".asset_ready").write_text(json.dumps({"sha256": "abc123"}))
    (tmp_path / "some_file.bin").write_bytes(b"\x00")
    assert _is_asset_ready(a, tmp_path)


def test_asset_ready_wrong_sha_marker(tmp_path):
    a = AssetSpec(id="x", sha256="abc123")
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".asset_ready").write_text(json.dumps({"sha256": "wrong"}))
    (tmp_path / "some_file.bin").write_bytes(b"\x00")
    assert not _is_asset_ready(a, tmp_path)


def test_asset_ready_no_sha_but_has_content(tmp_path):
    a = AssetSpec(id="x", sha256="")  # 无 sha 要求
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "data.bin").write_bytes(b"\x00")
    assert _is_asset_ready(a, tmp_path)


# ---------------------------------------------------------------------------
# list_module_assets
# ---------------------------------------------------------------------------

def test_list_assets(fake_modules, tmp_path):
    with patch("agent.module_catalog.get_catalog_modules", return_value=fake_modules):
        with patch("agent.module_catalog._vermes_home", return_value=tmp_path):
            assets = list_module_assets("testmod")
            assert len(assets) == 2
            assert assets[0]["id"] == "engine-venv"
            assert assets[0]["ready"] is False
            assert assets[1]["id"] == "sample-data"
            assert assets[1]["optional"] is True


def test_list_assets_nonexistent_module():
    with patch("agent.module_catalog.get_catalog_modules", return_value=[]):
        assert list_module_assets("nope") == []


# ---------------------------------------------------------------------------
# install_module_asset
# ---------------------------------------------------------------------------

def test_install_asset_success(fake_modules, fake_tarball, tmp_path):
    data, sha = fake_tarball
    # 修改 fake_modules 的资产 sha 匹配 fake_tarball
    fake_modules[0].assets[0].url = "https://example.com/engine.tar.gz"
    fake_modules[0].assets[0].sha256 = sha
    fake_modules[0].assets[0].target = str(tmp_path / "engines" / "testmod")

    def mock_download(url, dest, sha256=None, progress=None):
        Path(dest).write_bytes(data)
        return True

    with patch("agent.module_catalog.get_catalog_modules", return_value=fake_modules):
        with patch("agent.module_catalog.download_file", side_effect=mock_download):
            result = install_module_asset("testmod", "engine-venv")
            assert result.exists()
            assert (result / "hello.txt").exists()
            assert (result / ".asset_ready").exists()
            marker = json.loads((result / ".asset_ready").read_text())
            assert marker["sha256"] == sha


def test_install_asset_not_in_catalog(fake_modules):
    with patch("agent.module_catalog.get_catalog_modules", return_value=fake_modules):
        with pytest.raises(ValueError, match="没有资产"):
            install_module_asset("testmod", "nonexistent-asset")


def test_install_asset_module_not_found():
    with patch("agent.module_catalog.get_catalog_modules", return_value=[]):
        with pytest.raises(ModuleNotFoundError):
            install_module_asset("nope", "x")


def test_install_asset_download_fail(fake_modules):
    with patch("agent.module_catalog.get_catalog_modules", return_value=fake_modules):
        with patch("agent.module_catalog.download_file", return_value=False):
            with pytest.raises(RuntimeError, match="下载/校验"):
                install_module_asset("testmod", "engine-venv")


# ---------------------------------------------------------------------------
# ensure_assets_ready
# ---------------------------------------------------------------------------

def test_ensure_all_ready(fake_modules, fake_tarball, tmp_path):
    data, sha = fake_tarball
    fake_modules[0].assets[0].sha256 = sha
    fake_modules[0].assets[0].target = str(tmp_path / "engines" / "testmod")

    def mock_download(url, dest, sha256=None, progress=None):
        Path(dest).write_bytes(data)
        return True

    with patch("agent.module_catalog.get_catalog_modules", return_value=fake_modules):
        with patch("agent.module_catalog.download_file", side_effect=mock_download):
            ok, msg = ensure_assets_ready("testmod")
            assert ok, f"expected ok but got: {msg}"


def test_ensure_already_ready(fake_modules, tmp_path):
    # 模拟已就绪：建目录 + marker + 内容
    target = tmp_path / "engines" / "testmod"
    target.mkdir(parents=True)
    (target / "data.bin").write_bytes(b"\x00")
    (target / ".asset_ready").write_text(
        json.dumps({"sha256": fake_modules[0].assets[0].sha256})
    )
    fake_modules[0].assets[0].target = str(target)

    with patch("agent.module_catalog.get_catalog_modules", return_value=fake_modules):
        ok, msg = ensure_assets_ready("testmod")
        assert ok
        assert msg == ""


def test_ensure_no_auto_install(fake_modules):
    with patch("agent.module_catalog.get_catalog_modules", return_value=fake_modules):
        ok, msg = ensure_assets_ready("testmod", auto_install=False)
        assert not ok
        assert "缺少重资产" in msg


def test_ensure_no_assets():
    """模块无资产时直接返回 ok。"""
    mod = CatalogModule(name="empty", display_name="空", latest="1.0.0")
    with patch("agent.module_catalog.get_catalog_modules", return_value=[mod]):
        ok, msg = ensure_assets_ready("empty")
        assert ok
        assert msg == ""


def test_ensure_optional_skipped(fake_modules, fake_tarball, tmp_path):
    """可选资产缺失时不触发安装。"""
    data, sha = fake_tarball
    fake_modules[0].assets[0].sha256 = sha
    fake_modules[0].assets[0].target = str(tmp_path / "engines" / "testmod")

    # 只安装必选（engine-venv），可选（sample-data）应跳过
    installed = []

    def mock_download(url, dest, sha256=None, progress=None):
        Path(dest).write_bytes(data)
        installed.append(url)
        return True

    with patch("agent.module_catalog.get_catalog_modules", return_value=fake_modules):
        with patch("agent.module_catalog.download_file", side_effect=mock_download):
            ok, msg = ensure_assets_ready("testmod")
            assert ok
            # 只下载了一个（必选），可选未触发
            assert len(installed) == 1
