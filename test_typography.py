"""
字号 / 字体缩放回归测试（离线，需要可用的 Tk 环境但不显示窗口）。

覆盖：
1. 默认字号与字体
2. 放大 / 缩小的步长与上下限夹取
3. 标题字号随正文联动（+2）、段落缩进与行距随字号缩放
4. 字体切换（仅允许系统已安装字体）
5. reset_zoom 恢复默认
6. 配置持久化（settings_store 读写）

运行：python test_typography.py
"""

import os
import sys
import tempfile
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.text_panel import TextPanel
import settings_store

RESULTS = []


def check(name: str, ok: bool, detail: str = ""):
    RESULTS.append((name, ok))
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f"  | {detail}" if detail else ""))


def main():
    root = tk.Tk()
    root.withdraw()
    panel = TextPanel(ttk.Frame(root), {})

    # 1. 默认值
    check("默认字号为 16", panel.font_size == 16, str(panel.font_size))
    check("默认字体为微软雅黑", panel.font_family == "微软雅黑", panel.font_family)
    check("控件字体已应用", panel.text_area.cget("font") == "微软雅黑 16",
          panel.text_area.cget("font"))

    # 2. 缩放与夹取
    panel.zoom_in()
    check("放大一次 +2", panel.font_size == 18, str(panel.font_size))
    panel.zoom_out()
    panel.zoom_out()
    check("缩小两次 -4", panel.font_size == 14, str(panel.font_size))
    panel.set_font_size(999)
    check("超过上限夹取到 48", panel.font_size == 48, str(panel.font_size))
    panel.set_font_size(-5)
    check("低于下限夹取到 12", panel.font_size == 12, str(panel.font_size))

    # 3. 标签联动
    panel.set_font_size(24)
    heading_font = panel.text_area.tag_cget("heading", "font")
    check("标题字号 = 正文 +2", "26" in heading_font, heading_font)
    check("标题为粗体", "bold" in heading_font, heading_font)
    spacing = int(panel.text_area.tag_cget("paragraph", "spacing1"))
    indent = int(panel.text_area.tag_cget("paragraph", "lmargin1"))
    check("段落行距随字号缩放", spacing == max(2, int(24 * 0.45)), str(spacing))
    check("段落缩进随字号缩放", indent == int(24 * 1.5), str(indent))

    # 4. 字体切换
    available = panel.available_families
    check("可用字体列表非空", len(available) > 0, f"{len(available)} 个")
    if len(available) > 1:
        target = available[1]
        panel.set_font_family(target)
        check("切换到可用字体", panel.font_family == target, panel.font_family)
    panel.set_font_family("不存在的字体XYZ")
    check("无效字体被忽略", panel.font_family in available, panel.font_family)

    # 5. 重置
    panel.reset_zoom()
    check("重置后字号 16", panel.font_size == 16, str(panel.font_size))
    check("重置后字体微软雅黑", panel.font_family == "微软雅黑", panel.font_family)

    # 6. 持久化（把 APPDATA 指向临时目录，避免污染真实配置）
    old_appdata = os.environ.get("APPDATA")
    tmpdir = tempfile.mkdtemp(prefix="pdfvr_settings_")
    os.environ["APPDATA"] = tmpdir
    try:
        settings_store.save_settings({"font_family": "宋体", "font_size": 28})
        loaded = settings_store.load_settings()
        check("配置写入后可读回",
              loaded.get("font_family") == "宋体" and loaded.get("font_size") == 28,
              str(loaded))
        check("配置文件位于 APPDATA 下",
              os.path.exists(os.path.join(tmpdir, "PDFVoiceReader", "settings.json")),
              settings_store.config_path())

        # 损坏文件应静默回退为空字典
        with open(os.path.join(tmpdir, "PDFVoiceReader", "settings.json"), "w", encoding="utf-8") as f:
            f.write("{broken json")
        check("配置损坏时回退默认值", settings_store.load_settings() == {})
    finally:
        if old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old_appdata

    root.destroy()

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        for n in failed:
            print(" -", n)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
