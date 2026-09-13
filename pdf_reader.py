"""
PDF 文本提取模块（字符保真版）

使用 PyMuPDF (fitz) 解析 PDF 文档，按页提取文本内容。
支持普通 PDF 和加密 PDF（自动尝试空密码解密）。

## 字符保真原则（本模块的核心约束）

1. **不做任何字符替换**：不把 α/∑/∫/√/π 等符号改写成中文或其它读法。
2. **不做 Unicode 归一化**：不调用 unicodedata.normalize，保留原始码位，
   连字 ﬁ/ﬂ、"½/⅓"、带圈数字 ①②、全角标点等均原样输出。
3. **不删除内容字符**：零宽字符、私用区字符也照原样保留（清理属朗读层职责）。
4. **上下标结构化保留**：借助 PyMuPDF 的字号/基线/字体标志识别视觉上下标，
   并用 Unicode 上下标字符（x²、a₁）或 LaTeX 标记（x^{2}、a_{1}）显式表达，
   不丢失数学结构，也不引入自然语言改写。
5. **仅整理空白**：视觉换行转空格、段落分隔保留、跨行断词合并，
   这些是可读性所必需的结构性处理，不改变任何字符内容。

语音朗读所需的"符号 → 中文读法"转换由独立的朗读层模块
`tts_normalizer` 在**发声时刻**完成（见 PDFDocument.get_page_text_for_speech），
与提取结果完全解耦，互不影响。
"""

import re
import statistics
from dataclasses import dataclass, field
from typing import Optional

import pymupdf as fitz  # PyMuPDF (使用新 API 名)

# 兼容平铺模块与包两种导入方式
try:
    from math_unicode import normalize_signs, to_subscript, to_superscript
except ImportError:  # pragma: no cover - 作为包成员导入时
    from .math_unicode import normalize_signs, to_subscript, to_superscript

# 句子结束标点（中文 + 英文基础）
_SENTENCE_END_PUNCT = frozenset("。！？；.!?;")
# 表示语义延续的结尾标点
_CONTINUATION_PUNCT = frozenset("，、：:" "\"'" "《〈（【「『")
# 常见英文缩写（句点后继续，避免误断句）
_EN_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st",
    "e.g", "i.e", "etc", "et al", "vs", "vol", "vols",
    "inc", "ltd", "co", "corp", "llc", "plc",
    "a.m", "p.m", "am", "pm",
    "fig", "figs", "no", "nos", "pp", "pg",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
})

# 上下标表示方式
SCRIPT_MODE_UNICODE = "unicode"   # 优先 Unicode 上下标字符（x²），不可映射时回退 LaTeX
SCRIPT_MODE_LATEX = "latex"       # 统一 LaTeX 标记（x^{2}、a_{1}）
SCRIPT_MODE_PLAIN = "plain"       # 不标注，按视觉顺序平铺（最原始）
SCRIPT_MODES = (SCRIPT_MODE_UNICODE, SCRIPT_MODE_LATEX, SCRIPT_MODE_PLAIN)

# 判定为上下标的阈值（相对正文字号 / 行基线）
_SCRIPT_SIZE_RATIO = 0.82      # 小于正文字号 82% 视为候选
_SCRIPT_SIZE_MIN_RATIO = 0.40  # 过小（脚注编号以外）不作标注，避免误判
_SCRIPT_BASELINE_RATIO = 0.12  # 基线偏移超过正文 12% 判定为上/下标


@dataclass
class PDFDocument:
    """表示一个已解析的 PDF 文档（字符保真）。"""

    file_path: str
    page_count: int
    # PyMuPDF 原始提取文本（逐页，最忠实的基线）
    pages: list[str] = field(default_factory=list)
    # 结构化整理后的页面文本：段落合并 + 上下标标注（供显示与朗读定位）
    pages_clean: list[str] = field(default_factory=list)
    # 每页的段落列表
    pages_paragraphs: list[list[str]] = field(default_factory=list)
    # 上下标表示方式（unicode / latex / plain）
    script_mode: str = SCRIPT_MODE_UNICODE
    # 上下标内的符号变体（破折号/全角负号）是否归一为 ASCII 符号
    normalize_script_signs: bool = True
    # 提取过程中识别到的上下标数量（统计，便于诊断）
    script_marks: int = 0

    # ------------------------------------------------------------------
    # 文本访问
    # ------------------------------------------------------------------

    def get_page_text(self, page_num: int) -> str:
        """获取指定页码的原始提取文本（0-based）。"""
        if 0 <= page_num < len(self.pages):
            return self.pages[page_num]
        return ""

    def get_page_text_clean(self, page_num: int) -> str:
        """获取指定页码的结构化文本（0-based，字符保真）。"""
        if 0 <= page_num < len(self.pages_clean):
            return self.pages_clean[page_num]
        return ""

    def get_page_paragraphs(self, page_num: int) -> list[str]:
        """获取指定页码的段落列表（0-based，字符保真）。"""
        if 0 <= page_num < len(self.pages_paragraphs):
            return self.pages_paragraphs[page_num]
        return []

    def get_full_text(self) -> str:
        """获取全文（结构化、字符保真）。"""
        return "\n\n".join(p for p in self.pages_clean if p.strip())

    # ------------------------------------------------------------------
    # 朗读层入口（显式调用，提取结果不受影响）
    # ------------------------------------------------------------------

    def get_page_text_for_speech(self, page_num: int) -> str:
        """
        获取指定页的"语音朗读文本"。

        该文本由朗读层 tts_normalizer 生成（符号 → 中文读法、上下标读法、
        特殊字符清洗）。**仅用于 TTS 发声**，不会写回 pages/pages_clean，
        因此显示与朗读定位始终基于保真文本。
        """
        try:
            from tts_normalizer import normalize_for_tts
        except ImportError:  # pragma: no cover
            from .tts_normalizer import normalize_for_tts
        return normalize_for_tts(self.get_page_text_clean(page_num))

    def get_full_text_for_speech(self) -> str:
        """获取全文的语音朗读文本（按页处理，保持跨页一致性）。"""
        try:
            from tts_normalizer import normalize_for_tts
        except ImportError:  # pragma: no cover
            from .tts_normalizer import normalize_for_tts
        return "\n\n".join(
            normalize_for_tts(p) for p in self.pages_clean if p.strip()
        )

    # ------------------------------------------------------------------
    # 空白与段落整理（不改变字符内容）
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_page_text(text: str, blocks: Optional[list] = None) -> str:
        """
        整理 PDF 提取文本的排版空白（不做字符替换、不做 Unicode 归一化）。

        Args:
            text: 原始文本
            blocks: 可选，PyMuPDF 的 blocks 列表（优先用于重建段落）

        Returns:
            段落化文本
        """
        if not text or not text.strip():
            return ""

        if blocks:
            paragraphs = PDFDocument._clean_blocks(blocks)
            if paragraphs:
                return "\n\n".join(paragraphs)

        # 回退：纯文本启发式整理
        # 0. 不换行空格统一为普通空格（结构性空白整理）
        text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2007", " ")

        # 1. 合并跨行断词：resea-\nrch -> research（仅字母之间的连字符）
        text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)

        # 2. 规范化换行：3+ 连续换行压缩为 2 个（段落分隔）
        text = re.sub(r"\n{3,}", "\n\n", text)

        # 3. 段落内单换行转空格（双换行=段落分隔，保留）
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)

        # 4. 压缩多余空格
        text = re.sub(r"[ \t]{2,}", " ", text)

        # 5. 修正 ASCII 标点前空格
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)

        paragraphs = text.split("\n\n")
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        return "\n\n".join(paragraphs)

    @staticmethod
    def _clean_blocks(blocks: list) -> list[str]:
        """
        根据 PyMuPDF 的 blocks 重建段落列表，并合并被视觉排版拆散的同一段落。

        blocks 是 (x0, y0, x1, y1, text, block_no, block_type) 的列表。
        PDF 排版常把一句长话按视觉行拆成多个 block，本方法通过几何间距
        和行尾标点判断连续 block 是否属于同一段落，并在段落内部把换行转空格。
        """
        if not blocks:
            return []

        valid_blocks = [b for b in blocks if b[4] and b[4].strip()]
        if not valid_blocks:
            return []

        sorted_blocks = sorted(valid_blocks, key=lambda b: (b[1], b[0]))

        # 用中位行高估算排版行距
        heights = sorted(b[3] - b[1] for b in sorted_blocks)
        median_h = heights[len(heights) // 2] or 10.0
        paragraph_gap = median_h * 1.4   # 超过此值视为段落间距
        x_tolerance = median_h * 1.0     # x0 差异容忍（保留参数，便于调优）
        indent_threshold = median_h * 0.8  # 首行缩进阈值
        del x_tolerance  # 当前判定逻辑未直接使用，保留命名以便后续扩展

        paragraphs: list[list[tuple]] = []
        current = [sorted_blocks[0]]

        def _last_text(para: list) -> str:
            return para[-1][4].rstrip()

        for i in range(1, len(sorted_blocks)):
            prev = current[-1]
            curr = sorted_blocks[i]

            vertical_gap = curr[1] - prev[3]
            prev_end = _last_text(current)[-1] if _last_text(current) else ""
            force_merge = prev_end in _CONTINUATION_PUNCT or prev_end in "—–…"
            force_split = prev_end in _SENTENCE_END_PUNCT and vertical_gap > paragraph_gap

            is_paragraph_gap = vertical_gap > paragraph_gap
            # 与上一行垂直重叠 → 同一视觉行（PDF 常把上下标单独输出为一个 line，
            # 若按缩进规则处理会被误拆成新段落）
            same_line = curr[1] < prev[3] - median_h * 0.3
            is_indent = (not same_line) and (curr[0] - prev[0] > indent_threshold)

            if force_split or (is_paragraph_gap and not force_merge) or is_indent:
                paragraphs.append(PDFDocument._merge_blocks_to_text(current))
                current = [curr]
            else:
                current.append(curr)

        if current:
            paragraphs.append(PDFDocument._merge_blocks_to_text(current))

        return [p for p in paragraphs if p.strip()]

    @staticmethod
    def _merge_blocks_to_text(blocks: list[tuple]) -> str:
        """
        将属于同一段落的 blocks 合并为一段连续文本（字符内容不变）。

        仅处理：跨行断词合并、视觉换行转空格、CJK 间视觉空格、标点前空格。
        """
        sorted_blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
        lines = [b[4] for b in sorted_blocks]
        text = "\n".join(lines)

        # 不换行空格 / 窄空格 / 数字空格统一为普通空格
        # （PDF 提取常把空格输出为 NBSP，属结构性空白整理，不改变可见字符）
        text = text.replace("\u00a0", " ").replace("\u202f", " ").replace("\u2007", " ")

        # 合并跨行断词：corri-\ndor -> corridor（仅限字母之间的连字符，
        # 避免误伤数学减号、连字符数字区间 1990-\n1995）
        text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)

        # 其余视觉换行转为空格（段内换行）
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)

        # 中文字符之间的视觉空格应移除，恢复连续语义（覆盖 CJK 与假名）
        text = re.sub(
            r"([\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff])\s+([\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff])",
            r"\1\2",
            text,
        )

        # 压缩多余空白
        text = re.sub(r"[ \t]{2,}", " ", text)

        # 修正标点前空格（中文+英文）
        text = re.sub(r"\s+([,.!?;:，。！？；、：])", r"\1", text)

        # 移除破折号、省略号内部空格
        text = re.sub(r"—\s+—", "——", text)
        text = re.sub(r"…\s+…", "……", text)

        return text.strip()

    # ------------------------------------------------------------------
    # 上下标标注（结构化保留，不做语义改写）
    # ------------------------------------------------------------------

    @staticmethod
    def mark_script(
        text: str,
        kind: str,
        mode: str = SCRIPT_MODE_UNICODE,
        normalize_variants: bool = True,
    ) -> str:
        """
        把一段文本标记为上标或下标。

        Args:
            text: 上下标内容（如 "2"、"n-1"、"‒43"）
            kind: "super" 或 "sub"
            mode: unicode（优先 Unicode 字符，不可映射时回退 LaTeX）/ latex / plain
            normalize_variants: 是否把破折号/全角符号变体归一为 ASCII 符号。
                PDF 字体常把负号映射成 U+2012 FIGURE DASH，若不归一，
                10⁻⁴³ 会退化成无效的 LaTeX 标记 ``10^{‒43}``；
                归一后可正确得到 ``10⁻⁴³`` / ``10^{-43}``。

        Returns:
            标记后的文本；plain 模式或空内容时原样返回
        """
        core = text.strip()
        if not core or mode == SCRIPT_MODE_PLAIN:
            return text

        # 符号变体归一（在生成标记时进行，正文里的 – — 等排版字符不受影响）
        if normalize_variants:
            core = normalize_signs(core)

        if mode == SCRIPT_MODE_UNICODE:
            mapped = to_superscript(core, normalize_variants=False) if kind == "super" \
                else to_subscript(core, normalize_variants=False)
            if mapped is not None:
                return text.replace(text.strip(), mapped, 1)

        # LaTeX 回退：清理会破坏标记的字符，保证标记合法可解析
        safe = core.replace("{", "(").replace("}", ")").replace("\\", "/").strip()
        marker = "^{" if kind == "super" else "_{"
        return text.replace(text.strip(), f"{marker}{safe}}}", 1)

    @staticmethod
    def _page_body_metrics(span_records: list[dict]) -> tuple[float, float]:
        """
        估算页面"正文"字号与基线参考值。

        以较长 span（≥4 字符）的字号中位数作为正文字号，避免被标题/上下标带偏。
        """
        body_spans = [s for s in span_records if len(s["text"].strip()) >= 4]
        pool = body_spans or span_records
        if not pool:
            return 0.0, 0.0
        body_size = statistics.median(s["size"] for s in pool)
        return body_size, body_size

    @classmethod
    def _assemble_line(
        cls,
        spans: list[dict],
        body_size: float,
        script_mode: str,
        normalize_variants: bool = True,
    ) -> tuple[str, int]:
        """
        组装一行文本，并按字号/基线把视觉上下标转成 Unicode 或 LaTeX 标记。

        Args:
            spans: 同一行的 span 列表，每项含 text / size / origin_y / flags
            body_size: 本页正文字号（0 表示未知）
            script_mode: 上下标表示方式
            normalize_variants: 上下标内的破折号/全角符号变体是否归一为 ASCII

        Returns:
            (行文本, 标注数量)
        """
        if not spans:
            return "", 0

        # 行基线：取正文尺寸 span 的基线中位数；无正文 span 时退回整行中位数
        body_spans = [s for s in spans if body_size <= 0 or s["size"] >= body_size * 0.95]
        baseline_pool = body_spans or spans
        line_baseline = statistics.median(s["origin_y"] for s in baseline_pool)
        ref_size = body_size or statistics.median(s["size"] for s in spans)

        parts: list[str] = []
        marks = 0
        prev_x1: Optional[float] = None
        prev_y: Optional[float] = None
        # 词间空隙阈值：超过则补空格。
        # 注意 "dict" 模式不会像 "text" 模式那样自动补空格，且同一 line 对象里
        # 可能包含多个视觉行（每个视觉行一个 span），因此需要同时判断
        # 水平间隙 与 基线跳变，否则会出现 "abigger"、"minuteASTRONOMY" 这类粘连。
        gap_threshold = max(ref_size * 0.18, 0.8)
        line_jump = max(ref_size * 0.4, 1.5)

        for span in spans:
            text = span["text"]
            if not text:
                continue

            # 判定该 span 是否为视觉上下标（几何判定与表示方式无关）
            script_kind = None
            if ref_size > 0 and text.strip():
                ratio = span["size"] / ref_size
                dy = span["origin_y"] - line_baseline
                is_sup_flag = bool(span.get("flags", 0) & 1)  # PyMuPDF: bit0 = superscript
                candidate = ratio < _SCRIPT_SIZE_RATIO or is_sup_flag
                tiny = ratio < _SCRIPT_SIZE_MIN_RATIO and not is_sup_flag
                if candidate and not tiny:
                    if dy < -_SCRIPT_BASELINE_RATIO * ref_size:
                        script_kind = "super"
                    elif dy > _SCRIPT_BASELINE_RATIO * ref_size:
                        script_kind = "sub"

            if script_kind:
                marked = (
                    text if script_mode == SCRIPT_MODE_PLAIN
                    else cls.mark_script(text, script_kind, script_mode, normalize_variants)
                )
                if marked != text or script_mode == SCRIPT_MODE_PLAIN:
                    # 吸收基数与上下标之间的视觉空隙（PyMuPDF 常插入独立空格 span）
                    while parts and not parts[-1].strip():
                        parts.pop()
                    if parts:
                        parts[-1] = parts[-1].rstrip()
                    parts.append(marked.lstrip())
                    if script_mode != SCRIPT_MODE_PLAIN and marked != text:
                        marks += 1
                    prev_x1 = span.get("bbox_x1", prev_x1)
                    prev_y = span.get("origin_y", prev_y)
                    continue

            # 依据"水平间隙 / 基线跳变"补空格（span 之间或 span 内部的视觉换行）
            x0 = span.get("bbox_x0")
            y0 = span.get("origin_y")
            needs_space = False
            if parts and not parts[-1].endswith((" ", "\u00a0")) and not text.startswith((" ", "\u00a0")):
                if prev_y is not None and y0 is not None and abs(y0 - prev_y) > line_jump:
                    needs_space = True          # 新的一行（同一 line 对象内的视觉换行）
                elif prev_x1 is not None and x0 is not None and x0 - prev_x1 > gap_threshold:
                    needs_space = True          # 同一行内的词间空隙
                elif x0 is not None and prev_x1 is not None and x0 < prev_x1 - 0.5:
                    needs_space = True          # 文本回退到左边距 → 必然换行
            if needs_space:
                parts.append(" ")
            parts.append(text)
            prev_x1 = span.get("bbox_x1", prev_x1)
            prev_y = y0 if y0 is not None else prev_y

        line = "".join(parts)
        return line, marks

    @classmethod
    def _extract_page_lines(
        cls,
        page,
        script_mode: str,
        normalize_variants: bool = True,
    ) -> tuple[list[tuple], int]:
        """
        从页面的 span 级信息提取"行记录"，并把上下标结构化标注。

        Returns:
            (blocks 形式的行记录列表 [(x0,y0,x1,y1,text,no,type)], 标注数量)
        """
        data = page.get_text("dict")
        span_records: list[dict] = []
        line_groups: list[tuple[float, float, list[dict]]] = []

        for block in data.get("blocks", []):
            if block.get("type") != 0:  # 跳过图片块
                continue
            for line in block.get("lines", []):
                spans = []
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text:
                        continue
                    origin = span.get("origin", (0.0, 0.0))
                    sbbox = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
                    spans.append({
                        "text": text,
                        "size": float(span.get("size", 0.0)),
                        "origin_x": float(origin[0]),
                        "origin_y": float(origin[1]),
                        "bbox_x0": float(sbbox[0]),
                        "bbox_x1": float(sbbox[2]),
                        "flags": int(span.get("flags", 0)),
                        "font": span.get("font", ""),
                    })
                if not spans:
                    continue
                span_records.extend(spans)
                bbox = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
                line_groups.append((float(bbox[1]), float(bbox[0]), spans, bbox))

        if not line_groups:
            return [], 0

        body_size, _ = cls._page_body_metrics(span_records)
        merged_groups = cls._merge_script_lines(line_groups, body_size)

        lines: list[tuple] = []
        total_marks = 0
        for _, _, spans, bbox in merged_groups:
            text, marks = cls._assemble_line(
                spans, body_size, script_mode, normalize_variants
            )
            total_marks += marks
            if text.strip():
                lines.append((bbox[0], bbox[1], bbox[2], bbox[3], text, 0, 0))

        return lines, total_marks

    @staticmethod
    def _merge_script_lines(
        line_groups: list[tuple],
        body_size: float,
    ) -> list[tuple]:
        """
        合并"同一视觉行"的上下标碎片。

        PyMuPDF 常把小字号、抬升/下沉基线的上下标单独输出为一个 line，
        与基数所在行垂直重叠。若不合并，段落重建会把它们误判为独立段落，
        上下标检测也失去参照（既无法判断相对字号，也无法判断基线偏移）。

        合并条件（同时满足，避免误合并分栏/表格）：
        - 垂直方向重叠，或上下相邻距离 < 1/3 正文字号
        - 水平方向紧邻：间隙 < 0.6 正文字号；
          或其中一行明显更矮（< 85%）且间隙 < 3 倍正文字号（典型上下标场景）

        Returns:
            合并后的行分组列表（与输入同构）
        """
        if not line_groups:
            return []
        ref = body_size if body_size > 0 else 10.0

        merged: list[tuple] = []
        for y0, x0, spans, bbox in line_groups:
            placed = False
            for i, (my0, mx0, mspans, mbbox) in enumerate(merged):
                top = max(bbox[1], mbbox[1])
                bottom = min(bbox[3], mbbox[3])
                overlap = bottom - top
                h1 = max(bbox[3] - bbox[1], 1e-6)
                h2 = max(mbbox[3] - mbbox[1], 1e-6)
                # 垂直接近：有重叠，或上下间距很小
                v_gap = max(bbox[1], mbbox[1]) - min(bbox[3], mbbox[3])
                vertical_close = overlap > 0.4 * min(h1, h2) or v_gap < ref / 3
                if not vertical_close:
                    continue

                # 水平间隙（按 y 区间重叠判断，同列才可能紧邻）
                if bbox[2] < mbbox[0]:
                    h_gap = mbbox[0] - bbox[2]
                elif mbbox[2] < bbox[0]:
                    h_gap = bbox[0] - mbbox[2]
                else:
                    h_gap = 0.0
                height_ratio = min(h1, h2) / max(h1, h2)
                # 收紧条件：必须是"小字号碎片 + 正文行"的组合，
                # 避免把不同字号的标题/分栏内容误合并（标题字号通常 >= 正文字号）
                size1 = statistics.median(s["size"] for s in spans) if spans else ref
                size2 = statistics.median(s["size"] for s in mspans) if mspans else ref
                small_size, big_size = min(size1, size2), max(size1, size2)
                is_fragment = (
                    small_size < ref * _SCRIPT_SIZE_RATIO and big_size >= ref * 0.9
                )
                if not is_fragment:
                    continue
                horizontal_close = h_gap < ref * 0.6 or (
                    height_ratio < 0.85 and h_gap < ref * 3.0
                )
                if not horizontal_close:
                    continue

                new_bbox = (
                    min(bbox[0], mbbox[0]), min(bbox[1], mbbox[1]),
                    max(bbox[2], mbbox[2]), max(bbox[3], mbbox[3]),
                )
                sorted_spans = sorted(mspans + spans, key=lambda s: s["origin_x"])
                merged[i] = (min(my0, y0), min(mx0, x0), sorted_spans, new_bbox)
                placed = True
                break

            if not placed:
                merged.append((y0, x0, sorted(spans, key=lambda s: s["origin_x"]), bbox))

        return merged


class PDFReader:
    """PDF 文档读取器（字符保真）。"""

    @staticmethod
    def load(
        file_path: str,
        script_mode: str = SCRIPT_MODE_UNICODE,
        normalize_script_signs: bool = True,
    ) -> PDFDocument:
        """
        加载并解析 PDF 文件，忠实保留原始字符。

        Args:
            file_path: PDF 文件路径
            script_mode: 上下标表示方式
                - "unicode"：优先使用 Unicode 上下标字符（x²、a₁、10⁻⁴³），
                  无法映射时回退 LaTeX 标记，默认值
                - "latex"：统一使用 LaTeX 标记（x^{2}、a_{1}、10^{-43}）
                - "plain"：不做标注，按视觉顺序平铺（原始字符不变）
            normalize_script_signs: 上下标内的破折号/全角符号变体是否归一为
                ASCII 符号（默认 True）。PDF 字体常把负号映射为 U+2012 FIGURE
                DASH，不归一会出现 ``10^{‒43}``；归一后为 ``10⁻⁴³``。
                设为 False 可严格保留标记内的原始码位。

        Returns:
            PDFDocument 对象

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件无法解析或需要密码
        """
        if script_mode not in SCRIPT_MODES:
            raise ValueError(
                f"不支持的 script_mode: {script_mode!r}，可选 {SCRIPT_MODES}"
            )

        doc = fitz.open(file_path)

        # 尝试解密（如果加密）
        if doc.needs_pass:
            if not doc.authenticate(""):
                doc.close()
                raise ValueError("该 PDF 文件已加密，需要密码才能打开。")

        pages: list[str] = []
        pages_clean: list[str] = []
        pages_paragraphs: list[list[str]] = []
        total_marks = 0

        for page in doc:
            # 1) 原始文本：PyMuPDF 直出，保留连字与空白，作为最忠实基线
            raw = page.get_text(
                "text",
                flags=fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE,
            )
            pages.append(raw.strip() if raw else "")

            # 2) 结构化文本：span 级组装（上下标标注）+ 段落重建
            lines, marks = PDFDocument._extract_page_lines(
                page, script_mode, normalize_script_signs
            )
            total_marks += marks
            paragraphs = PDFDocument._clean_blocks(lines)
            if not paragraphs and raw:
                # span 提取失败时回退到纯文本整理（仍不做字符替换）
                paragraphs = PDFDocument._clean_page_text(raw).split("\n\n") if raw else []
                paragraphs = [p for p in paragraphs if p.strip()]

            pages_paragraphs.append(paragraphs)
            pages_clean.append("\n\n".join(paragraphs))

        page_count = doc.page_count
        doc.close()

        return PDFDocument(
            file_path=file_path,
            page_count=page_count,
            pages=pages,
            pages_clean=pages_clean,
            pages_paragraphs=pages_paragraphs,
            script_mode=script_mode,
            normalize_script_signs=normalize_script_signs,
            script_marks=total_marks,
        )

    @staticmethod
    def get_file_info(file_path: str) -> Optional[dict]:
        """获取 PDF 文件基本信息（不提取全文）。"""
        try:
            doc = fitz.open(file_path)
            info = {
                "page_count": doc.page_count,
                "metadata": doc.metadata,
                "is_encrypted": doc.needs_pass,
            }
            doc.close()
            return info
        except Exception:
            return None
