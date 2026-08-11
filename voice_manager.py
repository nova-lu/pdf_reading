"""
统一语音管理模块

整合两类 TTS 后端：
1. SAPI5 本地语音（通过 pyttsx3 枚举系统已安装的语音包）
2. Edge-TTS 在线神经语音（Microsoft Azure 高质量神经网络语音）

提供统一的 VoiceInfo 数据结构和 VoiceManager 管理类，
上层 TTS 引擎根据 voice.provider 决定使用哪种后端播放。
"""

import pyttsx3
from dataclasses import dataclass
from typing import Optional


# Edge-TTS 语音别名前缀，用于区分后端
EDGE_VOICE_PREFIX = "edge:"


@dataclass
class VoiceInfo:
    """语音信息（统一格式，适用于 SAPI5 和 Edge-TTS）。"""
    id: str            # 唯一标识（SAPI5: 注册表路径; Edge: "edge:ShortName"）
    name: str          # 显示名称
    languages: str     # 语言代码
    provider: str      # "sapi5" 或 "edge"
    gender: str = ""   # "Female" / "Male"（仅 edge 有意义）
    short_name: str = ""  # Edge-TTS ShortName（仅 edge 使用）


class VoiceManager:
    """
    语音管理器。

    负责枚举和统一管理所有可用语音：
    - SAPI5: 通过 pyttsx3 获取系统已安装的语音
    - Edge-TTS: 使用预定义的高质量神经语音列表

    语音列表合并后统一返回，SAPI5 在前，Edge-TTS 在后。
    """

    # Edge-TTS 精选语音列表（无需联网枚举，直接使用已知可用的语音）
    EDGE_VOICES_PRESET = [
        # 中文
        ("zh-CN-XiaoxiaoNeural",   "Microsoft Xiaoxiao (晓晓) - Chinese",     "zh-CN", "Female"),
        ("zh-CN-YunxiNeural",      "Microsoft Yunxi (云希) - Chinese",         "zh-CN", "Male"),
        ("zh-CN-XiaoyiNeural",     "Microsoft Xiaoyi (晓伊) - Chinese",        "zh-CN", "Female"),
        # 英文
        ("en-US-JennyNeural",      "Microsoft Jenny - English (US)",           "en-US", "Female"),
        ("en-US-GuyNeural",        "Microsoft Guy - English (US)",             "en-US", "Male"),
        ("en-US-AriaNeural",       "Microsoft Aria - English (US)",            "en-US", "Female"),
    ]

    def __init__(self):
        self._voices: list[VoiceInfo] = []
        self._load_sapi5_voices()
        self._load_edge_voices()

    def _load_sapi5_voices(self):
        """通过 pyttsx3 枚举系统 SAPI5 语音。"""
        try:
            temp_engine = pyttsx3.init()
            raw_voices = temp_engine.getProperty("voices")
            for v in raw_voices:
                langs = ""
                try:
                    decoded = []
                    for lang in v.languages:
                        if isinstance(lang, bytes):
                            decoded.append(lang.decode("utf-8", errors="ignore"))
                        else:
                            decoded.append(str(lang))
                    langs = ",".join(decoded)
                except Exception:
                    pass

                self._voices.append(VoiceInfo(
                    id=v.id,
                    name=v.name,
                    languages=langs,
                    provider="sapi5",
                ))
            try:
                temp_engine.endLoop()
            except Exception:
                pass
        except Exception:
            pass  # SAPI5 不可用时静默跳过

    def _load_edge_voices(self):
        """加载预定义的 Edge-TTS 语音列表。"""
        for short_name, display_name, lang, gender in self.EDGE_VOICES_PRESET:
            self._voices.append(VoiceInfo(
                id=f"{EDGE_VOICE_PREFIX}{short_name}",
                name=display_name,
                languages=lang,
                provider="edge",
                gender=gender,
                short_name=short_name,
            ))

    @property
    def voices(self) -> list[VoiceInfo]:
        """获取所有可用语音（SAPI5 + Edge-TTS）。"""
        return self._voices

    def get_voice(self, voice_id: str) -> Optional[VoiceInfo]:
        """根据 ID 获取语音信息。"""
        for v in self._voices:
            if v.id == voice_id:
                return v
        return None

    @property
    def count(self) -> int:
        """语音总数。"""
        return len(self._voices)

    @property
    def default_voice(self) -> Optional[VoiceInfo]:
        """默认语音（第一个）。"""
        return self._voices[0] if self._voices else None
