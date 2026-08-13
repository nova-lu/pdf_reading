# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置文件（.spec）

本文件由 build_exe.py 自动调用，通常无需手动编辑。
如需调整打包行为（如添加资源文件、修改图标），可编辑此文件后运行：
    python build/build_exe.py --no-clean

关键配置说明：
- datas: 额外的资源文件（图标、配置等），格式为 (源路径, 目标目录)
- hiddenimports: PyInstaller 无法自动检测的模块
- collect_all: 收集指定包的所有子模块、数据文件和二进制文件
"""

import os
import sys

# 项目根目录（spec 文件位于 build/ 下，根目录是上一级）
PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

block_cipher = None

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'main.py')],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[
        # pywin32 / COM 相关（SAPI5 需要）
        'pythoncom',
        'pywintypes',
        'win32com',
        'win32com.client',
        # edge-tts 异步依赖
        'aiohttp',
        'asyncio',
        # tkinter（通常自动检测，显式声明保险）
        'tkinter',
        'tkinter.ttk',
        'tkinter.scrolledtext',
        'tkinter.filedialog',
        'tkinter.messagebox',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的大模块，减小体积
        'pytest',
        'unittest',
        'pydoc',
        'doctest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# 收集需要完整打包的第三方库（包括数据文件和子模块）
from PyInstaller.utils.hooks import collect_all

for package in ('edge_tts', 'pyttsx3', 'pymupdf', 'aiohttp'):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    a.datas += pkg_datas
    a.binaries += pkg_binaries
    a.hiddenimports += pkg_hidden

pyz = PYZ(a.pure, a.zipped_data,
          cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='PDF语音阅读助手',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # 如需自定义图标，取消注释并指定路径：
    # icon=os.path.join(PROJECT_ROOT, 'build', 'app.ico'),
    runtime_tmpdir=None,
    console=False,          # GUI 应用，不显示控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
