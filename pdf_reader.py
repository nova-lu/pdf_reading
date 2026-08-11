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


@dataclass
class PDFDocument:
    """表示一个已解析的 PDF 文档。"""

    file_path: str
    page_count: int
    pages: list[str] = field(default_factory=list)
    # 清理后的页面文本（用于显示和朗读）
    pages_clean: list[str] = field(default_factory=list)

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

    def get_full_text(self) -> str:
        """获取全文（清理后）。"""
        return "\n\n".join(p for p in self.pages_clean if p.strip())

    @staticmethod
    def _clean_page_text(text: str) -> str:
        """
        清理 PDF 提取的原始文本，改善排版可读性。

        处理内容：
        1. 合并跨行断词：resea-\\nrch -> research
        2. 段落内单换行转空格：保留段落结构
        3. 压缩多余空白
        4. 修正标点前空格
        """
        if not text or not text.strip():
            return ""

        # 1. 合并跨行断词：word-\\n -> word
        text = re.sub(r'-\s*\n\s*', '', text)

        # 2. 规范化换行：3+ 连续换行压缩为 2 个（段落分隔）
        text = re.sub(r'\n{3,}', '\n\n', text)

        # 3. 段落内单换行转空格（双换行=段落分隔，保留）
        text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)

        # 4. 压缩多余空格
        text = re.sub(r'[ \t]{2,}', ' ', text)

        # 5. 修正标点前空格
        text = re.sub(r'\s+([,.!?;:])', r'\1', text)

        # 6. 清理每段落首尾空白
        paragraphs = text.split('\n\n')
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        return '\n\n'.join(paragraphs)


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
        for page in doc:
            text = page.get_text("text")
            raw = text.strip() if text else ""
            pages.append(raw)
            pages_clean.append(PDFDocument._clean_page_text(raw))

        page_count = doc.page_count
        doc.close()

        return PDFDocument(
            file_path=file_path,
            page_count=page_count,
            pages=pages,
            pages_clean=pages_clean,
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
