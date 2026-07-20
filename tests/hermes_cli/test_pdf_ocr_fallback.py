"""PDF extraction OCR-fallback + explicit-error tests (Chinese content).

Source-level fix for the "PDF 中文读不出" problem:
  - _extract_pdf now falls back to local OCR (pytesseract chi_sim+eng) when the
    text layer is empty (scanned/image-only PDFs).
  - When OCR is also unavailable, it raises a RuntimeError with an explicit
    Chinese message instead of silently returning "" (which previously led to a
    generic "No text extracted" and confused users into thinking the tool broke).

These tests run without a real tesseract binary, so they exercise the explicit
error path and the non-empty-text early-return path.
"""

import importlib

import pytest

# PyMuPDF (fitz) is a runtime dependency of the app but may be absent from the
# test venv; the OCR-fallback code path can only be exercised where it exists.
fitz = pytest.importorskip("fitz")


@pytest.fixture
def rag_module():
    return importlib.import_module("agent.rag_provider")


def test_extract_pdf_returns_text_layer(rag_module):
    """A normal PDF with a text layer returns its text, no OCR attempt.

    The point is to prove the fast text-layer path returns early (without
    invoking OCR). CJK glyph fidelity in the fixture depends on font embedding,
    so we assert the Latin content that unambiguously proves extraction ran.
    """
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Vermes 中文测试 hello world")
    raw = doc.tobytes()
    doc.close()
    out = rag_module._extract_pdf(raw)
    assert "Vermes" in out and "hello world" in out
    # Must NOT raise (i.e. did not fall into the OCR/error path).
    assert out.strip()


def test_extract_pdf_scanned_no_ocr_raises_explicit(rag_module, monkeypatch):
    """A scanned (empty text layer) PDF with no OCR engine raises an explicit
    Chinese RuntimeError rather than returning silently."""
    # Make sure pytesseract import fails to simulate a missing engine.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "pytesseract" or name.startswith("pytesseract."):
            raise ImportError("no pytesseract")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    import fitz
    doc = fitz.open()
    doc.new_page(width=200, height=200)  # no text layer
    raw = doc.tobytes()
    doc.close()

    with pytest.raises(RuntimeError) as exc:
        rag_module._extract_pdf(raw)
    assert "OCR" in str(exc.value)
    assert "扫描件" in str(exc.value) or "图片型" in str(exc.value)


def test_ingest_file_surfaces_explicit_error(rag_module, monkeypatch, tmp_path):
    """ingest_file must surface the explicit OCR error instead of a generic
    'No text extracted' when the PDF is a scanned image without an OCR engine."""
    import builtins
    import json
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "pytesseract" or name.startswith("pytesseract."):
            raise ImportError("no pytesseract")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    import fitz
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    raw = doc.tobytes()
    doc.close()
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(raw)

    # minimal provider instance (no DB wiring needed; we just call ingest_file
    # which will hit _extract_text -> _extract_pdf -> raise)
    provider = rag_module.RAGProvider()
    result = json.loads(provider.ingest_file(str(pdf_path)))
    assert "error" in result
    assert "OCR" in result["error"]
