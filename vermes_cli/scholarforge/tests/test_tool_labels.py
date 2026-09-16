"""
工具中文名守卫测试 —— 防止「新增工具忘了配中文名」和「改了名字两边不同步」。

背景
----
工具的内部 name（如 `scholarforge_check_stats`）是 LLM function-calling 与后端调度的
**标识符**，不能改；但直接显示给非技术用户就是天书。显示层的中文名维护在
`frontend/src/utils/toolLabels.js`（单一真源）。

这个测试做的事：从 `vermes_cli/scholarforge/**.py` 里扫描**真实的** registry.register 调用，
拿到全部论文工具，再逐个去前端那个文件里比对。于是：

  · 新增工具而没配中文名        → 测试挂（不用靠自觉）
  · 中文名写错 / 拼错工具名     → 测试挂
  · 工具已删除但中文名还留着    → 测试挂（残留键）
  · 中文名只是把英文前缀一去了事 → 测试挂（防"假本地化"）

为什么用源码扫描而不是 import 注册表：scholarforge 通过 `host_api.ProxyRegistry` 转发注册，
测试环境没有宿主注入，import 进来注册表是空的（实测 `_snapshot_entries()` 返回 0 条）。
而做法是在测试里注入假宿主 + reload 大模块，会污染全局状态、影响同会话其它测试 ——
不值得。**静态扫描无副作用，且覆盖所有 .py（不管工具注册在哪个文件里）**。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

def _find_repo_root() -> Path:
    """向上找仓库根（以「存在 frontend/src」为锚）。

    🔴 别再手写 parents[N] —— 层级一改就静默指到别的目录去（我第一版写成 parents[4]，
    直接算到 /Users/dongzusheng/Projects/frontend/... 去了）。用锚点特征找，层级变化不影响。
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "frontend" / "src").is_dir():
            return parent
    return here.parents[3]  # 兜底：退回归档约定


REPO_ROOT = _find_repo_root()
SF_DIR = REPO_ROOT / "vermes_cli" / "scholarforge"
LABELS_JS = REPO_ROOT / "frontend" / "src" / "utils" / "toolLabels.js"
TOOLBOX_VUE = (
    REPO_ROOT / "frontend" / "src" / "components" / "scholar" / "ToolBox.vue"
)


def _grouped_tool_names() -> list[str]:
    """解析 ToolBox.vue 的 TOOL_GROUPS，返回组内出现的全部工具名（保留重复，便于查重）"""
    if not TOOLBOX_VUE.exists():
        return []
    src = TOOLBOX_VUE.read_text(encoding="utf-8")
    start = src.find("const TOOL_GROUPS")
    if start == -1:
        return []
    end = src.find("\n]", start)
    segment = src[start:end] if end != -1 else src[start:]
    return re.findall(r"'(scholarforge_[a-z0-9_]+)'", segment)

# registry.register(name="scholarforge_x", toolset="scholarforge", ...)
_REGISTER_RE = re.compile(r"registry\.register\((.*?)\n\s*\)", re.S)
_NAME_RE = re.compile(r"""name\s*=\s*["']([^"']+)["']""")
_TOOLSET_RE = re.compile(r"""toolset\s*=\s*["']([^"']+)["']""")

# 匹配 SCHOLARFORGE_LABELS 块里的 scholarforge_xxx: '中文名'
# 🔴 键的引号是**可选**的 —— JS 对象字面量里 `search: '找文献'` 与 `'search': '找文献'`
#    都合法，toolLabels.js 用的是无引号写法。第一版正则只认带引号的，导致全部漏匹配。
_LABEL_RE = re.compile(
    r"""['"]?(scholarforge_[a-z0-9_]+)['"]?\s*:\s*['"]([^'"]+)['"]"""
)


def _registered_scholarforge_tools() -> dict[str, str]:
    """扫描源码，返回 {工具名: 所在文件}（只取 toolset=scholarforge 的）"""
    found: dict[str, str] = {}
    for py in SF_DIR.rglob("*.py"):
        # 🔴 必须排除 tests/ 自身：本文件注释里就有 registry.register(...) 的示例，
        #    会被扫描器当成真实注册（第一版因此凭空多出一个 scholarforge_x）。
        if "tests" in py.relative_to(SF_DIR).parts:
            continue
        try:
            src = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for block in _REGISTER_RE.findall(src):
            m_name = _NAME_RE.search(block)
            if not m_name:
                continue
            m_toolset = _TOOLSET_RE.search(block)
            if not m_toolset or m_toolset.group(1) != "scholarforge":
                continue
            found[m_name.group(1)] = str(py.relative_to(REPO_ROOT))
    return found


def _frontend_labels() -> dict[str, str]:
    """解析 toolLabels.js 里的论文工具中文名"""
    src = LABELS_JS.read_text(encoding="utf-8")
    # 只取 SCHOLARFORGE_LABELS 段，避免把 GENERAL_LABELS 混进来
    start = src.find("SCHOLARFORGE_LABELS")
    end = src.find("export const TOOL_NAME_MAP")
    segment = src[start:end] if start != -1 and end != -1 else src
    return dict(_LABEL_RE.findall(segment))


@unittest.skipUnless(LABELS_JS.exists(), f"前端标签文件不存在: {LABELS_JS}")
class TestScholarforgeToolLabels(unittest.TestCase):
    """论文工具中文名的完整性守卫"""

    @classmethod
    def setUpClass(cls):
        cls.tools = _registered_scholarforge_tools()
        cls.labels = _frontend_labels()

    def test_registry_scan_finds_tools(self):
        """守卫本身要有效：扫不到工具说明正则失效了，不是"没有工具" """
        self.assertGreater(
            len(self.tools), 20,
            f"只扫到 {len(self.tools)} 个论文工具，扫描正则可能已失效",
        )

    def test_every_tool_has_chinese_label(self):
        """🔴 核心：每个工具都必须有中文名"""
        missing = sorted(n for n in self.tools if n not in self.labels)
        self.assertEqual(
            [], missing,
            "以下论文工具缺少中文名，请到 frontend/src/utils/toolLabels.js 补上：\n  "
            + "\n  ".join(missing),
        )

    def test_no_stale_labels(self):
        """工具已删/改名，中文名却还留着 → 提示清理"""
        stale = sorted(n for n in self.labels if n not in self.tools)
        self.assertEqual([], stale, f"中文名对应的工具已不存在，请删除：{stale}")

    def test_labels_non_empty_and_chinese(self):
        """中文名必须真的含中文（防止留空或写成英文占位）"""
        for name, label in self.labels.items():
            self.assertTrue(label.strip(), f"{name} 的中文名为空")
            self.assertTrue(
                any("\u4e00" <= ch <= "\u9fff" for ch in label),
                f"{name} 的中文名「{label}」不含中文字符",
            )

    def test_labels_not_lazy_prefix_strip(self):
        """防"假本地化"：中文名不能只是把 scholarforge_ 前缀一去了事"""
        for name, label in self.labels.items():
            stripped = name.replace("scholarforge_", "")
            self.assertNotEqual(
                label, stripped,
                f"{name} 的中文名就是去掉前缀的英文名「{label}」，等于没翻译",
            )

    def test_labels_unique(self):
        """中文名不能撞车 —— 两个工具同名用户会分不清"""
        seen: dict[str, str] = {}
        for name, label in self.labels.items():
            self.assertNotIn(label, seen, f"中文名「{label}」被 {seen.get(label)} 和 {name} 同时使用")
            seen[label] = name


@unittest.skipUnless(TOOLBOX_VUE.exists(), f"工具箱组件不存在: {TOOLBOX_VUE}")
class TestToolBoxGrouping(unittest.TestCase):
    """工具箱分组的完整性守卫

    没入组的工具会掉进「其他」组。2026-09-16 实测：28 个工具里曾有 5 个落在「其他」，
    等于分组形同虚设 —— 用户找「查看章节」得在一堆杂项里翻。加这条守卫防复发。
    """

    @classmethod
    def setUpClass(cls):
        cls.grouped = _grouped_tool_names()
        cls.tools = _registered_scholarforge_tools()

    def test_group_scan_works(self):
        """守卫本身要有效：解析不到说明锚点变了"""
        self.assertGreater(len(self.grouped), 20, "解析到的工具名过少，TOOL_GROUPS 锚点可能已失效")

    def test_every_tool_is_grouped(self):
        """每个工具都必须归到某个组，不能靠「其他」兜底"""
        ungrouped = sorted(set(self.tools) - set(self.grouped))
        self.assertEqual(
            [], ungrouped,
            "以下工具未归入任何分组，会掉进「其他」组：\n  " + "\n  ".join(ungrouped),
        )

    def test_no_tool_listed_twice(self):
        """同一个工具不能出现在两个组里（用户会以为是两个不同工具）"""
        dupes = sorted({n for n in self.grouped if self.grouped.count(n) > 1})
        self.assertEqual([], dupes, f"以下工具被分到了多个组：{dupes}")

    def test_no_grouped_name_that_does_not_exist(self):
        """组里写了已不存在的工具名 → 提示清理（否则那个位置永远空着）"""
        ghost = sorted(set(self.grouped) - set(self.tools))
        self.assertEqual([], ghost, f"分组里引用了不存在的工具：{ghost}")


@unittest.skipUnless(LABELS_JS.exists(), f"前端标签文件不存在: {LABELS_JS}")
class TestToolLabelHelper(unittest.TestCase):
    """映射表的双向一致性（工具名 ↔ 中文名一一对应）"""

    def test_coverage_is_bidirectional(self):
        """不仅是"每个工具都有中文名"，还要"每个中文名都有对应工具" """
        labels = _frontend_labels()
        tools = _registered_scholarforge_tools()
        self.assertEqual(set(tools), set(labels))


if __name__ == "__main__":
    unittest.main()
