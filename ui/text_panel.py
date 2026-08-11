"""
文本预览面板组件

负责：
- 显示 PDF 当前页面文本
- 句子高亮（朗读进度同步）
- 空状态提示
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
from typing import Optional


class TextPanel:
    """PDF 文本预览面板。"""

    # 主题色（与主窗口一致）
    COLOR_PANEL = "#ffffff"
    COLOR_HIGHLIGHT = "#fef08a"
    COLOR_TEXT = "#1e293b"
    COLOR_TEXT_MUTED = "#64748b"

    def __init__(self, parent, styles):
        """
        Args:
            parent: 父容器 (ttk.Frame)
            styles: 样式配置字典，包含主题色
        """
        self._styles = styles

        panel = ttk.Frame(parent, style="Panel.TFrame")
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        # 标题栏
        header = ttk.Frame(panel, style="Panel.TFrame")
        header.pack(fill=tk.X, padx=12, pady=(10, 6))

        self.lbl_doc_title = ttk.Label(header, text="未打开文件", style="Title.TLabel")
        self.lbl_doc_title.pack(side=tk.LEFT)

        self.lbl_page_info = ttk.Label(header, text="", style="Panel.TLabel", foreground=self.COLOR_TEXT_MUTED)
        self.lbl_page_info.pack(side=tk.RIGHT)

        # 文本区域
        text_frame = ttk.Frame(panel, style="Panel.TFrame")
        text_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))

        self.text_area = scrolledtext.ScrolledText(
            text_frame,
            wrap=tk.WORD,
            font=("Segoe UI", 12),
            bg=self.COLOR_PANEL,
            fg=self.COLOR_TEXT,
            relief=tk.FLAT,
            bd=0,
            padx=20,
            pady=16,
            spacing1=4,
            spacing3=4,
            state=tk.DISABLED,
        )
        self.text_area.pack(fill=tk.BOTH, expand=True)

        # 高亮标签
        self.text_area.tag_configure("highlight", background=self.COLOR_HIGHLIGHT, foreground=self.COLOR_TEXT)
        self.text_area.tag_configure("placeholder", foreground=self.COLOR_TEXT_MUTED, justify=tk.CENTER,
                                     font=("Microsoft YaHei UI", 13))

        self._show_placeholder()

    def _show_placeholder(self):
        """显示空状态提示。"""
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete("1.0", tk.END)
        self.text_area.insert("1.0",
                              "\n\n\n\n\n  请点击「打开 PDF」选择一个文档开始阅读\n\n"
                              "  支持快捷键：\n"
                              "    Ctrl+O  打开文件\n"
                              "    空格    播放/暂停\n"
                              "    Esc    停止朗读\n"
                              "    ← / →  上一句 / 下一句")
        self.text_area.tag_add("placeholder", "1.0", tk.END)
        self.text_area.config(state=tk.DISABLED)

    def display_page(self, text: str, page_num: int, total_pages: int, filename: str):
        """显示指定页面的文本。"""
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete("1.0", tk.END)

        if text.strip():
            self.text_area.insert("1.0", text)
        else:
            self.text_area.insert("1.0", f"\n\n  [第 {page_num + 1} 页无文本内容]\n\n  该页面可能只包含图片或扫描件。")

        self.text_area.config(state=tk.DISABLED)
        self.lbl_page_info.config(text=f"第 {page_num + 1} / {total_pages} 页")
        self.lbl_doc_title.config(text=filename)

    def highlight_sentence(self, sentence: str):
        """在文本区域高亮当前句子（多级搜索回退）。"""
        self.text_area.config(state=tk.NORMAL)
        self.text_area.tag_remove("highlight", "1.0", tk.END)

        search_text = sentence.strip()
        if not search_text:
            self.text_area.config(state=tk.DISABLED)
            return

        # 多级搜索：完整句子 -> 前 80 字 -> 前 50 字 -> 前 30 字
        pos = None
        for prefix_len in (len(search_text), 80, 50, 30):
            candidate = search_text[:prefix_len]
            if not candidate:
                continue
            pos = self.text_area.search(candidate, "1.0", tk.END, nocase=False)
            if pos:
                break

        if pos:
            end_pos = f"{pos}+{len(sentence)}c"
            try:
                self.text_area.tag_add("highlight", pos, end_pos)
                self.text_area.see(pos)
            except tk.TclError:
                pass

        self.text_area.config(state=tk.DISABLED)

    def clear_highlight(self):
        """清除高亮。"""
        self.text_area.config(state=tk.NORMAL)
        self.text_area.tag_remove("highlight", "1.0", tk.END)
        self.text_area.config(state=tk.DISABLED)

    def show_placeholder(self):
        """显示空状态。"""
        self._show_placeholder()
