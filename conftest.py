# conftest.py — root-level pytest configuration
# Skip test modules that depend on upstream-only packages not in Vermes fork

# 中线：per-file 进程隔离（--isolate-files）。必须注册在 rootdir conftest：
# pytest 9 起非 rootdir 的 pytest_plugins 会告警/未来报错。
pytest_plugins = ["tests._isolate_plugin"]

collect_ignore_glob = [
    "tests/skills/test_fetch_transcript.py",
]
