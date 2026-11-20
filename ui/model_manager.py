"""
本地语音模型管理对话框

功能：
- 列出可下载的本地神经语音模型（中文 / 中英混读 / 英文）
- 一键下载（后台线程 + 进度条 + 可取消），下载完成后自动注册为可选语音
- 删除已安装模型
- 显示模型存储目录并提供打开入口

依赖：local_tts（sherpa-onnx）。未安装 sherpa-onnx 时给出安装提示，
不阻塞应用其他功能。
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

import local_tts


class ModelManagerDialog:
    """本地语音模型管理窗口。"""

    COLOR_BG = "#f8fafc"
    COLOR_TEXT = "#1e293b"
    COLOR_MUTED = "#64748b"
    COLOR_ACCENT = "#2563eb"

    def __init__(self, parent: tk.Misc, on_models_changed: Optional[Callable[[], None]] = None):
        self._parent = parent
        self._on_models_changed = on_models_changed
        self._busy = False
        self._cancel = threading.Event()
        self._queue: queue.Queue = queue.Queue()
        self._rows: dict[str, dict] = {}

        self.top = tk.Toplevel(parent)
        self.top.title("本地语音模型")
        self.top.geometry("680x520")
        self.top.minsize(620, 440)
        self.top.configure(bg=self.COLOR_BG)
        self.top.transient(parent)
        self.top.grab_set()

        self._build()
        self._refresh_rows()
        self.top.after(100, self._poll_queue)

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------

    def _build(self):
        header = tk.Frame(self.top, bg=self.COLOR_BG)
        header.pack(fill=tk.X, padx=16, pady=(14, 6))

        tk.Label(
            header, text="本地神经语音模型", bg=self.COLOR_BG, fg=self.COLOR_TEXT,
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(anchor=tk.W)

        tk.Label(
            header,
            text="模型下载后完全离线运行，朗读文本不会上传到任何服务器。\n"
                 "下载源为 sherpa-onnx 官方发布页，支持断点续传。",
            bg=self.COLOR_BG, fg=self.COLOR_MUTED,
            font=("Microsoft YaHei UI", 9), justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 0))

        if not local_tts.is_available():
            warn = tk.Frame(self.top, bg="#fef3c7")
            warn.pack(fill=tk.X, padx=16, pady=(8, 0))
            tk.Label(
                warn,
                text="未检测到 sherpa-onnx 运行时，下载按钮不可用。\n"
                     "请在项目目录执行：venv\\Scripts\\pip.exe install \"sherpa-onnx>=1.12,<2.0\"",
                bg="#fef3c7", fg="#92400e", justify=tk.LEFT,
                font=("Microsoft YaHei UI", 9),
            ).pack(anchor=tk.W, padx=10, pady=8)

        # 模型列表
        list_frame = tk.Frame(self.top, bg=self.COLOR_BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(10, 4))

        for entry in local_tts.catalog():
            self._rows[entry.id] = self._build_row(list_frame, entry)

        # 底部：进度 + 操作
        footer = tk.Frame(self.top, bg=self.COLOR_BG)
        footer.pack(fill=tk.X, padx=16, pady=(4, 12))

        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=100)
        self.progress.pack(fill=tk.X, pady=(0, 4))

        self.lbl_status = tk.Label(
            footer, text="", bg=self.COLOR_BG, fg=self.COLOR_MUTED,
            font=("Microsoft YaHei UI", 9),
        )
        self.lbl_status.pack(anchor=tk.W)

        actions = tk.Frame(footer, bg=self.COLOR_BG)
        actions.pack(fill=tk.X, pady=(6, 0))

        tk.Button(
            actions, text="打开模型目录", command=self._open_models_dir,
            font=("Microsoft YaHei UI", 9), bg="#e2e8f0", fg=self.COLOR_TEXT,
            relief=tk.FLAT, padx=10, pady=4, cursor="hand2",
        ).pack(side=tk.LEFT)

        tk.Button(
            actions, text="关闭", command=self._close,
            font=("Microsoft YaHei UI", 9), bg="#e2e8f0", fg=self.COLOR_TEXT,
            relief=tk.FLAT, padx=14, pady=4, cursor="hand2",
        ).pack(side=tk.RIGHT)

        self.btn_cancel = tk.Button(
            actions, text="取消下载", command=self._cancel_download,
            font=("Microsoft YaHei UI", 9), bg="#fecaca", fg="#991b1b",
            relief=tk.FLAT, padx=10, pady=4, cursor="hand2", state=tk.DISABLED,
        )
        self.btn_cancel.pack(side=tk.RIGHT, padx=(0, 8))

    def _build_row(self, parent: tk.Misc, entry) -> dict:
        row = tk.Frame(parent, bg="white", highlightbackground="#e2e8f0", highlightthickness=1)
        row.pack(fill=tk.X, pady=4)

        left = tk.Frame(row, bg="white")
        left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10, pady=8)

        tk.Label(
            left, text=entry.name, bg="white", fg=self.COLOR_TEXT,
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor=tk.W)

        tk.Label(
            left,
            text=f"{entry.language} · 约 {entry.size_mb} MB · {entry.note}",
            bg="white", fg=self.COLOR_MUTED, font=("Microsoft YaHei UI", 9),
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(2, 0))

        right = tk.Frame(row, bg="white")
        right.pack(side=tk.RIGHT, padx=10)

        status = tk.Label(right, text="", bg="white", fg=self.COLOR_MUTED,
                          font=("Microsoft YaHei UI", 9))
        status.pack(side=tk.TOP)

        btn = tk.Button(
            right, text="下载", font=("Microsoft YaHei UI", 9),
            bg=self.COLOR_ACCENT, fg="white", activebackground="#1d4ed8",
            activeforeground="white", relief=tk.FLAT, padx=12, pady=4, cursor="hand2",
        )
        btn.pack(side=tk.TOP, pady=(4, 0))

        return {"frame": row, "status": status, "button": btn, "entry": entry}

    # ------------------------------------------------------------------
    # 状态刷新
    # ------------------------------------------------------------------

    def _installed_ids(self) -> set[str]:
        try:
            return {m.id for m in local_tts.discover_models()}
        except Exception:
            return set()

    def _refresh_rows(self):
        installed = self._installed_ids()
        available = local_tts.is_available()
        for mid, row in self._rows.items():
            is_installed = mid in installed
            row["status"].config(
                text="已安装" if is_installed else "未安装",
                fg="#16a34a" if is_installed else self.COLOR_MUTED,
            )
            if self._busy:
                row["button"].config(state=tk.DISABLED, text="下载中..." if not is_installed else "删除")
            elif is_installed:
                row["button"].config(
                    state=tk.NORMAL, text="删除", bg="#e2e8f0", fg=self.COLOR_TEXT,
                    command=lambda m=mid: self._delete_model(m),
                )
            else:
                row["button"].config(
                    state=tk.NORMAL if available else tk.DISABLED,
                    text="下载", bg=self.COLOR_ACCENT, fg="white",
                    command=lambda m=mid: self._start_download(m),
                )

    # ------------------------------------------------------------------
    # 下载
    # ------------------------------------------------------------------

    def _start_download(self, model_id: str):
        if self._busy:
            return
        self._busy = True
        self._cancel.clear()
        self.progress.config(value=0)
        self.lbl_status.config(text="准备下载...")
        self.btn_cancel.config(state=tk.NORMAL)
        self._refresh_rows()

        def worker():
            try:
                model = local_tts.download_and_install(
                    model_id,
                    progress_cb=lambda done, total: self._queue.put(("progress", done, total)),
                    cancel_event=self._cancel,
                )
                self._queue.put(("done", model))
            except Exception as exc:  # 网络/解压失败统一上报
                self._queue.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel_download(self):
        self._cancel.set()
        self.lbl_status.config(text="正在取消...")

    def _poll_queue(self):
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    done, total = item[1], item[2]
                    if total > 0:
                        pct = min(100.0, done / total * 100.0)
                        self.progress.config(value=pct)
                        self.lbl_status.config(
                            text=f"下载中 {done / 1048576:.1f} / {total / 1048576:.1f} MB（{pct:.0f}%）"
                        )
                    else:
                        self.lbl_status.config(text=f"下载中 {done / 1048576:.1f} MB")
                elif kind == "done":
                    model = item[1]
                    self._finish_busy()
                    self.lbl_status.config(text=f"已安装：{model.name}")
                    self._refresh_rows()
                    self._notify_changed()
                    messagebox.showinfo(
                        "安装完成",
                        f"{model.name} 已安装。\n\n请在下拉框中选中「{model.name}（本地神经）」开始朗读。",
                        parent=self.top,
                    )
                elif kind == "error":
                    self._finish_busy()
                    self.lbl_status.config(text="下载失败")
                    self._refresh_rows()
                    messagebox.showerror("下载失败", item[1], parent=self.top)
        except queue.Empty:
            pass
        try:
            self.top.after(100, self._poll_queue)
        except tk.TclError:
            pass

    def _finish_busy(self):
        self._busy = False
        self._cancel.clear()
        self.btn_cancel.config(state=tk.DISABLED)
        self.progress.config(value=0)

    def _delete_model(self, model_id: str):
        if self._busy:
            return
        if not messagebox.askyesno("删除模型", "确定删除该本地模型？删除后需要重新下载。", parent=self.top):
            return
        if local_tts.remove_model(model_id):
            self.lbl_status.config(text="已删除")
            self._refresh_rows()
            self._notify_changed()
        else:
            messagebox.showerror("删除失败", "模型目录删除失败，请手动清理。", parent=self.top)

    # ------------------------------------------------------------------
    # 其他
    # ------------------------------------------------------------------

    def _notify_changed(self):
        if self._on_models_changed:
            try:
                self._on_models_changed()
            except Exception:
                pass

    def _open_models_dir(self):
        path = local_tts.models_dir()
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            messagebox.showinfo("模型目录", path, parent=self.top)

    def _close(self):
        self._cancel.set()
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.destroy()
