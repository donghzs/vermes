"""Local literature-library indexer.

Turns a user-prepared folder (PDFs, a BibTeX/RIS export, …) into a searchable
index so the agent can cite from it and verify citations against the *actual*
papers the user already owns.

Design choices (per user decision: "元数据 + 懒抽全文"):

* **Index time is fast and dependency-free.** We extract *metadata* only
  (from sidecar ``.bib``/``.ris`` files, or a filename heuristic). We do **not**
  parse PDF bodies during indexing.
* **Full text is extracted lazily**, on first need, via :func:`ensure_fulltext`
  (which uses PyMuPDF / ``fitz`` — already present in the venv). Once extracted
  it is cached in the index DB so repeat calls are cheap.
* **Incremental.** Each file is keyed by a sha256 + mtime, so re-indexing a
  10k-PDF library only touches changed files.
* **Read-only & safe.** We never execute anything in the folder and path
  matching is confined to the configured root.

The index lives in ``~/.vermes/literature_local_index.db`` (SQLite).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_INDEX_DB = os.path.join(os.path.expanduser("~/.vermes"), "literature_local_index.db")

# Reference/metadata files whose *entries* become records.
METADATA_EXTS = (".bib", ".ris", ".enl")
# Paper files we can index (and, for PDF, lazily extract full text from).
PAPER_EXTS = (".pdf", ".txt", ".html", ".htm", ".doc", ".docx", ".epub")
# How many PDF pages to pull for lazy full-text (quotes usually live up front).
_PDF_PAGES = 20


# ── db helpers ───────────────────────────────────────────────────────────────

def ensure_db() -> None:
    d = os.path.dirname(_INDEX_DB)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    with sqlite3.connect(_INDEX_DB) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY,
                source_id TEXT,
                path TEXT,
                rel TEXT,
                sha TEXT,
                mtime REAL,
                kind TEXT,
                title TEXT,
                authors TEXT,
                year TEXT,
                doi TEXT,
                journal TEXT,
                abstract TEXT,
                has_fulltext INTEGER DEFAULT 0,
                fulltext TEXT DEFAULT '',
                indexed_at REAL
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_files_src ON files(source_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files(path)")


def drop_library_index(lib_id: str) -> None:
    if not os.path.exists(_INDEX_DB):
        return
    with sqlite3.connect(_INDEX_DB) as con:
        con.execute("DELETE FROM files WHERE source_id=?", (lib_id,))


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _rows_for(lib_id: str) -> List[Dict[str, Any]]:
    ensure_db()
    with sqlite3.connect(_INDEX_DB) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute("SELECT * FROM files WHERE source_id=?", (lib_id,))
        return [dict(r) for r in cur.fetchall()]


# ── metadata parsing ──────────────────────────────────────────────────────────

def _clean_braces(s: str) -> str:
    return (s or "").replace("{", "").replace("}", "").strip()


def parse_bibtex(text: str) -> List[Dict[str, str]]:
    """Parse BibTeX into a list of metadata dicts.

    Uses a brace-balanced scanner (not a rigid regex) so it tolerates both
    single-line and multi-line entries, nested braces, and the occasional
    stray/unbalanced brace found in hand-edited ``.bib`` exports.
    """
    out: List[Dict[str, str]] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] == "@":
            j = i + 1
            while j < n and text[j].isalnum():
                j += 1
            if j >= n or text[j] != "{":
                i += 1
                continue
            # entry key = up to first comma
            k = j + 1
            while k < n and text[k] != ",":
                k += 1
            # scan the balanced body
            depth = 1
            p = k + 1
            start = p
            while p < n and depth > 0:
                c = text[p]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
                p += 1
            body = text[start:p]
            fields: Dict[str, str] = {}
            for fm in re.finditer(r"(\w+)\s*=\s*\{([^{}]*)\}", body):
                fields.setdefault(fm.group(1).lower(), _clean_braces(fm.group(2)))
            for fm in re.finditer(r'(\w+)\s*=\s*"([^"]*)"', body):
                fields.setdefault(fm.group(1).lower(), fm.group(2).strip())
            for fm in re.finditer(r"(\w+)\s*=\s*([0-9]+)", body):
                fields.setdefault(fm.group(1).lower(), fm.group(2).strip())
            out.append(
                {
                    "title": fields.get("title", ""),
                    "authors": fields.get("author", ""),
                    "year": (fields.get("year", "") or "")[:4],
                    "doi": fields.get("doi", ""),
                    "journal": fields.get("journal") or fields.get("booktitle", ""),
                    "abstract": fields.get("abstract", ""),
                }
            )
            i = p + 1
            continue
        i += 1
    return out


def parse_ris(text: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    cur: Dict[str, List[str]] = {}
    for line in text.splitlines():
        if line.startswith("TY"):
            cur = {}
        elif line.startswith("ER"):
            if cur:
                out.append(_ris_record(cur))
            cur = {}
            continue
        mm = re.match(r"([A-Z][A-Z0-9])\s*-\s*(.*)", line)
        if mm and cur is not None:
            cur.setdefault(mm.group(1), []).append(mm.group(2).strip())
    if cur:
        out.append(_ris_record(cur))
    return out


def _ris_record(cur: Dict[str, List[str]]) -> Dict[str, str]:
    def first(*tags: str) -> str:
        for t in tags:
            v = cur.get(t)
            if v:
                return v[0]
        return ""

    authors = cur.get("AU") or cur.get("A1") or []
    return {
        "title": first("TI", "T1"),
        "authors": " and ".join(authors),
        "year": (first("PY") or "")[:4],
        "doi": first("DO"),
        "journal": first("JO", "T2", "JA"),
        "abstract": first("AB"),
    }


def _meta_from_filename(path: str) -> Dict[str, str]:
    base = os.path.splitext(os.path.basename(path))[0]
    ym = re.search(r"(19|20)\d{2}", base)
    year = ym.group(0) if ym else ""
    title = re.sub(r"(19|20)\d{2}", "", base)
    title = re.sub(r"[_\-]+", " ", title).strip()
    return {
        "title": title,
        "authors": "",
        "year": year,
        "doi": "",
        "journal": "",
        "abstract": "",
    }


def _parse_metadata_file(path: str) -> List[Dict[str, str]]:
    try:
        text = open(path, "r", encoding="utf-8", errors="ignore").read()
    except OSError:
        return []
    ext = os.path.splitext(path)[1].lower()
    if ext == ".bib":
        return parse_bibtex(text)
    if ext == ".ris" or ext == ".enl":
        return parse_ris(text)
    return []


def _meta_for_paper(path: str, root: str) -> Dict[str, str]:
    stem, _ = os.path.splitext(path)
    for side in (stem + ".bib", stem + ".ris"):
        if os.path.exists(side):
            recs = _parse_metadata_file(side)
            if recs:
                return recs[0]
    return _meta_from_filename(path)


# ── indexing ────────────────────────────────────────────────────────────────

def _find_row(lib_id: str, path: str, title: str):
    with sqlite3.connect(_INDEX_DB) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT * FROM files WHERE source_id=? AND path=? AND lower(title)=lower(?)",
            (lib_id, path, (title or "").strip()),
        )
        return cur.fetchone()


def _upsert(lib_id: str, path: str, rel: str, meta: Dict[str, str], kind: str,
            force: bool) -> str:
    """Insert or update a record. Returns 'skipped' | 'updated' | 'inserted'."""
    sha = _sha256(path)
    mtime = os.path.getmtime(path) if os.path.exists(path) else 0.0
    row = _find_row(lib_id, path, meta.get("title", ""))
    if row is not None and not force:
        if row["sha"] == sha and abs((row["mtime"] or 0) - mtime) < 1.0:
            return "skipped"
    title = (meta.get("title") or "").strip()
    authors = (meta.get("authors") or "").strip()
    year = str(meta.get("year") or "").strip()[:4]
    doi = (meta.get("doi") or "").strip()
    journal = (meta.get("journal") or "").strip()
    abstract = (meta.get("abstract") or "").strip()
    reset_ft = force or row is None or row["sha"] != sha
    with sqlite3.connect(_INDEX_DB) as con:
        if row is None:
            con.execute(
                """INSERT INTO files
                   (source_id, path, rel, sha, mtime, kind, title, authors, year,
                    doi, journal, abstract, has_fulltext, fulltext, indexed_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (lib_id, path, rel, sha, mtime, kind, title, authors, year,
                 doi, journal, abstract, 0 if reset_ft else (row["has_fulltext"] or 0),
                 "" if reset_ft else (row["fulltext"] or ""), time.time()),
            )
            return "inserted"
        con.execute(
            """UPDATE files SET sha=?, mtime=?, kind=?, title=?, authors=?, year=?,
               doi=?, journal=?, abstract=?, has_fulltext=?, fulltext=?, indexed_at=?
               WHERE id=?""",
            (sha, mtime, kind, title, authors, year, doi, journal, abstract,
             0 if reset_ft else (row["has_fulltext"] or 0),
             "" if reset_ft else (row["fulltext"] or ""), time.time(), row["id"]),
        )
        return "updated"


def index_library(lib_id: str, root: str, force: bool = False) -> Dict[str, Any]:
    """Walk *root*, index every paper/reference file. Fast (metadata only).

    Returns a summary dict: scanned / indexed / updated / removed / errors.
    """
    ensure_db()
    if not os.path.isdir(root):
        return {"lib_id": lib_id, "error": f"路径不存在: {root}"}

    signatures: set = set()
    scanned = indexed = updated = errors = 0

    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            full = os.path.join(dirpath, fn)
            try:
                if ext in METADATA_EXTS:
                    for r in _parse_metadata_file(full):
                        if not (r.get("title") or "").strip():
                            continue
                        scanned += 1
                        sig = (full, (r.get("title") or "").strip().lower())
                        if sig in signatures:
                            continue
                        signatures.add(sig)
                        st = _upsert(lib_id, full, os.path.relpath(full, root), r,
                                     "reference", force)
                        if st == "inserted":
                            indexed += 1
                        elif st == "updated":
                            updated += 1
                elif ext in PAPER_EXTS:
                    scanned += 1
                    meta = _meta_for_paper(full, root)
                    title = (meta.get("title") or "").strip() or os.path.basename(full)
                    sig = (full, title.lower())
                    if sig in signatures:
                        continue
                    signatures.add(sig)
                    st = _upsert(lib_id, full, os.path.relpath(full, root), meta,
                                 "paper", force)
                    if st == "inserted":
                        indexed += 1
                    elif st == "updated":
                        updated += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.debug("index_library skip %s: %s", full, exc)

    # drop rows whose files are gone or no longer in this run
    removed = 0
    for row in _rows_for(lib_id):
        if (row["path"], (row["title"] or "").lower()) not in signatures:
            with sqlite3.connect(_INDEX_DB) as con:
                con.execute("DELETE FROM files WHERE id=?", (row["id"],))
            removed += 1

    return {
        "lib_id": lib_id,
        "scanned": scanned,
        "indexed": indexed,
        "updated": updated,
        "removed": removed,
        "errors": errors,
    }


# ── lazy full text ────────────────────────────────────────────────────────────

def extract_pdf_text(path: str) -> str:
    """Extract up to _PDF_PAGES of text from a PDF. Best-effort, '' on failure."""
    if not path.lower().endswith(".pdf"):
        # plain-text-ish files: read directly (capped)
        if path.lower().endswith((".txt", ".html", ".htm")):
            try:
                return open(path, "r", encoding="utf-8", errors="ignore").read(200_000)
            except OSError:
                return ""
        return ""
    try:
        import fitz  # PyMuPDF — already in the venv
    except Exception as exc:  # noqa: BLE001
        logger.debug("extract_pdf_text: fitz 不可用: %s", exc)
        return ""
    try:
        doc = fitz.open(path)
        try:
            n = min(len(doc), _PDF_PAGES)
            return "\n".join(doc[i].get_text() for i in range(n))
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("extract_pdf_text failed %s: %s", path, exc)
        return ""


def ensure_fulltext(lib_id: str, path: str) -> str:
    """Return cached full text, extracting + caching it on first need."""
    row = _find_row(lib_id, path, _title_for_path(lib_id, path))
    if row is not None and row["has_fulltext"] and row["fulltext"]:
        return row["fulltext"]
    text = extract_pdf_text(path)
    if text and row is not None:
        with sqlite3.connect(_INDEX_DB) as con:
            con.execute(
                "UPDATE files SET has_fulltext=1, fulltext=? WHERE id=?",
                (text, row["id"]),
            )
    return text


def _title_for_path(lib_id: str, path: str) -> str:
    with sqlite3.connect(_INDEX_DB) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT title FROM files WHERE source_id=? AND path=? LIMIT 1",
            (lib_id, path),
        )
        r = cur.fetchone()
        return r["title"] if r else ""


# ── search & verify ───────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", " ", s)
    return s.strip()


def _tokens(s: str) -> set:
    return {t for t in _norm(s).split() if len(t) >= 2}


def _score(rec: Dict[str, Any], q_tokens: set, q_norm: str) -> float:
    title = _norm(rec.get("title", ""))
    if not title:
        return 0.0
    if q_norm and q_norm in title:
        return 1.0
    if q_tokens and title:
        t_tokens = _tokens(rec.get("title", ""))
        if t_tokens:
            ov = len(q_tokens & t_tokens) / max(1, len(q_tokens))
            if ov >= 0.5:
                return max(0.6, ov)
    a = _norm(rec.get("authors", ""))
    if q_norm and q_norm in a:
        return 0.7
    return 0.0


def _paper_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": row.get("title", ""),
        "authors": [a.strip() for a in (row.get("authors") or "").split(" and ") if a.strip()],
        "year": str(row.get("year") or ""),
        "journal": row.get("journal", ""),
        "abstract": row.get("abstract", ""),
        "doi": row.get("doi", ""),
        "url": row.get("path", ""),
        "source": row.get("source_id", ""),
        "local_path": row.get("path", ""),
        "has_fulltext": bool(row.get("has_fulltext")),
    }


def search_local(lib_id: str, query: str, limit: int = 10) -> List[Dict[str, Any]]:
    q_norm = _norm(query)
    q_tokens = _tokens(query)
    if not q_norm:
        return []
    pat = f"%{query}%"
    rows = _rows_for(lib_id)
    scored = []
    for r in rows:
        # cheap pre-filter
        hay = f"{r.get('title','')} {r.get('authors','')}"
        if q_norm not in _norm(hay) and not (r.get("has_fulltext") and q_norm in _norm(r.get("fulltext", ""))):
            continue
        sc = _score(r, q_tokens, q_norm)
        if sc > 0:
            scored.append((sc, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [_paper_from_row(r) for _sc, r in scored[:limit]]


def search_all_local(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    ensure_db()
    with sqlite3.connect(_INDEX_DB) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute("SELECT DISTINCT source_id FROM files")
        ids = [r["source_id"] for r in cur.fetchall()]
    out: List[Dict[str, Any]] = []
    for sid in ids:
        out.extend(search_local(sid, query, limit))
    # re-rank globally by title score
    return out[:limit]


def verify_local(title: str, authors: str = "", year: str = "") -> Dict[str, Any]:
    """Strong verification signal: the user literally has the paper locally.

    Returns ``{"verified": bool, "confidence": float, "source": str,
    "hit_title": str, "hit_path": str}``.
    """
    q_norm = _norm(title)
    q_tokens = _tokens(title)
    if not q_norm:
        return {"verified": False, "confidence": 0.0, "source": "local_library"}
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for row in _rows_for_all():
        sc = _score(row, q_tokens, q_norm)
        if sc <= 0:
            continue
        # corroborate with authors / year
        bonus = 0.0
        a_norm = _norm(authors)
        if a_norm and a_norm in _norm(row.get("authors", "")):
            bonus += 0.1
        if year and str(year).strip() == str(row.get("year") or "").strip():
            bonus += 0.1
        sc = min(1.0, sc + bonus)
        if sc > best_score:
            best_score = sc
            best = row
    if best is not None and best_score >= 0.6:
        return {
            "verified": True,
            "confidence": round(0.7 + 0.28 * best_score, 2),
            "source": "local_library",
            "hit_title": best.get("title", ""),
            "hit_path": best.get("path", ""),
        }
    return {"verified": False, "confidence": 0.0, "source": "local_library"}


def _rows_for_all() -> List[Dict[str, Any]]:
    ensure_db()
    with sqlite3.connect(_INDEX_DB) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute("SELECT * FROM files")
        return [dict(r) for r in cur.fetchall()]
