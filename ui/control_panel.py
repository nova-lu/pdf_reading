"""
控制面板组件

负责：
- 语音设置（引擎选择、语速、音量）
- 播放控制（播放/暂停、停止、上一句、下一句）
- 朗读进度显示
- 页面导航
- 朗读范围选择
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional


class ControlPanel:
    """右侧控制面板。"""

    COLOR_PANEL = "#ffffff"
    COLOR_ACCENT = "#2563eb"
    COLOR_ACCENT_HOVER = "#1d4ed8"
    COLOR_TEXT = "#1e293b"
    COLOR_TEXT_MUTED = "#64748b"

    def __init__(self, parent, width: int = 320):
        """
        Args:
            parent: 父容器 (ttk.Frame)
            width:  面板固定宽度（像素），默认 320。设置后无论外部如何拉伸
                    都保持该宽度，不会被左侧内容挤压。
        """
        self._panel_width = width
        panel = ttk.Frame(parent, style="Panel.TFrame", width=width)
        # sticky="ns"：高度跟随父容器拉伸，宽度由 grid_propagate + width 锁死
        panel.grid(row=0, column=1, sticky="ns")
        # 禁止 grid 根据内部子控件要求调整大小，保持面板宽度恒定
        panel.grid_propagate(False)

        self._build_voice_section(panel)
        self._build_playback_section(panel)
        self._build_progress_section(panel)
        self._build_page_section(panel)
        self._build_range_section(panel)

    # ------------------------------------------------------------------
    # 语音设置
    # ------------------------------------------------------------------

    def _build_voice_section(self, parent):
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.pack(fill=tk.X, padx=12, pady=(10, 6))

        ttk.Label(section, text="语音设置", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 8))

        ttk.Label(section, text="语音引擎", style="Panel.TLabel").pack(anchor=tk.W)
        self.combo_voice = ttk.Combobox(section, state="readonly", width=36)
        self.combo_voice.pack(fill=tk.X, pady=(2, 10))

        ttk.Label(section, text="语速", style="Panel.TLabel").pack(anchor=tk.W)
        self.lbl_rate_value = ttk.Label(section, text="200", style="Panel.TLabel", foreground=self.COLOR_TEXT_MUTED)
        self.lbl_rate_value.pack(anchor=tk.E)
        self.scale_rate = ttk.Scale(section, from_=50, to=400, orient=tk.HORIZONTAL)
        self.scale_rate.set(200)
        self.scale_rate.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(section, text="音量", style="Panel.TLabel").pack(anchor=tk.W)
        self.lbl_volume_value = ttk.Label(section, text="100%", style="Panel.TLabel", foreground=self.COLOR_TEXT_MUTED)
        self.lbl_volume_value.pack(anchor=tk.E)
        self.scale_volume = ttk.Scale(section, from_=0, to=100, orient=tk.HORIZONTAL)
        self.scale_volume.set(100)
        self.scale_volume.pack(fill=tk.X, pady=(0, 10))

    # ------------------------------------------------------------------
    # 播放控制
    # ------------------------------------------------------------------

    def _build_playback_section(self, parent):
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.pack(fill=tk.X, padx=12, pady=(6, 6))

        ttk.Label(section, text="播放控制", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 8))

        btn_frame = ttk.Frame(section, style="Panel.TFrame")
        btn_frame.pack(fill=tk.X, pady=(0, 8))

        self.btn_play = tk.Button(
            btn_frame, text="\u25b6 播放", font=("Microsoft YaHei UI", 11, "bold"),
            bg=self.COLOR_ACCENT, fg="white", activebackground=self.COLOR_ACCENT_HOVER,
            activeforeground="white", relief=tk.FLAT, bd=0, padx=16, pady=8,
            cursor="hand2",
        )
        self.btn_play.pack(fill=tk.X, pady=(0, 6))

        sub_btn_frame = ttk.Frame(btn_frame, style="Panel.TFrame")
        sub_btn_frame.pack(fill=tk.X)

        self.btn_stop = tk.Button(
            sub_btn_frame, text="\u25a0 停止", font=("Microsoft YaHei UI", 10),
            bg="#e2e8f0", fg=self.COLOR_TEXT, activebackground="#cbd5e1",
            relief=tk.FLAT, bd=0, padx=10, pady=6, cursor="hand2",
        )
        self.btn_stop.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))

        self.btn_prev = tk.Button(
            sub_btn_frame, text="\u23ee 上一句", font=("Microsoft YaHei UI", 10),
            bg="#e2e8f0", fg=self.COLOR_TEXT, activebackground="#cbd5e1",
            relief=tk.FLAT, bd=0, padx=10, pady=6, cursor="hand2",
        )
        self.btn_prev.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 3))

        self.btn_next = tk.Button(
            sub_btn_frame, text="下一句 \u23ed", font=("Microsoft YaHei UI", 10),
            bg="#e2e8f0", fg=self.COLOR_TEXT, activebackground="#cbd5e1",
            relief=tk.FLAT, bd=0, padx=10, pady=6, cursor="hand2",
        )
        self.btn_next.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        # 朗读选中内容按钮
        self.btn_read_selection = tk.Button(
            section, text="\u25b6 朗读选中内容", font=("Microsoft YaHei UI", 10),
            bg="#8b5cf6", fg="white", activebackground="#7c3aed",
            relief=tk.FLAT, bd=0, padx=10, pady=6, cursor="hand2",
        )
        self.btn_read_selection.pack(fill=tk.X, pady=(8, 0))

    # ------------------------------------------------------------------
    # 朗读进度
    # ------------------------------------------------------------------

    def _build_progress_section(self, parent):
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.pack(fill=tk.X, padx=12, pady=(6, 6))

        ttk.Label(section, text="朗读进度", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 8))

        self.lbl_progress = ttk.Label(section, text="\u2014 / \u2014", style="Panel.TLabel",
                                       foreground=self.COLOR_TEXT_MUTED)
        self.lbl_progress.pack(anchor=tk.W, pady=(0, 4))

        # 用 Scale 替代 Progressbar，支持拖拽跳转
        self.progress_scale = tk.Scale(
            section,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            showvalue=False,
            length=200,
            sliderlength=14,
            width=8,
            bg=self.COLOR_PANEL,
            fg="#2563eb",
            troughcolor="#e2e8f0",
            highlightthickness=0,
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.progress_scale.pack(fill=tk.X, pady=(0, 10))

        self._progress_total = 0
        self._updating_progress = False

    # ------------------------------------------------------------------
    # 页面导航
    # ------------------------------------------------------------------

    def _build_page_section(self, parent):
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.pack(fill=tk.X, padx=12, pady=(6, 10))

        ttk.Label(section, text="页面导航", style="Title.TLabel").pack(anchor=tk.W, pady=(0, 8))

        nav_frame = ttk.Frame(section, style="Panel.TFrame")
        nav_frame.pack(fill=tk.X)

        self.btn_prev_page = tk.Button(
            nav_frame, text="\u25c0 上一页", font=("Microsoft YaHei UI", 9),
            bg="#e2e8f0", fg=self.COLOR_TEXT, activebackground="#cbd5e1",
            relief=tk.FLAT, bd=0, padx=8, pady=5, cursor="hand2", state=tk.DISABLED,
        )
        self.btn_prev_page.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_next_page = tk.Button(
            nav_frame, text="下一页 \u25b6", font=("Microsoft YaHei UI", 9),
            bg="#e2e8f0", fg=self.COLOR_TEXT, activebackground="#cbd5e1",
            relief=tk.FLAT, bd=0, padx=8, pady=5, cursor="hand2", state=tk.DISABLED,
        )
        self.btn_next_page.pack(side=tk.LEFT, padx=(4, 8))

        self.var_page = tk.StringVar(value="1")
        self.entry_page = ttk.Entry(nav_frame, textvariable=self.var_page, width=6, justify=tk.CENTER)
        self.entry_page.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_jump = tk.Button(
            nav_frame, text="跳转", font=("Microsoft YaHei UI", 9),
            bg=self.COLOR_ACCENT, fg="white", activebackground=self.COLOR_ACCENT_HOVER,
            relief=tk.FLAT, bd=0, padx=10, pady=5, cursor="hand2",
        )
        self.btn_jump.pack(side=tk.LEFT)

        # 从当前页开始朗读按钮
        self.btn_play_from_here = tk.Button(
            section, text="\u25b6 从此页开始朗读", font=("Microsoft YaHei UI", 9, "bold"),
            bg="#16a34a", fg="white", activebackground="#15803d",
            relief=tk.FLAT, bd=0, padx=10, pady=6, cursor="hand2",
        )
        self.btn_play_from_here.pack(fill=tk.X, pady=(8, 0))

    # ------------------------------------------------------------------
    # 朗读范围
    # ------------------------------------------------------------------

    def _build_range_section(self, parent):
        section = ttk.Frame(parent, style="Panel.TFrame")
        section.pack(fill=tk.X, padx=12, pady=(6, 12))

        self.var_read_all = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            section, text="朗读全部页面（非仅当前页）",
            variable=self.var_read_all, style="Panel.TCheckbutton",
        ).pack(anchor=tk.W)

    # ------------------------------------------------------------------
    # 公共方法：设置回调
    # ------------------------------------------------------------------

    def set_callbacks(
        self,
        on_play_pause: Optional[Callable] = None,
        on_stop: Optional[Callable] = None,
        on_skip_prev: Optional[Callable] = None,
        on_skip_next: Optional[Callable] = None,
        on_prev_page: Optional[Callable] = None,
        on_next_page: Optional[Callable] = None,
        on_jump_page: Optional[Callable] = None,
        on_play_from_here: Optional[Callable] = None,
        on_voice_changed: Optional[Callable] = None,
        on_rate_changed: Optional[Callable] = None,
        on_volume_changed: Optional[Callable] = None,
        on_read_range_changed: Optional[Callable] = None,
        on_progress_seek: Optional[Callable[[int], None]] = None,
        on_read_selection: Optional[Callable] = None,
    ):
        """设置所有控件的回调。"""
        if on_play_pause:
            self.btn_play.config(command=on_play_pause)
        if on_stop:
            self.btn_stop.config(command=on_stop)
        if on_skip_prev:
            self.btn_prev.config(command=on_skip_prev)
        if on_skip_next:
            self.btn_next.config(command=on_skip_next)
        if on_prev_page:
            self.btn_prev_page.config(command=on_prev_page)
        if on_next_page:
            self.btn_next_page.config(command=on_next_page)
        if on_jump_page:
            self.btn_jump.config(command=on_jump_page)
            self.entry_page.bind("<Return>", lambda e: on_jump_page())
        if on_play_from_here:
            self.btn_play_from_here.config(command=on_play_from_here)
        if on_voice_changed:
            self.combo_voice.bind("<<ComboboxSelected>>", on_voice_changed)
        if on_rate_changed:
            self.scale_rate.config(command=on_rate_changed)
        if on_volume_changed:
            self.scale_volume.config(command=on_volume_changed)
        if on_read_range_changed:
            self.var_read_all.trace_add("write", lambda *_: on_read_range_changed())
        if on_progress_seek:
            self._bind_progress_seek(on_progress_seek)
        if on_read_selection:
            self.btn_read_selection.config(command=on_read_selection)

    # ------------------------------------------------------------------
    # 公共方法：状态更新
    # ------------------------------------------------------------------

    def update_voices(self, display_names: list[str], select_index: int = 0, voice_count_text: str = ""):
        """更新语音下拉列表。"""
        self.combo_voice["values"] = display_names
        if display_names:
            self.combo_voice.current(select_index)

    def update_play_button(self, state_name: str):
        """根据播放状态更新按钮文字。"""
        if state_name == "playing":
            self.btn_play.config(text="\u23f8 暂停", bg="#f59e0b", activebackground="#d97706")
        elif state_name == "paused":
            self.btn_play.config(text="\u25b6 继续", bg=self.COLOR_ACCENT, activebackground=self.COLOR_ACCENT_HOVER)
        else:
            self.btn_play.config(text="\u25b6 播放", bg=self.COLOR_ACCENT, activebackground=self.COLOR_ACCENT_HOVER)

    def _bind_progress_seek(self, callback: Callable[[int], None]):
        """绑定进度滑块的用户拖拽/点击释放事件。"""
        def _on_seek_release(event=None):
            if self._updating_progress:
                return
            if self._progress_total <= 0:
                return
            pct = self.progress_scale.get()
            target_index = int(round((pct / 100.0) * (self._progress_total - 1)))
            target_index = max(0, min(target_index, self._progress_total - 1))
            callback(target_index)

        self.progress_scale.bind("<ButtonRelease-1>", _on_seek_release)
        # 也响应点击轨道直接跳转
        self.progress_scale.bind("<Button-1>", lambda e: self.progress_scale.after(50, _on_seek_release))

    def update_progress(self, current: int, total: int):
        """更新进度显示（程序更新，不触发 seek 回调）。"""
        self._progress_total = total
        self.lbl_progress.config(text=f"第 {current + 1} / {total} 句")
        if total > 0:
            self._updating_progress = True
            self.progress_scale.set((current / total) * 100)
            self._updating_progress = False

    def clear_progress(self):
        """清除进度显示。"""
        self._progress_total = 0
        self.lbl_progress.config(text="\u2014 / \u2014")
        self._updating_progress = True
        self.progress_scale.set(0)
        self._updating_progress = False

    def update_page_controls(self, current_page: int, total_pages: int):
        """更新页面导航按钮状态。"""
        self.btn_prev_page.config(state=tk.NORMAL if current_page > 0 else tk.DISABLED)
        self.btn_next_page.config(state=tk.NORMAL if current_page < total_pages - 1 else tk.DISABLED)
        self.var_page.set(str(current_page + 1))

    def get_rate(self) -> int:
        return int(float(self.scale_rate.get()))

    def get_volume(self) -> int:
        return int(float(self.scale_volume.get()))

    def get_selected_page(self) -> int:
        """获取用户输入的页码（1-based -> 0-based）。"""
        try:
            return int(self.var_page.get()) - 1
        except ValueError:
            return -1

    @property
    def read_all_pages(self) -> bool:
        return self.var_read_all.get()

    @property
    def current_voice_index(self) -> int:
        return self.combo_voice.current()

    def set_rate_display(self, rate: int):
        self.lbl_rate_value.config(text=str(rate))

    def set_volume_display(self, vol_percent: int):
        self.lbl_volume_value.config(text=f"{vol_percent}%")
