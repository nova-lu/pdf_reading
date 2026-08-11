"""
主窗口模块

协调 TextPanel、ControlPanel、ReadingController、VoiceManager 的工作：
- 打开 PDF 文件并显示
- 语音设置（引擎、语速、音量）
- 播放控制（播放/暂停/停止/跳转）
- 页面导航 + 从指定页开始朗读
- 朗读进度和高亮同步
- TTS 事件队列轮询（线程安全 UI 更新）
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import sys
import queue

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_reader import PDFReader, PDFDocument
from tts_engine import TTSEngine, ReaderState
from reading_controller import ReadingController
from voice_manager import VoiceManager
from ui.text_panel import TextPanel
from ui.control_panel import ControlPanel


class PDFVoiceReaderApp:
    """PDF 语音阅读助手主应用。"""

    # 主题色
    COLOR_BG = "#f0f0f0"
    COLOR_PANEL = "#ffffff"
    COLOR_ACCENT = "#2563eb"
    COLOR_ACCENT_HOVER = "#1d4ed8"
    COLOR_TEXT = "#1e293b"
    COLOR_TEXT_MUTED = "#64748b"
    COLOR_BORDER = "#e2e8f0"
    COLOR_SUCCESS = "#16a34a"
    COLOR_DANGER = "#dc2626"

    WINDOW_WIDTH = 1100
    WINDOW_HEIGHT = 720

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PDF 语音阅读助手")
        self.root.geometry(f"{self.WINDOW_WIDTH}x{self.WINDOW_HEIGHT}")
        self.root.minsize(900, 600)
        self.root.configure(bg=self.COLOR_BG)

        # 核心组件
        self.voice_manager = VoiceManager()
        self.tts = TTSEngine(self.voice_manager)
        self.controller = ReadingController(self.tts)

        # PDF 文档
        self.pdf_doc: PDFDocument | None = None

        # TTS 事件队列（worker 线程 -> 主线程）
        self._tts_event_queue: queue.Queue = queue.Queue()

        # 注册控制器回调（通过队列线程安全传递）
        self.controller.set_callbacks(
            on_page_change=lambda page: self._tts_event_queue.put(("page_change", page)),
            on_sentence_highlight=lambda idx, page, sentence: self._tts_event_queue.put(("highlight", idx, page, sentence)),
            on_state_change=lambda state: self._tts_event_queue.put(("state_change", state)),
            on_finish=lambda: self._tts_event_queue.put(("finish", None)),
        )

        # 构建 UI
        self._build_menu()
        self._build_main_layout()
        self._build_status_bar()
        self._apply_styles()

        # 初始化语音列表
        self._refresh_voice_list()

        # 窗口关闭处理
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 启动事件轮询
        self._poll_tts_events()

    # ==================================================================
    # 样式
    # ==================================================================

    def _apply_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("TFrame", background=self.COLOR_BG)
        style.configure("Panel.TFrame", background=self.COLOR_PANEL)
        style.configure("TLabel", background=self.COLOR_BG, foreground=self.COLOR_TEXT,
                        font=("Microsoft YaHei UI", 10))
        style.configure("Panel.TLabel", background=self.COLOR_PANEL, foreground=self.COLOR_TEXT,
                        font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background=self.COLOR_PANEL, foreground=self.COLOR_TEXT,
                        font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Status.TLabel", background=self.COLOR_BG, foreground=self.COLOR_TEXT_MUTED,
                        font=("Microsoft YaHei UI", 9))

        style.configure("TButton", font=("Microsoft YaHei UI", 10))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TCombobox", font=("Microsoft YaHei UI", 10))
        style.configure("Horizontal.TScale", background=self.COLOR_PANEL)
        style.configure("Panel.TCheckbutton", background=self.COLOR_PANEL, foreground=self.COLOR_TEXT,
                        font=("Microsoft YaHei UI", 10))

    # ==================================================================
    # 菜单栏
    # ==================================================================

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="打开 PDF...", command=self._open_pdf, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.root.config(menu=menubar)

        # 快捷键
        self.root.bind("<Control-o>", lambda e: self._open_pdf())
        self.root.bind("<space>", lambda e: self._toggle_play_pause())
        self.root.bind("<Escape>", lambda e: self._stop_reading())
        self.root.bind("<Left>", lambda e: self._skip_prev())
        self.root.bind("<Right>", lambda e: self._skip_next())

    # ==================================================================
    # 主布局
    # ==================================================================

    def _build_main_layout(self):
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 0))

        main_frame.columnconfigure(0, weight=7)
        main_frame.columnconfigure(1, weight=3)
        main_frame.rowconfigure(0, weight=1)

        self.text_panel = TextPanel(main_frame, styles={})
        self.control_panel = ControlPanel(main_frame)

        # 绑定回调
        self.control_panel.set_callbacks(
            on_play_pause=self._toggle_play_pause,
            on_stop=self._stop_reading,
            on_skip_prev=self._skip_prev,
            on_skip_next=self._skip_next,
            on_prev_page=self._prev_page,
            on_next_page=self._next_page,
            on_jump_page=self._jump_to_page,
            on_play_from_here=self._play_from_here,
            on_voice_changed=self._on_voice_changed,
            on_rate_changed=self._on_rate_changed,
            on_volume_changed=self._on_volume_changed,
            on_read_range_changed=self._on_read_range_changed,
        )

    # ==================================================================
    # 状态栏
    # ==================================================================

    def _build_status_bar(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X, padx=12, pady=(6, 10))

        self.lbl_status = ttk.Label(bar, text="就绪", style="Status.TLabel")
        self.lbl_status.pack(side=tk.LEFT)

        self.lbl_voice_count = ttk.Label(bar, text="", style="Status.TLabel")
        self.lbl_voice_count.pack(side=tk.RIGHT)

    # ==================================================================
    # PDF 文件操作
    # ==================================================================

    def _open_pdf(self):
        file_path = filedialog.askopenfilename(
            title="选择 PDF 文件",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        if not file_path:
            return

        self._stop_reading()
        try:
            self.pdf_doc = PDFReader.load(file_path)
            self.controller.set_document(self.pdf_doc)
            self._display_page(0)
            self._update_page_controls()
            filename = os.path.basename(file_path)
            self.text_panel.lbl_doc_title.config(text=filename)
            self._set_status(f"已加载：{filename}（共 {self.pdf_doc.page_count} 页）")
        except ValueError as e:
            messagebox.showerror("错误", str(e))
        except Exception as e:
            messagebox.showerror("错误", f"无法打开 PDF 文件：\n{e}")

    def _display_page(self, page_num: int):
        """显示指定页面。"""
        if not self.pdf_doc:
            return

        self.controller.current_page = page_num
        text = self.pdf_doc.get_page_text_clean(page_num)
        filename = os.path.basename(self.pdf_doc.file_path) if self.pdf_doc.file_path else ""
        self.text_panel.display_page(text, page_num, self.pdf_doc.page_count, filename)

    # ==================================================================
    # 页面导航
    # ==================================================================

    def _prev_page(self):
        if self.pdf_doc and self.controller.current_page > 0:
            self._stop_reading()
            self._display_page(self.controller.current_page - 1)
            self._update_page_controls()
            self._set_status(f"第 {self.controller.current_page + 1} 页")

    def _next_page(self):
        if self.pdf_doc and self.controller.current_page < self.pdf_doc.page_count - 1:
            self._stop_reading()
            self._display_page(self.controller.current_page + 1)
            self._update_page_controls()
            self._set_status(f"第 {self.controller.current_page + 1} 页")

    def _jump_to_page(self):
        if not self.pdf_doc:
            return
        page = self.control_panel.get_selected_page()
        if 0 <= page < self.pdf_doc.page_count:
            self._stop_reading()
            self._display_page(page)
            self._update_page_controls()
            self._set_status(f"第 {page + 1} 页")
        else:
            messagebox.showwarning("提示", f"页码范围：1 - {self.pdf_doc.page_count}")

    def _play_from_here(self):
        """从当前页开始朗读。"""
        if not self.pdf_doc:
            messagebox.showinfo("提示", "请先打开一个 PDF 文件")
            return
        # 确保是朗读全部页面模式（从当前页开始）
        self.controller.read_all_pages = True
        self.control_panel.var_read_all.set(True)
        # 停止当前朗读
        self._stop_reading()
        # 开始朗读
        self._start_playback()
        self._set_status(f"从第 {self.controller.current_page + 1} 页开始朗读...")

    def _update_page_controls(self):
        if not self.pdf_doc:
            self.control_panel.update_page_controls(0, 0)
            return
        self.control_panel.update_page_controls(self.controller.current_page, self.pdf_doc.page_count)

    # ==================================================================
    # 语音设置
    # ==================================================================

    def _refresh_voice_list(self):
        voices = self.voice_manager.voices
        if not voices:
            self.control_panel.update_voices(["（无可用语音）"])
            self.lbl_voice_count.config(text="未检测到语音引擎")
            return

        display_names = []
        for v in voices:
            label = v.name
            if v.languages:
                label += f"  [{v.languages}]"
            if v.provider == "edge":
                label += " (在线)"
            display_names.append(label)

        self.control_panel.update_voices(display_names, select_index=0)
        self.controller.set_voice(voices[0].id)
        self.lbl_voice_count.config(text=f"共 {len(voices)} 个语音")

    def _on_voice_changed(self, event=None):
        idx = self.control_panel.current_voice_index
        voices = self.voice_manager.voices
        if 0 <= idx < len(voices):
            self.controller.set_voice(voices[idx].id)
            self._set_status(f"已切换语音：{voices[idx].name}")

    def _on_rate_changed(self, value):
        rate = int(float(value))
        self.controller.set_rate(rate)
        self.control_panel.set_rate_display(rate)

    def _on_volume_changed(self, value):
        vol = int(float(value))
        self.controller.set_volume(vol / 100.0)
        self.control_panel.set_volume_display(vol)

    def _on_read_range_changed(self):
        self.controller.read_all_pages = self.control_panel.read_all_pages
        if self.controller.read_all_pages:
            self._set_status("朗读范围：从当前页到末尾")
        else:
            self._set_status(f"朗读范围：仅第 {self.controller.current_page + 1} 页")

    # ==================================================================
    # 播放控制
    # ==================================================================

    def _toggle_play_pause(self):
        if not self.pdf_doc:
            messagebox.showinfo("提示", "请先打开一个 PDF 文件")
            return

        state = self.controller.state
        if state == ReaderState.PLAYING:
            self.controller.pause()
            self._set_status("已暂停")
        elif state == ReaderState.PAUSED:
            self.controller.resume()
            self._set_status("朗读中...")
        else:
            self._start_playback()
            self._set_status("朗读中...")

    def _start_playback(self):
        """启动朗读。"""
        if not self.pdf_doc:
            return

        # 同步朗读范围设置
        self.controller.read_all_pages = self.control_panel.read_all_pages

        # 检查当前页是否有文本
        page_text = self.pdf_doc.get_page_text_clean(self.controller.current_page)
        if not page_text.strip():
            messagebox.showinfo("提示", "该文档没有可朗读的文本内容")
            return

        self.controller.play()

    def _stop_reading(self):
        self.controller.stop()
        self.text_panel.clear_highlight()
        self.control_panel.clear_progress()
        self._set_status("已停止")

    def _skip_next(self):
        self.controller.skip_next()

    def _skip_prev(self):
        self.controller.skip_prev()

    # ==================================================================
    # TTS 事件轮询（主线程安全更新 UI）
    # ==================================================================

    def _poll_tts_events(self):
        try:
            while True:
                event_type, *args = self._tts_event_queue.get_nowait()
                if event_type == "page_change":
                    self._on_event_page_change(args[0])
                elif event_type == "highlight":
                    self._on_event_highlight(args[0], args[1], args[2])
                elif event_type == "state_change":
                    self._on_event_state_change(args[0])
                elif event_type == "finish":
                    self._on_event_finish()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_tts_events)

    def _on_event_page_change(self, page: int):
        """跨页朗读时自动翻页。"""
        self._display_page(page)
        self._update_page_controls()
        self._set_status(f"朗读中... 第 {page + 1} 页")

    def _on_event_highlight(self, index: int, page: int, sentence: str):
        """更新高亮和进度。"""
        total = self.controller.total_sentences
        self.control_panel.update_progress(index, total)
        self.text_panel.highlight_sentence(sentence)

    def _on_event_state_change(self, state: ReaderState):
        """更新播放按钮。"""
        self.control_panel.update_play_button(state.value)

    def _on_event_finish(self):
        """朗读完成。"""
        self.text_panel.clear_highlight()
        self.control_panel.clear_progress()
        self.control_panel.update_play_button("idle")
        self._set_status("朗读完成")

    # ==================================================================
    # 工具方法
    # ==================================================================

    def _set_status(self, message: str):
        self.lbl_status.config(text=message)

    def _show_about(self):
        messagebox.showinfo(
            "关于",
            "PDF 语音阅读助手 v2.0\n\n"
            "功能：\n"
            "  - 打开并解析 PDF 文档\n"
            "  - 语音朗读（TTS）全文或指定页\n"
            "  - 多语音切换（本地 SAPI5 + 在线 Edge-TTS）\n"
            "  - 语速 / 音量调节\n"
            "  - 播放 / 暂停 / 停止 / 跳转控制\n"
            "  - 从指定页开始朗读\n"
            "  - 跨页自动翻页高亮\n\n"
            "技术栈：Python + PyMuPDF + pyttsx3 + edge-tts + tkinter\n\n"
            "快捷键：\n"
            "  Ctrl+O  打开 PDF\n"
            "  空格    播放/暂停\n"
            "  Esc     停止朗读\n"
            "  <- / -> 上一句 / 下一句",
        )

    def _on_close(self):
        self.controller.shutdown()
        self.root.destroy()

    # ==================================================================
    # 启动
    # ==================================================================

    def run(self):
        self.root.mainloop()
