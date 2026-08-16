"""测试 module_catalog P1：catalog 加载、索引构建、下载安装、热重载。

用本地 file:// URL 模拟 GitHub Release，验证完整链路：
  load_catalog → build_tool_index → install_module_code → ensure_module_ready
"""
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from agent.module_catalog import (
    AssetSpec,
    CatalogModule,
    build_keyword_index,
    build_tool_index,
    catalog_modules,
    download_file,
    find_module_for_tool,
    is_module_installed,
    load_catalog,
    match_modules_by_keywords,
    safe_extract,
    verify_sha256,
)


# ── fixture：本地 catalog + tarball ──────────────────────────

@pytest.fixture
def fake_catalog(tmp_path):
    """构造一个本地 catalog.json + 两个假模块 tarball。"""
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()

    # 假模块 1：mfgcad
    mod1_dir = modules_dir / "mfgcad"
    mod1_dir.mkdir()
    (mod1_dir / "module.yaml").write_text(
        "name: mfgcad\nversion: 0.2.0\nprovides_tools:\n  - mfg_text_to_cad\n  - mfg_setup_engine\n"
        "keywords:\n  - 3D\n  - 建模\n  - CAD\n"
    )
    (mod1_dir / "tools.py").write_text("# mfgcad tools\n")
    tar1 = tmp_path / "mfgcad-0.2.0.tar.gz"
    import tarfile
    with tarfile.open(tar1, "w:gz") as tf:
        tf.add(mod1_dir / "module.yaml", arcname="mfgcad/module.yaml")
        tf.add(mod1_dir / "tools.py", arcname="mfgcad/tools.py")
    shutil.rmtree(mod1_dir)

    # 假模块 2：scholarforge
    mod2_dir = modules_dir / "scholarforge"
    mod2_dir.mkdir()
    (mod2_dir / "module.yaml").write_text(
        "name: scholarforge\nversion: 1.0.0\nprovides_tools:\n  - scholarforge_search\n"
        "keywords:\n  - 论文\n  - 写作\n"
    )
    (mod2_dir / "tools.py").write_text("# scholarforge tools\n")
    tar2 = tmp_path / "scholarforge-1.0.0.tar.gz"
    with tarfile.open(tar2, "w:gz") as tf:
        tf.add(mod2_dir / "module.yaml", arcname="scholarforge/module.yaml")
        tf.add(mod2_dir / "tools.py", arcname="scholarforge/tools.py")
    shutil.rmtree(mod2_dir)

    # catalog.json
    import hashlib
    sha1 = hashlib.sha256(tar1.read_bytes()).hexdigest()
    sha2 = hashlib.sha256(tar2.read_bytes()).hexdigest()
    catalog = {
        "generated_at": "2026-08-16T00:00:00Z",
        "modules": [
            {
                "name": "mfgcad",
                "display_name": "制造 CAD",
                "latest": "0.2.0",
                "code_asset": f"file://{tar1}",
                "code_sha256": sha1,
                "size_code": tar1.stat().st_size,
                "provides_tools": ["mfg_text_to_cad", "mfg_setup_engine"],
                "keywords": ["3D", "建模", "CAD"],
                "recommended": True,
            },
            {
                "name": "scholarforge",
                "display_name": "论文写作",
                "latest": "1.0.0",
                "code_asset": f"file://{tar2}",
                "code_sha256": sha2,
                "size_code": tar2.stat().st_size,
                "provides_tools": ["scholarforge_search"],
                "keywords": ["论文", "写作"],
            },
        ],
    }
    cat_path = tmp_path / "catalog.json"
    cat_path.write_text(json.dumps(catalog))
    return cat_path, modules_dir, tar1, tar2


# ── catalog 加载 ─────────────────────────────────────────────

def test_load_catalog_local(fake_catalog):
    cat_path, *_ = fake_catalog
    data = load_catalog(str(cat_path))
    assert len(data["modules"]) == 2
    assert data["modules"][0]["name"] == "mfgcad"


def test_load_catalog_missing_returns_empty():
    data = load_catalog("/nonexistent/path/catalog.json")
    assert data["modules"] == []


def test_load_catalog_malformed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"not_modules": []}')
    data = load_catalog(str(bad))
    assert data["modules"] == []


# ── 解析 + 索引 ──────────────────────────────────────────────

def test_catalog_modules_parsed(fake_catalog):
    cat_path, *_ = fake_catalog
    mods = catalog_modules(load_catalog(str(cat_path)))
    assert len(mods) == 2
    assert mods[0].name == "mfgcad"
    assert mods[0].provides_tools == ["mfg_text_to_cad", "mfg_setup_engine"]
    assert "3D" in mods[0].keywords


def test_build_tool_index(fake_catalog):
    cat_path, *_ = fake_catalog
    mods = catalog_modules(load_catalog(str(cat_path)))
    idx = build_tool_index(mods)
    assert idx["mfg_text_to_cad"] == "mfgcad"
    assert idx["scholarforge_search"] == "scholarforge"


def test_build_keyword_index(fake_catalog):
    cat_path, *_ = fake_catalog
    mods = catalog_modules(load_catalog(str(cat_path)))
    idx = build_keyword_index(mods)
    assert "mfgcad" in idx["3d"]
    assert "mfgcad" in idx["建模".lower()]


def test_find_module_for_tool(fake_catalog):
    cat_path, *_ = fake_catalog
    mods = catalog_modules(load_catalog(str(cat_path)))
    m = find_module_for_tool("mfg_text_to_cad", mods)
    assert m is not None
    assert m.name == "mfgcad"
    assert find_module_for_tool("nonexistent_tool", mods) is None


def test_match_modules_by_keywords(fake_catalog):
    cat_path, *_ = fake_catalog
    mods = catalog_modules(load_catalog(str(cat_path)))
    matches = match_modules_by_keywords("帮我建一个3D模型", mods)
    assert len(matches) > 0
    assert matches[0][0].name == "mfgcad"
    assert matches[0][1] >= 1  # 至少命中 "3D"


def test_match_modules_no_hits(fake_catalog):
    cat_path, *_ = fake_catalog
    mods = catalog_modules(load_catalog(str(cat_path)))
    assert match_modules_by_keywords("帮我做个饭", mods) == []


# ── 供应链校验 ───────────────────────────────────────────────

def test_verify_sha256_ok(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    import hashlib
    sha = hashlib.sha256(b"hello").hexdigest()
    assert verify_sha256(f, sha)


def test_verify_sha256_mismatch(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    assert not verify_sha256(f, "0" * 64)


def test_verify_sha256_skip_empty(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    assert verify_sha256(f, "")


# ── 下载 ─────────────────────────────────────────────────────

def test_download_file_local(fake_catalog):
    cat_path, _, tar1, _ = fake_catalog
    dest = cat_path.parent / "downloaded.tar.gz"
    import hashlib
    sha = hashlib.sha256(tar1.read_bytes()).hexdigest()
    assert download_file(f"file://{tar1}", dest, sha256=sha)
    assert dest.exists()
    assert dest.stat().st_size == tar1.stat().st_size


def test_download_file_sha_fail(fake_catalog):
    _, _, tar1, _ = fake_catalog
    dest = tar1.parent / "bad_download.tar.gz"
    assert not download_file(f"file://{tar1}", dest, sha256="0" * 64)
    assert not dest.exists()


# ── 安全解压 ─────────────────────────────────────────────────

def test_safe_extract_ok(tmp_path):
    import tarfile
    src = tmp_path / "src"
    src.mkdir()
    (src / "module.yaml").write_text("name: test\n")
    tar = tmp_path / "test.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        tf.add(src / "module.yaml", arcname="test/module.yaml")
    dest = tmp_path / "dest"
    safe_extract(tar, dest)
    assert (dest / "test" / "module.yaml").exists()


def test_safe_extract_blocks_path_traversal(tmp_path):
    import tarfile
    tar = tmp_path / "evil.tar.gz"
    with tarfile.open(tar, "w:gz") as tf:
        info = tarfile.TarInfo(name="../../../etc/passwd")
        import io
        tf.addfile(info, io.BytesIO(b"evil"))
    dest = tmp_path / "dest"
    with pytest.raises(ValueError, match="越界"):
        safe_extract(tar, dest)


# ── 安装 + 就绪检查 ──────────────────────────────────────────

def test_is_module_installed_false(tmp_path):
    assert not is_module_installed("mfgcad", modules_dir=tmp_path)


def test_install_module_code(fake_catalog):
    cat_path, modules_dir, _, _ = fake_catalog
    mods = catalog_modules(load_catalog(str(cat_path)))
    root = Path(modules_dir) if isinstance(modules_dir, str) else modules_dir
    # 需要把 modules_dir 传进去
    from agent.module_catalog import install_module_code
    result = install_module_code("mfgcad", modules=mods, modules_dir=root)
    assert (result / "module.yaml").exists()
    assert is_module_installed("mfgcad", modules_dir=root)


def test_install_module_not_in_catalog(fake_catalog):
    cat_path, modules_dir, _, _ = fake_catalog
    mods = catalog_modules(load_catalog(str(cat_path)))
    from agent.module_catalog import install_module_code
    with pytest.raises(ModuleNotFoundError):
        install_module_code("nonexistent", modules=mods, modules_dir=modules_dir)


def test_is_builtin_module():
    from agent.module_loader import is_builtin_module
    assert is_builtin_module("scholarforge")
    assert is_builtin_module("mfgcad")
    assert not is_builtin_module("nonexistent")
