"""
字符保真度测试：验证 PDF 提取"忠实还原"原始 Unicode 字符。

覆盖：
1. math_unicode 工具（上下标映射、区块识别、重点区块检查）
2. 上下标标注（unicode / latex / plain 三种模式）
3. _assemble_line 行组装（字号+基线识别上下标）
4. 合成 PDF 端到端（中文 + 数学符号 + 上标），验证字符零改写
5. 真实 PDF 端到端（英文书），验证特殊字符原样保留、无中文改写
6. 提取层 / 朗读层解耦：朗读转换不影响提取结果

运行：python test_text_fidelity.py
"""

import io
import os
import sys
import tempfile

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from math_unicode import (
    block_name,
    char_profile,
    check_critical_chars,
    is_math_char,
    normalize_signs,
    to_subscript,
    to_superscript,
)
from pdf_reader import (
    PDFDocument,
    PDFReader,
    SCRIPT_MODE_LATEX,
    SCRIPT_MODE_PLAIN,
    SCRIPT_MODE_UNICODE,
)

# 期望"原样保留"的数学/特殊字符样本
MATH_SAMPLE = "α β γ Ω ∑ ∫ √ π → ≤ ≥ ≈ ± × ÷ ² ³ ₁ ½ ⅓ ° é ’ – … © ∈ ∞"
CJK_SAMPLE = "勾股定理：直角三角形两直角边平方和等于斜边平方。"

_FONT_CANDIDATES_MATH = [
    r"C:\Windows\Fonts\cambria.ttc",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:/Windows/Fonts/arial.ttf",
]
_FONT_CANDIDATES_CJK = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
]

_results: list[bool] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append(bool(condition))
    status = "[OK]  " if condition else "[FAIL]"
    print(f"{status} {name}" + (f"  <- {detail}" if (detail and not condition) else ""))


def _first_existing(paths: list[str]) -> str | None:
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def _make_synthetic_pdf(path: str) -> bool:
    """
    生成中文 + 数学符号 + 上标的合成 PDF（需要系统字体，缺失则跳过）。

    布局：
      第 1 行：完整数学符号样本（含 ² ³ ₁ ½ ⅓ ° 等）
      第 2 行：中文句子
      第 3 行："E = mc" + 小字号抬升基线的 "2"（模拟上标）
      第 4 行："a" + 小字号下沉基线的 "1"（模拟下标）
    """
    import pymupdf as fitz

    math_font = _first_existing(_FONT_CANDIDATES_MATH)
    cjk_font = _first_existing(_FONT_CANDIDATES_CJK)
    if not math_font:
        return False

    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="mathf", fontfile=math_font)
    page.insert_text((50, 80), MATH_SAMPLE, fontsize=10, fontname="mathf")

    if cjk_font:
        page.insert_font(fontname="cjkf", fontfile=cjk_font)
        page.insert_text((50, 110), CJK_SAMPLE, fontsize=10, fontname="cjkf")
        cjk_fontname = "cjkf"
    else:
        # 回退到 PyMuPDF 内置简体中文字体
        page.insert_text((50, 110), CJK_SAMPLE, fontsize=10, fontname="china-s")
        cjk_fontname = "china-s"

    # 上标：字号 7 < 正文 11，基线抬高 3pt
    page.insert_text((50, 150), "E = mc", fontsize=11, fontname="mathf")
    page.insert_text((97, 147), "2", fontsize=7, fontname="mathf")

    # 下标：字号 7，基线下沉 3pt
    page.insert_text((50, 180), "a", fontsize=11, fontname="mathf")
    page.insert_text((58, 183), "1", fontsize=7, fontname="mathf")

    page.insert_text((80, 180), "（下标测试）", fontsize=11, fontname=cjk_fontname)

    # 用户场景：t = 0 up to t = 10^-43
    # 负号在 PDF 中被映射为 U+2012 FIGURE DASH（"‒"），需归一为负号才能得到 10⁻⁴³
    math_font_obj = fitz.Font(fontfile=math_font)
    prefix = "t = 0 up to t = 10"
    prefix_w = math_font_obj.text_length(prefix, fontsize=11)
    page.insert_text((50, 215), prefix, fontsize=11, fontname="mathf")
    page.insert_text((50 + prefix_w, 212), "\u201243", fontsize=7, fontname="mathf")

    doc.save(path)
    doc.close()
    return True


def main() -> int:
    print("=" * 70)
    print("一、math_unicode 工具")
    print("=" * 70)
    check("上标映射 2 → ²", to_superscript("2") == "\u00b2", repr(to_superscript("2")))
    check("上标映射 -1 → ⁻¹", to_superscript("-1") == "\u207b\u00b9", repr(to_superscript("-1")))
    check("下标映射 n → ₙ", to_subscript("n") == "\u2099", repr(to_subscript("n")))
    check("不可映射返回 None（q）", to_superscript("q") is None)
    check("空串返回 None", to_superscript("") is None)

    # 符号变体：PDF 常把负号映射成破折号（本例：10^{‒43} 中的 U+2012 FIGURE DASH）
    check("FIGURE DASH 负号归一 → ⁻⁴³",
          to_superscript("\u201243") == "\u207b\u2074\u00b3", repr(to_superscript("\u201243")))
    check("EN DASH 负号归一 → ⁻⁴³",
          to_superscript("\u201343") == "\u207b\u2074\u00b3", repr(to_superscript("\u201343")))
    check("U+2212 减号归一 → ⁻¹",
          to_superscript("\u22121") == "\u207b\u00b9", repr(to_superscript("\u22121")))
    check("全角负号归一 → ⁻²",
          to_superscript("\uff0d2") == "\u207b\u00b2", repr(to_superscript("\uff0d2")))
    check("关闭变体归一后返回 None（严格保真模式）",
          to_superscript("\u201243", normalize_variants=False) is None)
    check("正文排版字符不被全局改写（normalize_signs 仅用于标记）",
          normalize_signs("\u2012\u2013\u2212") == "---")
    check("数学字符识别 ∑/∫/α", all(is_math_char(c) for c in "∑∫α√∞"))
    check("普通字母不算数学字符", not is_math_char("a"))
    check("区块名 Greek", block_name("α") == "Greek and Coptic", block_name("α"))
    check("区块名 Math Operators", block_name("∑") == "Mathematical Operators", block_name("∑"))
    check("区块名 上下标（Latin-1 兼容语义归类）",
          block_name("²") == "Superscripts and Subscripts", block_name("²"))
    check("区块名 数字形式 ½", block_name("½") == "Number Forms", block_name("½"))
    check("Latin-1 数学符号识别", all(is_math_char(c) for c in "±×÷°²½"),
          str([c for c in "±×÷°²½" if not is_math_char(c)]))
    crit = check_critical_chars("∑∫απ²₁→𝒙")
    check("重点区块覆盖 5 类", len(crit) == 5, str(list(crit)))
    prof = char_profile("α∑中A")
    check("字符画像统计正确", prof.get("Greek and Coptic") == 1 and prof.get("ASCII") == 1, str(prof))

    print()
    print("=" * 70)
    print("二、上下标标注 mark_script")
    print("=" * 70)
    check("unicode 模式标记上标", PDFDocument.mark_script("2", "super") == "\u00b2")
    check("unicode 模式标记下标", PDFDocument.mark_script("1", "sub") == "\u2081")
    check("unicode 不可映射回退 LaTeX", PDFDocument.mark_script("q", "super") == "^{q}",
          PDFDocument.mark_script("q", "super"))
    check("latex 模式上标", PDFDocument.mark_script("2", "super", SCRIPT_MODE_LATEX) == "^{2}")
    check("latex 模式下标", PDFDocument.mark_script("n", "sub", SCRIPT_MODE_LATEX) == "_{n}")
    check("plain 模式不标注", PDFDocument.mark_script("2", "super", SCRIPT_MODE_PLAIN) == "2")

    # 符号变体（用户场景：10^{‒43} 里的 ‒ 是 U+2012 FIGURE DASH）
    check("FIGURE DASH 上标 → ⁻⁴³",
          PDFDocument.mark_script("\u201243", "super") == "\u207b\u2074\u00b3",
          repr(PDFDocument.mark_script("\u201243", "super")))
    check("FIGURE DASH 在 latex 模式下归一为合法标记",
          PDFDocument.mark_script("\u201243", "super", SCRIPT_MODE_LATEX) == "^{-43}",
          repr(PDFDocument.mark_script("\u201243", "super", SCRIPT_MODE_LATEX)))
    check("关闭变体归一时严格保留原始码位",
          PDFDocument.mark_script("\u201243", "super", SCRIPT_MODE_LATEX,
                                  normalize_variants=False) == "^{\u201243}",
          repr(PDFDocument.mark_script("\u201243", "super", SCRIPT_MODE_LATEX,
                                       normalize_variants=False)))
    check("含花括号的上下标不会破坏标记",
          PDFDocument.mark_script("a}b", "super", SCRIPT_MODE_LATEX) == "^{a)b}",
          repr(PDFDocument.mark_script("a}b", "super", SCRIPT_MODE_LATEX)))

    print()
    print("=" * 70)
    print("三、行组装 _assemble_line（字号 + 基线识别上下标）")
    print("=" * 70)
    spans_super = [
        {"text": "E = mc", "size": 11.0, "origin_y": 150.0, "flags": 0},
        {"text": "2", "size": 7.0, "origin_y": 147.0, "flags": 0},
    ]
    text, marks = PDFDocument._assemble_line(spans_super, 11.0, SCRIPT_MODE_UNICODE)
    check("上标识别 → mc²", text.endswith("mc\u00b2") and marks == 1, repr(text))

    spans_sub = [
        {"text": "a", "size": 11.0, "origin_y": 180.0, "flags": 0},
        {"text": "1", "size": 7.0, "origin_y": 183.0, "flags": 0},
    ]
    text, marks = PDFDocument._assemble_line(spans_sub, 11.0, SCRIPT_MODE_UNICODE)
    check("下标识别 → a₁", text == "a\u2081" and marks == 1, repr(text))

    text, _ = PDFDocument._assemble_line(spans_super, 11.0, SCRIPT_MODE_LATEX)
    check("latex 模式下上标 → mc^{2}", text.endswith("mc^{2}"), repr(text))

    text, marks = PDFDocument._assemble_line(spans_super, 11.0, SCRIPT_MODE_PLAIN)
    check("plain 模式不加标记", text.endswith("mc2") and marks == 0, repr(text))

    spans_flag = [
        {"text": "sin", "size": 11.0, "origin_y": 100.0, "flags": 0},
        {"text": "2", "size": 11.0, "origin_y": 97.0, "flags": 1},  # bit0 = superscript
    ]
    text, marks = PDFDocument._assemble_line(spans_flag, 11.0, SCRIPT_MODE_UNICODE)
    check("字体上标标志生效", text.endswith("sin\u00b2"), repr(text))

    spans_body = [
        {"text": CJK_SAMPLE, "size": 10.0, "origin_y": 110.0, "flags": 0},
    ]
    text, marks = PDFDocument._assemble_line(spans_body, 11.0, SCRIPT_MODE_UNICODE)
    check("正文不受影响", text == CJK_SAMPLE and marks == 0, repr(text))

    text, _ = PDFDocument._assemble_line(spans_super, 0.0, SCRIPT_MODE_UNICODE)
    check("未知正文字号时不误判", "的平方" not in text and "mc" in text, repr(text))

    # 用户场景：10 的上标内容为 "‒43"（FIGURE DASH 负号）
    spans_neg = [
        {"text": "t = 0 up to t = 10", "size": 11.0, "origin_y": 215.0, "flags": 0,
         "bbox_x0": 50.0, "bbox_x1": 150.0},
        {"text": "\u201243", "size": 7.0, "origin_y": 212.0, "flags": 0,
         "bbox_x0": 150.0, "bbox_x1": 162.0},
    ]
    text, marks = PDFDocument._assemble_line(spans_neg, 11.0, SCRIPT_MODE_UNICODE)
    check("破折号负号上标 → 10⁻⁴³", text.endswith("10\u207b\u2074\u00b3"), repr(text))

    text, _ = PDFDocument._assemble_line(spans_neg, 11.0, SCRIPT_MODE_LATEX)
    check("latex 模式下 → 10^{-43}", text.endswith("10^{-43}"), repr(text))

    text, _ = PDFDocument._assemble_line(
        spans_neg, 11.0, SCRIPT_MODE_LATEX, normalize_variants=False
    )
    check("关闭变体归一时保留 ‒", text.endswith("10^{\u201243}"), repr(text))

    print()
    print("=" * 70)
    print("四、合成 PDF 端到端（中文 + 数学符号）")
    print("=" * 70)
    tmp_pdf = os.path.join(tempfile.gettempdir(), "_fidelity_synth.pdf")
    built = _make_synthetic_pdf(tmp_pdf)
    if not built:
        print("[SKIP] 未找到可用字体，跳过合成 PDF 测试")
    else:
        doc = PDFReader.load(tmp_pdf)
        clean = doc.get_page_text_clean(0)

        missing = [c for c in MATH_SAMPLE if c != " " and c not in clean]
        check("数学符号零丢失", not missing, f"丢失: {''.join(missing)}")
        check("希腊字母原样保留", all(c in clean for c in "αβγΩπ"))
        check("数学运算符原样保留", all(c in clean for c in "∑∫√≤≥≈±×÷∈∞"))
        check("上下标字符原样保留", all(c in clean for c in "²³₁"))
        check("Unicode 分数原样保留（非 ½ → 二分之一）",
              "½" in clean and "⅓" in clean and "二分之一" not in clean)
        check("度符号未被改写为『度』字", "°" in clean and "摄氏度" not in clean)
        check("中文内容原样保留", CJK_SAMPLE.rstrip("。")[:12] in clean)
        check("中文未被符号化改写", "阿尔法" not in clean and "求和" not in clean)
        check("NBSP 已归一为普通空格", "\u00a0" not in clean)
        check("模拟上标已标注（unicode 模式）", "mc\u00b2" in clean, repr(clean))
        check("模拟下标已标注（unicode 模式）", "a\u2081" in clean, repr(clean))
        check("标注计数 > 0", doc.script_marks >= 2, str(doc.script_marks))
        # 用户场景：10^‒43 → 10⁻⁴³（负号变体已归一）
        check("破折号负号场景 → 10⁻⁴³",
              "10\u207b\u2074\u00b3" in clean,
              repr([s for s in clean.split("\n\n") if "10" in s][-1:]))

        doc_latex = PDFReader.load(tmp_pdf, script_mode=SCRIPT_MODE_LATEX)
        clean_latex = doc_latex.get_page_text_clean(0)
        check("latex 模式上标标记", "mc^{2}" in clean_latex, repr(clean_latex))
        check("latex 模式下标标记", "a_{1}" in clean_latex, repr(clean_latex))
        check("latex 模式负号合法 → 10^{-43}", "10^{-43}" in clean_latex)
        check("latex 标记内不含破折号变体", "\u2012" not in clean_latex)

        doc_strict = PDFReader.load(tmp_pdf, normalize_script_signs=False)
        check("关闭变体归一时严格保留 ‒43",
              "10^{\u201243}" in doc_strict.get_page_text_clean(0),
              repr(doc_strict.get_page_text_clean(0)[-60:]))

        doc_plain = PDFReader.load(tmp_pdf, script_mode=SCRIPT_MODE_PLAIN)
        check("plain 模式不标注", "mc2" in doc_plain.get_page_text_clean(0))

        # 提取层 / 朗读层解耦
        before = doc.get_page_text_clean(0)
        speech = doc.get_page_text_for_speech(0)
        after = doc.get_page_text_clean(0)
        check("朗读文本与保真文本不同", speech != before)
        check("朗读调用不污染提取结果", before == after)
        check("朗读文本做了中文读法转换",
              ("阿尔法" in speech or "派" in speech or "求和" in speech), speech[:120])

        os.remove(tmp_pdf)

    print()
    print("=" * 70)
    print("五、真实 PDF 端到端（英文书，22 万字符级）")
    print("=" * 70)
    real_pdf = "30-Second Astronomy The 50 most mindblowing discoveries in astronomy each explained in half a minute (Francois Fressin) (Z-Library).pdf"
    if not os.path.exists(real_pdf):
        print("[SKIP] 未找到样例 PDF")
    else:
        doc = PDFReader.load(real_pdf)
        full_clean = doc.get_full_text()
        raw_len = sum(len(p) for p in doc.pages)
        clean_len = len(full_clean)
        check("全文提取成功率 > 95%", clean_len / raw_len > 0.95,
              f"raw={raw_len} clean={clean_len}")

        for ch, name in [("\u2019", "右单引号"), ("\u2013", "连接号"), ("\u00b0", "度符号"),
                         ("\u00d7", "乘号"), ("\u00bd", "½"), ("\u2153", "⅓"),
                         ("\u00a9", "版权符"), ("\u00e9", "é")]:
            check(f"{name} {ch!r} 原样保留", ch in full_clean)

        # 反向验证：不应出现任何"符号→中文"改写痕迹
        for bad in ["二分之一", "三分之一", "摄氏度", "阿尔法", "贝塔", "求和符号", "根号"]:
            check(f"未出现中文改写『{bad}』", bad not in full_clean)

        crit = check_critical_chars(full_clean)
        print(f"       重点区块统计: {crit}")

        # 朗读层与提取层解耦
        page5_clean = doc.get_page_text_clean(4)
        page5_speech = doc.get_page_text_for_speech(4)
        check("真实文档：朗读文本独立于保真文本", page5_speech != page5_clean or len(page5_clean) == 0)

    print()
    print("=" * 70)
    print("六、边界与容错")
    print("=" * 70)
    try:
        PDFReader.load(real_pdf if os.path.exists(real_pdf) else __file__, script_mode="bogus")
        check("非法 script_mode 抛 ValueError", False)
    except ValueError:
        check("非法 script_mode 抛 ValueError", True)

    empty_text, marks = PDFDocument._assemble_line([], 10.0, SCRIPT_MODE_UNICODE)
    check("空行安全", empty_text == "" and marks == 0)

    doc_ok = PDFDocument(file_path="x", page_count=0)
    check("空文档接口安全",
          doc_ok.get_page_text(0) == "" and doc_ok.get_full_text() == "")

    passed = sum(1 for r in _results if r)
    total = len(_results)
    print()
    print(f"===== 测试结果: {passed}/{total} 通过 =====")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
