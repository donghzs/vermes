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
        # 🔴 只去行尾换行符，**不要 rstrip()**。制表符分隔的表里，行尾空单元格是
        # **合法的列占位**（SPSS 的「组内」行，F / 显著性 两列本就是空的）。
        # rstrip() 会把结尾的 tab 一起吃掉 → 该行列数变少 → 被下面的列数过滤
        # **整行丢弃**。2026-09-17 实测：一张 4 行的 ANOVA 表只剩 2 行，
        # 「组内」「总计」两行直接消失，而 F 的分母自由度 df_error 正来自「组内」行。
        line = line.rstrip("\r\n")
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


# ── 表头驱动的统计量提取（让粘贴的 SPSS 表也能跑一致性校验）──────────────
# 背景（2026-09-17 实测）：detect_table_rows 早就能识别 SPSS 复制出来的表，
# 但 build_stats_report 只拿 parse_inline_stats 的结果去做校验 —— 而后者认的是
# 正文写法 `F(2,87)=4.12, p=.023`。SPSS 表里这些数是**躺在列里**的裸值（列名才叫
# 「F」「显著性」），于是粘贴表格时 stats 恒为空 → **一致性校验一次都没跑过**，
# 用户却以为校验过了。这里补上「按表头语义取列」的提取器。
#
# 取舍仍然是「宁可漏不可错」：认不出的表头就不提取，绝不猜。

def _canon_header(s: str) -> str:
    """表头归一化：去括号内容（「Sig.（双尾）」→「Sig.」）、去尾点、小写。"""
    s = _norm(s or "").strip().lower()
    s = re.sub(r"[（(][^（()）]*[)）]", "", s)
    return s.strip().rstrip(".").strip()


# 语义键 → 表头别名集合（SPSS 中英文 + 常见变体）
_HEADER_MAP: dict[str, set[str]] = {
    "f": {"f"},
    "p": {"显著性", "sig", "p"},
    "t": {"t"},
    "df": {"自由度", "df"},
    "ss": {"平方和", "ss"},
    "ms": {"均方", "ms"},
    "mean": {"平均值", "均值", "平均数", "mean", "m"},
    "sd": {"标准差", "标准偏差", "sd", "std. deviation"},
    "n": {"个案数", "样本量", "个数", "n"},
}

# 行标签 → 角色（ANOVA 表的「组之间 / 组内」决定 F 的两个自由度取自哪行）
_ROW_BETWEEN = {"组之间", "组间", "组间变异", "between groups", "between", "模型", "回归"}
_ROW_ERROR = {"组内", "组内变异", "within groups", "within", "误差", "error", "残差"}
# 独立样本 t 检验有两行：假定等方差（默认取这行）/ 不假定等方差（Welch，跳过）
_ROW_EQUAL_VAR = {"假定等方差", "假设方差相等", "equal variances assumed"}
_ROW_UNEQUAL_VAR = {"不假定等方差", "假设方差不相等", "equal variances not assumed"}


def _row_label(rows: list[list[str]], i: int) -> str:
    """行标签 = 该行**位于数值区之前的最后一个非空单元格**。

    SPSS 常把变量名与条件分列（如 `成绩 | 假定等方差 | 1.234 | ...`），
    所以取「最后一个非数值单元格」，而不是固定取第 0 列。
    🔴 判定数值后必须**先 break 再赋值**，否则标签会被覆盖成第一个数字。
    """
    label = ""
    for c in rows[i]:
        if not c:
            continue
        if _f(c) is not None:  # 进入数据区，标签到此为止
            break
        label = c
    return (label or "").strip()


def _pick_p_col(p_cols: list[int], stat_col: Optional[int]) -> Optional[int]:
    """给统计量配一个 p 列：取其**右侧最近的** p 列。

    SPSS「独立样本检验」表里有两个 p：莱文检验的「显著性」在 F 右侧，
    t 的「Sig.（双尾）」在最右。用「右侧最近」即可各自配对，
    避免把莱文的 p 当成均值差异检验的 p（那是另一个检验，混用必错）。
    """
    if not p_cols:
        return None
    if stat_col is None:
        return p_cols[-1]
    right = [j for j in p_cols if j > stat_col]
    return right[0] if right else p_cols[-1]


def _p_from_cell(cell: str, op_hint: str = "=") -> Optional[dict[str, Any]]:
    """解析 p 单元格。

    🔴 SPSS 三位小数显示下 `.000` 表示 p < .001（不是 p = 0）。
    论文里写 `p = .000` 是错的，按学术惯例记为 `p < .001` 并显式告知用户。
    """
    v = _f(cell)
    if v is None:
        return None
    if v == 0.0:
        return {"p_value": 0.001, "p_op": "<", "p_raw": "< .001",
                "p_zero_display": True}
    return {"p_value": v, "p_op": op_hint, "p_raw": (cell or "").strip()}


def extract_table_stats(rows: list[list[str]]) -> dict[str, Any]:
    """按表头语义从已识别的表格里提取统计量（纯函数，认不出就返回空）。

    只处理两类能真正喂给一致性校验的表：
      · 有 t 列 → 独立样本 t 检验（取「假定等方差」行；忽略莱文 F，那不是均值差异检验）
      · 只有 F 列 → 单因素 ANOVA（组之间行出 F/p/df_between，组内行出 df_error）
    描述统计（个案数/平均值/标准差）也提取，但它本身没有可校验的推论统计量。
    """
    if not rows or len(rows) < 2:
        return {}

    header = rows[0]
    canon = [_canon_header(c) for c in header]
    cols: dict[str, int] = {}
    for idx, c in enumerate(canon):
        if not c:
            continue
        for key, aliases in _HEADER_MAP.items():
            if key == "p":
                continue  # p 可能有多列，单独处理
            if c in aliases and key not in cols:
                cols[key] = idx
    p_cols = [i for i, c in enumerate(canon) if c in _HEADER_MAP["p"]]

    out: dict[str, Any] = {}
    # 🔴 **原始单元格文本**（数字保真）：表里是 `.83` 就必须输出 `.83`，
    # 不能被 float 化后再 `f"{v:g}"` 成 `0.83`。汇总表展示时优先用这里的原文。
    raws: dict[str, str] = {}

    def _cell(row: list[str], idx: Optional[int]) -> str:
        if idx is None or idx >= len(row):
            return ""
        return (row[idx] or "").strip()

    def _put(key: str, cell: str, as_int: bool = False) -> None:
        v = _f(cell)
        if v is None:
            return
        out[key] = int(v) if as_int and float(v).is_integer() else v
        raws[key] = cell.strip()

    # ── t 检验分支（优先：独立样本检验表里也有 F，但那是莱文方差齐性检验）──
    if "t" in cols and p_cols and "df" in cols:
        target = None
        target_label = ""
        for i in range(1, len(rows)):
            raw = _row_label(rows, i)
            if _canon_header(raw) in _ROW_UNEQUAL_VAR:
                continue
            target, target_label = i, raw
            break
        if target is not None:
            row = rows[target]
            _put("t_value", _cell(row, cols["t"]))
            _put("df", _cell(row, cols["df"]), as_int=True)
            pinfo = _p_from_cell(_cell(row, _pick_p_col(p_cols, cols["t"])))
            if pinfo:
                out.update(pinfo)
                raws["p_value"] = pinfo.get("p_raw", "")
            out["_source_row"] = target_label or f"第 {target} 行"
            out["_raw"] = raws
            return out

    # ── ANOVA 分支 ──
    if "f" in cols and p_cols:
        between_i = next((i for i in range(1, len(rows))
                          if _canon_header(_row_label(rows, i)) in _ROW_BETWEEN), None)
        error_i = next((i for i in range(1, len(rows))
                        if _canon_header(_row_label(rows, i)) in _ROW_ERROR), None)
        # 认不出「组之间/组内」行标签时，**不要**盲取第一行 —— ANOVA 表的第二行
        # 可能是「组内」（F 列为空），盲取会拿到空值或错值。宁可漏。
        if between_i is None:
            return out
        brow = rows[between_i]
        _put("f_value", _cell(brow, cols["f"]))
        pinfo = _p_from_cell(_cell(brow, _pick_p_col(p_cols, cols["f"])))
        if pinfo:
            out.update(pinfo)
            raws["p_value"] = pinfo.get("p_raw", "")
        if "df" in cols:
            _put("df_between", _cell(brow, cols["df"]), as_int=True)
            if error_i is not None:
                _put("df_error", _cell(rows[error_i], cols["df"]), as_int=True)
        out["_source_row"] = _row_label(rows, between_i) or f"第 {between_i} 行"
        out["_raw"] = raws
        return out

    # ── 描述统计：无推论统计量，只把 n / M / SD 提出来展示 ──
    for key in ("n", "mean", "sd"):
        if key in cols and len(rows) > 1:
            _put(key, _cell(rows[1], cols[key]), as_int=(key == "n"))
    if out:
        out["_source_row"] = _row_label(rows, 1) or "第 1 行"
        out["_raw"] = raws
    return out


def derive_eta_squared(stats: dict[str, Any]) -> Optional[float]:
    """由 F 与两个自由度推算 η² = F·df₁ / (F·df₁ + df₂)。

    SPSS 的 ANOVA 表**不会**直接给 η²（要另勾效应量），而论文几乎必报效应量 ——
    这是用户抄表时最容易漏、也最容易算错的一项。

    🔴 返回的是**推算值**，绝不能喂回一致性校验：用它去校 F↔η² 等于自己验自己，
    恒真通过，反而给出虚假的安全感。故调用方需把 eta_squared 记入 `_derived`。
    """
    f, d1, d2 = stats.get("f_value"), stats.get("df_between"), stats.get("df_error")
    if f is None or d1 is None or d2 is None:
        return None
    try:
        num = float(f) * float(d1)
        den = num + float(d2)
        if den <= 0:
            return None
        return num / den
    except (TypeError, ValueError):
        return None


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
    # 2026-09-16 新增：r_value 启用 d↔r 校验（此前 docstring 承诺了但代码未实现）；
    # t_test_type 用于指定配对/独立样本，让 t↔d 不必在三种候选式里宽判。
    "r_value", "t_test_type",
}


def stats_summary_table(stats: dict[str, Any], caption: str = "") -> str:
    """把零散统计量排成「统计量 | 值」汇总表（没有分组数据时的兜底形态）。"""
    derived = set(stats.get("_derived") or ())
    rows = [["统计量", "值"]]
    for key, label in _DISPLAY:
        if key not in stats:
            continue
        val = stats[key]
        if key in derived:
            label = f"{label}（推算）"
        if key == "p_value" and stats.get("p_raw"):
            # p_raw 形如 "< .001" / "= .52"。表格里「p 值 | = .52」读着别扭，
            # 等号是冗余的（列名已经是「p 值」），去掉；不等号必须保留（那是语义）。
            text = stats["p_raw"].strip()
            if text.startswith("="):
                text = text[1:].strip()
        elif key not in derived and (stats.get("_raw") or {}).get(key):
            # 原样透传原始单元格文本：数字保真。表里是 `.83` 就输出 `.83`，
            # 不能 float 化后再格式化成 `0.83`（会被 test_numbers_are_not_reformatted 抓住）。
            text = str((stats["_raw"] or {})[key])
        elif isinstance(val, float):
            # 推算值（如 η²）不是从表里抄来的，保留 3 位小数即可 —— 原样输出
            # 0.0865758 这种长尾既难读，也误导读者以为精度来自原始数据。
            text = f"{val:.3f}" if key in derived else f"{val:g}"
        else:
            text = str(val)
        rows.append([label, text])
    return to_gfm_table(rows, caption) if len(rows) > 1 else ""


def consistency_block(stats: dict[str, Any]) -> str:
    """复用已有校验器做一致性检查（不重写任何公式）。"""
    # 🔴 排除**推算值**（如由 F+df 推出来的 η²）：拿它去校 F↔η² 是自己验自己，
    # 恒真通过 —— 那不是"校验通过"，是校验被架空了，比不校验更危险。
    derived = set(stats.get("_derived") or ())
    payload = {k: v for k, v in stats.items()
               if k in _CONSISTENCY_KEYS and k not in derived}
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
    # 🔴 不要对整段 .strip()：末行结尾的 tab 是**列占位**（ANOVA 的「总计」行
    # 形如 `总计\t14258.023\t89\t\t\t`），strip() 会把结尾 tab 连同换行一起吃掉
    # → 该行从 6 列变 3 列 → 被列数过滤丢弃。与 detect_table_rows 里那个
    # rstrip() 是**同一个坑的第二次出现**（首次修在行级，这次在整段级）。
    text = _norm(raw_text or "")
    if not text.strip():
        return _NO_MATCH_HINT

    table_rows = detect_table_rows(text)
    inline = parse_inline_stats(text)
    table_stats = extract_table_stats(table_rows) if table_rows else {}

    # 同时有表和正文结论时，**按正文结论校验**：那才是要写进论文的数字。
    # 不把两者合并 —— 表里的 F 配正文的 t 会拼出一个根本不存在的检验。
    if inline and table_stats:
        stats = dict(inline)
        both = True
    else:
        stats = dict(inline or table_stats)
        both = False

    # 由 F + 自由度推算 η²（SPSS ANOVA 表不直接给，而论文几乎必报效应量）
    if "eta_squared" not in stats:
        eta = derive_eta_squared(stats)
        if eta is not None:
            stats["eta_squared"] = eta
            stats["_derived"] = ["eta_squared"]

    parts: list[str] = ["## 🧮 统计结果 → 三线表\n"]

    if table_rows:
        parts.append(f"**识别到表格：{len(table_rows)} 行 × {len(table_rows[0])} 列**\n")
        parts.append(to_gfm_table(table_rows, caption))
        parts.append("")
        if stats:
            # 表模式下也把提取到的统计量单独列出：① 便于逐项回表核对（数字保真）；
            # ② 推算的 η² 需要有地方展示（它不在原表里）。
            summary = stats_summary_table(stats, caption or "从表中提取到的统计量")
            if summary:
                parts.append(summary)
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

    # ── 出处与口径说明（数字保真：让用户能逐项回表核对）──
    notes: list[str] = []
    if stats.get("_source_row") and not inline:
        notes.append(f"上表数字取自**「{stats['_source_row']}」**行（按表头语义匹配列）。")
    if both:
        notes.append(
            "同时识别到表格与正文结论 —— 这里**按正文结论校验**（那是要写进论文的数字），"
            "未与表格合并。"
        )
    if stats.get("p_zero_display"):
        notes.append(
            "⚠️ 表中「显著性」显示为 `.000`：这是 SPSS 三位小数的舍入显示，"
            "**不是 p = 0**。已按学术惯例记为 **p < .001**，论文里请照此书写。"
        )
    if stats.get("_derived"):
        notes.append(
            "η² 是**由 F 与自由度推算**的（`F·df₁/(F·df₁+df₂)`）—— SPSS 的 ANOVA 表不直接给，"
            "论文几乎必报效应量。🔴 它**不参与**一致性校验（否则等于自己验自己）。"
        )
    if notes:
        parts.append("\n".join(f"> {n}" for n in notes) + "\n")

    if stats:
        block = consistency_block(stats)
        if block:
            parts.append("---\n")
            parts.append(block)

    return "\n".join(p for p in parts if p is not None)
