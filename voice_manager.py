"""
统一语音管理模块

整合三类 TTS 后端：
1. SAPI5 本地语音（pyttsx3 枚举系统语音包，零依赖、离线可用）
2. 本地神经语音（sherpa-onnx 模型，模型可下载到本机，完全离线）
3. Edge-TTS 在线神经语音（微软 322+ 音色，在线免费）

设计要点：
- Edge 音色列表优先从本地缓存加载，启动不阻塞；后台可刷新为完整列表。
- 刷新失败时回退到内置精选预设（6 个），保证离线也能用。
- 本地模型由 local_tts.discover_models() 扫描模型目录得到，
  未安装 sherpa-onnx 或没有模型时静默跳过。
"""

import asyncio
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import pyttsx3

# Edge-TTS 语音别名前缀，用于区分后端
EDGE_VOICE_PREFIX = "edge:"
# 本地神经语音前缀
LOCAL_VOICE_PREFIX = "local:"

# 缓存有效期（天）：超过则后台刷新
_EDGE_CACHE_MAX_AGE_DAYS = 7


def _app_data_dir() -> str:
    """用户数据目录（缓存、模型、配置统一放这里，避免写入程序目录）。"""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config"
        )
    path = os.path.join(base, "PDFVoiceReader")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


@dataclass
class VoiceInfo:
    """语音信息（统一格式，适用于 SAPI5 / 本地神经 / Edge-TTS）。"""
    id: str            # 唯一标识（SAPI5: 注册表路径; Edge: "edge:ShortName"; 本地: "local:模型ID"）
    name: str          # 显示名称
    languages: str     # 语言代码
    provider: str      # "sapi5" / "local" / "edge"
    gender: str = ""   # "Female" / "Male"
    short_name: str = ""  # Edge-TTS ShortName / 本地模型 ID


class VoiceManager:
    """
    语音管理器。

    语音顺序：SAPI5（系统原生）→ 本地神经（可选）→ Edge 在线。
    UI 默认选中第一个（SAPI5），保证离线可见即可用。
    """

    # Edge-TTS 精选语音列表（离线兜底：无网络、无缓存时至少可用这些）
    EDGE_VOICES_PRESET = [
        ("zh-CN-XiaoxiaoNeural",   "晓晓 (Xiaoxiao) · zh-CN · 女",  "zh-CN", "Female"),
        ("zh-CN-YunxiNeural",      "云希 (Yunxi) · zh-CN · 男",     "zh-CN", "Male"),
        ("zh-CN-XiaoyiNeural",     "晓伊 (Xiaoyi) · zh-CN · 女",    "zh-CN", "Female"),
        ("en-US-JennyNeural",      "Jenny · en-US · 女",            "en-US", "Female"),
        ("en-US-GuyNeural",        "Guy · en-US · 男",              "en-US", "Male"),
        ("en-US-AriaNeural",       "Aria · en-US · 女",             "en-US", "Female"),
    ]

    # 中文音色名映射（ShortName 末段 -> 中文名）
    ZH_ALIASES = {
        "Xiaoxiao": "晓晓", "Xiaoyi": "晓伊", "Yunjian": "云健",
        "Yunxi": "云希", "Yunxia": "云夏", "Yunyang": "云扬",
        "Xiaobei": "晓北", "Xiaoni": "晓妮",
        "HiuGaai": "曉佳", "HiuMaan": "曉曼", "WanLung": "雲龍",
        "HsiaoChen": "曉臻", "YunJhe": "雲哲", "HsiaoYu": "曉雨",
    }

    def __init__(self):
        self._sapi5_voices: list[VoiceInfo] = []
        self._local_voices: list[VoiceInfo] = []
        self._edge_voices: list[VoiceInfo] = []
        self._lock = threading.RLock()
        self._edge_cache_time: float = 0.0

        self._load_sapi5_voices()
        self._load_local_voices()
        self._load_edge_voices_from_cache()

    # ------------------------------------------------------------------
    # 语音列表
    # ------------------------------------------------------------------

    @property
    def voices(self) -> list[VoiceInfo]:
        """所有可用语音（SAPI5 + 本地神经 + Edge）。"""
        with self._lock:
            return list(self._sapi5_voices) + list(self._local_voices) + list(self._edge_voices)

    def get_voice(self, voice_id: str) -> Optional[VoiceInfo]:
        """根据 ID 获取语音信息。"""
        with self._lock:
            for v in self._sapi5_voices + self._local_voices + self._edge_voices:
                if v.id == voice_id:
                    return v
        return None

    @property
    def count(self) -> int:
        return len(self.voices)

    @property
    def default_voice(self) -> Optional[VoiceInfo]:
        voices = self.voices
        return voices[0] if voices else None

    # ------------------------------------------------------------------
    # SAPI5
    # ------------------------------------------------------------------

    def _load_sapi5_voices(self):
        """通过 pyttsx3 枚举系统 SAPI5 语音。"""
        try:
            temp_engine = pyttsx3.init()
            raw_voices = temp_engine.getProperty("voices")
            voices = []
            for v in raw_voices:
                langs = ""
                try:
                    decoded = []
                    for lang in v.languages:
                        if isinstance(lang, bytes):
                            decoded.append(lang.decode("utf-8", errors="ignore").strip("\x00"))
                        else:
                            decoded.append(str(lang))
                    langs = ",".join(x for x in decoded if x)
                except Exception:
                    pass
                voices.append(VoiceInfo(
                    id=v.id,
                    name=f"{v.name}（本地 SAPI5）",
                    languages=langs,
                    provider="sapi5",
                ))
            with self._lock:
                self._sapi5_voices = voices
            try:
                temp_engine.endLoop()
            except Exception:
                pass
        except Exception:
            pass  # SAPI5 不可用时静默跳过（例如非 Windows 或语音包缺失）

    # ------------------------------------------------------------------
    # 本地神经语音（sherpa-onnx）
    # ------------------------------------------------------------------

    def _load_local_voices(self):
        """扫描已下载的本地神经语音模型。"""
        try:
            from local_tts import discover_models
        except ImportError:
            return
        try:
            models = discover_models()
        except Exception:
            return
        local = []
        for m in models:
            local.append(VoiceInfo(
                id=f"{LOCAL_VOICE_PREFIX}{m.id}",
                name=f"{m.name}（本地神经）",
                languages=m.language,
                provider="local",
                short_name=m.id,
            ))
        with self._lock:
            self._local_voices = local

    def reload_local_voices(self):
        """模型安装/删除后重新扫描本地语音（公开接口）。"""
        self._load_local_voices()

    # ------------------------------------------------------------------
    # Edge-TTS
    # ------------------------------------------------------------------

    @staticmethod
    def _edge_cache_path() -> str:
        return os.path.join(_app_data_dir(), "edge_voices.json")

    def _load_edge_voices_from_cache(self):
        """从缓存加载完整音色列表；缓存缺失/过期/损坏时回退内置预设。"""
        cached: list[VoiceInfo] = []
        try:
            with open(self._edge_cache_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            ts = float(data.get("ts", 0))
            raw = data.get("voices") or []
            for item in raw:
                short = item.get("short_name", "")
                if not short:
                    continue
                cached.append(self._build_edge_voice(
                    short_name=short,
                    locale=item.get("locale", ""),
                    gender=item.get("gender", ""),
                ))
            self._edge_cache_time = ts
        except (OSError, ValueError, TypeError):
            cached = []

        if not cached:
            cached = [
                VoiceInfo(
                    id=f"{EDGE_VOICE_PREFIX}{short}",
                    name=display,
                    languages=lang,
                    provider="edge",
                    gender=gender,
                    short_name=short,
                )
                for short, display, lang, gender in self.EDGE_VOICES_PRESET
            ]
            self._edge_cache_time = 0.0

        with self._lock:
            self._edge_voices = cached

    def _save_edge_cache(self, voices: list[VoiceInfo]):
        payload = {
            "ts": time.time(),
            "voices": [
                {
                    "short_name": v.short_name,
                    "locale": v.languages,
                    "gender": v.gender,
                }
                for v in voices
            ],
        }
        path = self._edge_cache_path()
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _build_edge_voice(self, short_name: str, locale: str, gender: str) -> VoiceInfo:
        """把 Edge 音色元数据转成 VoiceInfo（含中文别名与展示名）。"""
        seg = short_name.split("-")[-1].replace("Neural", "")
        zh = self.ZH_ALIASES.get(seg)
        base = f"{zh} ({seg})" if zh else seg
        gender_cn = {"Female": "女", "Male": "男"}.get(gender, "")
        name = f"{base} · {locale}" + (f" · {gender_cn}" if gender_cn else "")
        return VoiceInfo(
            id=f"{EDGE_VOICE_PREFIX}{short_name}",
            name=name,
            languages=locale,
            provider="edge",
            gender=gender,
            short_name=short_name,
        )

    @property
    def edge_cache_age_days(self) -> float:
        if self._edge_cache_time <= 0:
            return float("inf")
        return (time.time() - self._edge_cache_time) / 86400.0

    def edge_refresh_needed(self) -> bool:
        """是否需要刷新 Edge 音色（缓存缺失或超过有效期）。"""
        with self._lock:
            edge_count = len(self._edge_voices)
        return edge_count <= len(self.EDGE_VOICES_PRESET) or \
            self.edge_cache_age_days > _EDGE_CACHE_MAX_AGE_DAYS

    def refresh_edge_voices_sync(self, timeout: float = 20.0) -> bool:
        """
        同步刷新 Edge 音色列表（阻塞；调用方需自行放到后台线程）。

        Returns:
            True 表示刷新成功且列表已更新；False 表示网络失败，保留原列表。
        """
        try:
            import edge_tts
        except ImportError:
            return False

        async def _fetch():
            return await asyncio.wait_for(edge_tts.list_voices(), timeout)

        try:
            raw = asyncio.run(_fetch())
        except Exception:
            return False
        if not raw:
            return False

        voices = []
        for item in raw:
            short = item.get("ShortName") or ""
            if not short:
                continue
            voices.append(self._build_edge_voice(
                short_name=short,
                locale=item.get("Locale", ""),
                gender=item.get("Gender", ""),
            ))
        if not voices:
            return False

        voices.sort(key=self._edge_sort_key)
        with self._lock:
            self._edge_voices = voices
            self._edge_cache_time = time.time()
        self._save_edge_cache(voices)
        return True

    @staticmethod
    def _edge_sort_key(v: VoiceInfo) -> tuple:
        """排序：简体中文 → 港台 → 英文 → 其他，同组按名称。"""
        loc = v.languages.lower()
        if loc.startswith("zh-cn"):
            pri = 0
        elif loc.startswith("zh-hk") or loc.startswith("zh-tw"):
            pri = 1
        elif loc.startswith("en-us"):
            pri = 2
        elif loc.startswith("en"):
            pri = 3
        else:
            pri = 4
        return (pri, v.short_name)
