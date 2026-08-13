"""
PDF 文本提取模块

使用 PyMuPDF (fitz) 解析 PDF 文档，按页提取文本内容。
支持普通 PDF 和加密 PDF（自动尝试空密码解密）。
提取后自动清理文本格式：合并跨行单词、段落内换行转空格、保留段落分隔。
"""

import pymupdf as fitz  # PyMuPDF (使用新 API 名)
import re
from dataclasses import dataclass, field
from typing import Optional

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


@dataclass
class PDFDocument:
    """表示一个已解析的 PDF 文档。"""

    file_path: str
    page_count: int
    pages: list[str] = field(default_factory=list)
    # 清理后的页面文本（用于显示和朗读）
    pages_clean: list[str] = field(default_factory=list)
    # 每页的段落列表（用于保留排版结构）
    pages_paragraphs: list[list[str]] = field(default_factory=list)

    def get_page_text(self, page_num: int) -> str:
        """获取指定页码的原始文本（0-based）。"""
        if 0 <= page_num < len(self.pages):
            return self.pages[page_num]
        return ""

    def get_page_text_clean(self, page_num: int) -> str:
        """获取指定页码的清理后文本（0-based）。"""
        if 0 <= page_num < len(self.pages_clean):
            return self.pages_clean[page_num]
        return ""

    def get_page_paragraphs(self, page_num: int) -> list[str]:
        """获取指定页码的段落列表（0-based）。"""
        if 0 <= page_num < len(self.pages_paragraphs):
            return self.pages_paragraphs[page_num]
        return []

    def get_full_text(self) -> str:
        """获取全文（清理后）。"""
        return "\n\n".join(p for p in self.pages_clean if p.strip())

    @staticmethod
    def _clean_page_text(text: str, blocks: Optional[list] = None) -> str:
        """
        清理 PDF 提取的原始文本，改善排版可读性。

        优先使用 PyMuPDF 的文本块（blocks）重建段落结构；
        如果未提供 blocks，则回退到纯文本启发式清理。

        处理内容：
        1. 按 blocks 识别段落边界
        2. 合并跨行断词：resea-\nrch -> research
        3. 段落内单换行转空格
        4. 压缩多余空白
        5. 修正标点前空格
        """
        if not text or not text.strip():
            return ""

        if blocks:
            paragraphs = PDFDocument._clean_blocks(blocks)
            if paragraphs:
                return "\n\n".join(paragraphs)

        # 回退：纯文本启发式清理
        # 1. 合并跨行断词：word-\n -> word
        text = re.sub(r"-\s*\n\s*", "", text)

        # 2. 规范化换行：3+ 连续换行压缩为 2 个（段落分隔）
        text = re.sub(r"\n{3,}", "\n\n", text)

        # 3. 段落内单换行转空格（双换行=段落分隔，保留）
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)

        # 4. 压缩多余空格
        text = re.sub(r"[ \t]{2,}", " ", text)

        # 5. 修正标点前空格
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)

        # 6. 清理每段落首尾空白
        paragraphs = text.split("\n\n")
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        return "\n\n".join(paragraphs)

    @staticmethod
    def _clean_blocks(blocks: list) -> list[str]:
        """
        根据 PyMuPDF 的 blocks 重建段落列表，并合并被视觉排版拆散的同一段落。

        blocks 是 (x0, y0, x1, y1, text, block_no, block_type) 的列表。
        PDF 小说排版经常把一句长话按视觉行拆成多个 block，本方法通过几何间距
        和行尾标点判断连续 block 是否属于同一段落，并在段落内部把换行转空格，
        使后续分句逻辑基于语义完整的段落文本工作。
        """
        if not blocks:
            return []

        # 过滤空 block，按阅读顺序排序（从上到下、从左到右）
        valid_blocks = [
            b for b in blocks
            if b[4] and b[4].strip()
        ]
        if not valid_blocks:
            return []

        sorted_blocks = sorted(valid_blocks, key=lambda b: (b[1], b[0]))

        # 用中位行高估算排版行距
        heights = sorted(b[3] - b[1] for b in sorted_blocks)
        median_h = heights[len(heights) // 2] or 10.0
        paragraph_gap = median_h * 1.4   # 超过此值视为段落间距
        x_tolerance = median_h * 1.0     # x0 差异容忍
        indent_threshold = median_h * 0.8  # 首行缩进阈值

        paragraphs: list[list[tuple]] = []
        current = [sorted_blocks[0]]

        def _last_text(para: list) -> str:
            return para[-1][4].rstrip()

        for i in range(1, len(sorted_blocks)):
            prev = current[-1]
            curr = sorted_blocks[i]

            prev_x0, prev_y0, prev_x1, prev_y1 = prev[0], prev[1], prev[2], prev[3]
            curr_x0, curr_y0, curr_x1, curr_y1 = curr[0], curr[1], curr[2], curr[3]
            vertical_gap = curr_y0 - prev_y1

            prev_end = _last_text(current)[-1] if _last_text(current) else ""
            force_merge = prev_end in _CONTINUATION_PUNCT or prev_end in "—–…"
            force_split = prev_end in _SENTENCE_END_PUNCT and vertical_gap > paragraph_gap

            is_paragraph_gap = vertical_gap > paragraph_gap
            is_indent = curr_x0 - prev_x0 > indent_threshold

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
        """将属于同一段落的 blocks 合并为一段连续文本。"""
        # 同一段落内偶尔有同一行多个 block，按阅读位置排序
        sorted_blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
        lines = [b[4] for b in sorted_blocks]

        # 先用换行连接，再统一处理视觉换行
        text = "\n".join(lines)

        # 合并跨行断词：corri-\n dor -> corridor
        text = re.sub(r"-\s*\n\s*", "", text)

        # 其余视觉换行转为空格（段内换行）
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)

        # 中文字符之间的视觉空格应移除，恢复连续语义
        # 覆盖常见 CJK 范围与假名
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


class PDFReader:
    """PDF 文档读取器。"""

    @staticmethod
    def load(file_path: str) -> PDFDocument:
        """
        加载并解析 PDF 文件。

        Args:
            file_path: PDF 文件路径

        Returns:
            PDFDocument 对象

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件无法解析或需要密码
        """
        doc = fitz.open(file_path)

        # 尝试解密（如果加密）
        if doc.needs_pass:
            if not doc.authenticate(""):
                doc.close()
                raise ValueError("该 PDF 文件已加密，需要密码才能打开。")

        pages = []
        pages_clean = []
        pages_paragraphs = []
        for page in doc:
            text = page.get_text("text")
            raw = text.strip() if text else ""
            pages.append(raw)

            # 使用 blocks 保留段落结构
            blocks = page.get_text("blocks")
            paragraphs = PDFDocument._clean_blocks(blocks)
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
