"""W-L5 工作区事实块 — git/项目现场探测（fail-open）。

判据：**有无 git 工作区**，不是「是否 coding 姿态」。
探测失败 / 无 git → 返回空串，永不抛错、永不挡消息。

cwd 来源约定（与 system_prompt 注入层一致）：
  * 有 ``TERMINAL_CWD``（gateway）→ 用它
  * 交互式平台且无 TERMINAL_CWD → 进程 cwd
  * messaging/未知且无 TERMINAL_CWD → 跳过（避免探测安装目录的 git 区）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


def _find_git_root_local(start: Path) -> Optional[Path]:
    current = Path(start)
    try:
        current = current.resolve()
    except OSError:
        return None
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _project_markers() -> tuple:
    try:
        from agent.prompt_builder import _PROJECT_MARKERS
        return tuple(_PROJECT_MARKERS)
    except Exception:
        return (
            "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
            "AGENTS.md", "CLAUDE.md", ".cursorrules",
        )

# 与 A′ 白名单同集：这些表面允许在无 TERMINAL_CWD 时用进程 cwd
_INTERACTIVE_PLATFORMS = frozenset({
    "cli", "web", "desktop", "tui", "acp", "local", "api", "api_server",
})

_CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".c", ".cpp", ".h",
    ".rb", ".php", ".cs", ".swift", ".kt", ".scala", ".vue", ".svelte",
})
_VERIFY_TARGETS = ("test", "tests", "lint", "typecheck", "check", "build", "fmt", "format")
_MAX_VERIFY_COMMANDS = 6
_MAX_FACT_FILE_BYTES = 256 * 1024
_GIT_TIMEOUT = 2.5
_CONTEXT_FILES = ("AGENTS.md", "CLAUDE.md", ".cursorrules")
_PY_LOCKFILES = (("uv.lock", "uv"), ("poetry.lock", "poetry"), ("Pipfile.lock", "pipenv"))
_JS_LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"), ("bun.lockb", "bun"), ("bun.lock", "bun"),
    ("yarn.lock", "yarn"), ("package-lock.json", "npm"),
)


def resolve_workspace_cwd(platform: "str | None" = None) -> "str | None":
    """返回探测用 cwd；``"SKIP"`` 表示明确不注入。"""
    term = (os.getenv("TERMINAL_CWD") or "").strip()
    if term:
        return term
    plat = (platform or "").strip().lower()
    if plat in _INTERACTIVE_PLATFORMS:
        return None  # 调用方用进程 cwd
    return "SKIP"


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if proc.returncode != 0:
            return ""
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def _read_small(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > _MAX_FACT_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _parse_status(porcelain: str) -> tuple[dict, dict]:
    branch: dict = {}
    counts = {"staged": 0, "modified": 0, "untracked": 0, "conflicts": 0}
    for line in (porcelain or "").splitlines():
        if line.startswith(("# branch.head", "# branch.upstream")):
            parts = line.split(maxsplit=2)
            if len(parts) >= 3:
                branch[parts[1].removeprefix("branch.")] = parts[-1]
        elif line.startswith("# branch.ab"):
            parts = line.split()
            if len(parts) >= 4:
                branch["ahead"], branch["behind"] = parts[2].lstrip("+"), parts[3].lstrip("-")
        elif line.startswith(("1 ", "2 ")):
            xy = line.split(maxsplit=2)
            if len(xy) >= 2:
                counts["staged"] += xy[1][0] != "."
                counts["modified"] += xy[1][1] != "."
        elif line.startswith(("u ", "? ")):
            key = "conflicts" if line[0] == "u" else "untracked"
            counts[key] += 1
    return branch, counts


@dataclass
class ProjectFacts:
    manifests: List[str]
    package_managers: List[str]
    verify_commands: List[str]
    context_files: List[str]


def detect_project_facts(root: Path) -> ProjectFacts:
    verify: List[str] = []
    if (root / "scripts" / "run_tests.sh").is_file():
        verify.append("scripts/run_tests.sh")
    if (root / "package.json").is_file():
        try:
            scripts = json.loads(_read_small(root / "package.json") or "{}").get("scripts") or {}
        except (json.JSONDecodeError, AttributeError):
            scripts = {}
        js_pm = next((pm for lock, pm in _JS_LOCKFILES if (root / lock).is_file()), "npm")
        verify.extend(f"{js_pm} run {name}" for name in _VERIFY_TARGETS if name in scripts)
    if (root / "pytest.ini").is_file() or "[tool.pytest" in _read_small(root / "pyproject.toml"):
        verify.append("pytest")
    makefile = _read_small(root / "Makefile")
    verify.extend(
        f"make {name}" for name in _VERIFY_TARGETS
        if makefile and re.search(rf"^{re.escape(name)}\s*:", makefile, re.MULTILINE)
    )
    manifests = [m for m in _project_markers() if m not in _CONTEXT_FILES and (root / m).is_file()]
    pms = list(dict.fromkeys(pm for lock, pm in (*_PY_LOCKFILES, * _JS_LOCKFILES) if (root / lock).is_file()))
    ctx = [c for c in _CONTEXT_FILES if (root / c).is_file()]
    return ProjectFacts(
        manifests=manifests,
        package_managers=pms,
        verify_commands=list(dict.fromkeys(verify))[:_MAX_VERIFY_COMMANDS],
        context_files=ctx,
    )


def build_workspace_block(cwd: "str | os.PathLike | None" = None, platform: "str | None" = None) -> str:
    """工作区事实块；无 git 区或探测失败 → 空串。

    ``cwd`` 显式传入时优先使用（测试/调用方指定工作区）；否则按 platform + TERMINAL_CWD 决策。
    """
    try:
        if cwd is not None:
            root_probe = Path(cwd).expanduser()
        else:
            resolved = resolve_workspace_cwd(platform)
            if resolved == "SKIP":
                return ""
            if resolved is None:
                root_probe = Path.cwd()
            else:
                root_probe = Path(resolved).expanduser()
        if not root_probe.is_dir():
            return ""
        git_root = _find_git_root_local(root_probe)
        if git_root is None:
            return ""
        lines = [
            "Workspace (snapshot at session start — re-check with git before acting on it):",
            f"- Root: {git_root}",
        ]
        branch, counts = _parse_status(_git(git_root, "status", "--porcelain=2", "--branch"))
        head = branch.get("head", "")
        if head == "(detached)":
            lines.append("- Branch: (detached HEAD)")
        elif head:
            upstream = f" → {branch['upstream']}" if branch.get("upstream") else ""
            ahead, behind = branch.get("ahead", "0"), branch.get("behind", "0")
            ab = f" (ahead {ahead}, behind {behind})" if upstream and (ahead, behind) != ("0", "0") else ""
            lines.append(f"- Branch: {head}{upstream}{ab}")
        dirty = [f"{n} {label}" for label, n in counts.items() if n]
        lines.append(f"- Status: {', '.join(dirty) if dirty else 'clean'}")
        facts = detect_project_facts(git_root)
        if facts.manifests:
            mgr = f" ({'/'.join(facts.package_managers)})" if facts.package_managers else ""
            lines.append(f"- Project: {', '.join(facts.manifests[:6])}{mgr}")
        if facts.verify_commands:
            lines.append(f"- Verify: {'; '.join(facts.verify_commands)}")
        if facts.context_files:
            lines.append(f"- Context files: {', '.join(facts.context_files)}")
        return "\n".join(lines)
    except Exception:
        return ""
