"""Non-mock regression tests for the P0-3 structured jsonl audit log.

These exercise the REAL read/write code paths of ``tools.skills_hub``
(``append_audit_log`` -> jsonl, ``get_audit_entries`` -> parse), redirecting
``AUDIT_LOG`` to a temp file so ``~/.vermes`` is never touched.

Reverse-verified (R5): on the pre-P0-3 commit (``8c789ad5d9^``), every test
here fails — ``append_audit_log`` rejected the ``findings_summary`` kwarg and
``get_audit_entries`` did not exist — proving the suite actually guards the
P0-3 contract rather than passing by accident.
"""
from pathlib import Path


def _bind_tmp_audit_log(tmp_path):
    import tools.skills_hub as sh

    sh.AUDIT_LOG = Path(tmp_path) / "audit.log"
    return sh


def test_install_roundtrip_preserves_findings_and_scan_summary(tmp_path):
    _bind_tmp_audit_log(tmp_path)
    from tools.skills_hub import append_audit_log, get_audit_entries

    append_audit_log(
        "INSTALL", "demo-skill", "github", "community", "safe", "sha256:abc123",
        findings_summary=[{"pattern_id": "P1", "severity": "low", "category": "shell",
                           "file": "run.sh", "line": 3, "description": "uses curl"}],
        scan_summary="1 finding, low risk",
    )
    entries = get_audit_entries(skill_name="demo-skill", limit=20)
    assert len(entries) == 1
    assert entries[0]["verdict"] == "safe"
    assert entries[0]["findings"][0]["pattern_id"] == "P1"
    assert entries[0]["scan_summary"].startswith("1 finding")


def test_newest_first_ordering_and_blocked_findings(tmp_path):
    _bind_tmp_audit_log(tmp_path)
    from tools.skills_hub import append_audit_log, get_audit_entries

    append_audit_log(
        "INSTALL", "demo-skill", "github", "community", "safe", "h1",
        findings_summary=[{"pattern_id": "P1", "severity": "low", "category": "shell",
                           "file": "a", "line": 1, "description": "x"}],
    )
    append_audit_log(
        "BLOCKED", "demo-skill", "github", "community", "dangerous", "3_findings",
        findings_summary=[{"pattern_id": "P9", "severity": "high", "category": "exfil",
                           "file": "x.py", "line": 1, "description": "malicious"}],
        scan_summary="blocked: exfiltration pattern",
    )
    entries = get_audit_entries(skill_name="demo-skill", limit=20)
    assert len(entries) == 2
    assert entries[0]["action"] == "BLOCKED"
    assert entries[1]["action"] == "INSTALL"
    assert entries[0]["findings"][0]["pattern_id"] == "P9"


def test_legacy_plain_text_line_degrades_gracefully(tmp_path):
    sh = _bind_tmp_audit_log(tmp_path)
    from tools.skills_hub import get_audit_entries

    # Simulate a v2.4.2-era plain-text audit line written by older code.
    with open(sh.AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write("2026-01-01T00:00:00Z INSTALL oldskill official:trusted safe old-note\n")

    entries = get_audit_entries(limit=50)
    legacy = [e for e in entries if e.get("skill") == "oldskill"]
    assert legacy, "legacy text line was not parsed"
    assert legacy[0]["verdict"] == "safe"
    assert legacy[0]["extra"] == "old-note"
    assert legacy[0]["source"] == "official"
    assert legacy[0]["trust_level"] == "trusted"


def test_missing_file_is_fail_open(tmp_path):
    _bind_tmp_audit_log(tmp_path)
    from tools.skills_hub import get_audit_entries

    # AUDIT_LOG path does not exist yet -> fail-open returns empty list.
    assert get_audit_entries(skill_name="nope") == []
