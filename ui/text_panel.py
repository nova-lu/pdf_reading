"""
文本预览面板组件

负责：
- 显示 PDF 当前页面文本（按段落排版）
- 句子高亮（朗读进度同步，基于 clean_text 偏移精确定位）
- 选中文本朗读（右键菜单）
- 字号缩放与字体切换（标题栏控件 + Ctrl+滚轮，配置持久化由主窗口负责）
- 空状态提示
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, font as tkfont
from typing import Callable, Optional


class TextPanel:
    """PDF 文本预览面板。"""

    # 主题色（与主窗口一致）
    COLOR_PANEL = "#ffffff"
    COLOR_HIGHLIGHT = "#fef08a"
    COLOR_TEXT = "#1e293b"
    COLOR_TEXT_MUTED = "#64748b"

    # 字号范围（正文字号；标题自动 +2，占位提示 +1）
    FONT_SIZE_MIN = 12
    FONT_SIZE_MAX = 48
    FONT_SIZE_STEP = 2
    DEFAULT_FONT_SIZE = 16
    DEFAULT_FONT_FAMILY = "微软雅黑"

    # 候选字体（按可用性过滤后展示）：中文阅读优先，其次西文
    FONT_FAMILY_CANDIDATES = [
        "微软雅黑", "Microsoft YaHei UI", "宋体", "楷体", "仿宋",
        "等线", "黑体", "Noto Serif SC", "Noto Sans SC",
        "Segoe UI", "Georgia", "Times New Roman", "Consolas",
    ]

    def __init__(self, parent, styles):
        """
        Args:
            parent: 父容器 (ttk.Frame)
            styles: 样式配置字典，包含主题色
        """
        self._styles = styles
        self._on_read_selection: Optional[Callable[[str], None]] = None
        # 排版变化回调：(font_family, font_size) -> None，供主窗口持久化
        self._on_typography_changed: Optional[Callable[[str, int], None]] = None

        # 当前页 clean_text（与高亮偏移计算保持一致）
        self._current_text: str = ""
        # 每个段落在 clean_text 中的 (start_offset, end_offset)
        self._paragraph_offsets: list[tuple[int, int]] = []

        # 排版状态
        self._font_family: str = self.DEFAULT_FONT_FAMILY
        self._font_size: int = self.DEFAULT_FONT_SIZE
        self._available_families: list[str] = self._detect_font_families()

        panel = ttk.Frame(parent, style="Panel.TFrame")
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        # 标题栏
        header = ttk.Frame(panel, style="Panel.TFrame")
        header.pack(fill=tk.X, padx=12, pady=(10, 6))

        self.lbl_doc_title = ttk.Label(header, text="未打开文件", style="Title.TLabel")
        self.lbl_doc_title.pack(side=tk.LEFT)

        self.lbl_page_info = ttk.Label(header, text="", style="Panel.TLabel", foreground=self.COLOR_TEXT_MUTED)
        self.lbl_page_info.pack(side=tk.RIGHT)

        # ---- 排版控件（从右往左：页码 | A+ 字号 A- | 字体下拉）----
        self.btn_font_plus = tk.Button(
            header, text="A+", font=("Microsoft YaHei UI", 9),
            bg="#e2e8f0", fg=self.COLOR_TEXT, activebackground="#cbd5e1",
            relief=tk.FLAT, bd=0, padx=7, pady=2, cursor="hand2",
            command=self.zoom_in,
        )
        self.btn_font_plus.pack(side=tk.RIGHT, padx=(6, 0))

        self.lbl_font_size = ttk.Label(
            header, text=str(self._font_size), style="Panel.TLabel",
            foreground=self.COLOR_TEXT_MUTED, width=3, anchor=tk.CENTER,
        )
        self.lbl_font_size.pack(side=tk.RIGHT, padx=(2, 0))

        self.btn_font_minus = tk.Button(
            header, text="A-", font=("Microsoft YaHei UI", 9),
            bg="#e2e8f0", fg=self.COLOR_TEXT, activebackground="#cbd5e1",
            relief=tk.FLAT, bd=0, padx=7, pady=2, cursor="hand2",
            command=self.zoom_out,
        )
        self.btn_font_minus.pack(side=tk.RIGHT, padx=(8, 0))

        self.combo_font = ttk.Combobox(
            header, state="readonly", width=11, values=self._available_families,
        )
        self.combo_font.set(self._font_family)
        self.combo_font.pack(side=tk.RIGHT)
        self.combo_font.bind("<<ComboboxSelected>>", self._on_family_selected)

        # 文本区域
        text_frame = ttk.Frame(panel, style="Panel.TFrame")
        text_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))

        self.text_area = scrolledtext.ScrolledText(
            text_frame,
            wrap=tk.WORD,
            font=(self._font_family, self._font_size),
            bg=self.COLOR_PANEL,
            fg=self.COLOR_TEXT,
            relief=tk.FLAT,
            bd=0,
            padx=20,
            pady=16,
            spacing1=2,
            spacing3=2,
            state=tk.DISABLED,
            cursor="arrow",
            selectbackground="#93c5fd",
            selectforeground=self.COLOR_TEXT,
            exportselection=True,
        )
        self.text_area.pack(fill=tk.BOTH, expand=True)

        # 段落 / 标题 / 占位样式（字体在 _apply_typography 中按字号统一设置）
        self.text_area.tag_configure("paragraph")
        self.text_area.tag_configure("heading")
        # 高亮标签
        self.text_area.tag_configure("highlight", background=self.COLOR_HIGHLIGHT, foreground=self.COLOR_TEXT)
        self.text_area.tag_configure("placeholder", foreground=self.COLOR_TEXT_MUTED, justify=tk.CENTER)

        # 绑定选择和右键菜单
        self._bind_context_menu()
        # Ctrl+滚轮缩放
        self.text_area.bind("<Control-MouseWheel>", self._on_ctrl_mousewheel)
        # 应用初始排版
        self._apply_typography()
        self._show_placeholder()

    # ------------------------------------------------------------------
    # 排版：字号 / 字体
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_font_families() -> list[str]:
        """按候选顺序返回系统实际安装的字体（保证下拉框不会出现无效字体）。"""
        try:
            installed = set(tkfont.families())
        except tk.TclError:
            installed = set()
        families = [f for f in TextPanel.FONT_FAMILY_CANDIDATES if f in installed]
        if not families:
            families = [TextPanel.DEFAULT_FONT_FAMILY]
        return families

    def _apply_typography(self):
        """把当前字体/字号应用到文本控件与各标签。

        Text widget 的 tag 字体优先级高于控件默认字体，因此段落、标题、
        占位提示都需要显式设置，否则调整字号后部分文本不会变化。
        """
        family, size = self._font_family, self._font_size
        self.text_area.configure(font=(family, size))

        indent = max(0, int(size * 1.5))
        self.text_area.tag_configure(
            "paragraph",
            spacing1=max(2, int(size * 0.45)),
            spacing3=max(2, int(size * 0.45)),
            lmargin1=indent,
            lmargin2=indent,
        )
        self.text_area.tag_configure(
            "heading",
            spacing1=max(4, int(size * 0.6)),
            spacing3=max(2, int(size * 0.5)),
            lmargin1=0,
            lmargin2=0,
            font=(family, size + 2, "bold"),
        )
        self.text_area.tag_configure(
            "placeholder",
            font=(family, size + 1),
        )
        self.lbl_font_size.config(text=str(size))

    def _notify_typography_changed(self):
        self.combo_font.set(self._font_family)
        if self._on_typography_changed:
            try:
                self._on_typography_changed(self._font_family, self._font_size)
            except Exception:
                pass

    def _on_family_selected(self, event=None):
        family = self.combo_font.get()
        if family:
            self.set_font_family(family)

    def _on_ctrl_mousewheel(self, event):
        """Ctrl+滚轮缩放（Windows 下 delta 为 120 的整数倍）。"""
        if event.delta > 0:
            self.zoom_in()
        elif event.delta < 0:
            self.zoom_out()
        return "break"

    def set_font_family(self, family: str):
        """设置正文字体（无效字体静默忽略）。"""
        if not family or family == self._font_family:
            return
        if family not in self._available_families:
            return
        self._font_family = family
        self._apply_typography()
        self._notify_typography_changed()

    def set_font_size(self, size: int):
        """设置正文字号（自动夹取到允许范围并取整）。"""
        try:
            size = int(size)
        except (TypeError, ValueError):
            return
        size = max(self.FONT_SIZE_MIN, min(self.FONT_SIZE_MAX, size))
        if size == self._font_size:
            return
        self._font_size = size
        self._apply_typography()
        self._notify_typography_changed()

    def zoom_in(self):
        self.set_font_size(self._font_size + self.FONT_SIZE_STEP)

    def zoom_out(self):
        self.set_font_size(self._font_size - self.FONT_SIZE_STEP)

    def reset_zoom(self):
        """恢复默认字体与字号。"""
        self._font_family = self.DEFAULT_FONT_FAMILY
        self._font_size = self.DEFAULT_FONT_SIZE
        self._apply_typography()
        self._notify_typography_changed()

    def set_on_typography_changed(self, callback: Optional[Callable[[str, int], None]]):
        """注册排版变化回调（主窗口用于持久化配置）。"""
        self._on_typography_changed = callback

    @property
    def font_family(self) -> str:
        return self._font_family

    @property
    def font_size(self) -> int:
        return self._font_size

    @property
    def available_families(self) -> list[str]:
        return list(self._available_families)

    def _bind_context_menu(self):
        """绑定右键菜单。"""
        self.context_menu = tk.Menu(self.text_area, tearoff=False, bg=self.COLOR_PANEL,
                                    fg=self.COLOR_TEXT, activebackground="#e2e8f0",
                                    font=("Microsoft YaHei UI", 10))
        self.context_menu.add_command(label="朗读选中内容", command=self._on_read_selection_menu)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="复制", command=self._copy_selection)
        self.context_menu.add_command(label="全选", command=self._select_all)

        # 右键弹出菜单
        self.text_area.bind("<Button-3>", self._show_context_menu)
        # 左键释放后高亮选区
        self.text_area.bind("<ButtonRelease-1>", self._on_selection_changed)

    def _show_context_menu(self, event):
        """显示右键菜单。"""
        try:
            selected = self.text_area.tag_ranges(tk.SEL)
            # 如果有选区且右键在选区内，才启用“朗读选中内容”
            if selected:
                self.context_menu.entryconfig("朗读选中内容", state=tk.NORMAL)
            else:
                self.context_menu.entryconfig("朗读选中内容", state=tk.DISABLED)
            self.context_menu.post(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def _on_selection_changed(self, event=None):
        """选区变化时触发回调（供外部读取）。"""
        pass

    def _on_read_selection_menu(self):
        """右键菜单：朗读选中内容。"""
        selected = self.get_selection_text()
        if selected and self._on_read_selection:
            self._on_read_selection(selected)

    def _copy_selection(self):
        """复制选中文本到剪贴板。"""
        selected = self.get_selection_text()
        if selected:
            self.text_area.clipboard_clear()
            self.text_area.clipboard_append(selected)

    def _select_all(self):
        """全选文本。"""
        self.text_area.config(state=tk.NORMAL)
        self.text_area.tag_add(tk.SEL, "1.0", tk.END)
        self.text_area.config(state=tk.DISABLED)

    def set_on_read_selection(self, callback: Optional[Callable[[str], None]]):
        """设置“朗读选中内容”的回调。"""
        self._on_read_selection = callback

    def get_selection_text(self) -> str:
        """获取当前选中的文本。"""
        try:
            return self.text_area.get(tk.SEL_FIRST, tk.SEL_LAST).strip()
        except tk.TclError:
            return ""

    def _show_placeholder(self):
        """显示空状态提示。"""
        self._current_text = ""
        self._paragraph_offsets = []
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete("1.0", tk.END)
        self.text_area.insert("1.0",
                              "\n\n\n\n\n  请点击「打开 PDF」选择一个文档开始阅读\n\n"
                              "  支持快捷键：\n"
                              "    Ctrl+O  打开文件\n"
                              "    空格    播放/暂停\n"
                              "    Esc    停止朗读\n"
                              "    ← / →  上一句 / 下一句\n"
                              "    Ctrl + 滚轮  缩放正文字号")
        self.text_area.tag_add("placeholder", "1.0", tk.END)
        self.text_area.config(state=tk.DISABLED)

    def display_page(self, text: str, page_num: int, total_pages: int, filename: str):
        """显示指定页面的文本（严格保持 clean_text 结构，便于偏移高亮）。"""
        self.text_area.config(state=tk.NORMAL)
        self.text_area.delete("1.0", tk.END)

        self._current_text = text
        self._paragraph_offsets = []

        if text.strip():
            paragraphs = text.split("\n\n")
            for idx, para in enumerate(paragraphs):
                # 段落在 clean_text 中的偏移
                # 计算：前面所有段落长度 + 前面所有 "\n\n" 分隔符长度（每个 2 字符）
                start_offset = sum(len(p) for p in paragraphs[:idx]) + idx * 2
                end_offset = start_offset + len(para)
                self._paragraph_offsets.append((start_offset, end_offset))

                # 在 Text widget 中也用 "\n\n" 分隔，保持与 clean_text 完全一致
                if idx > 0:
                    self.text_area.insert(tk.END, "\n\n")

                # 记录插入该段落前的 index
                start = self.text_area.index("end - 1c")
                self.text_area.insert(tk.END, para)
                end = self.text_area.index("end - 1c")

                tag = self._choose_paragraph_tag(para)
                self.text_area.tag_add(tag, start, end)
        else:
            self.text_area.insert("1.0", f"\n\n  [第 {page_num + 1} 页无文本内容]\n\n  该页面可能只包含图片或扫描件。")
            self.text_area.tag_add("placeholder", "1.0", tk.END)

        self.text_area.config(state=tk.DISABLED)
        self.lbl_page_info.config(text=f"第 {page_num + 1} / {total_pages} 页")
        self.lbl_doc_title.config(text=filename)

    @staticmethod
    def _choose_paragraph_tag(para: str) -> str:
        """根据段落特征选择样式标签。"""
        stripped = para.strip()
        # 较短、无句末标点的独立块视为标题/列表项
        if len(stripped) < 60 and not any(stripped.endswith(c) for c in ".。！？；!?;"):
            return "heading"
        return "paragraph"

    def _offset_to_index(self, offset: int) -> Optional[str]:
        """将 clean_text 中的字符偏移转换为 Text widget 的 line.char index。"""
        if not self._current_text or offset < 0:
            return None
        offset = min(offset, len(self._current_text))
        line = 1
        char = 0
        for i, c in enumerate(self._current_text[:offset]):
            if c == "\n":
                line += 1
                char = 0
            else:
                char += 1
        return f"{line}.{char}"

    def _find_paragraph_for_offset(self, offset: int) -> Optional[tuple[str, str]]:
        """根据 clean_text 偏移找到对应段落，返回 (para_text, start_index)。"""
        for idx, (p_start, p_end) in enumerate(self._paragraph_offsets):
            if p_start <= offset < p_end or p_start <= offset <= p_end:
                para_text = self._current_text[p_start:p_end]
                # 该段落在 Text widget 中的起始位置
                # paragraphs 之间有两个换行，所以前面所有段落 + 分隔符 = 前面总字符数
                preceding_chars = sum(len(self._current_text[s:e]) for s, e in self._paragraph_offsets[:idx]) + idx * 2
                start_idx = self._offset_to_index(preceding_chars)
                return para_text, start_idx
        return None

    def highlight_sentence(self, sentence: str, page_num: int = -1, start_offset: int = -1, end_offset: int = -1):
        """
        在文本区域高亮当前句子。

        优先使用 clean_text 偏移精确定位；如果偏移不可用或不匹配，
        则回退到段落内字符串搜索，避免全局搜索导致高亮错位。
        """
        self.text_area.config(state=tk.NORMAL)
        self.text_area.tag_remove("highlight", "1.0", tk.END)

        search_text = sentence.strip()
        if not search_text:
            self.text_area.config(state=tk.DISABLED)
            return

        # 精确偏移定位（跳过前导空白，避免高亮包含句子前的空格）
        if start_offset >= 0 and end_offset > start_offset and self._current_text:
            raw = self._current_text[start_offset:end_offset]
            # 调整起始位置，跳过前导空白
            trimmed_start = start_offset
            while trimmed_start < end_offset and self._current_text[trimmed_start].isspace():
                trimmed_start += 1
            actual = self._current_text[trimmed_start:end_offset].strip()
            expected = search_text
            # 允许因清理导致的小差异：句子开头 20 个字符匹配即可认为正确
            if actual.startswith(expected[:min(len(expected), 20)]) or expected.startswith(actual[:min(len(actual), 20)]):
                start_idx = self._offset_to_index(trimmed_start)
                end_idx = self._offset_to_index(end_offset)
                if start_idx and end_idx:
                    try:
                        self.text_area.tag_add("highlight", start_idx, end_idx)
                        self.text_area.see(start_idx)
                    except tk.TclError:
                        pass
                    self.text_area.config(state=tk.DISABLED)
                    return

        # 回退：在对应段落内搜索（限制范围，避免跨段落误匹配）
        if start_offset >= 0 and self._current_text:
            found = self._find_paragraph_for_offset(start_offset)
            if found:
                para_text, start_idx = found
                pos = self.text_area.search(search_text, start_idx, tk.END, nocase=False)
                if pos:
                    # 确保找到的位置在段落范围内
                    for idx, (p_start, p_end) in enumerate(self._paragraph_offsets):
                        if p_start <= start_offset < p_end:
                            para_end_idx = self._offset_to_index(p_end)
                            if para_end_idx and self.text_area.compare(pos, ">=", start_idx) and \
                                    self.text_area.compare(pos, "<", para_end_idx):
                                end_pos = f"{pos}+{len(search_text)}c"
                                try:
                                    self.text_area.tag_add("highlight", pos, end_pos)
                                    self.text_area.see(pos)
                                except tk.TclError:
                                    pass
                                self.text_area.config(state=tk.DISABLED)
                                return
                            break

        # 最后回退：全局搜索
        pos = self.text_area.search(search_text, "1.0", tk.END, nocase=False)
        if pos:
            end_pos = f"{pos}+{len(search_text)}c"
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
