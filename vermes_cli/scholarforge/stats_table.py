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
    kept = [r for r in raw_rows if len(r) == common]

    # 🔴 混合型表的例外：「KMO 和巴特利特检验」里，KMO 那行只有 2 列
    # （`KMO 取样适切性量数\t.812`），而巴特利特的三行是 3 列 —— 按列数过滤
    # 会把**问卷效度分析里最常被抄进论文的那个数**直接丢掉。
    # 只放行**形态明确**的两列行：首列非数字（是标签）、次列是数字（是值）。
    # 宁可漏：宁可放过一行，也不能把说明文字当数据混进来。
    for r in raw_rows:
        if len(r) != 2 or len(r) == common:
            continue
        if r[0].strip() and _f(r[0]) is None and _f(r[1]) is not None:
            kept.append(r)

    # 🔴 多层表头的例外（2026-09-18 实测）：回归「系数」表的**上层**表头
    #   `模型 | | 未标准化系数 | | 标准化系数 | t | 显著性 | 共线性统计`（8 列）
    # 与数据行（9 列）列数不同 → 被上面的过滤**直接丢掉**。
    # 而 `t` 与 `显著性` 两个关键列名**只在上层**，丢了就整张表提取为空。
    # 只放行：位于**首个数据行之前** 且 **整行不含数字**（形态上是表头 / 分组标题）。
    # 宁可漏：数据区里的异形行一律不放行，避免把说明文字当数据。
    first_kept = next((i for i, r in enumerate(raw_rows) if len(r) == common), None)
    if first_kept:
        extra = [r for i, r in enumerate(raw_rows)
                 if i < first_kept and len(r) != common
                 and all(_f(c) is None for c in r)]
        kept = extra + kept
    return kept


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
    # 问卷信度（可靠性统计）：α 没有可校验的 p，只进汇总表展示，故不入校验键。
    "cronbach_alpha": {"cronbach's alpha", "cronbach alpha", "克龙巴赫 alpha",
                       "克隆巴赫 alpha", "α"},
    # 2026-09-18 回归分析「模型摘要」：R / R² / 调整后 R²。
    # 🔴 `r 方` 是**一个**别名（含空格），不能拆成 `r` + `方` 去匹配 —— 否则
    #    R² 那一列会被误认成相关系数 r。故 `_col_keys` 采用「整体优先、分词回退」。
    "r": {"r"},
    "r_squared": {"r 方", "r方", "r²", "r-square", "r-squared", "r square"},
    "adj_r_squared": {"调整后 r 方", "调整 r 方", "调整后r方", "调整r方",
                      "adjusted r square", "adjusted r-squared", "adjusted r²"},
    # 回归「系数」表：列名**分散在两层表头**（上层 `未标准化系数 / 标准化系数`、
    # 下层 `B / 标准错误 / Beta`），故这里登记的是**下层那个词**，
    # 由 `_normalize_header` 按列拼接后再匹配。
    "b": {"b"},
    "se": {"标准错误", "标准误差", "se", "std. error", "standard error"},
    "beta": {"beta", "β"},
    "vif": {"vif"},
    "tolerance": {"容差", "tolerance"},
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
# 回归「系数」表首列是**模型编号**（`1`）—— 数字，会干扰行标签提取（见提取分支）。
_COL_MODEL_NO = {"模型", "model"}
# 回归「系数」表首行是 `(常量)`：它的 t 检验是"截距是否为 0"，
# 与任何研究假设无关，且 p 几乎恒为 .000 —— 取它会答非所问。
_ROW_CONSTANT = {"(常量)", "常量", "(constant)", "constant"}
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
    return _best_header(rows)[0]


def _col_keys(cell: str) -> list[str]:
    """一个表头单元格对应的语义键候选，**整体优先、分词回退**。

    🔴 为什么要回退：SPSS 回归「系数」表的列名分散在**两层**表头里，
    `_normalize_header` 拼接后一个单元格是 `未标准化系数 B` —— 整体匹配不上，
    但分词后 `B` 命中。
    🔴 为什么要**优先整体**：`R 方` 若先分词会得到 `R`（相关系数）与 `方`，
    把 R² 那一列误认成相关系数 r。整体优先可避免。
    """
    cn = _canon_header(cell)
    if not cn:
        return []
    out = []
    for tok in [cn] + cn.split():
        for key, aliases in _HEADER_MAP.items():
            if tok in aliases and key not in out:
                out.append(key)
    return out


def _score_row(cells: list[str]) -> int:
    """一行"像表头"的程度：每列最多 1 分（与 `_best_header` 的旧口径一致）。"""
    return sum(1 for c in cells if _col_keys(c))


def _best_header(rows: list[list[str]]) -> tuple[int, int]:
    """返回 (表头行下标, 命中数)。见 `_pick_header_row`；命中数用于判是否横向表。"""
    best_i, best = 0, 0
    for i in range(len(rows) - 1):  # 最后一行不可能是表头（后面得有数据）
        score = _score_row(rows[i])
        if score > best:
            best_i, best = i, score
    return best_i, best


def _normalize_header(rows: list[list[str]]) -> tuple[list[list[str]], int]:
    """把**双层表头**按列拼接成一行，返回 (新 rows, 表头下标)。

    SPSS 回归「系数」表是两层表头：
        第 0 行：模型 |  | 未标准化系数 |     | 标准化系数 | t | 显著性 | 共线性统计
        第 1 行：     |  | B | 标准错误 | Beta |   |       | 容差 | VIF
    `t` / `显著性` 在上层，`B` / `标准错误` / `Beta` 在下层 —— **单层定位
    无论取哪行都只能命中一半列名**，必须按列纵向拼接。

    🔴 闸门（宁可漏，绝不拿数据行当表头）：
      · 只试前 3 行（表头不会更低）
      · 参与合并的两行**都不得含数字单元格**
      · 合并后命中数必须**严格大于**单层最佳 —— 不划算就不合并
    """
    best_i, best = _best_header(rows)
    pick_i, pick_score, pick_row = None, best, None
    for i in range(min(3, len(rows) - 2)):
        a, b = rows[i], rows[i + 1]
        if any(_f(c) is not None for r in (a, b) for c in r):
            continue
        w = max(len(a), len(b))
        cand = [" ".join(x for x in (
            (a[j] if j < len(a) else ""), (b[j] if j < len(b) else "")) if x.strip())
            for j in range(w)]
        score = _score_row(cand)
        if score > pick_score:
            pick_i, pick_score, pick_row = i, score, cand
    if pick_i is None:
        return rows, best_i
    # 合并后两行变一行，原 `range(h+1, ...)` 的数据区口径自动保持正确
    return rows[:pick_i] + [pick_row] + rows[pick_i + 2:], pick_i


# ── 纵向键值对表（统计量名在**行**，值在列）────────────────────────────
# SPSS 有一大类表是「转置」的：`Z | -2.271` / `渐近显著性（双尾） | .023`，
# 例如非参数检验的「检验统计」、问卷的「KMO 和巴特利特检验」。
# 此前这类表**全部返回空** —— 而它们在小样本 / 非正态 / 问卷信效度分析里是主力。

# 统计量名（canon 后）→ 输出键
_KV_MAP: dict[str, str] = {
    "z": "z_value",
    "曼-惠特尼 u": "u_value", "mann-whitney u": "u_value",
    "威尔科克森 w": "w_value", "wilcoxon w": "w_value",
    # 🔴 克鲁斯卡尔-沃利斯 H **渐近服从 χ²(df=k−1)**，可直接复用 χ²↔p 校验；
    #    巴特利特球形度检验的「近似卡方」同理。故二者都归到 chi_square。
    "克鲁斯卡尔-沃利斯 h": "chi_square", "kruskal-wallis h": "chi_square",
    "克-瓦氏 h": "chi_square",
    "近似卡方": "chi_square", "近似 χ²": "chi_square", "近似 chi-square": "chi_square",
    "自由度": "df", "df": "df",
    "kmo 取样适切性量数": "kmo", "kmo": "kmo",
}
# 出处标签（人类可读）+ 主统计量的优先级（一张表只报一个"出处"）
_KV_LABEL = {
    "z_value": "Z", "u_value": "曼-惠特尼 U", "w_value": "威尔科克森 W",
    "chi_square": "χ²", "df": "自由度", "kmo": "KMO",
}
_KV_PRIMARY = ("chi_square", "z_value", "u_value", "w_value", "kmo")
# p 的候选名，按**优先级**排列：渐近显著性是论文最常报的那个
_KV_P_ORDER = ("渐近显著性", "asymp. sig", "显著性", "sig", "p", "精确显著性", "exact sig")


def _is_vertical_kv(rows: list[list[str]]) -> bool:
    """是不是「纵向键值对」表？

    判据（宁可漏，两个条件缺一不可）：
      · 表头行**认不出 ≥2 个语义列名**（横向统计表通常一认就是 4–5 个）；
      · 且**至少 2 行**的最后一个单元格是可解析的数字（真的是"名 → 值"）。
    """
    if _best_header(rows)[1] >= 2:
        return False
    n_val = sum(1 for r in rows if r and _f(r[-1]) is not None)
    return n_val >= 2


def _extract_kv(rows: list[list[str]]) -> dict[str, Any]:
    """从纵向键值对表里取统计量：每行「最后一个单元格 = 值」，其余是标签。

    🔴 标签取**最后一个非空**单元格（不是第一个）：KMO 表里巴特利特的三行是
    `巴特利特球形度检验 | 近似卡方 | 326.450` —— 第一个是**分组名**，
    最后一个才是统计量名。取错就会把「巴特利特球形度检验」当成统计量名。
    """
    out: dict[str, Any] = {}
    raws: dict[str, str] = {}
    p_best: tuple[int, str, str] | None = None  # (优先级, 名字, 原始值)

    for r in rows:
        if len(r) < 2:
            continue
        val_raw = r[-1]
        v = _f(val_raw)
        if v is None:
            continue
        name = ""
        for c in reversed(r[:-1]):
            if c.strip():
                name = c
                break
        if not name:
            continue
        cn = _canon_header(name)

        p_rank = next((i for i, p in enumerate(_KV_P_ORDER) if cn.startswith(p)), None)
        if p_rank is not None:
            if p_best is None or p_rank < p_best[0]:
                p_best = (p_rank, name, val_raw.strip())
            continue
        key = _KV_MAP.get(cn)
        if key is None:
            continue
        out[key] = int(v) if key == "df" and float(v).is_integer() else v
        raws[key] = _clean_cell(val_raw)

    if p_best is not None:
        pinfo = _p_from_cell(p_best[2])
        if pinfo:
            out.update(pinfo)
            raws["p_value"] = pinfo.get("p_raw", "")

    if not out:
        return {}
    # 出处只报**主统计量**（一张表一个），便于回表核对
    for k in _KV_PRIMARY:
        if k in out:
            out["_source_row"] = _KV_LABEL.get(k, k)
            break
    out["_raw"] = raws
    return out


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

    # ── 纵向键值对表（统计量名在行）：先判，因为它**没有**表头行 ──
    if _is_vertical_kv(rows):
        kv = _extract_kv(rows)
        if kv:
            return kv
        # 认不出任何统计量名就继续走横向路径（不吞掉本来能认的表）

    # 双层表头（回归系数表）先合并成一行；单层表原样返回
    rows, h = _normalize_header(rows)
    canon = [_canon_header(c) for c in rows[h]]
    cols: dict[str, int] = {}
    for idx, c in enumerate(canon):
        if not c:
            continue
        # 「整体优先、分词回退」：见 `_col_keys`（防 `R 方` 被当成相关系数 r）
        for key in _col_keys(c):
            if key == "p":
                continue  # p 可能有多列，单独处理
            if key not in cols:
                cols[key] = idx
            break
    p_cols = [i for i, c in enumerate(canon) if "p" in _col_keys(c)]

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

    # ── ②b 回归系数表（有 t 列，但**没有 df 列**）────────────────────────
    # 标志：t 列 +（B 或 Beta 列）+ 无 df 列。
    # 🔴 系数表的 t 检验自由度在**另一张** ANOVA 表里（残差 df），本表没有 →
    #    换算不出精确 p。故**不校 t↔p**（宁可漏），但必须显式告知为什么没校 ——
    #    否则用户拿到"未发现矛盾"会以为校过了，那就又是一个假安全。
    if ("t" in cols and p_cols and "df" not in cols
            and ("b" in cols or "beta" in cols)):
        # 🔴 变量名**不能**用 `_row_parts` 取：系数表第 0 列是**模型编号**（数字 1），
        #    `_row_parts` 一遇到数字就 break → 标签恒为空 → `(常量)` 行跳不掉，
        #    于是校的是"截距是否为 0"，与研究假设毫无关系。
        #    判据：表头第 0 列若叫「模型」，变量名就在第 1 列；否则在第 0 列。
        name_col = 1 if _canon_header(rows[h][0]) in _COL_MODEL_NO else 0
        picks: list[int] = []
        labels: list[str] = []
        for i in range(h + 1, len(rows)):
            label = _cell(rows[i], name_col).strip()
            # 🔴 `(常量)` 经 `_canon_header` 会变成**空串** —— 该函数会删掉
            #    括号及其内容，而 `(常量)` 整个都在括号里。直接判就永远跳不过
            #    常量行（会去校"截距是否为 0"）。必须先剥括号再 canon。
            if _canon_header(re.sub(r"[()（）]", "", label)) in _ROW_CONSTANT:
                continue
            if _f(_cell(rows[i], cols["t"])) is None:
                continue
            picks.append(i)
            labels.append(label)
        if picks:
            row = rows[picks[0]]
            for key in ("b", "se", "beta"):
                if key in cols:
                    _put(key + "_value", _cell(row, cols[key]))
            _put("t_value", _cell(row, cols["t"]))
            pinfo = _p_from_cell(_cell(row, _pick_p_col(p_cols, cols["t"])))
            if pinfo:
                out.update(pinfo)
                raws["p_value"] = pinfo.get("p_raw", "")
            out["_source_row"] = labels[0] or f"第 {picks[0]} 行"
            if len(picks) > 1:
                out["_extra_effects"] = labels[1:]
            out["_unchecked"] = [
                "t ↔ p（系数表没有自由度列；自由度在回归 ANOVA 表的「残差」行，"
                "把两张表一起粘贴即可校）"
            ]
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

    # ── ④b 回归模型摘要（R / R² / 调整后 R²）───────────────────────────
    # 🔴 `R 方` 必须**整体**匹配（见 `_col_keys`）：若被拆成 `R` + `方`，
    #    这一列会被当成相关系数 r，与真正的 R 列撞车。
    if "r_squared" in cols or "adj_r_squared" in cols:
        if h + 1 < len(rows):
            row = rows[h + 1]
            _put("r_value", _cell(row, cols.get("r")))
            _put("r_squared", _cell(row, cols.get("r_squared")))
            _put("adj_r_squared", _cell(row, cols.get("adj_r_squared")))
        if out:
            out["_source_row"] = "模型摘要"
            out["_raw"] = raws
            return out

    # ── ⑤ 描述统计：无推论统计量，只把 n / M / SD 提出来展示 ──
    if h + 1 < len(rows):
        for key in ("n", "mean", "sd", "cronbach_alpha"):
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
    ("chi_square", "χ²（卡方值）"),
    ("t_value", "t 值"),
    ("df", "自由度"),
    ("z_value", "Z（标准正态检验量）"),
    ("u_value", "曼-惠特尼 U"),
    ("w_value", "威尔科克森 W"),
    ("p_value", "p 值"),
    ("eta_squared", "η²（效应量）"),
    ("cohens_d", "Cohen's d（效应量）"),
    ("r_value", "r（相关系数）"),
    ("r_squared", "R²（决定系数）"),
    ("adj_r_squared", "调整后 R²"),
    ("b_value", "B（未标准化系数）"),
    ("se_value", "标准误 SE"),
    ("beta_value", "Beta（标准化系数）"),
    ("kmo", "KMO（取样适切性量数）"),
    ("cronbach_alpha", "Cronbach's α"),
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
    "chi_square", "n", "z_value",
    # 2026-09-18 新增：回归「模型摘要」的 R / R² / 调整后 R²。
    # R² = R² 是**确定性代数关系**（非统计推断），可用严格容差断言；
    # 而 r↔p 需要样本量 n，模型摘要没有 n → 那条自然不触发，不会误伤。
    "r_squared", "adj_r_squared",
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
    if stats.get("_unchecked"):
        notes.append(
            "🔴 **以下项目本次未校验**：" + "；".join(stats["_unchecked"]) + "。"
            "这**不等于**「校验通过」，而是**缺参数算不出来** —— "
            "别把这里的「未发现矛盾」当成已经核对过了。"
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
