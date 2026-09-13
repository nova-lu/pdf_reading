"""
TTS 文本规范化模块（**朗读层专用**，不参与 PDF 提取）

⚠️ 职责边界（重要）：
本模块只在"语音发声"这一刻把保真文本转成好朗读的中文表达。
PDF 提取层（pdf_reader）输出的文本**不会被本模块改写**，
即显示与朗读定位始终基于原始 Unicode 字符（α、∑、∫、²、₁、½ 原样保留）。
调用入口：
- 朗读链路：TTSEngine 在 say()/合成前调用 normalize_for_tts（可用
  set_speech_normalize(False) 关闭，改成逐字原样朗读）
- 手动：PDFDocument.get_page_text_for_speech(page)

处理内容：

1. 隐形字符清洗：零宽字符、双向控制符、控制字符（保留 \\n \\r \\t）
2. 化学式与单位：H₂O、CO₂、m²、cm³、s⁻¹ 等转为中文读法
3. 数学结构转换：
   - 上下标：x² → x的平方；x⁻¹ → x的负一次方；a₁ → a下标1
   - LaTeX 标记（提取层 script_mode="latex" 时）：x^{2}、a_{1}
   - 分式：½ → 二分之一；3/4 → 4分之3；日期 2024/09/13 → 2024年9月13日
   - 根号：√9 → 根号9；√(x+1) → 根号下x+1；∛ → 立方根
   - 求和/积分：∑ᵢ₌₁ⁿ → 从i等于1到n求和；∫₀¹ → 从0到1积分
   - ASCII 记法：x^2、x^{n} → 的N次方
4. 统一符号读法规则：∑∫√π 等运算符、关系符、希腊字母、逻辑符号等
5. 不可朗读内容：按 Unicode 类别过滤（私用区/未定义/控制符/残留符号），
   默认跳过并记录到统计中，避免朗读中断或出现乱码

依赖：仅标准库（re / unicodedata / dataclasses / collections）
兼容：Python 3.9+
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

# ============================================================
# 一、字符表：上下标解码、符号读法
# ============================================================

# 上标字符 → ASCII（含上标字母，供公式变量使用）
_SUPER_TO_ASCII = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁺": "+", "⁻": "-", "⁽": "(", "⁾": ")", "ⁿ": "n", "ⁱ": "i",
    "ᵃ": "a", "ᵇ": "b", "ᶜ": "c", "ᵈ": "d", "ᵉ": "e", "ᶠ": "f",
    "ᵍ": "g", "ʰ": "h", "ʲ": "j", "ᵏ": "k", "ˡ": "l", "ᵐ": "m",
    "ᵒ": "o", "ᵖ": "p", "ʳ": "r", "ˢ": "s", "ᵗ": "t", "ᵘ": "u",
    "ᵛ": "v", "ʷ": "w", "ˣ": "x", "ʸ": "y", "ᶻ": "z",
}

# 下标字符 → ASCII
_SUB_TO_ASCII = {
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "₊": "+", "₋": "-", "₌": "=", "₍": "(", "₎": ")",
    "ₐ": "a", "ₑ": "e", "ₕ": "h", "ᵢ": "i", "ⱼ": "j", "ₖ": "k",
    "ₗ": "l", "ₘ": "m", "ₙ": "n", "ₒ": "o", "ₚ": "p", "ᵣ": "r",
    "ₛ": "s", "ₜ": "t", "ᵤ": "u", "ᵥ": "v", "ₓ": "x",
}

_SUPER_CLASS = "[" + re.escape("".join(_SUPER_TO_ASCII)) + "]"
_SUB_CLASS = "[" + re.escape("".join(_SUB_TO_ASCII)) + "]"

# 上下标的"基数"字符类：字母/数字/希腊/西里尔/CJK/括号。
# 注意不能用 \w —— Python 的 \w 会匹配上标数字（No 类），导致 x²⁵ 的基数贪婪吞掉 "2"。
_BASE_CLASS = r"0-9A-Za-z\u0370-\u03ff\u0400-\u04ff\u1e00-\u1fff\u4e00-\u9fff\u3040-\u30ff\)\]"

# 统一符号读法规则表（按"符号 → 中文读法"组织；可按需扩展）
SYMBOL_READINGS = {
    # --- 运算符 ---
    "±": "正负", "∓": "负正", "×": "乘以", "÷": "除以", "⋅": "乘",
    "⊕": "异或", "⊗": "张量积", "∞": "无穷大",
    # --- 关系符 ---
    "≤": "小于等于", "≥": "大于等于", "≦": "小于等于", "≧": "大于等于",
    "≠": "不等于", "≈": "约等于", "≅": "全等于", "≃": "近似等于",
    "≡": "恒等于", "∝": "正比于", "≪": "远小于", "≫": "远大于",
    # --- 集合论 ---
    "∈": "属于", "∉": "不属于", "∋": "包含",
    "⊂": "真包含于", "⊆": "包含于", "⊃": "真包含", "⊇": "包含",
    "∪": "并", "∩": "交", "∅": "空集", "∖": "差集", "∁": "补集",
    # --- 逻辑 ---
    "¬": "非", "∧": "且", "∨": "或",
    "∀": "任意", "∃": "存在", "∴": "所以", "∵": "因为",
    "⊢": "推导出", "⊨": "满足",
    # --- 几何 ---
    "∠": "角", "⊥": "垂直于", "∥": "平行于", "△": "三角形",
    "∽": "相似于", "○": "圆",
    # --- 微积分 ---
    "∂": "偏微分", "∇": "梯度",
    # --- 箭头（推理/映射/趋势）---
    "→": "到", "←": "到", "↦": "映射到", "↔": "对应",
    "⇒": "推出", "⇐": "推出", "⇔": "等价于",
    "↑": "上升", "↓": "下降", "↗": "上升", "↘": "下降",
    # --- 其他 ---
    "©": "版权", "®": "注册商标", "™": "商标",
    "§": "节", "†": "注", "‡": "注",
    "&": "和", "€": "欧元", "$": "美元", "£": "英镑",
    # --- 希腊字母（小写）---
    "α": "阿尔法", "β": "贝塔", "γ": "伽马", "δ": "德尔塔",
    "ε": "艾普西隆", "ϵ": "艾普西隆", "ζ": "泽塔", "η": "伊塔",
    "θ": "西塔", "ϑ": "西塔", "ι": "约塔", "κ": "卡帕",
    "λ": "拉姆达", "μ": "缪", "ν": "纽", "ξ": "克西",
    "ο": "奥米克戎", "π": "派", "ϖ": "派", "ρ": "柔",
    "σ": "西格玛", "ς": "西格玛", "τ": "陶", "υ": "宇普西龙",
    "φ": "斐", "ϕ": "斐", "χ": "希", "ψ": "普西", "ω": "欧米伽",
    # --- 希腊字母（大写）---
    "Γ": "伽马", "Δ": "德尔塔", "Θ": "西塔", "Λ": "拉姆达",
    "Ξ": "克西", "Π": "派", "Σ": "西格玛", "Φ": "斐",
    "Ψ": "普西", "Ω": "欧米伽",
}

# 预编译符号替换正则（长键优先，避免 π 被 ϖ 类似键截断）
_SYMBOL_RE = re.compile(
    "|".join(re.escape(k) for k in sorted(SYMBOL_READINGS, key=len, reverse=True))
)

# 常见化学式（含下标字符，替换为中文读法；仅收录带上下标字符的，避免误伤普通单词）
_CHEMICALS = {
    "H₂O": "水",
    "CO₂": "二氧化碳",
    "O₂": "氧气",
    "O₃": "臭氧",
    "N₂": "氮气",
    "SO₂": "二氧化硫",
    "NO₂": "二氧化氮",
    "CH₄": "甲烷",
    "NH₃": "氨气",
    "H₂SO₄": "硫酸",
    "HNO₃": "硝酸",
    "H₂CO₃": "碳酸",
    "C₆H₁₂O₆": "葡萄糖",
}

# 单位平方/立方读法
_UNIT_SQUARE = {
    "km": "平方千米", "m": "平方米", "dm": "平方分米",
    "cm": "平方厘米", "mm": "平方毫米", "μm": "平方微米",
    "µm": "平方微米", "nm": "平方纳米",
}
_UNIT_CUBE = {
    "km": "立方千米", "m": "立方米", "dm": "立方分米",
    "cm": "立方厘米", "mm": "立方毫米",
}
# 倒数单位（速率类）："每 + 单位"
_UNIT_RATE = {
    "s": "每秒", "sec": "每秒", "min": "每分钟",
    "h": "每小时", "hr": "每小时", "d": "每天",
    "mol": "每摩尔", "kg": "每千克", "g": "每克", "L": "每升",
}

# CJK 兼容单位字符（NFKC 会把它们拆坏，需在 NFKC 之前替换）
_UNIT_COMPAT = {
    "㎡": "平方米", "㎢": "平方千米", "㎠": "平方厘米", "㎟": "平方毫米",
    "㎥": "立方米", "㎦": "立方千米", "㎤": "立方厘米", "㎣": "立方毫米",
}

# Unicode 分数字符 → 中文读法
_FRACTION_CHARS = {
    "½": "二分之一", "⅓": "三分之一", "⅔": "三分之二",
    "¼": "四分之一", "¾": "四分之三",
    "⅕": "五分之一", "⅖": "五分之二", "⅗": "五分之三", "⅘": "五分之四",
    "⅙": "六分之一", "⅚": "六分之五",
    "⅐": "七分之一", "⅛": "八分之一", "⅜": "八分之三",
    "⅝": "八分之五", "⅞": "八分之七",
    "⅑": "九分之一", "⅒": "十分之一",
}

# 隐形/零宽字符（删除，不影响朗读）
_INVISIBLE_TRANSLATION = {
    ord(c): None
    for c in (
        "\u200b\u200c\u200d\u2060\ufeff\u00ad"      # 零宽、软连字符
        "\u200e\u200f"                                # 左右标记
        "\u2061\u2062\u2063\u2064"                   # 数学隐形算子
        "\u202a\u202b\u202c\u202d\u202e"             # 双向格式控制
    )
}

# 明确应删除的装饰性字符（列表符号、制表符、装饰图形等，TTS 无法朗读）
_DROP_CHARS = set(
    "•‣⁃▪▫◦●◐◑◎◆◇◈□▣❖✦✧✱✲✳❋※▪"
    "░▒▓█▀▁▂▃▄▅▆▇▏▐▌▍"
) | {chr(c) for c in range(0x2500, 0x2580)}  # 制表符区

# 清理后允许保留的符号类字符（其余 Sm/So/Sk 一律删除）
# +、=、<、> 允许保留：未命中数学语境规则的（如 C++、10+）由 TTS 引擎自行处理
_KEEP_SYMBOLS = set("—–…·’‘“”\"'+=<>")

# 符号变体归一表：PDF 字体常把负号/加号映射成破折号或全角形式，
# 仅供"公式读法"使用，正文排版字符（如 – —）不在普通文本中被改写
_SIGN_VARIANTS = {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "-", "\u2015": "-", "\u2212": "-", "\u2043": "-",
    "\u2e3a": "-", "\u2e3b": "-", "\ufe63": "-", "\uff0d": "-",
    "\uff0b": "+", "\uff1d": "=",
}
# 指数/下标中的符号变体字符类（供正则使用）
_SIGN_CLASS = "[-\u2010-\u2015\u2212\u2043\ufe63\uff0d]"


# ============================================================
# 二、统计信息
# ============================================================

@dataclass
class NormalizeStats:
    """规范化过程统计（用于诊断与调试）。"""

    replacements: Counter = field(default_factory=Counter)   # 规则名 -> 次数
    removed: Counter = field(default_factory=Counter)        # 被删字符 -> 次数

    def note(self, rule: str, n: int = 1) -> None:
        self.replacements[rule] += n

    def note_removed(self, ch: str) -> None:
        self.removed[ch] += 1

    @property
    def total_replaced(self) -> int:
        return sum(self.replacements.values())

    @property
    def total_removed(self) -> int:
        return sum(self.removed.values())

    def merge(self, other: "NormalizeStats") -> None:
        """合并另一份统计（用于全书逐段处理后汇总）。"""
        self.replacements.update(other.replacements)
        self.removed.update(other.removed)

    def summary(self) -> str:
        parts = [f"{rule}×{cnt}" for rule, cnt in self.replacements.most_common()]
        removed_desc = ""
        if self.removed:
            sample = "、".join(f"U+{ord(c):04X}" for c in list(self.removed)[:8])
            removed_desc = f"；清理不可朗读字符 {self.total_removed} 个（如 {sample}）"
        return f"共转换 {self.total_replaced} 处（{'，'.join(parts) or '无'}）{removed_desc}"


# ============================================================
# 三、各处理步骤
# ============================================================

def _decode_run(run: str, mapping: dict) -> str:
    """把上标/下标字符序列解码为普通 ASCII 文本。"""
    return "".join(mapping.get(c, c) for c in run)


def _exponent_text(exp: str) -> str:
    """指数读法：2→平方、3→立方、-1→负一次方，其余为 N次方。"""
    exp = exp.strip("()")
    # 符号变体归一：PDF 常把负号映射成 U+2012 FIGURE DASH 等破折号变体，
    # 若不归一，10⁻⁴³（或 10^{‒43}）会被当成普通字符而漏读
    exp = "".join(_SIGN_VARIANTS.get(c, c) for c in exp)
    if not exp:
        return ""
    if exp == "2":
        return "的平方"
    if exp == "3":
        return "的立方"
    if exp == "-1":
        return "的负一次方"
    if exp == "-2":
        return "的负二次方"
    if exp == "-3":
        return "的负三次方"
    if exp in ("+", "-", "±"):
        return ""  # 离子电荷等孤立正负号：跳过
    if exp.startswith("-"):
        return f"的负{exp[1:]}次方"
    return f"的{exp}次方"


def _strip_invisible(text: str, st: NormalizeStats) -> str:
    """删除零宽/控制字符（保留 \\n \\r \\t）。"""
    n_before = len(text)
    text = text.translate(_INVISIBLE_TRANSLATION)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    n_removed = n_before - len(text)
    if n_removed:
        st.note("strip_invisible", n_removed)
    return text


def _convert_chemicals(text: str, st: NormalizeStats) -> str:
    """常见化学式 → 中文名称（键含下标字符，不会误伤普通文本）。"""
    for formula, name in _CHEMICALS.items():
        if formula in text:
            st.note("chemical", text.count(formula))
            text = text.replace(formula, name)
    return text


def _convert_units(text: str, st: NormalizeStats) -> str:
    """单位上下标：m²→平方米、cm³→立方厘米、s⁻¹→每秒 等。"""
    changed = 0

    # CJK 兼容单位字符（必须在 NFKC 之前处理）
    for compat, name in _UNIT_COMPAT.items():
        if compat in text:
            changed += text.count(compat)
            text = text.replace(compat, name)

    # 平方/立方单位（区分大小写，m 是米、M 不处理）
    unit_alt = "|".join(sorted(_UNIT_SQUARE, key=len, reverse=True))
    def _sq(m: re.Match) -> str:
        return _UNIT_SQUARE[m.group("u")]
    text, n = re.subn(
        rf"(?<![A-Za-z])(?P<u>{unit_alt})²(?![A-Za-z])", _sq, text
    )
    changed += n

    unit_alt = "|".join(sorted(_UNIT_CUBE, key=len, reverse=True))
    def _cu(m: re.Match) -> str:
        return _UNIT_CUBE[m.group("u")]
    text, n = re.subn(
        rf"(?<![A-Za-z])(?P<u>{unit_alt})³(?![A-Za-z])", _cu, text
    )
    changed += n

    # 速率单位：s⁻¹ → 每秒
    rate_alt = "|".join(sorted(_UNIT_RATE, key=len, reverse=True))
    def _rate(m: re.Match) -> str:
        return _UNIT_RATE[m.group("u")]
    text, n = re.subn(
        rf"(?<![A-Za-z])(?P<u>{rate_alt})⁻¹(?![A-Za-z])", _rate, text
    )
    changed += n

    if changed:
        st.note("unit", changed)
    return text


def _bound_text(run: str, mapping: dict) -> str:
    """积分/求和上下界解码：ᵢ₌₁ → i等于1。"""
    decoded = _decode_run(run, mapping)
    decoded = decoded.replace("=", "等于").strip("()")
    return decoded


_NARY_NAMES = {
    "∑": "求和", "∏": "求积",
    "∫": "积分", "∬": "二重积分", "∭": "三重积分", "∮": "环路积分",
}


def _convert_nary_operators(text: str, st: NormalizeStats) -> str:
    """
    求和/积分符号及其上下界：
    ∑ᵢ₌₁ⁿ → 从i等于1到n求和；∫₀¹ → 从0到1积分；∑ → 求和
    兼容"下界在前/上界在前"两种提取顺序。
    """
    op_alt = "|".join(re.escape(k) for k in _NARY_NAMES)
    pattern = re.compile(
        r"(?P<op>" + op_alt + r")"
        r"(?:"
        r"(?P<lo1>" + _SUB_CLASS + r"+)(?P<hi1>" + _SUPER_CLASS + r"+)"
        r"|(?P<hi2>" + _SUPER_CLASS + r"+)(?P<lo2>" + _SUB_CLASS + r"+)"
        r"|(?P<lo3>" + _SUB_CLASS + r"+)"
        r"|(?P<hi3>" + _SUPER_CLASS + r"+)"
        r")?"
    )

    def _sub(m: re.Match) -> str:
        op = _NARY_NAMES[m.group("op")]
        lo = m.group("lo1") or m.group("lo2") or m.group("lo3")
        hi = m.group("hi1") or m.group("hi2") or m.group("hi3")
        if lo and hi:
            lo_t = _bound_text(lo, _SUB_TO_ASCII)
            hi_t = _bound_text(hi, _SUPER_TO_ASCII)
            return f"从{lo_t}到{hi_t}{op}"
        if lo:
            return f"从{_bound_text(lo, _SUB_TO_ASCII)}开始{op}"
        if hi:
            return f"{op}，上界为{_bound_text(hi, _SUPER_TO_ASCII)}"
        return op

    text, n = pattern.subn(_sub, text)
    if n:
        st.note("nary_operator", n)
    return text


def _convert_sqrt(text: str, st: NormalizeStats) -> str:
    """根号：√9 → 根号9；√(x+1) → 根号下x+1；∛ → 立方根。"""
    names = {"√": "根号", "∛": "立方根", "∜": "四次方根"}
    pattern = re.compile(
        r"(?P<root>[√∛∜])\s*"
        r"(?:\((?P<paren>[^()]{1,60})\)|(?P<atom>[\w]+))"
    )

    def _sub(m: re.Match) -> str:
        name = names[m.group("root")]
        if m.group("paren") is not None:
            return f"{name}下{m.group('paren')}"
        if m.group("atom"):
            return f"{name}{m.group('atom')}"
        return name

    text, n = pattern.subn(_sub, text)
    if n:
        st.note("sqrt", n)
    return text


def _convert_superscripts(text: str, st: NormalizeStats) -> str:
    """
    上标 → 指数读法：x² → x的平方；x⁻¹ → x的负一次方；
    孤立上标（无基数，常见于 PDF 公式断行）直接解码为普通文本。
    """
    pattern = re.compile(r"(?P<base>[" + _BASE_CLASS + r"]*)" + _SUPER_CLASS + "+")

    def _sub(m: re.Match) -> str:
        run = _decode_run(m.group(0)[len(m.group("base")):], _SUPER_TO_ASCII)
        base = m.group("base")
        exp = _exponent_text(run)
        if not base or not exp:
            return base + run  # 孤立上标或纯正负号：退化为普通文本
        return base + exp

    text, n = pattern.subn(_sub, text)
    if n:
        st.note("superscript", n)
    return text


def _convert_subscripts(text: str, st: NormalizeStats) -> str:
    """
    下标读法：
    - 小写变量 + 下标（数学语境）：a₁ → a下标1
    - 大写字母 + 数字下标（化学/序列语境）：C₆ → C6（保持行内）
    - 孤立下标：直接解码为普通文本
    """
    pattern = re.compile(r"(?P<base>[" + _BASE_CLASS + r"]*)" + _SUB_CLASS + "+")

    def _sub(m: re.Match) -> str:
        run = _decode_run(m.group(0)[len(m.group("base")):], _SUB_TO_ASCII)
        base = m.group("base")
        if not base:
            return run
        if base[-1].isupper():
            # 大写基数 + 数字下标：化学式风格，行内保留
            return base + run
        return f"{base}下标{run}"

    text, n = pattern.subn(_sub, text)
    if n:
        st.note("subscript", n)
    return text


def _convert_fraction_chars(text: str, st: NormalizeStats) -> str:
    """Unicode 分数字符：½ → 二分之一。"""
    changed = 0
    for frac, name in _FRACTION_CHARS.items():
        if frac in text:
            changed += text.count(frac)
            text = text.replace(frac, name)
    if changed:
        st.note("fraction_char", changed)
    return text


def _convert_caret_powers(text: str, st: NormalizeStats) -> str:
    """
    幂与下标记法：x^2 / x^{n} / x^-1 → 的N次方；a_{1} → a下标1。

    说明：提取层 script_mode="latex" 时会产出 x^{2}、a_{1} 这样的标记；
    只处理"花括号"形式的下标，避免误伤文件名/标识符（如 pdf_reader）。
    """
    pattern = re.compile(
        r"(?P<base>[\w\)\]]?)\^(?:\{(?P<exp1>" + _SIGN_CLASS + r"?[0-9A-Za-z]+)\}"
        r"|(?P<exp2>" + _SIGN_CLASS + r"?[0-9A-Za-z]+))"
    )

    def _sub(m: re.Match) -> str:
        exp = m.group("exp1") or m.group("exp2") or ""
        base = m.group("base")
        reading = _exponent_text(exp)
        if not reading:
            return base + exp
        return base + reading

    text, n = pattern.subn(_sub, text)

    # LaTeX 下标标记：a_{1} → a下标1
    text, n2 = re.subn(
        r"(?P<base>[\w\)\]]?)_\{(?P<sub>[^{}]{1,30})\}",
        lambda m: f"{m.group('base')}下标{m.group('sub')}",
        text,
    )

    if n + n2:
        st.note("caret_power", n + n2)
    return text


def _replace_symbols(text: str, st: NormalizeStats) -> str:
    """按统一读法表替换数学/逻辑/希腊字母等符号。"""
    if not _SYMBOL_RE.search(text):
        return text

    def _sub(m: re.Match) -> str:
        return SYMBOL_READINGS[m.group(0)]

    text, n = _SYMBOL_RE.subn(_sub, text)
    if n:
        st.note("symbol_reading", n)
    return text


def _nfkc_post_fixes(text: str, st: NormalizeStats) -> str:
    """NFKC 之后的修补：分数斜杠、摄氏度等。"""
    changed = 0
    if "⁄" in text:  # NFKC 可能把分数字符拆成 1⁄2 形式
        changed += text.count("⁄")
        text = text.replace("⁄", "/")
    for c in ("℃", "℉"):
        if c in text:
            name = "摄氏度" if c == "℃" else "华氏度"
            changed += text.count(c)
            text = text.replace(c, name)
    if changed:
        st.note("nfkc_fix", changed)
    return text


def _convert_dates_and_fractions(text: str, st: NormalizeStats) -> str:
    """斜杠内容：先日期（2024/09/13 → 2024年9月13日），再分数（3/4 → 4分之3）。"""
    text, n = re.subn(
        r"(?<!\d)(\d{4})/(\d{1,2})/(\d{1,2})(?!\d)",
        lambda m: f"{m.group(1)}年{int(m.group(2))}月{int(m.group(3))}日",
        text,
    )
    n_date = n

    # 简单分数：分子/分母 均为数字（年份已被上面消费；此处要求两边不是数字/字母）
    frac_re = re.compile(r"(?<![\w/])(\d{1,4})\s*/\s*(\d{1,4})(?![\w/])")

    def _frac(m: re.Match) -> str:
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0:
            return m.group(0)  # 非法分母：原样保留（后续按符号清理）
        if num % den == 0:  # 整除的写法 10/2 常表示比例，读作"二分之一"式拗口 → 保留原样由 TTS 处理
            return m.group(0)
        return f"{m.group(2)}分之{m.group(1)}"

    text, n = frac_re.subn(_frac, text)
    total = n_date + n
    if total:
        st.note("slash", total)
    return text


def _convert_ascii_operators(text: str, st: NormalizeStats) -> str:
    """
    数学语境中的 ASCII 运算符（仅在"字母/数字/括号/中文词"之间才转换，
    避免误伤 "C++"、"10+ 条评论" 等普通文本）：
    + → 加；= → 等于；< → 小于；> → 大于；U+2212 → 减。
    前瞻含中文是因为上标/单位等规则先执行（"a的平方 + b" 的前邻已是中文）。
    """
    _before = r"0-9A-Za-z\)\]\u4e00-\u9fff\u0370-\u03ff"
    rules = [
        (re.compile(r"(?<=[" + _before + r"])\s*\+\s*(?=[0-9A-Za-z\(])"), " 加 "),
        (re.compile(r"(?<=[" + _before + r"])\s*=\s*(?=[0-9A-Za-z\(])"), " 等于 "),
        (re.compile(r"(?<=[" + _before + r"])\s*<\s*(?=[0-9A-Za-z\(])"), " 小于 "),
        (re.compile(r"(?<=[" + _before + r"])\s*>\s*(?=[0-9A-Za-z\(])"), " 大于 "),
        (re.compile(r"(?<=[" + _before + r"])\s*−\s*(?=[0-9A-Za-z\(])"), " 减 "),
    ]
    changed = 0
    for regex, reading in rules:
        text, n = regex.subn(reading, text)
        changed += n
    # 中点乘号（仅字母/数字之间）：a·b → a 乘 b；中文人名间隔点不受影响
    text, n = re.subn(
        r"(?<=[0-9A-Za-z\)\]])\s*[·]\s*(?=[0-9A-Za-z\(])", " 乘 ", text
    )
    changed += n
    if changed:
        st.note("ascii_operator", changed)
    return text


def _convert_percent(text: str, st: NormalizeStats) -> str:
    """百分号：50% → 百分之50。"""
    text, n = re.subn(
        r"(\d+(?:\.\d+)?)\s*%", lambda m: f"百分之{m.group(1)}", text
    )
    text, n2 = re.subn(
        r"(\d+(?:\.\d+)?)\s*‰", lambda m: f"千分之{m.group(1)}", text
    )
    if n + n2:
        st.note("percent", n + n2)
    return text


def _convert_degrees(text: str, st: NormalizeStats) -> str:
    """角度/温度：90° → 90度；25°C → 25摄氏度。"""
    text, n1 = re.subn(
        r"(\d+(?:\.\d+)?)\s*°\s*[CFcf]\b", lambda m: f"{m.group(1)}摄氏度", text
    )
    text, n2 = re.subn(
        r"(\d+(?:\.\d+)?)\s*°", lambda m: f"{m.group(1)}度", text
    )
    if n1 + n2:
        st.note("degree", n1 + n2)
    return text


def _convert_primes(text: str, st: NormalizeStats) -> str:
    """
    撇号读法（必须在 NFKC 之前执行——NFKC 会把 ″ 拆成两个 ′）：
    - 数字后：角分/角秒，32°15′30″ → 32度15分30秒
    - 字母后：导数撇，f′ → f撇、f″ → f两撇
    """
    text, n1 = re.subn(r"(?<=\d)\s*′\s*(?=\d|$)", "分", text)
    text, n2 = re.subn(r"(?<=\d)\s*″\s*(?=\d|$)", "秒", text)
    text, n3 = re.subn(r"(?<=[A-Za-z])′", "撇", text)
    text, n4 = re.subn(r"(?<=[A-Za-z])″", "两撇", text)
    if n1 + n2 + n3 + n4:
        st.note("prime", n1 + n2 + n3 + n4)
    return text


# 全角恢复映射（NFKC 会把全角标点压成半角，中文语境需还原）
_FW_PUNCT = {",": "，", ".": "。", "!": "！", "?": "？", ";": "；", ":": "：",
             "(": "（", ")": "）"}


def _restore_fullwidth_punct(text: str, st: NormalizeStats) -> str:
    """
    NFKC 之后的中文标点恢复：
    - 前邻是中文（或中文标点）的 , . ! ? ; : ) 还原为全角
    - 后邻是中文的 : ; ( 还原为全角（数字小数点如 3.5亿 不受影响）
    """
    changed = 0
    # 前邻为 CJK 的标点
    text, n = re.subn(
        r"(?<=[\u4e00-\u9fff\u3040-\u30ff\u3001\u3002…—）】》”])\s*([,.!?;:])",
        lambda m: _FW_PUNCT[m.group(1)], text,
    )
    changed += n
    # 后邻为 CJK 的冒号/分号/左括号（排除 "3.5亿" 这类数字小数点）
    text, n = re.subn(
        r"(?<![\d.])([:;])\s*(?=[\u4e00-\u9fff])",
        lambda m: _FW_PUNCT[m.group(1)], text,
    )
    changed += n
    text, n = re.subn(
        r"(?<![0-9A-Za-z])\(\s*(?=[\u4e00-\u9fff])",
        lambda m: _FW_PUNCT["("], text,
    )
    changed += n
    text, n = re.subn(
        r"(?<=[\u4e00-\u9fff])\s*\)",
        lambda m: _FW_PUNCT[")"], text,
    )
    changed += n
    if changed:
        st.note("fullwidth_punct", changed)
    return text


def _remove_unpronounceable(text: str, st: NormalizeStats) -> str:
    """
    不可朗读内容清理（跳过策略）：
    - 私用区（Co）、未定义（Cn）、代理项（Cs）、控制符（Cc）
    - 替换字符 U+FFFD
    - 残留的数学/杂项符号（Sm/So/Sk，白名单除外）
    所有删除均记入统计，便于排查"读丢内容"的问题。
    """
    out = []
    for ch in text:
        if ch in _DROP_CHARS:
            st.note_removed(ch)
            continue
        cat = unicodedata.category(ch)
        if cat in ("Co", "Cn", "Cs") or ch == "\ufffd":
            st.note_removed(ch)
            continue
        if cat == "Cc" and ch not in "\n\r\t":
            st.note_removed(ch)
            continue
        if cat in ("Sm", "So", "Sk") and ch not in _KEEP_SYMBOLS:
            st.note_removed(ch)
            continue
        out.append(ch)
    return "".join(out)


def _tidy(text: str, st: NormalizeStats) -> str:
    """最终整理：压缩空白、中文间去空格、修正标点前空格。"""
    # 制表符转空格，压缩连续空白
    text = text.replace("\t", " ")
    text = re.sub(r"[ ]{2,}", " ", text)
    # 点线/分隔线（3 个以上连续 · ，PDF 页眉装饰常见）折叠为单个停顿
    text = re.sub(r"(?:\s*[·•]\s*){3,}", " ", text)
    # CJK 字符之间的视觉空格
    text = re.sub(
        r"([\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff，。！？；、：）】》”]) +"
        r"(?=[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff，。！？；、：（【《“])",
        r"\1",
        text,
    )
    # 标点前空格
    text = re.sub(r" +([,.!?;:，。！？；、：）])", r"\1", text)
    # 行首尾空白
    text = "\n".join(line.strip() for line in text.split("\n"))
    return text.strip()


# ============================================================
# 四、对外主入口
# ============================================================

def normalize_for_tts(text: str, stats: Optional[NormalizeStats] = None) -> str:
    """
    将 PDF 提取的原始文本转换为适合中文语音朗读的文本。

    Args:
        text: 原始文本（通常是清理后的段落）
        stats: 可选的统计对象（传入同一对象可跨段落累计）

    Returns:
        语音友好的文本；空输入原样返回
    """
    if not text or not text.strip():
        return text

    st = stats if stats is not None else NormalizeStats()

    # 顺序敏感：上下标/化学式/撇号等语义结构必须在 NFKC 之前处理，
    # 否则 ² 会被拆成普通 "2"、″ 被拆成两个 ′，语义永久丢失。
    text = _strip_invisible(text, st)
    text = _convert_chemicals(text, st)
    text = _convert_units(text, st)
    text = _convert_nary_operators(text, st)
    text = _convert_sqrt(text, st)
    text = _convert_superscripts(text, st)
    text = _convert_subscripts(text, st)
    text = _convert_fraction_chars(text, st)
    text = _convert_caret_powers(text, st)
    text = _replace_symbols(text, st)
    text = _convert_primes(text, st)

    # NFKC：处理连字（ﬁ→fi）、全角字符、带圈数字等兼容字符
    text = unicodedata.normalize("NFKC", text)
    text = _nfkc_post_fixes(text, st)
    text = _restore_fullwidth_punct(text, st)

    # 依赖 NFKC 归一化结果的处理
    text = _convert_dates_and_fractions(text, st)
    text = _convert_ascii_operators(text, st)
    text = _convert_percent(text, st)
    text = _convert_degrees(text, st)

    # 兜底：清理所有残留的不可朗读字符
    text = _remove_unpronounceable(text, st)
    text = _tidy(text, st)
    return text


def normalize_paragraphs(paragraphs: list, stats: Optional[NormalizeStats] = None) -> list:
    """
    批量规范化段落列表（保持段落结构，跳过空段落）。

    Args:
        paragraphs: 段落文本列表
        stats: 可选统计对象（跨段落累计）

    Returns:
        规范化后的段落列表
    """
    st = stats if stats is not None else NormalizeStats()
    return [normalize_for_tts(p, st) if p and p.strip() else p for p in paragraphs]
