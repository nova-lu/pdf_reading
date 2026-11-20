"""
用户偏好持久化。

存放位置：%APPDATA%\\PDFVoiceReader\\settings.json（Windows）
          ~/.config/PDFVoiceReader/settings.json（其他平台兜底）

只保存与阅读体验相关的轻量配置（字号、字体、上次使用的语音等），
读写失败一律静默降级为默认值——配置损坏不应影响应用启动。
"""

import json
import os
import sys
from typing import Any

_APP_DIR_NAME = "PDFVoiceReader"
_FILE_NAME = "settings.json"


def _config_dir() -> str:
    """返回配置目录（不存在则创建）。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
    path = os.path.join(base, _APP_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return base
    return path


def config_path() -> str:
    """配置文件完整路径（诊断用）。"""
    return os.path.join(_config_dir(), _FILE_NAME)


def load_settings() -> dict[str, Any]:
    """读取配置；文件缺失/损坏时返回空字典。"""
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict[str, Any]) -> None:
    """写回配置（原子写入，失败静默）。"""
    path = config_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
