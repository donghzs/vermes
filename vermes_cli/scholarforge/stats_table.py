"""统计结果 → 学术三线表（2026-09-16 新增，第 28 个 scholarforge 工具的内核）。

背景（用户真实痛点）：写论文第三章（研究设计 / 结果分析）时，用户在 SPSS 里跑完分析，
手上是两样东西之一 ——
  ① SPSS 复制出来的**制表符分隔**的表格（如「成对样本检验」）；
  ② 一句写进正文的**统计结论**：`实验组显著高于对照组，t(58)=2.34, p=.023, d=0.61`。
要把它变成论文里规范的三线表，得手工抄一遍数字：费时、且**极易抄错**（且抄错后没人会
逐字复核，直接进论文）。

本模块只做**一件事**：把上面两种文本翻译成 GFM 表格 + 结构化统计量。
后续两条链路都是现成的，本模块**刻意不重复造**：
  - 三线表样式 → `export/full.py::_style_three_line_tables`（导出 Word 时自动应用，
    无 pandoc 也走 `_add_markdown_table` 分支，桌面端实际就是这条路径）；
  - 一致性校验 → `validators.py::check_statistics_consistency`（F↔η²、t↔d 等公式不重写）。

设计取舍：
  - **纯规则、零 LLM 调用**。统计量解析要的是确定性和秒级返回，不是"聪明"。
    同一个输入必须永远给出同一个结果，否则无法单测、用户也无法复核。
  - **宁可漏，不可错**。正则一律带词边界和明确分隔符；解析不出来的项就不输出，
    绝不用模糊匹配"猜"一个数。学术数据里，错值比缺值危险得多。
  - **数字保真**。表格里的数值原样透传，不做四舍五入、不补零、不改写量纲。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Optional

# ── 全角 → 半角 ────────────────────────────────────────────────────────
# 中文输入法下 `p＝.023`、`p＜0.01` 很常见，不归一化会整条漏掉。
_FULLWIDTH = str.maketrans({
    "＝": "=", "＜": "<", "＞": ">", "（": "(", "）": ")",
    "，": ",", "：": ":", "％": "%", "　": " ",
})


def _norm(text: str) -> str:
    return (text or "").translate(_FULLWIDTH)


# ── 行内统计量正则 ──────────────────────────────────────────────────────
# 刻意保持大小写敏感（学术写法固定为 M / SD / t / F / r / p），只有 Cohen's d 与
# eta 允许小写 —— 这样 `\bd\s*=` 不会去匹配 `df =`，`\bM\s*=` 不会匹配 `m =`。
_RE_F_DF = re.compile(r"\bF\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*=\s*(-?\d*\.?\d+)")
_RE_T_DF = re.compile(r"\bt\s*\(\s*(\d+)\s*\)\s*=\s*(-?\d*\.?\d+)")
_RE_F = re.compile(r"\bF\s*=\s*(-?\d*\.?\d+)")
_RE_T = re.compile(r"\bt\s*=\s*(-?\d*\.?\d+)")
_RE_R = re.compile(r"\br\s*=\s*(-?\.?\d+(?:\.\d+)?)")
_RE_ETA = re.compile(r"(?:partial\s+)?(?:η|eta)\s*(?:²|2|\^2)?\s*=\s*(\.?\d+(?:\.\d+)?)", re.I)
_RE_ETA_WORD = re.compile(r"eta\s+squared\s*=\s*(\.?\d+(?:\.\d+)?)", re.I)
_RE_D = re.compile(r"(?:cohen'?s\s+)?\bd\s*=\s*(-?\.?\d+(?:\.\d+)?)", re.I)
_RE_P = re.compile(r"\bp\s*(<=|>=|<|>|=)\s*(\.\d+|\d+\.\d+)", re.I)
_RE_M = re.compile(r"\bM\s*=\s*(-?\d+(?:\.\d+)?)")
_RE_SD = re.compile(r"\bSD\s*=\s*(-?\d+(?:\.\d+)?)")
_RE_N = re.compile(r"\b[nN]\s*=\s*(\d+)")
# 中文写法（用户论文是中文，正文里常写「均值」「标准差」）
_RE_M_CN = re.compile(r"均值\s*(?:=|\s)\s*(-?\d+(?:\.\d+)?)")
_RE_SD_CN = re.compile(r"标准差\s*(?:=|\s)\s*(-?\d+(?:\.\d+)?)")


def _f(s: str) -> Optional[float]:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_inline_stats(text: str) -> dict[str, Any]:
    """从一段话里提取统计量。解析不出来的字段不出现（不塞 None 占位）。

    返回的键与 `check_statistics_consistency` 的入参对齐（多余的键调用方自行剔除）。
    """
    t = _norm(text)
    out: dict[str, Any] = {}

    # F 与 t 先匹配带自由度的写法（信息更全），再退回只有统计量的写法。
    m = _RE_F_DF.search(t)
    if m:
        out["f_value"] = _f(m.group(3))
        out["df_between"] = int(m.group(1))
        out["df_error"] = int(m.group(2))
    else:
        m = _RE_F.search(t)
        if m:
            out["f_value"] = _f(m.group(1))

    m = _RE_T_DF.search(t)
    if m:
        out["t_value"] = _f(m.group(2))
        out["df"] = int(m.group(1))
    else:
        m = _RE_T.search(t)
        if m:
            out["t_value"] = _f(m.group(1))

    m = _RE_R.search(t)
    if m:
        out["r_value"] = _f(m.group(1))

    m = _RE_ETA.search(t) or _RE_ETA_WORD.search(t)
    if m:
        out["eta_squared"] = _f(m.group(1))

    m = _RE_D.search(t)
    if m:
        out["cohens_d"] = _f(m.group(1))

    m = _RE_P.search(t)
    if m:
        op, val = m.group(1), _f(m.group(2))
        if val is not None:
            # p<.001 这类给的是**上界**而非点值：记原样，数值取上界（保守），
            # 避免把 "<.001" 当成 "=.001" 去做等价性判断。
            out["p_value"] = val
            out["p_op"] = op
            out["p_raw"] = f"{op} {m.group(2)}"

    m = _RE_M.search(t) or _RE_M_CN.search(t)
    if m:
        out["mean"] = _f(m.group(1))

    m = _RE_SD.search(t) or _RE_SD_CN.search(t)
    if m:
        out["sd"] = _f(m.group(1))

    m = _RE_N.search(t)
    if m:
        out["n"] = int(m.group(1))

    return {k: v for k, v in out.items() if v is not None or k == "p_op"}


# ── 表格形态识别 ────────────────────────────────────────────────────────
def detect_table_rows(text: str) -> list[list[str]]:
    """识别「制表符 / 2+ 空格」分隔的表格（SPSS 复制出来的样子）。

    判定门槛（宁缺勿滥）：至少 2 行、最普遍的列数 ≥2、且**有 2 行以上列数一致**。
    单行文本、普通段落里的偶然双空格都不会被误判成表格。
    """
    raw_rows: list[list[str]] = []
    for line in _norm(text).splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        if "\t" in line:
            cells = [c.strip() for c in line.split("\t")]
        else:
            cells = [c.strip() for c in re.split(r"\s{2,}", line.strip())]
        # 🔴 **不要过滤空单元格**：SPSS 的表头行首常带一个空单元格（行标签列占位），
        # 例如 `\t均值\t标准差\tt` —— 表头 6 列、数据行 6 列；一旦把空串滤掉就变成
        # 5 vs 6，列数不一致 → 整张表被判为非表格（2026-09-16 实测栽在此处）。
        # 空单元格是**合法的列占位**，保真透传。
        if len(cells) >= 2:
            raw_rows.append(cells)

    if len(raw_rows) < 2:
        return []

    common, hits = Counter(len(r) for r in raw_rows).most_common(1)[0]
    if common < 2 or hits < 2:
        return []

    # 只保留与该列数一致的行（避免把表格上方的一句说明混进来）
    return [r for r in raw_rows if len(r) == common]


def to_gfm_table(rows: list[list[str]], caption: str = "") -> str:
    """把已分列的行转成 GFM 表格。首行视为表头。

    🔴 契约（来自 `export/full.py::_add_markdown_table`）：
      - 首行 = 表头，分隔行 `| --- |` 会被识别并跳过；
      - 单元格支持 `**bold**`；
      - 列数按最大列数补齐。
    导出 Word 时，`_style_three_line_tables` 会自动把表格刷成学术三线表。
    """
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)

    def _cell(c: str) -> str:
        return (c or "").replace("|", r"\|").strip()

    lines: list[str] = []
    if caption:
        lines.append(f"**{caption}**")
        lines.append("")
    header = [_cell(c) for c in rows[0]]
    header += [""] * (ncols - len(header))
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * ncols) + " |")
    for r in rows[1:]:
        cells = [_cell(c) for c in r]
        cells += [""] * (ncols - len(cells))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ── 汇总表 ──────────────────────────────────────────────────────────────
# 展示名（中文，给非技术用户看）+ 是否属于 check_statistics_consistency 的入参
_DISPLAY = [
    ("f_value", "F 值"),
    ("df_between", "自由度（组间）"),
    ("df_error", "自由度（误差）"),
    ("t_value", "t 值"),
    ("df", "自由度"),
    ("p_value", "p 值"),
    ("eta_squared", "η²（效应量）"),
    ("cohens_d", "Cohen's d（效应量）"),
    ("r_value", "r（相关系数）"),
    ("n", "样本量 n"),
    ("mean", "均值 M"),
    ("sd", "标准差 SD"),
]

# check_statistics_consistency 认得的键（多余的不要传进去）
_CONSISTENCY_KEYS = {
    "eta_squared", "cohens_d", "t_value", "df", "f_value",
    # df_between：从 `F(2,87)=4.12` 里解析出的组间自由度。传进去才能让 F↔η² 走正确公式
    # （见 validators.py 校验 3 的 2026-09-16 修正）——这正是「解析器 + 校验器」接力的价值。
    "df_between", "df_error", "p_value", "n_group1", "n_group2", "mean_diff", "pooled_sd",
}


def stats_summary_table(stats: dict[str, Any], caption: str = "") -> str:
    """把零散统计量排成「统计量 | 值」汇总表（没有分组数据时的兜底形态）。"""
    rows = [["统计量", "值"]]
    for key, label in _DISPLAY:
        if key not in stats:
            continue
        val = stats[key]
        if key == "p_value" and stats.get("p_raw"):
            text = f"{stats['p_raw']}"
        elif isinstance(val, float):
            text = f"{val:g}"
        else:
            text = str(val)
        rows.append([label, text])
    return to_gfm_table(rows, caption) if len(rows) > 1 else ""


def consistency_block(stats: dict[str, Any]) -> str:
    """复用已有校验器做一致性检查（不重写任何公式）。"""
    payload = {k: v for k, v in stats.items() if k in _CONSISTENCY_KEYS}
    if len(payload) < 2:
        return ""
    try:
        from vermes_cli.scholarforge.validators import (
            check_statistics_consistency,
            format_statistics_report,
        )
        return format_statistics_report(check_statistics_consistency(payload))
    except Exception as e:  # 校验失败绝不能拖垮制表 —— 用户要的第一产物是表
        return f"> ⚠️ 一致性校验未能完成（{type(e).__name__}），不影响上面的表格。"


# ── 主入口 ──────────────────────────────────────────────────────────────
_NO_MATCH_HINT = """未识别到统计量或表格。

**可以这样给我数据：**

① **直接粘贴 SPSS 的表格**（从 SPSS 结果窗口选中整块复制即可，制表符分隔就能认），
   例如「成对样本检验」「描述统计」这类表：

```
        均值    标准差    t       df   Sig.(双尾)
前测-后测  -.74    .83    -6.92    39     .000
```

② **或者直接写统计结论**（正文里怎么写就怎么贴）：

```
实验组后测显著高于对照组，t(58)=2.34, p=.023, d=0.61
```

③ 已识别但想更完整 → 把 `M`、`SD`、`n`、`F(2,87)`、`η²`、`r` 这些一并写上。"""


def build_stats_report(raw_text: str, caption: str = "") -> str:
    """把 SPSS 输出 / 统计结论文本变成「三线表 + 一致性校验」报告（纯函数，可单测）。"""
    text = _norm(raw_text or "").strip()
    if not text:
        return _NO_MATCH_HINT

    table_rows = detect_table_rows(text)
    stats = parse_inline_stats(text)

    parts: list[str] = ["## 🧮 统计结果 → 三线表\n"]

    if table_rows:
        parts.append(f"**识别到表格：{len(table_rows)} 行 × {len(table_rows[0])} 列**\n")
        parts.append(to_gfm_table(table_rows, caption))
        parts.append("")
    elif stats:
        # 没有表格结构，但有可提取的统计量 → 生成汇总表
        sub_caption = caption or "统计结果汇总"
        summary = stats_summary_table(stats, sub_caption)
        if summary:
            parts.append(summary)
            parts.append("")
    if not table_rows and not stats:
        return _NO_MATCH_HINT

    if table_rows or stats:
        parts.append(
            "> 💡 上表是 GFM 表格。导出 Word 时会**自动转成学术三线表**"
            "（上下线 1.5pt、表头线 0.75pt、无竖线、无内部横线）——"
            "把这套 markdown 连同章节正文一起交给 `scholarforge_export` 即可。\n"
        )

    if stats:
        block = consistency_block(stats)
        if block:
            parts.append("---\n")
            parts.append(block)

    return "\n".join(p for p in parts if p is not None)
