"""Tests for the local literature-library feature (folder / USB).

Covers: config store, metadata indexing (.bib + filename heuristic), lazy
PDF full-text extraction (fitz), local search, local citation verification,
and registry bootstrap of LocalFileProvider.
"""

import os
import sys

import pytest

# Isolate the on-disk state (~/.vermes/*) from the developer's real libraries.
import agent.local_library_index as idx_mod
import agent.local_library_store as store_mod

_TMP_ROOT = None


def _make_lib(tmp_path):
    """Create a small fixture library: a .bib with 2 entries + a real PDF."""
    lib = tmp_path / "papers"
    lib.mkdir()
    bib = lib / "refs.bib"
    bib.write_text(
        """
@article{smith2020, title={Local cooperative learning in preschool},
  author={Smith, Jane and Doe, John}, year={2020}, journal={Edu Studies}, doi={10.1000/xyz}}
@inproceedings{lee2019, title={Outdoor construction play effects},
  author={Lee, Kai}, year={2019}, booktitle={ICCE}}
""",
        encoding="utf-8",
    )
    # a paper PDF addressed by filename heuristic (no sidecar)
    pdf_path = lib / "Zhang 2018 quantum review.pdf"
    try:
        import fitz

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 50), "quantum entanglement local review keyword")
        doc.save(str(pdf_path))
        doc.close()
        made_pdf = True
    except Exception:
        made_pdf = False
    return lib, made_pdf


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    global _TMP_ROOT
    _TMP_ROOT = tmp_path
    idx_db = tmp_path / "literature_local_index.db"
    src_json = tmp_path / "literature_local_sources.json"
    monkeypatch.setattr(idx_mod, "_INDEX_DB", str(idx_db))
    monkeypatch.setattr(store_mod, "_LOCAL_SOURCES_PATH", str(src_json))
    yield
    _TMP_ROOT = None


def test_add_rejects_missing_path():
    with pytest.raises(ValueError):
        store_mod.add_local_library("/nonexistent/path/xyz")


def test_add_and_index_and_search():
    lib, _ = _make_lib(_TMP_ROOT)
    rec = store_mod.add_local_library(str(lib), label="测试库")
    assert rec["id"].startswith("local_")
    assert os.path.isdir(rec["root"])

    summary = idx_mod.index_library(rec["id"], rec["root"])
    assert summary["scanned"] >= 3  # 2 bib entries + 1 pdf
    assert summary["indexed"] >= 3
    store_mod.touch_indexed(rec["id"], summary["indexed"])

    # search by bib title token
    hits = idx_mod.search_local(rec["id"], "cooperative learning", limit=5)
    assert any("cooperative learning" in (h["title"] or "").lower() for h in hits)

    # search by filename-derived paper
    hits2 = idx_mod.search_local(rec["id"], "quantum", limit=5)
    assert any("quantum" in (h["title"] or "").lower() for h in hits2)


def test_lazy_fulltext_extraction():
    lib, made_pdf = _make_lib(_TMP_ROOT)
    rec = store_mod.add_local_library(str(lib), label="全文库")
    idx_mod.index_library(rec["id"], rec["root"])
    pdf = str(lib / "Zhang 2018 quantum review.pdf")
    if made_pdf:
        text = idx_mod.ensure_fulltext(rec["id"], pdf)
        assert "quantum entanglement" in text
        row = idx_mod._find_row(rec["id"], pdf, idx_mod._title_for_path(rec["id"], pdf))
        assert row["has_fulltext"] == 1


def test_verify_local():
    lib, _ = _make_lib(_TMP_ROOT)
    rec = store_mod.add_local_library(str(lib), label="核实库")
    idx_mod.index_library(rec["id"], rec["root"])
    res = idx_mod.verify_local("Local cooperative learning in preschool",
                               authors="Smith, Jane and Doe, John", year="2020")
    assert res["verified"] is True
    assert res["source"] == "local_library"
    # a title not present locally should not verify
    miss = idx_mod.verify_local("Nonexistent paper about mars colonies", year="2099")
    assert miss["verified"] is False


def test_registry_bootstrap_and_provider_search():
    lib, _ = _make_lib(_TMP_ROOT)
    rec = store_mod.add_local_library(str(lib), label="注册库")
    idx_mod.index_library(rec["id"], rec["root"])

    from agent.literature_registry import bootstrap_local_providers, iter_local_providers

    bootstrap_local_providers()
    provs = iter_local_providers()
    assert any(p.name == rec["id"] for p in provs)

    from agent.literature_providers.local_file import LocalFileProvider

    prov = LocalFileProvider({"id": rec["id"], "root": rec["root"], "label": rec["label"]})
    assert prov.is_available() is True
    assert prov.supports_fulltext() is True
    r = prov.search("outdoor construction", limit=5)
    assert r["success"] is True
    assert any("outdoor" in (p["title"] or "").lower() for p in r["data"]["papers"])


def test_delete_removes_index():
    lib, _ = _make_lib(_TMP_ROOT)
    rec = store_mod.add_local_library(str(lib), label="删库")
    idx_mod.index_library(rec["id"], rec["root"])
    assert store_mod.delete_local_library(rec["id"]) is True
    assert store_mod.get_local_library(rec["id"]) is None
    # index rows dropped
    assert idx_mod._rows_for(rec["id"]) == []
