# conftest.py — root-level pytest configuration
# Skip test modules that depend on upstream-only packages not in Vermes fork

collect_ignore_glob = [
    "tests/acp/*",
    "tests/acp_adapter/*",
    "tests/skills/test_fetch_transcript.py",
]
