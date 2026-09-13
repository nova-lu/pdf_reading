#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PDF 语音阅读助手 - Windows EXE 自动化打包脚本

功能：
  1. 检查/安装 PyInstaller
  2. 清理旧的打包产物
  3. 调用 PyInstaller 生成独立 exe
  4. 验证输出文件

使用方法：
    # 默认打包（单文件模式，使用 spec 配置）
    python packaging/build_exe.py

    # 目录模式（启动更快，但需要分发整个文件夹）
    python packaging/build_exe.py --dir-mode

    # 跳过清理步骤（增量打包，加速二次打包）
    python packaging/build_exe.py --no-clean

    # 指定输出目录
    python packaging/build_exe.py --output ../dist

环境要求：
    - Windows 10/11
    - Python 3.10+
    - 项目虚拟环境（venv）已安装 requirements.txt 中的依赖

输出：
    dist/PDF语音阅读助手.exe  （单文件模式）
    dist/PDF语音阅读助手/     （目录模式）
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 路径常量
SCRIPT_DIR = Path(__file__).resolve().parent           # packaging/
PROJECT_ROOT = SCRIPT_DIR.parent                        # pdf_voice_reader/
SPEC_FILE = SCRIPT_DIR / "pdf_reader.spec"             # packaging/pdf_reader.spec
VENV_PYTHON = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
VENV_PIP = PROJECT_ROOT / "venv" / "Scripts" / "pip.exe"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "dist"
DEFAULT_BUILD_DIR = SCRIPT_DIR / "_pyinstaller_cache"
APP_NAME = "PDF语音阅读助手"


def print_header(title: str):
    """打印分隔标题。"""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def print_step(msg: str):
    """打印步骤信息。"""
    print(f"  >> {msg}")


def print_ok(msg: str = ""):
    """打印成功信息。"""
    print(f"  [OK] {msg}")


def print_error(msg: str):
    """打印错误信息。"""
    print(f"  [ERROR] {msg}", file=sys.stderr)


def get_python() -> str:
    """获取要使用的 Python 解释器路径。"""
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    print_step(f"未找到 venv，使用系统 Python: {sys.executable}")
    return sys.executable


def ensure_pyinstaller(python: str) -> bool:
    """确保 PyInstaller 已安装。"""
    print_header("检查 PyInstaller")
    try:
        result = subprocess.run(
            [python, "-c", "import PyInstaller; print(PyInstaller.__version__)"],
            capture_output=True, text=True, check=True
        )
        version = result.stdout.strip()
        print_ok(f"PyInstaller 已安装，版本: {version}")
        return True
    except (subprocess.CalledProcessError, ImportError):
        print_step("PyInstaller 未安装，正在安装...")
        try:
            subprocess.run(
                [python, "-m", "pip", "install", "PyInstaller>=6.0.0"],
                check=True
            )
            print_ok("PyInstaller 安装成功")
            return True
        except subprocess.CalledProcessError as e:
            print_error(f"PyInstaller 安装失败: {e}")
            return False


def clean_old_output(output_dir: Path, build_cache: Path):
    """清理旧的打包产物。"""
    print_header("清理旧产物")
    cleaned = []

    if output_dir.exists():
        print_step(f"删除输出目录: {output_dir}")
        shutil.rmtree(output_dir, ignore_errors=True)
        cleaned.append(str(output_dir))

    if build_cache.exists():
        print_step(f"删除缓存目录: {build_cache}")
        shutil.rmtree(build_cache, ignore_errors=True)
        cleaned.append(str(build_cache))

    if cleaned:
        print_ok(f"已清理 {len(cleaned)} 个目录")
    else:
        print_ok("无旧产物需要清理")


def run_pyinstaller(python: str, output_dir: Path, build_cache: Path,
                    dir_mode: bool) -> bool:
    """执行 PyInstaller 打包。"""
    print_header("执行打包")

    if dir_mode:
        print_step("模式: 目录模式（onedir）")
    else:
        print_step("模式: 单文件模式（onefile）")

    # 命令行参数打包（比 spec 文件更兼容不同 PyInstaller 版本）
    print_step("使用命令行参数打包")
    cmd = [
        python, "-m", "PyInstaller",
        str(PROJECT_ROOT / "main.py"),
        "--name", APP_NAME,
        "--distpath", str(output_dir),
        "--workpath", str(build_cache),
        "--noconfirm",
        "--windowed",  # GUI 应用，不显示控制台
        # 收集需要完整打包的第三方库（数据文件 + 子模块 + 二进制）
        "--collect-all", "edge_tts",
        "--collect-all", "pyttsx3",
        "--collect-all", "pymupdf",
        "--collect-all", "aiohttp",
        # hidden imports（COM 相关，SAPI5 需要）
        "--hidden-import", "pythoncom",
        "--hidden-import", "pywintypes",
        "--hidden-import", "win32com",
        "--hidden-import", "win32com.client",
        # 本地模块（函数内延迟导入，显式声明避免漏打包）
        "--hidden-import", "math_unicode",
        "--hidden-import", "tts_normalizer",
        # 排除不需要的模块，减小体积
        "--exclude-module", "pytest",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "doctest",
    ]
    if not dir_mode:
        cmd.append("--onefile")

    print_step(f"命令: {' '.join(cmd)}")
    print()

    start_time = time.time()
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in process.stdout:
            print(f"  {line}", end="")

        process.wait()
        elapsed = time.time() - start_time

        if process.returncode == 0:
            print()
            print_ok(f"打包成功，耗时 {elapsed:.1f} 秒")
            return True
        else:
            print()
            print_error(f"打包失败，返回码: {process.returncode}")
            return False
    except Exception as e:
        print_error(f"打包异常: {e}")
        return False


def verify_output(output_dir: Path, dir_mode: bool) -> bool:
    """验证打包输出。"""
    print_header("验证输出")

    if dir_mode:
        app_dir = output_dir / APP_NAME
        exe_path = app_dir / f"{APP_NAME}.exe"
    else:
        exe_path = output_dir / f"{APP_NAME}.exe"

    if not exe_path.exists():
        print_error(f"未找到输出文件: {exe_path}")
        return False

    file_size = exe_path.stat().st_size
    size_mb = file_size / (1024 * 1024)
    print_ok(f"输出文件: {exe_path}")
    print_ok(f"文件大小: {size_mb:.1f} MB")

    if size_mb < 5:
        print_error("文件过小（< 5MB），可能缺少依赖")
        return False

    return True


def print_summary(output_dir: Path, dir_mode: bool, success: bool):
    """打印打包摘要。"""
    print_header("打包摘要")

    if success:
        if dir_mode:
            exe_path = output_dir / APP_NAME / f"{APP_NAME}.exe"
            print(f"  状态: 成功")
            print(f"  输出目录: {output_dir / APP_NAME}")
            print(f"  启动文件: {exe_path}")
        else:
            exe_path = output_dir / f"{APP_NAME}.exe"
            print(f"  状态: 成功")
            print(f"  输出文件: {exe_path}")

        print()
        print("  使用说明：")
        print(f"    1. 将 {'exe 文件' if not dir_mode else '整个文件夹'} 复制到目标机器")
        print(f"    2. 双击 {APP_NAME}.exe 启动")
        print(f"    3. 首次启动可能需要几秒钟（正在解压资源）" if not dir_mode else "")
        print()
        print("  注意事项：")
        print("    - 目标机器需要安装 Windows SAPI5 语音（Windows 自带）")
        print("    - 在线语音（Edge-TTS）需要网络连接")
        print("    - 如被杀毒软件误报，请添加到信任列表")
    else:
        print(f"  状态: 失败")
        print()
        print("  常见问题排查：")
        print("    1. 确认 venv 中已安装 requirements.txt 的所有依赖")
        print("    2. 确认 PyInstaller 版本 >= 6.0")
        print("    3. 尝试清理后重新打包: python packaging/build_exe.py --clean")
        print("    4. 查看上方的错误日志，定位具体模块")
        print("    5. edge-tts 相关错误：确保网络可用（打包时需下载元数据）")


def main():
    parser = argparse.ArgumentParser(
        description="PDF 语音阅读助手 - EXE 打包脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python packaging/build_exe.py              # 单文件模式打包
  python packaging/build_exe.py --dir-mode   # 目录模式打包
  python packaging/build_exe.py --no-clean   # 跳过清理（增量打包）
  python packaging/build_exe.py --output D:\\dist  # 自定义输出路径
        """,
    )
    parser.add_argument(
        "--dir-mode", action="store_true",
        help="目录模式（onedir），启动更快但需要分发整个文件夹"
    )
    parser.add_argument(
        "--no-clean", action="store_true",
        help="跳过清理旧产物（适合增量打包，加速二次打包）"
    )
    parser.add_argument(
        "--output", type=str, default=str(DEFAULT_OUTPUT_DIR),
        help=f"输出目录（默认: {DEFAULT_OUTPUT_DIR}）"
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="仅清理旧产物，不执行打包"
    )

    args = parser.parse_args()

    output_dir = Path(args.output).resolve()
    build_cache = DEFAULT_BUILD_DIR

    print_header("PDF 语音阅读助手 - 打包工具")
    print(f"  项目根目录: {PROJECT_ROOT}")
    print(f"  输出目录:   {output_dir}")
    print(f"  打包模式:   {'目录' if args.dir_mode else '单文件'}")

    # 仅清理模式
    if args.clean:
        clean_old_output(output_dir, build_cache)
        print_ok("清理完成")
        return 0

    # 获取 Python 解释器
    python = get_python()

    # 检查 PyInstaller
    if not ensure_pyinstaller(python):
        return 1

    # 清理旧产物
    if not args.no_clean:
        clean_old_output(output_dir, build_cache)

    # 执行打包
    success = run_pyinstaller(python, output_dir, build_cache, args.dir_mode)

    # 验证输出
    if success:
        success = verify_output(output_dir, args.dir_mode)

    # 打印摘要
    print_summary(output_dir, args.dir_mode, success)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
