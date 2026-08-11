"""
PDF 语音阅读助手 - 主入口

基于 tkinter 的 Windows 桌面 GUI 应用。
集成 PDF 文本提取与 TTS 语音合成（SAPI5 + Edge-TTS），提供完整的朗读控制体验。

运行方式：
    venv\\Scripts\\python.exe main.py
"""

import os
import sys

# 确保能导入同目录模块和子包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.main_window import PDFVoiceReaderApp


if __name__ == "__main__":
    app = PDFVoiceReaderApp()
    app.run()
