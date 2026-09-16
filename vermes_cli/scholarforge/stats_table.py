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


# SPSS 单元格的两种装饰，不剥掉会让整行数据"认不出来" → 校验静默不跑：
#   · 脚注字母：`6.857a`（卡方值带脚注 a，说明"有单元格期望频数 < 5"）
#   · 显著性星号：`.482**` / `.035*`（相关矩阵、回归系数表里的显著性标记）
# 实测（2026-09-17）：一张 4 行的卡方表因此**一个数字都提不出来**。
_RE_TRAIL_STAR = re.compile(r"[\s*†‡]+$")
_RE_TRAIL_ALPHA = re.compile(r"[A-Za-z]+$")


def _f(s: str) -> Optional[float]:
    """解析数字；剥离脚注/星号后仍解析不出来则返回 None（宁可漏不可错）。

    🔴 顺序不能反：先去星号再去尾字母，否则 `.482**` 的尾字符是 `*`，
    尾字母正则匹配不上，`float(".482**")` 直接失败。
    """
    t = _clean_cell(s)
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _clean_cell(s: str) -> str:
    """剥掉脚注字母与显著性星号，返回可直接排进三线表的数字文本。

    🔴 为什么 `_raw`（数字保真的原文透传）也要剥：脚注 `a` 与显著性 `**` 是 SPSS
    的**表格装饰**，不进论文三线表（写 `6.857a` 会被当成笔误），而剥掉后数字本身
    仍与源表逐位一致，用户照样能回表核对 —— 保真保的是**数字**，不是装饰。
    """
    return _RE_TRAIL_STAR.sub("", _RE_TRAIL_ALPHA.sub("", (s or "").strip()))


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

# ── 行标签 → 角色 ────────────────────────────────────────────────────────
# 🔴 关键教训（2026-09-17 实测）：SPSS 同一类分析有**多种表名 / 行标签**，
#    只认「组之间 / 组内」会漏掉 GLM 单变量（「组别 / 误差」）与重复测量
#    （「时间·采用的球形度 / 误差(时间)」）—— 这两张表在教育学论文里比
#    单因素 ANOVA 还常见，而它们此前**一次校验都没跑过**（静默空）。
_ROW_BETWEEN = {"组之间", "组间", "组间变异", "between groups", "between"}
_ROW_ERROR = {"组内", "组内变异", "within groups", "within",
              "误差", "error", "残差", "residual"}
# GLM「主体间效应检验」里**不是**单个效应的行：整模型 / 截距 / 总计。
# 取它们的 F 去算 η² 会得到一个答非所问的数（模型整体效应量 ≠ 组别效应量）。
_ROW_MODEL = {"修正模型", "模型", "回归", "corrected model", "model", "regression"}
_ROW_INTERCEPT = {"截距", "intercept"}
_ROW_TOTAL = {"总计", "修正后总计", "total", "corrected total"}
# 重复测量「主体内效应检验」：同一个效应有 4 行（球形度成立 + 3 种校正）。
# 论文默认报「采用的球形度」；三种校正行的 F 相同但 df 被 ε 修正过，
# 混用会让 p↔F 对不上 —— 必须认出来并跳过。
_ROW_SPHERE_OK = {"采用的球形度", "假设球形度", "sphericity assumed"}
_ROW_SPHERE_CORR = {"格林豪斯-盖斯勒", "辛-费德特", "下限",
                    "greenhouse-geisser", "huynh-feldt", "lower-bound"}
# 独立样本 t 检验有两行：假定等方差（默认取这行）/ 不假定等方差（Welch，跳过）
_ROW_EQUAL_VAR = {"假定等方差", "假设方差相等", "equal variances assumed"}
_ROW_UNEQUAL_VAR = {"不假定等方差", "假设方差不相等", "equal variances not assumed"}
# 卡方检验：只有「皮尔逊卡方」行是标准 χ²，似然比 / 线性关联是别的检验
_ROW_CHI_PEARSON = {"皮尔逊卡方", "皮尔逊 卡方", "pearson chi-square", "chi-square"}
# 相关矩阵：每个变量占一个块，块内三行（皮尔逊相关性 / Sig. / 个案数）
_ROW_R = {"皮尔逊相关性", "pearson 相关性", "pearson correlation", "相关性"}
_ROW_SIG = {"sig", "显著性", "p"}
_ROW_N = {"个案数", "样本量", "个数", "n"}
# 卡方表里 χ² 所在列的表头就叫「值」—— 这个词太泛，不进 `_HEADER_MAP`
# （否则任何带「值」列的表都会被贴上 chi 标签），只在**行标签已确认是卡方**时才认。
_CHI_ALIASES = {"值", "卡方", "卡方值", "χ²", "χ2", "chi-square", "chi2"}


def _row_label(rows: list[list[str]], i: int) -> str:
    """行标签 = 该行**位于数值区之前的最后一个非空单元格**。

    SPSS 常把变量名与条件分列（如 `成绩 | 假定等方差 | 1.234 | ...`），
    所以取「最后一个非数值单元格」，而不是固定取第 0 列。
    🔴 判定数值后必须**先 break 再赋值**，否则标签会被覆盖成第一个数字。
    """
    return _row_parts(rows, i)[1]


def _row_parts(rows: list[list[str]], i: int) -> tuple[str, str]:
    """返回 (主标签, 子标签) = 数值区之前的**第一个**与**最后一个**非空单元格。

    为什么要两个：重复测量「主体内效应检验」把效应名与球形度条件分列 ——
    `时间 | 采用的球形度 | 45.625 | ...`，而它的误差行是 `误差(时间) | 采用的球形度 | ...`。
    两者**子标签完全相同**，只靠子标签（旧 `_row_label`）根本区分不出效应行与误差行。
    """
    head = ""
    tail = ""
    for c in rows[i]:
        if not c:
            continue
        if _f(c) is not None:  # 进入数据区，标签到此为止
            break
        if not head:
            head = c
        tail = c
    return (head or "").strip(), (tail or "").strip()


def _label_of(head: str, tail: str) -> str:
    """拼人类可读的行标签：`时间` + `采用的球形度` → `时间（采用的球形度）`；相同时只给一个。"""
    head, tail = (head or "").strip(), (tail or "").strip()
    if head and tail and head != tail:
        return f"{head}（{tail}）"
    return head or tail


_ALL_ALIASES: set[str] = set().union(*_HEADER_MAP.values())


def _pick_header_row(rows: list[list[str]]) -> int:
    """在**多层表头**里定位真正带列名的那一行（返回行下标）。

    🔴 旧实现固定取 `rows[0]`，而 SPSS「成对样本检验」的表头有两层：
       第 0 行 `  |  | 成对差值 | …`（只是分组大标题）
       第 1 行 `  |  | 平均值 | 标准差 | … | t | 自由度 | 显著性`（真正的列名）
    取第 0 行 → 一个语义列都匹配不上 → 整张表提取为空 → 校验静默不跑。
    判据：认出语义别名最多的一行；并列时取**靠前**的（数据行几乎不可能并列）。
    """
    best_i, best = 0, 0
    for i in range(len(rows) - 1):  # 最后一行不可能是表头（后面得有数据）
        score = sum(1 for c in rows[i] if _canon_header(c) in _ALL_ALIASES)
        if score > best:
            best_i, best = i, score
    return best_i


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

    支持的表（教育学/心理学论文里 SPSS 的高频输出）：
      · 相关矩阵   → r / p / n（变量名在**列**，每变量占三行）
      · t 检验     → 独立样本（取「假定等方差」行）、成对样本、单样本
      · 卡方检验   → χ² / df / p（只取「皮尔逊卡方」行）
      · F 类       → 单因素 ANOVA「组之间/组内」、GLM「主体间效应检验」（组别/误差）、
                     重复测量「主体内效应检验」（时间·采用的球形度 / 误差(时间)）
    描述统计（个案数/平均值/标准差）也提取，但它本身没有可校验的推论统计量。

    🔴 宁可漏不可错：行标签认不出来就返回空（校验不跑），绝不"猜"一行。
    """
    if not rows or len(rows) < 2:
        return {}

    out: dict[str, Any] = {}
    # 🔴 **原始单元格文本**（数字保真）：表里是 `.83` 就必须输出 `.83`，
    # 不能被 float 化后再 `f"{v:g}"` 成 `0.83`。汇总表展示时优先用这里的原文。
    raws: dict[str, str] = {}

    # ── ① 相关矩阵（形态特殊，必须**先于**表头定位处理）─────────────────
    # 相关矩阵的 rows[0] 放的是变量名（` | | 成绩 | 学习动机`），而下面每行的
    # 第二列是「皮尔逊相关性 / Sig.（双尾）/ 个案数」——这些都会被 `_pick_header_row`
    # 判成表头（各命中一个语义别名）。形态不同就得先短路，否则必错。
    r_probe = next((i for i in range(1, len(rows))
                    if _canon_header(_row_parts(rows, i)[1]) in _ROW_R), None)
    if r_probe is not None:
        return _extract_correlation(rows, r_probe)

    h = _pick_header_row(rows)
    header = rows[h]
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
        raws[key] = _clean_cell(cell)

    # ── ② t 检验分支（优先：独立样本检验表里也有 F，但那是莱文方差齐性检验）──
    if "t" in cols and p_cols and "df" in cols:
        target = None
        target_label = ""
        for i in range(h + 1, len(rows)):
            head, tail = _row_parts(rows, i)
            if _canon_header(tail) in _ROW_UNEQUAL_VAR:
                continue
            target, target_label = i, _label_of(head, tail)
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

    # ── ③ 卡方检验分支 ──
    chi_i = next((i for i in range(h + 1, len(rows))
                  if _canon_header(_row_parts(rows, i)[1]) in _ROW_CHI_PEARSON), None)
    if chi_i is not None:
        # 「值」这个词太泛，只在**已确认本行是卡方行**的前提下才当 χ² 取值列。
        chi_col = next((j for j, c in enumerate(canon) if c in _CHI_ALIASES), None)
        if chi_col is None:  # 表头不叫「值」也没有「卡方」→ 取该行第一个可解析的数字
            chi_col = next((j for j, c in enumerate(rows[chi_i])
                            if _f(c) is not None), None)
        row = rows[chi_i]
        _put("chi_square", _cell(row, chi_col))
        if "df" in cols:
            _put("df", _cell(row, cols["df"]), as_int=True)
        pinfo = _p_from_cell(_cell(row, _pick_p_col(p_cols, chi_col)))
        if pinfo:
            out.update(pinfo)
            raws["p_value"] = pinfo.get("p_raw", "")
        out["_source_row"] = _label_of(*_row_parts(rows, chi_i)) or f"第 {chi_i} 行"
        out["_raw"] = raws
        return out

    # ── ④ F 分支（单因素 ANOVA / GLM 主体间效应 / 重复测量主体内效应）──
    if "f" in cols and p_cols:
        data = list(range(h + 1, len(rows)))
        # 误差行：主标签或子标签命中「组内 / 误差 / 残差」均可
        # （重复测量的误差行主标签是「误差(时间)」，canon 后即「误差」）。
        error_i = next((i for i in data
                        if _canon_header(_row_parts(rows, i)[1]) in _ROW_ERROR
                        or _canon_header(_row_parts(rows, i)[0]) in _ROW_ERROR), None)
        between_i = next((i for i in data
                          if _canon_header(_row_parts(rows, i)[1]) in _ROW_BETWEEN), None)
        others: list[str] = []
        if between_i is None and error_i is not None:
            # GLM / 重复测量没有「组之间」行：取第一个**非**整模型/截距/总计/误差、
            # **非**球形度校正、且 F 列非空的行 —— 那才是用户要报的那个效应。
            #
            # 🔴 为什么必须**先认出误差行**才敢取名字未知的效应行：
            #    GLM 的效应行标签就是用户自己的变量名（组别 / 性别 / 教学法 …），
            #    不可能预先枚举，所以不能只认固定标签；但一张连「组内 / 误差 /
            #    残差」都没有的表，取出来的 F 既算不出 df_error、也跑不了任何
            #    一致性校验 —— 那就是在给用户一个**没验过的数**，宁可不给。
            #    （旧行为「认不出组之间/组内就返回空」的保守精神由此保留。）
            for i in data:
                head, tail = _row_parts(rows, i)
                ch, ct = _canon_header(head), _canon_header(tail)
                if (ct in _ROW_MODEL or ct in _ROW_INTERCEPT or ct in _ROW_TOTAL
                        or ct in _ROW_ERROR or ch in _ROW_ERROR
                        or ct in _ROW_SPHERE_CORR):
                    continue
                if _f(_cell(rows[i], cols["f"])) is None:
                    continue
                if between_i is None:
                    between_i = i
                else:
                    others.append(_label_of(head, tail))
        # 认不出效应行时**不要**盲取第一行 —— ANOVA 表第二行可能就是「组内」
        # （F 列为空），盲取会拿到空值或错值。宁可漏。
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
        out["_source_row"] = _label_of(*_row_parts(rows, between_i)) or f"第 {between_i} 行"
        if others:
            # 多因素 / 多效应表：只校一个，但必须告诉用户还有哪些没校。
            out["_extra_effects"] = others
        out["_raw"] = raws
        return out

    # ── ⑤ 描述统计：无推论统计量，只把 n / M / SD 提出来展示 ──
    if h + 1 < len(rows):
        for key in ("n", "mean", "sd"):
            if key in cols:
                _put(key, _cell(rows[h + 1], cols[key]), as_int=(key == "n"))
    if out:
        out["_source_row"] = _label_of(*_row_parts(rows, h + 1)) or "第 1 行"
        out["_raw"] = raws
    return out


def _extract_correlation(rows: list[list[str]], r_i: int) -> dict[str, Any]:
    """从 SPSS 相关矩阵里取**一对**变量的相关（r / p / n）。

    矩阵是「块」结构：每个变量占三行（皮尔逊相关性 / Sig.（双尾）/ 个案数），
    变量名**只写在块的第一行**，后两行行首为空。所以必须沿行序继承「当前块」，
    不能按行首单元格匹配 —— 后两行的行首是空的。

    🔴 只取**第一对**并显式告知：4 变量矩阵有 6 对相关，把它们混成一个"校验"
    等于把 6 个不同检验揉在一起，比不校验更糟。多对时由调用方提示用户。
    """
    header = rows[0]
    canon = [_canon_header(c) for c in header]
    out: dict[str, Any] = {}
    raws: dict[str, str] = {}

    def _at(row: list[str], j: int) -> str:
        return (row[j] or "").strip() if j < len(row) else ""

    # 当前块变量名（沿行序继承；只认非数字的单元格，避免把数字当变量名）
    blocks: dict[int, str] = {}
    cur = ""
    for i in range(1, len(rows)):
        c0 = _at(rows[i], 0)
        if c0 and _f(c0) is None:
            cur = c0
        blocks[i] = cur

    vname = blocks.get(r_i) or ""
    row = rows[r_i]
    # 对角线 = 表头里与本行变量同名的那一列（相关矩阵必然自相关 = 1）
    diag_j = next((j for j in range(1, len(header))
                   if canon[j] and canon[j] == _canon_header(vname)), None)
    vals = [(j, _f(c)) for j, c in enumerate(row) if _f(c) is not None]
    if diag_j is not None:
        off = [(j, v) for j, v in vals if j != diag_j]
    else:
        off = [(j, v) for j, v in vals if abs(v - 1.0) > 1e-9]
    if not off:
        return {}
    j, rv = off[0]
    out["r_value"] = rv
    raws["r_value"] = _at(row, j)
    pair = _at(header, j) or f"第 {j + 1} 列"

    def _in_block(i: int, aliases: set[str]) -> bool:
        return blocks.get(i) == vname and _canon_header(_at(rows[i], 1)) in aliases

    p_i = next((i for i in range(1, len(rows)) if _in_block(i, _ROW_SIG)), None)
    if p_i is not None:
        pinfo = _p_from_cell(_at(rows[p_i], j))
        if pinfo:
            out.update(pinfo)
            raws["p_value"] = pinfo.get("p_raw", "")
    n_i = next((i for i in range(1, len(rows)) if _in_block(i, _ROW_N)), None)
    if n_i is not None:
        v = _f(_at(rows[n_i], j))
        if v is not None and float(v).is_integer():
            out["n"] = int(v)
            raws["n"] = _at(rows[n_i], j)

    out["_source_row"] = f"{vname} × {pair}".strip(" ×")
    if len(off) > 1:
        out["_corr_pairs"] = len(off)
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
    # 2026-09-17 新增：chi_square 启用 χ²↔p 校验；n 供 r↔p 换算
    # （t = r·√((n-2)/(1-r²))，df = n-2）。二者缺一，对应的表就只是"能看不能校"。
    "chi_square", "n",
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
    if (stats.get("_corr_pairs") or 0) > 1:
        notes.append(
            f"本表共 {stats['_corr_pairs']} 对相关，一致性校验只针对"
            f"**「{stats.get('_source_row', '')}」**这一对 —— 多对相关的 F/df/p 各不相同，"
            "揉在一起校验没有意义；其余各对请单独粘贴两变量的相关表。"
        )
    if stats.get("_extra_effects"):
        notes.append(
            f"本表还有其它效应行（{'、'.join(stats['_extra_effects'])}），一致性校验只针对"
            f"**「{stats.get('_source_row', '')}」**行 —— 各效应行的 F 与自由度不同，"
            "不能混校。"
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
