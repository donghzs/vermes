"""Secret stores under VERMES_HOME are write-denied (read + write).

Aligned with upstream hermes #110464 (fix(security): write-deny HERMES_HOME secret
stores). ``vault/`` (key + ciphertext side by side) and ``browser-profile/`` (copied
cookies / Login Data) are secret material, not control files — so they must be
write-denied even though #45947 relaxed control files.

NOTE (known divergence): Vermes does NOT follow #45947's "control files stay
writable" semantics — ``auth.json`` / ``config.yaml`` / ``webhook_subscriptions.json``
are still write-denied in Vermes. That divergence is out of scope for this fix
(separate decision); these tests only pin the secret-store half.

Also out of scope (same drift, separate TAKEALONG item): ``auth/google_oauth.json``
and ``cache/bws_cache.json`` are read-denied but not yet write-denied in Vermes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import agent.file_safety as fs

SECRET_STORES = ("vault/vault.key", "browser-profile/Default/Cookies")


@pytest.fixture()
def vermes_layout(tmp_path, monkeypatch):
    """Profile VERMES_HOME plus a distinct global root, both patched."""
    root = tmp_path / "vermes_root"
    profile = root / "profiles" / "coder"
    profile.mkdir(parents=True)
    monkeypatch.setattr(fs, "_vermes_home_path", lambda: profile)
    monkeypatch.setattr(fs, "_vermes_root_path", lambda: root)
    return root, profile


def _touch(base: Path, rel: str) -> Path:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("dummy", encoding="utf-8")
    return p


def test_secret_stores_write_denied_on_profile_and_root(vermes_layout):
    root, profile = vermes_layout
    for base in (profile, root):
        for rel in SECRET_STORES:
            path = _touch(base, rel)
            assert fs.get_read_block_error(str(path)), f"fixture drift: not read-denied: {path}"
            assert fs.is_write_denied(str(path)), f"write allowed: {path}"


def test_secret_dir_itself_is_denied(vermes_layout):
    root, profile = vermes_layout
    for base in (profile, root):
        vault_dir = _touch(base, "vault/placeholder.txt").parent
        bp_dir = _touch(base, "browser-profile/placeholder.txt").parent
        assert fs.is_write_denied(str(vault_dir)), "vault/ dir itself must be write-denied"
        assert fs.is_write_denied(str(bp_dir)), "browser-profile/ dir itself must be write-denied"
        assert fs.get_read_block_error(str(vault_dir)), "vault/ dir itself must be read-denied"
        assert fs.get_read_block_error(str(bp_dir)), "browser-profile/ dir itself must be read-denied"


def test_lookalikes_outside_home_stay_writable(vermes_layout, tmp_path):
    for rel in SECRET_STORES:
        assert fs.is_write_denied(str(_touch(tmp_path / "myproject", rel))) is False
