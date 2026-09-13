"""
Unicode 数学字符工具模块（字符保真层）

职责：让 PDF 提取结果"忠实还原"原文档字符，而不是把符号改写成其他文字。

提供三类能力：
1. 上下标映射：ASCII ↔ Unicode 上标/下标字符（²、³、₁、ₙ …），
   用于把 PDF 中的视觉上下标无损地表示成 Unicode。
2. 数学/符号 Unicode 区块表：覆盖希腊字母（U+0370–U+03FF）、数学运算符
   （U+2200–U+22FF）、箭头、字母式符号、数学字母数字符号等，用于识别与校验。
3. 字符画像 char_profile / format_profile：按区块统计文本字符构成，
   用于验证"提取是否丢字"、"是否被意外改写"。

设计原则：
- 只提供"识别 / 映射 / 统计"，绝不把符号改写成自然语言（那是朗读层的事）。
- 映射失败返回 None，由调用方决定回退策略（保真优先，不猜）。
"""

from typing import Optional

# ============================================================
# 一、上下标映射（ASCII → Unicode）
# ============================================================

# ASCII → Unicode 上标
SUPERSCRIPTS = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ",
    "g": "ᵍ", "h": "ʰ", "i": "ⁱ", "j": "ʲ", "k": "ᵏ", "l": "ˡ",
    "m": "ᵐ", "n": "ⁿ", "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ",
    "t": "ᵗ", "u": "ᵘ", "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ",
    "z": "ᶻ",
}

# ASCII → Unicode 下标
SUBSCRIPTS = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
    "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ",
    "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}

# 符号变体表：PDF 字体经常把连字符/负号映射成各种破折号
# （例如 10⁻⁴³ 中的负号被映射为 U+2012 FIGURE DASH），
# 在"生成上下标标记"这一结构化表示时，这些变体与 ASCII 符号等价。
# 注意：仅在生成标记/读法时归一，正文里的 – — 等排版字符仍原样保留。
SIGN_VARIANTS = {
    # 减号类
    "\u2010": "-",   # HYPHEN
    "\u2011": "-",   # NON-BREAKING HYPHEN
    "\u2012": "-",   # FIGURE DASH      ← 本例：10^{‒43} 的 "‒"
    "\u2013": "-",   # EN DASH
    "\u2014": "-",   # EM DASH
    "\u2015": "-",   # HORIZONTAL BAR
    "\u2212": "-",   # MINUS SIGN
    "\u2043": "-",   # HYPHEN BULLET
    "\u2e3a": "-",   # TWO-EM DASH
    "\u2e3b": "-",   # THREE-EM DASH
    "\ufe63": "-",   # SMALL HYPHEN-MINUS
    "\uff0d": "-",   # FULLWIDTH HYPHEN-MINUS
    # 加号 / 等号类
    "\uff0b": "+",   # FULLWIDTH PLUS SIGN
    "\uff1d": "=",   # FULLWIDTH EQUALS SIGN
}


def normalize_sign(ch: str) -> str:
    """把符号变体归一为 ASCII（- / + / =），非变体字符原样返回。"""
    return SIGN_VARIANTS.get(ch, ch)


def normalize_signs(text: str) -> str:
    """批量归一符号变体（用于上下标标记、公式读法）。"""
    return "".join(SIGN_VARIANTS.get(c, c) for c in text)


def to_superscript(text: str, normalize_variants: bool = True) -> Optional[str]:
    """
    把 ASCII 文本转为 Unicode 上标字符。

    Args:
        text: 如 "2"、"n"、"-1"、"‒43"
        normalize_variants: 是否先把破折号/全角变体归一为 ASCII 符号（默认 True）。
            这样 10^{‒43} 这类"负号被映射成 FIGURE DASH"的情况可以正确转成 10⁻⁴³。

    Returns:
        可完全映射时返回上标文本；存在无法映射的字符时返回 None（不猜、不乱改）
    """
    if not text:
        return None
    out = []
    for ch in text:
        if ch == " ":
            out.append(" ")
            continue
        key = normalize_sign(ch) if normalize_variants else ch
        mapped = SUPERSCRIPTS.get(key)
        if mapped is None:
            return None
        out.append(mapped)
    return "".join(out)


def to_subscript(text: str, normalize_variants: bool = True) -> Optional[str]:
    """
    把 ASCII 文本转为 Unicode 下标字符。

    Returns:
        可完全映射时返回下标文本；否则返回 None
    """
    if not text:
        return None
    out = []
    for ch in text:
        if ch == " ":
            out.append(" ")
            continue
        key = normalize_sign(ch) if normalize_variants else ch
        mapped = SUBSCRIPTS.get(key)
        if mapped is None:
            return None
        out.append(mapped)
    return "".join(out)


def is_superscript(ch: str) -> bool:
    """判断字符是否为 Unicode 上标字符。"""
    return ch in _SUPER_SET


def is_subscript(ch: str) -> bool:
    """判断字符是否为 Unicode 下标字符。"""
    return ch in _SUB_SET


_SUPER_SET = frozenset(SUPERSCRIPTS.values())
_SUB_SET = frozenset(SUBSCRIPTS.values())


# ============================================================
# 二、数学/符号 Unicode 区块表
# ============================================================

# (区块名, 起始码位, 结束码位)
UNICODE_BLOCKS = [
    ("Combining Diacritical Marks", 0x0300, 0x036F),
    ("Greek and Coptic", 0x0370, 0x03FF),          # α β γ Δ π Ω …
    ("Cyrillic", 0x0400, 0x04FF),
    ("Hebrew", 0x0590, 0x05FF),
    ("Arabic", 0x0600, 0x06FF),
    ("General Punctuation", 0x2000, 0x206F),       # – — ’ “ ” … † ‰
    ("Superscripts and Subscripts", 0x2070, 0x209F),  # ⁰ ¹ ² ⁿ ₀ ₁ ₙ
    ("Currency Symbols", 0x20A0, 0x20CF),          # € ₽ ₹
    ("Letterlike Symbols", 0x2100, 0x214F),        # ℓ № ℃ ℉ ™ ℮
    ("Number Forms", 0x2150, 0x218F),              # ½ ⅓ ⅔ ①
    ("Arrows", 0x2190, 0x21FF),                    # ← ↑ → ⇒ ⇔
    ("Mathematical Operators", 0x2200, 0x22FF),    # ∀ ∂ ∑ ∏ ∫ √ ∞ ≤ ≠ ≈ ⊂ ∈
    ("Miscellaneous Technical", 0x2300, 0x23FF),   # ⌐ ⌀ ⌘
    ("Enclosed Alphanumerics", 0x2460, 0x24FF),    # ① ② ⒈ Ⓐ
    ("Box Drawing", 0x2500, 0x257F),
    ("Block Elements", 0x2580, 0x259F),
    ("Geometric Shapes", 0x25A0, 0x25FF),          # ■ ▲ ● ○ ◆
    ("Miscellaneous Symbols", 0x2600, 0x26FF),     # ★ ☆ ☀ ☂
    ("Dingbats", 0x2700, 0x27BF),                  # ✁ ✂ ✓ ✗
    ("Miscellaneous Mathematical Symbols-A", 0x27C0, 0x27EF),
    ("Supplemental Arrows-A", 0x27F0, 0x27FF),
    ("Braille Patterns", 0x2800, 0x28FF),
    ("Supplemental Arrows-B", 0x2900, 0x297F),
    ("Miscellaneous Mathematical Symbols-B", 0x2980, 0x29FF),
    ("Supplemental Mathematical Operators", 0x2A00, 0x2AFF),
    ("Miscellaneous Symbols and Arrows", 0x2B00, 0x2BFF),
    ("CJK Symbols and Punctuation", 0x3000, 0x303F),
    ("Hiragana", 0x3040, 0x309F),
    ("Katakana", 0x30A0, 0x30FF),
    ("CJK Unified Ideographs", 0x4E00, 0x9FFF),    # 常用汉字
    ("Hangul Syllables", 0xAC00, 0xD7AF),
    ("Halfwidth and Fullwidth Forms", 0xFF00, 0xFFEF),
    ("Mathematical Alphanumeric Symbols", 0x1D400, 0x1D7FF),  # 𝐀 𝒂 𝞪
    ("Arabic Mathematical Alphabetic Symbols", 0x1EE00, 0x1EEFF),
    ("Emoticons", 0x1F600, 0x1F64F),
    ("Miscellaneous Symbols and Pictographs", 0x1F300, 0x1F5FF),
]

# 数学/科学类区块（用于判断"这是数学字符"）
MATH_BLOCK_RANGES = [
    (0x0300, 0x036F),   # 组合音标（向量箭头、上划线等）
    (0x0370, 0x03FF),   # 希腊字母
    (0x2000, 0x206F),   # 通用标点（撇号、破折号、省略号）
    (0x2070, 0x209F),   # 上下标
    (0x2100, 0x214F),   # 字母式符号（ℓ ℃ ℉）
    (0x2150, 0x218F),   # 数字形式（½ ⅓）
    (0x2190, 0x21FF),   # 箭头
    (0x2200, 0x22FF),   # 数学运算符
    (0x2300, 0x23FF),   # 杂项技术符号
    (0x2460, 0x24FF),   # 带圈字母数字
    (0x25A0, 0x25FF),   # 几何图形（△ ○ □）
    (0x27C0, 0x27EF),   # 杂项数学符号 A
    (0x2980, 0x29FF),   # 杂项数学符号 B
    (0x2A00, 0x2AFF),   # 补充数学运算符
    (0x1D400, 0x1D7FF),  # 数学字母数字符号
]

# 需要"原样保留"的重点区块（用户明确要求）
CRITICAL_RANGES = {
    "希腊字母 U+0370-U+03FF": (0x0370, 0x03FF),
    "数学运算符 U+2200-U+22FF": (0x2200, 0x22FF),
    "上下标 U+2070-U+209F": (0x2070, 0x209F),
    "箭头 U+2190-U+21FF": (0x2190, 0x21FF),
    "数学字母数字 U+1D400-U+1D7FF": (0x1D400, 0x1D7FF),
}


# Latin-1 区中被广泛当作数学符号使用的字符
# （U+00B2 ²、U+00B9 ¹、U+00BD ½ 等在 Unicode 上属 Latin-1，但语义是数学符号）
_LATIN1_MATH = frozenset("±×÷¬°µ¹²³¼½¾")

# Latin-1 中的数学字符 → 语义区块名
_LATIN1_SEMANTIC = {
    "¹": "Superscripts and Subscripts",
    "²": "Superscripts and Subscripts",
    "³": "Superscripts and Subscripts",
    "¼": "Number Forms",
    "½": "Number Forms",
    "¾": "Number Forms",
    "±": "Mathematical Operators",
    "×": "Mathematical Operators",
    "÷": "Mathematical Operators",
    "¬": "Mathematical Operators",
    "°": "Mathematical Operators",
}


def is_math_char(ch: str) -> bool:
    """判断字符是否属于数学/科学符号（含 Latin-1 中的 ± × ÷ ² ½ 等）。"""
    if ch in _LATIN1_MATH:
        return True
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in MATH_BLOCK_RANGES)


def block_name(ch: str) -> str:
    """返回字符所属的 Unicode 区块名（对 Latin-1 数学字符按语义归类）。"""
    cp = ord(ch)
    if cp < 0x80:
        return "ASCII"
    if ch in _LATIN1_SEMANTIC:
        return _LATIN1_SEMANTIC[ch]
    if cp < 0x0250:
        return "Latin-1 Supplement"
    if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
        return "CJK Unified Ideographs"
    for name, lo, hi in UNICODE_BLOCKS:
        if lo <= cp <= hi:
            return name
    return "Other"


def char_profile(text: str) -> dict:
    """
    文本字符画像：按 Unicode 区块统计非 ASCII 字符，ASCII 单独归类。

    Returns:
        dict: {区块名: 数量}，附加 "ASCII"(可打印) / "Control"(控制符) 两项
    """
    profile: dict = {}
    for ch in text:
        if ch.isascii():
            cat = "Control/Whitespace" if (ord(ch) < 0x20 or ord(ch) == 0x7F) else "ASCII"
        else:
            cat = block_name(ch)
        profile[cat] = profile.get(cat, 0) + 1
    return profile


def math_chars(text: str) -> dict:
    """提取文本中的数学/科学符号及其出现次数（用于保真度校验）。"""
    found: dict = {}
    for ch in text:
        if not ch.isascii() and is_math_char(ch):
            found[ch] = found.get(ch, 0) + 1
    return found


def check_critical_chars(text: str) -> dict:
    """检查重点区块字符的出现次数（希腊字母 / 数学运算符 / 上下标 / 箭头）。"""
    result = {}
    for name, (lo, hi) in CRITICAL_RANGES.items():
        chars = [ch for ch in text if lo <= ord(ch) <= hi]
        if chars:
            result[name] = {"count": len(chars), "sample": "".join(sorted(set(chars))[:12])}
    return result
