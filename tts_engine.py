"""
TTS 语音合成引擎模块（重构版）

核心改进：
1. 双后端支持：SAPI5（pyttsx3 本地语音）+ Edge-TTS（在线神经语音）
2. skip_to 不再杀线程重建，用 _skip_to_index 信号在 worker 内跳转
3. pause/resume 使用 engine.stop() 立即停止音频，恢复时重播当前句
4. Edge-TTS 通过 MCI (Windows Media Control Interface) 播放 MP3
5. 持久 worker 线程，play/skip/pause 不需要重建引擎
6. UI 同步以"音频真正开始"为准：SAPI5 用首个 started-word（StartStream）
   事件、Edge-TTS 用 MCI playing 状态，避免高亮提前于语音
7. stop()/skip() 后必须排空 SAPI5 陈旧 EndStream 事件再 say() 下一句，
   否则新句子会被误判为立即完成（表现为跳过/跳回错误句子）

关键技术点：
- SAPI5 是基于 COM 的，pyttsx3 引擎必须在调用线程内创建并初始化 COM
- Edge-TTS 是异步库，在 worker 线程中用 asyncio.run() 同步调用
- MCI 播放 MP3 无需额外依赖，使用 ctypes.windll.winmm
"""

import pyttsx3
import pythoncom
import threading
import time
import re
import os
import sys
import asyncio
import ctypes
import tempfile
from enum import Enum
from functools import lru_cache
from typing import Callable, Optional

# 确保能导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voice_manager import VoiceManager, VoiceInfo, EDGE_VOICE_PREFIX

# 尝试导入 edge-tts（可选依赖）。
# 注意：先导入 edge_tts_compat——它会在 import 时把合成端点切换到
# api.msedgeservices.com（微软 2026-10 起的 v2 端点），旧版
# speech.platform.bing.com 已在多区域对旧握手返回 403。
try:
    import edge_tts_compat  # noqa: F401  # import 时即应用端点补丁
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    _EDGE_TTS_AVAILABLE = False

# 朗读层文本规范化（符号 → 中文读法）。仅在"发声"时使用，
# 不改写句子列表，因此 UI 显示与高亮偏移始终基于 PDF 原始字符。
try:
    from tts_normalizer import normalize_for_tts as _normalize_for_tts
except ImportError:  # 允许在未部署朗读层时降级为原样朗读
    _normalize_for_tts = None


@lru_cache(maxsize=1024)
def _speech_normalized(text: str) -> str:
    """把待朗读文本转成语音友好表达（带缓存，异常时回退原文）。"""
    if _normalize_for_tts is None or not text:
        return text
    try:
        return _normalize_for_tts(text)
    except Exception:
        return text


class ReaderState(Enum):
    """朗读状态。"""
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"


# Edge-TTS 连续合成失败达到阈值后的致命错误消息。
# UI 层可据此常量识别「需要用户干预」的错误（弹窗提供本地语音回退），
# 避免用魔法字符串匹配。
EDGE_FATAL_ERROR_MESSAGE = (
    "Edge-TTS 连续多次合成失败，已停止朗读。\n"
    "可能原因：网络不通或微软在线语音服务暂不可用。\n"
    "建议切换到本地 SAPI5 语音，或检查网络后重试。"
)


class TTSEngine:
    """
    线程安全的 TTS 引擎（双后端：SAPI5 + Edge-TTS）。

    工作原理：
    1. __init__ 中通过 VoiceManager 获取所有语音列表
    2. play() 根据 voice.provider 启动对应的 worker 线程
    3. SAPI5 worker：CoInitialize → 创建引擎 → 逐句朗读
    4. Edge-TTS worker：asyncio 生成音频 → MCI 播放
    5. skip_to 用 _skip_to_index 信号，无需杀线程
    6. pause/resume 用 engine.pause()/resume()（SAPI5）或 MCI 命令（Edge）
    """

    # MCI 别名（用于 Edge-TTS 播放）
    _MCI_ALIAS = "pdf_voice_reader"

    # 句间额外停顿（毫秒）。当前仅作为统一配置常量保留；
    # SAPI5 的句间停顿由语音包自身特性决定，pyttsx3 没有提供精确调整接口；
    # Edge-TTS 由于每次只合成一个句子，句间延迟主要来自网络生成时间，
    # 已通过预生成下一句音频来压缩空档。
    INTER_SENTENCE_PAUSE_MS = 0

    def __init__(self, voice_manager: Optional[VoiceManager] = None):
        self._voice_manager = voice_manager or VoiceManager()
        self._voices: list[VoiceInfo] = self._voice_manager.voices

        self._state = ReaderState.IDLE
        self._state_lock = threading.Lock()

        # 朗读内容
        self._sentences: list[str] = []
        self._current_index = 0

        # 工作线程
        self._worker_thread: Optional[threading.Thread] = None

        # 控制信号
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始为非暂停
        self._pause_request = threading.Event()  # 请求立即暂停（SAPI5 用 engine.stop()）
        self._stop_flag = threading.Event()
        self._skip_to_index: Optional[int] = None  # skip 目标索引

        # SAPI5 朗读完成信号
        self._utterance_done = threading.Event()
        # SAPI5 开始播放信号（用于同步 UI 高亮与音频播放）
        self._utterance_started = threading.Event()

        # 当前已真正开始播放（音频出声）的句子索引——导航按钮以此为基准
        self._spoken_index = 0
        # SAPI5 首个 word 事件（StartStream）是否已到达
        self._word_seen = False
        # 当前 say() 对应的句子索引（word 事件不带索引，靠时序关联）
        self._pending_utterance_index = -1
        # 最近一次 SAPI5 finished-utterance 事件时间（用于判断事件队列静默）
        self._last_finish_event = 0.0

        # 引擎参数（主线程设置，worker 线程应用）
        self._voice_id: Optional[str] = None
        self._rate: int = 200
        self._volume: float = 1.0

        # 朗读层文本规范化开关：
        # True  = 发声前把符号/上下标转成中文读法（如 x² → x的平方）
        # False = 逐字原样朗读（严格保留 α、∑、² 等 Unicode 字符）
        # 注意：只影响发声，句子列表与 UI 显示/高亮始终是保真文本。
        self._speech_normalize: bool = True

        # 当前播放使用的后端和语音
        self._current_provider: str = "sapi5"
        self._current_edge_short_name: str = ""

        # Edge-TTS 当前生效的参数（worker 启动时计算，预生成线程也会读取）
        self._edge_rate: int = 0
        self._edge_volume_pct: int = 0

        # Edge-TTS 预生成音频缓存：index -> temp_file_path
        self._edge_prefetch: dict[int, str] = {}
        self._edge_prefetch_lock = threading.Lock()

        # SAPI5 引擎实例（仅在 worker 线程中使用）
        self._sapi5_engine: Optional[pyttsx3.Engine] = None

        # 回调
        self._on_sentence_start: Optional[Callable[[int, str], None]] = None
        self._on_state_change: Optional[Callable[[ReaderState], None]] = None
        self._on_finish: Optional[Callable[[], None]] = None
        # 错误回调（引擎不可用 / 合成连续失败等）。消息为中文，可直接展示给用户。
        self._on_error: Optional[Callable[[str], None]] = None

        # Edge-TTS 连续合成失败计数（达到阈值后中止朗读，避免静默空跑）
        self._edge_fail_streak = 0
        # Edge-TTS 连续失败阈值：首失败即提示，达到阈值后停止朗读
        self.EDGE_MAX_CONSECUTIVE_FAILURES = 3

        # 设置默认语音
        if self._voices:
            self._voice_id = self._voices[0].id

    # ------------------------------------------------------------------
    # 属性访问
    # ------------------------------------------------------------------

    @property
    def voices(self) -> list[VoiceInfo]:
        return self._voices

    @property
    def state(self) -> ReaderState:
        with self._state_lock:
            return self._state

    @property
    def current_index(self) -> int:
        """
        当前【真正在播放】的句子索引（音频已出声）。

        注意：worker 线程内部推进的 _current_index 会在音频开始前就
        指向下一句，若用它做导航基准会出现"下一句跳过两句"的问题，
        因此对外统一暴露音频已实际开始播放的 _spoken_index。
        """
        return self._spoken_index

    @property
    def total_sentences(self) -> int:
        return len(self._sentences)

    @property
    def rate(self) -> int:
        return self._rate

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def voice_id(self) -> Optional[str]:
        return self._voice_id

    @property
    def speech_normalize(self) -> bool:
        """是否在发声前做朗读规范化（符号 → 中文读法）。"""
        return self._speech_normalize

    def set_speech_normalize(self, enabled: bool):
        """
        开关朗读规范化。

        - True（默认）：符号、希腊字母、上下标等转为中文读法后朗读，
          例如 "x² + y² = r²" 读作 "x的平方 加 y的平方 等于 r的平方"。
        - False：严格按 PDF 原始字符朗读，不做任何改写。

        该开关只影响 TTS 发声内容，不影响 PDF 提取结果、界面显示与高亮定位。
        """
        self._speech_normalize = bool(enabled)

    def _speech_text(self, sentence: str) -> str:
        """返回实际送去合成的文本（可能已做朗读规范化）。"""
        if not self._speech_normalize or not sentence:
            return sentence
        return _speech_normalized(sentence)

    # ------------------------------------------------------------------
    # 回调注册
    # ------------------------------------------------------------------

    def set_callbacks(
        self,
        on_sentence_start: Optional[Callable[[int, str], None]] = None,
        on_state_change: Optional[Callable[[ReaderState], None]] = None,
        on_finish: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        self._on_sentence_start = on_sentence_start
        self._on_state_change = on_state_change
        self._on_finish = on_finish
        self._on_error = on_error

    def _notify_error(self, message: str):
        """向 UI 上报一条错误消息（worker 线程调用，需保证回调线程安全）。"""
        if self._on_error:
            try:
                self._on_error(message)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 参数设置
    # ------------------------------------------------------------------

    def set_voice(self, voice_id: str):
        self._voice_id = voice_id

    def set_rate(self, rate: int):
        self._rate = rate
        # 如果正在播放且使用 SAPI5，实时更新
        if self._sapi5_engine and self._current_provider == "sapi5":
            try:
                self._sapi5_engine.setProperty("rate", rate)
            except Exception:
                pass
        # Edge-TTS 的 rate 在 worker 循环和预生成线程中读取；
        # 清空预生成缓存，避免旧 rate 的音频被继续使用
        if self._current_provider == "edge":
            self._edge_rate = int((self._rate - 200) / 2)
            self._edge_rate = max(-100, min(100, self._edge_rate))
            self._clear_edge_prefetch()

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, volume))
        if self._sapi5_engine and self._current_provider == "sapi5":
            try:
                self._sapi5_engine.setProperty("volume", self._volume)
            except Exception:
                pass
        if self._current_provider == "edge":
            self._edge_volume_pct = int((self._volume - 1.0) * 100)
            self._edge_volume_pct = max(-100, min(100, self._edge_volume_pct))
            self._clear_edge_prefetch()

    # ------------------------------------------------------------------
    # 文本处理
    # ------------------------------------------------------------------

    @staticmethod
    def split_sentences(text: str) -> list[str]:
        """将文本拆分为句子（支持中英文标点）。"""
        return [s for s, _, _ in TTSEngine.split_sentences_with_positions(text)]

    @staticmethod
    def split_sentences_with_positions(text: str) -> list[tuple[str, int, int]]:
        """
        将文本拆分为句子，并返回每个句子在原文中的起止偏移。

        支持中文、英文及混排文本；对英文缩写（Mr./e.g.）、数字小数点（3.14）、
        省略号、引号等场景做了保护，避免在句子中间错误断句。

        Returns:
            list of (sentence_text, start_offset, end_offset)
        """
        if not text or not text.strip():
            return []

        text = text.strip()

        # 常见英文缩写/称谓（小写集合）
        en_abbreviations = {
            "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st",
            "e.g", "i.e", "etc", "et al", "vs", "vol", "vols",
            "inc", "ltd", "co", "corp", "llc", "plc",
            "a.m", "p.m", "am", "pm",
            "fig", "figs", "no", "nos", "pp", "pg",
            "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
            "mon", "tue", "wed", "thu", "fri", "sat", "sun",
        }

        def _is_sentence_end(pos: int) -> bool:
            ch = text[pos]
            # 中文结束标点直接视为句末
            if ch in "。！？；":
                return True
            if ch not in ".!?":
                return False

            # 省略号 ... 或 …… 不单独断句
            if ch == ".":
                if (pos >= 1 and text[pos - 1] == ".") or (pos + 1 < len(text) and text[pos + 1] == "."):
                    return False

            # 数字小数点：如 3.14、v1.2.3
            if ch == "." and pos + 1 < len(text) and text[pos + 1].isdigit():
                return False

            # 向后看：若是 p.m. / e.g. / i.e. 这类缩写中的中间点（.字母.），不结束
            if ch == "." and pos + 2 < len(text) and text[pos + 1].isalpha() and text[pos + 2] == ".":
                return False

            # 英文缩写：向前取完整 token（允许内部有点，如 p.m / e.g）
            if ch == ".":
                start = pos
                while start > 0 and (text[start - 1].isalnum() or text[start - 1] == "."):
                    start -= 1
                token = text[start:pos].lower().rstrip(".")
                if token in en_abbreviations:
                    return False

            return True

        result = []
        start = 0
        n = len(text)
        i = 0
        quote_chars = "\"'\u201c\u201d\u2018\u2019"  # 中英文单双引号
        trailing = quote_chars + "）]}\u300d\u300f\uff09\u3015\uff3d\uff5d \t\n\r"

        while i < n:
            if _is_sentence_end(i):
                end = i + 1
                # 把紧随其后的引号/括号/空白纳入本句
                while end < n and text[end] in trailing:
                    end += 1
                sentence = text[start:end].strip()
                if sentence:
                    result.append((sentence, start, end))
                start = end
                i = end
            else:
                i += 1

        # 剩余文本作为最后一句
        if start < n:
            tail = text[start:].strip()
            if tail:
                result.append((tail, start, n))

        return result

    # ------------------------------------------------------------------
    # 播放控制
    # ------------------------------------------------------------------

    def load_text(self, text: str, start_index: int = 0):
        """加载要朗读的文本。"""
        self.stop()
        self._sentences = self.split_sentences(text)
        self._current_index = max(0, min(start_index, len(self._sentences) - 1)) if self._sentences else 0
        self._spoken_index = self._current_index

    def load_sentences(self, sentences: list[str], start_index: int = 0):
        """直接加载句子列表（避免重复拆分）。"""
        self.stop()
        self._sentences = list(sentences)
        self._current_index = max(0, min(start_index, len(self._sentences) - 1)) if self._sentences else 0
        self._spoken_index = self._current_index

    def play(self, text: Optional[str] = None, start_index: int = 0, sentences: Optional[list[str]] = None):
        """开始朗读。可传入 text（自动拆句）或 sentences（直接使用）。"""
        if sentences is not None:
            self.load_sentences(sentences, start_index)
        elif text is not None:
            self.load_text(text, start_index)

        if not self._sentences:
            return

        # 如果正在暂停，直接恢复
        if self.state == ReaderState.PAUSED:
            self.resume()
            return

        # 如果正在播放，先停止
        if self.state == ReaderState.PLAYING:
            self.stop()
            if self._worker_thread and self._worker_thread.is_alive():
                self._worker_thread.join(timeout=2.0)

        # 确定后端
        voice = self._voice_manager.get_voice(self._voice_id)
        if voice:
            self._current_provider = voice.provider
            self._current_edge_short_name = voice.short_name
        else:
            self._current_provider = "sapi5"
            self._current_edge_short_name = ""

        # 重置控制标志
        self._stop_flag.clear()
        self._pause_event.set()
        self._pause_request.clear()
        self._skip_to_index = None
        self._utterance_started.clear()
        self._utterance_done.clear()
        self._edge_fail_streak = 0

        self._set_state(ReaderState.PLAYING)
        self._spoken_index = self._current_index
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def pause(self):
        """暂停朗读（立即停止当前音频）。"""
        if self.state == ReaderState.PLAYING:
            self._pause_event.clear()

            if self._current_provider == "sapi5" and self._sapi5_engine:
                # SAPI5 的 pause() 不会立即停止当前播放的音频，
                # 因此改为 stop() 立即终止，恢复时从当前句重新播放。
                self._pause_request.set()
                try:
                    self._sapi5_engine.stop()
                except Exception:
                    pass
                # 唤醒正在 iterate 等待的 worker
                self._utterance_done.set()
                self._utterance_started.set()

            self._set_state(ReaderState.PAUSED)

    def resume(self):
        """恢复朗读。"""
        if self.state == ReaderState.PAUSED:
            self._pause_request.clear()
            self._pause_event.set()
            self._set_state(ReaderState.PLAYING)

    def stop(self):
        """停止朗读。"""
        self._stop_flag.set()
        self._pause_event.set()
        self._utterance_done.set()
        self._utterance_started.set()

        self._set_state(ReaderState.STOPPED)

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)

        # 停止后清理 Edge-TTS 预生成缓存，避免残留临时文件
        self._clear_edge_prefetch()

        self._set_state(ReaderState.IDLE)

    def skip_to(self, index: int):
        """
        跳转到指定句子。

        不再杀线程重建，而是设置 _skip_to_index 信号，
        worker 线程在下一个循环迭代时自动跳转。
        """
        if not (0 <= index < len(self._sentences)):
            return

        was_state = self.state
        if was_state in (ReaderState.PLAYING, ReaderState.PAUSED):
            # 设置 skip 信号
            self._skip_to_index = index
            # 解除暂停状态（如果在暂停中）
            if was_state == ReaderState.PAUSED:
                self._pause_event.set()
                if self._sapi5_engine and self._current_provider == "sapi5":
                    try:
                        self._sapi5_engine.resume()
                    except Exception:
                        pass
                self._set_state(ReaderState.PLAYING)
            # 解除 utterance 等待
            self._utterance_done.set()
            self._utterance_started.set()
        else:
            # 未在播放，仅更新索引
            self._current_index = index
            self._spoken_index = index

    # ------------------------------------------------------------------
    # 工作线程入口
    # ------------------------------------------------------------------

    def _worker_loop(self):
        """工作线程入口，根据后端分发。"""
        if self._current_provider == "edge":
            self._run_edge_worker()
        else:
            self._run_sapi5_worker()

    # ------------------------------------------------------------------
    # SAPI5 后端
    # ------------------------------------------------------------------

    def _on_utterance_finished(self, name, completed):
        """pyttsx3 朗读完成回调（由 SAPI5 事件循环在 worker 线程中调用）。"""
        self._last_finish_event = time.time()
        if completed:
            self._utterance_done.set()

    def _sapi5_drain_events(self):
        """
        排空 SAPI5 挂起事件（在 stop()/跳过之后、下一次 say() 之前必须调用）。

        原因：pyttsx3 的 engine.stop() 通过 Speak("", 3) 冲刷队列，会异步产生
        【两个】EndStream 事件：一个终止当前句、另一个来自空流本身。后者是
        异步投递的，若未等它处理完就 say() 下一句，它会被误判为新句子的
        完成事件（completed=True），导致新句子刚 say() 就被标记为 finished——
        表现为"下一句跳过两句"、"上一句跳回错误位置"、"恢复播放后当前句被吞掉"。

        排空策略：持续泵送事件，直到驱动内部 _speaking 复位 且 最近 150ms
        内没有新的完成事件（事件队列进入静默期），确保陈旧事件全部处理完。
        """
        if not self._sapi5_engine:
            return
        driver = getattr(self._sapi5_engine.proxy, "_driver", None)
        self._last_finish_event = time.time()
        deadline = time.time() + 1.0  # 最多排空约 1s
        while time.time() < deadline:
            try:
                self._sapi5_engine.iterate()
            except Exception:
                break
            time.sleep(0.01)
            if driver is not None and not driver._speaking \
                    and (time.time() - self._last_finish_event) > 0.15:
                # 事件队列已静默，陈旧事件全部处理完
                return

    def _sapi5_stop_and_drain(self):
        """停止当前音频并排空陈旧事件（skip 跳转时使用）。"""
        try:
            self._sapi5_engine.stop()
        except Exception:
            pass
        self._sapi5_drain_events()

    def _on_utterance_started(self, name):
        """
        pyttsx3 的 SAPI5 驱动会在 say() 时【同步】触发 started-utterance，
        此时音频尚未开始合成/播放，若用它通知 UI 会导致高亮提前于语音。
        真正的"音频开始"信号是 started-word 事件（StartStream）。
        保留该回调仅为兼容，不再做任何 UI 通知。
        """

    def _on_word_started(self, name=None, location=None, length=None):
        """
        SAPI5 事件回调：首个 started-word 事件对应 StartStream，
        表示音频流真正开始输出。此时才通知 UI 高亮，与语音精确对齐。
        后续逐词事件一律忽略（_word_seen 已置位）。
        """
        if self._word_seen:
            return
        self._word_seen = True
        idx = self._pending_utterance_index
        if 0 <= idx < len(self._sentences):
            self._notify_sentence_start(idx, self._sentences[idx])
        self._utterance_started.set()

    def _notify_sentence_start(self, index: int, sentence: str):
        """通知 UI 某句开始播放，并更新『已播放索引』（导航基准）。"""
        self._spoken_index = index
        if self._on_sentence_start:
            try:
                self._on_sentence_start(index, sentence)
            except Exception:
                pass

    def _run_sapi5_worker(self):
        """SAPI5 后端工作循环。"""
        pythoncom.CoInitialize()
        try:
            self._run_sapi5_loop()
        except Exception as exc:
            # 引擎初始化/运行失败必须让用户知道，不能静默回到 IDLE
            # （否则表现为"点了播放没声音也没反应"）
            self._notify_error(f"本地语音引擎（SAPI5）启动失败：{exc}")
            self._set_state(ReaderState.IDLE)
        finally:
            pythoncom.CoUninitialize()

    def _run_sapi5_loop(self):
        """SAPI5 朗读循环。"""
        # 在本线程内创建引擎
        self._sapi5_engine = pyttsx3.init()

        # 应用参数
        if self._voice_id and not self._voice_id.startswith(EDGE_VOICE_PREFIX):
            try:
                self._sapi5_engine.setProperty("voice", self._voice_id)
            except Exception:
                pass
        self._sapi5_engine.setProperty("rate", self._rate)
        self._sapi5_engine.setProperty("volume", self._volume)

        # 注册永久的事件回调
        self._sapi5_engine.connect("finished-utterance", self._on_utterance_finished)
        self._sapi5_engine.connect("started-utterance", self._on_utterance_started)
        self._sapi5_engine.connect("started-word", self._on_word_started)

        # 启动非阻塞手动事件循环
        self._sapi5_engine.startLoop(False)

        while self._current_index < len(self._sentences):
            if self._stop_flag.is_set():
                break

            # 处理立即暂停请求：SAPI5 用 stop() 已终止音频，这里等待恢复
            if self._pause_request.is_set():
                self._pause_request.clear()
                # 排空 pause() 的 engine.stop() 产生的陈旧事件，
                # 否则恢复后重播当前句会被误判为立即完成
                self._sapi5_drain_events()
                self._pause_event.wait()
                if self._stop_flag.is_set():
                    break
                # 恢复后重播当前句
                continue

            # 处理 skip 信号
            if self._skip_to_index is not None:
                self._current_index = self._skip_to_index
                self._skip_to_index = None
                # stop 后必须先排空陈旧事件，否则下一句会被误判为立即完成
                self._sapi5_stop_and_drain()
                continue

            self._pause_event.wait()
            if self._stop_flag.is_set():
                break

            # 处理两句之间的暂停请求（pause 可能落在两句之间的窗口）
            if self._pause_request.is_set():
                self._pause_request.clear()
                self._sapi5_drain_events()
                self._pause_event.wait()
                if self._stop_flag.is_set():
                    break
                continue

            sentence = self._sentences[self._current_index]
            if not sentence.strip():
                self._current_index += 1
                continue

            # 不在这里通知 UI —— 改为等首个 started-word 事件（StartStream），
            # 确保 SAPI5 音频真正开始输出时才高亮，消除提前/累积偏移。

            # 重置完成信号，开始朗读
            self._utterance_done.clear()
            self._utterance_started.clear()
            self._word_seen = False
            self._pending_utterance_index = self._current_index
            try:
                # say() 传入 name=句索引；音频真正开始的信号是 started-word。
                # 送合成的是朗读层文本（可开关），句索引/高亮仍用保真原文。
                self._sapi5_engine.say(self._speech_text(sentence), name=f"sent_{self._current_index}")

                # 阶段 1：等待首个 word 事件（StartStream，音频真正开始输出）
                # 事件回调中会通知 UI 高亮 + set _utterance_started
                started_wait = 0
                while not self._utterance_started.is_set() and not self._stop_flag.is_set():
                    if self._skip_to_index is not None or self._pause_request.is_set():
                        break
                    try:
                        self._sapi5_engine.iterate()
                    except Exception:
                        break
                    time.sleep(0.01)
                    started_wait += 1
                    if started_wait > 100:  # 1 秒超时
                        # 超时回退：极少数无发音词的句子可能没有 word 事件
                        self._notify_sentence_start(self._current_index, sentence)
                        break

                # 阶段 2：等待 finished-utterance 回调（音频播放完成）
                timeout_count = 0
                pause_during_sentence = False
                while not self._utterance_done.is_set() and not self._stop_flag.is_set():
                    # 检测 skip 信号
                    if self._skip_to_index is not None:
                        break

                    # 检测暂停请求（pause() 已调用 engine.stop()，需尽快退出）
                    if self._pause_request.is_set():
                        pause_during_sentence = True
                        break

                    try:
                        self._sapi5_engine.iterate()
                    except Exception:
                        break

                    if not self._pause_event.is_set():
                        time.sleep(0.05)  # 暂停时降低 CPU 占用
                    else:
                        time.sleep(0.01)

                    timeout_count += 1
                    if timeout_count > 6000:  # 安全阈值 ~60s
                        break
            except Exception:
                pause_during_sentence = self._pause_request.is_set()

            # 如果 iterate 过程中触发了暂停请求，进入暂停等待
            if pause_during_sentence or self._pause_request.is_set():
                self._pause_request.clear()
                # pause() 已调用 engine.stop()，先排空其产生的陈旧事件，
                # 否则恢复后重播当前句会被误判为立即完成
                self._sapi5_drain_events()
                self._pause_event.wait()
                if self._stop_flag.is_set():
                    break
                # 恢复后重播当前句
                continue

            # skip 信号处理：跳到目标句子
            if self._skip_to_index is not None:
                self._current_index = self._skip_to_index
                self._skip_to_index = None
                # stop 后必须先排空陈旧事件，否则目标句会被误判为立即完成
                self._sapi5_stop_and_drain()
                continue

            if self._stop_flag.is_set():
                break

            self._current_index += 1

        # 清理
        try:
            self._sapi5_engine.stop()
        except Exception:
            pass
        try:
            self._sapi5_engine.endLoop()
        except Exception:
            pass
        self._sapi5_engine = None

        # 朗读完成回调
        if not self._stop_flag.is_set() and self._current_index >= len(self._sentences):
            self._set_state(ReaderState.IDLE)
            if self._on_finish:
                try:
                    self._on_finish()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Edge-TTS 后端
    # ------------------------------------------------------------------

    def _run_edge_worker(self):
        """Edge-TTS 后端工作循环（带预生成优化，减少句间网络延迟）。"""
        mci = ctypes.windll.winmm
        alias = self._MCI_ALIAS

        # Edge-TTS 参数转换（写入成员变量，供预生成线程读取）
        self._edge_rate = int((self._rate - 200) / 2)  # 200 -> 0, 400 -> 100, 50 -> -75
        self._edge_rate = max(-100, min(100, self._edge_rate))
        # Edge-TTS volume 是相对百分比：1.0 -> "+0%", 0.5 -> "-50%", 0.0 -> "-100%"
        self._edge_volume_pct = int((self._volume - 1.0) * 100)
        self._edge_volume_pct = max(-100, min(100, self._edge_volume_pct))

        # 清空历史缓存，避免播放新文档时误用过期音频
        self._clear_edge_prefetch()

        while self._current_index < len(self._sentences):
            if self._stop_flag.is_set():
                break

            # 处理 skip 信号：跳转目标句之前的预生成缓存全部失效
            if self._skip_to_index is not None:
                self._current_index = self._skip_to_index
                self._skip_to_index = None
                self._mci_stop(mci, alias)
                self._clear_edge_prefetch()
                continue

            self._pause_event.wait()
            if self._stop_flag.is_set():
                break

            sentence = self._sentences[self._current_index]
            if not sentence.strip():
                self._current_index += 1
                continue

            # 优先使用预生成的音频；没有命中则同步生成当前句
            temp_file = self._get_prefetched_edge_audio(self._current_index)
            if temp_file is None:
                temp_file = os.path.join(tempfile.gettempdir(), f"edge_tts_{threading.get_ident()}_{self._current_index}.mp3")
                try:
                    self._generate_edge_audio(self._speech_text(sentence), self._current_edge_short_name, temp_file, self._edge_rate, self._edge_volume_pct)
                except Exception as exc:
                    # 合成失败不再静默跳句：首失败即上报，连续失败达到阈值后
                    # 中止朗读并提示切换本地语音，避免"看起来在读其实没声"。
                    self._edge_fail_streak += 1
                    if self._edge_fail_streak == 1:
                        self._notify_error(f"Edge-TTS 合成失败：{exc}")
                    elif self._edge_fail_streak >= self.EDGE_MAX_CONSECUTIVE_FAILURES:
                        self._notify_error(EDGE_FATAL_ERROR_MESSAGE)
                        self._set_state(ReaderState.IDLE)
                        break
                    self._current_index += 1
                    continue
            # 合成成功，重置连续失败计数
            self._edge_fail_streak = 0

            if self._stop_flag.is_set():
                self._cleanup_temp(temp_file)
                break

            # 当前句播放的同时，后台预生成下一句，压缩句间网络等待
            next_idx = self._current_index + 1
            if next_idx < len(self._sentences):
                threading.Thread(
                    target=self._prefetch_edge_audio,
                    args=(next_idx,),
                    daemon=True,
                ).start()

            # 用 MCI 播放
            self._mci_open(mci, alias, temp_file)
            self._mci_send(mci, f"play {alias}")

            # 等音频真正开始播放后再通知 UI，避免高亮提前于语音
            self._mci_wait_playing(mci, alias, timeout=1.0)
            if not self._stop_flag.is_set() and self._skip_to_index is None:
                self._notify_sentence_start(self._current_index, sentence)

            # 轮询播放状态
            self._poll_edge_playback(mci, alias)

            # 关闭并清理
            self._mci_stop(mci, alias)
            self._cleanup_temp(temp_file)

            # skip 信号处理
            if self._skip_to_index is not None:
                self._current_index = self._skip_to_index
                self._skip_to_index = None
                # 跳转后预生成缓存失效
                self._clear_edge_prefetch()
                continue

            if self._stop_flag.is_set():
                break

            self._current_index += 1

        # 退出时清理预生成缓存
        self._clear_edge_prefetch()

        # 朗读完成回调
        if not self._stop_flag.is_set() and self._current_index >= len(self._sentences):
            self._set_state(ReaderState.IDLE)
            if self._on_finish:
                try:
                    self._on_finish()
                except Exception:
                    pass

    def _generate_edge_audio(self, text: str, voice_short_name: str, output_path: str, rate: int, volume_pct: int):
        """使用 Edge-TTS 生成音频文件。

        经 edge_tts_compat 兼容层执行：自动使用微软 v2 合成端点
        （api.msedgeservices.com），v2 不可用时回退旧端点，
        并附带 locale 无关的 Date 头解析修复。
        """
        if not _EDGE_TTS_AVAILABLE:
            raise RuntimeError("edge-tts 库未安装，无法使用在线语音（请 pip install edge-tts）")
        edge_tts_compat.synthesize_to_file(
            text=text,
            voice=voice_short_name,
            output_path=output_path,
            rate=rate,
            volume_pct=volume_pct,
        )

    def _prefetch_edge_audio(self, index: int):
        """后台预生成指定句子的 Edge-TTS 音频，减少句间网络延迟。"""
        if not (0 <= index < len(self._sentences)) or not self._current_edge_short_name:
            return

        with self._edge_prefetch_lock:
            if index in self._edge_prefetch:
                return
            # 先占位，避免多个线程重复生成同一句
            self._edge_prefetch[index] = ""

        temp_file = os.path.join(tempfile.gettempdir(), f"edge_tts_prefetch_{threading.get_ident()}_{index}.mp3")
        try:
            self._generate_edge_audio(
                self._speech_text(self._sentences[index]),
                self._current_edge_short_name,
                temp_file,
                self._edge_rate,
                self._edge_volume_pct,
            )
        except Exception:
            with self._edge_prefetch_lock:
                self._edge_prefetch.pop(index, None)
            self._cleanup_temp(temp_file)
            return

        with self._edge_prefetch_lock:
            if self._stop_flag.is_set():
                self._cleanup_temp(temp_file)
                self._edge_prefetch.pop(index, None)
            else:
                self._edge_prefetch[index] = temp_file

    def _get_prefetched_edge_audio(self, index: int) -> Optional[str]:
        """获取并移除指定句子的预生成音频路径。"""
        with self._edge_prefetch_lock:
            path = self._edge_prefetch.pop(index, None)
            if path and os.path.exists(path):
                return path
            return None

    def _clear_edge_prefetch(self):
        """清理所有预生成音频文件与缓存。"""
        with self._edge_prefetch_lock:
            for path in self._edge_prefetch.values():
                if path:
                    self._cleanup_temp(path)
            self._edge_prefetch.clear()

    def _poll_edge_playback(self, mci, alias: str):
        """轮询 Edge-TTS 播放状态，处理暂停/恢复/停止/跳转。"""
        status_buf = ctypes.create_unicode_buffer(256)
        mci_paused = False

        while True:
            # 检查停止
            if self._stop_flag.is_set():
                self._mci_send(mci, f"stop {alias}")
                break

            # 检查 skip
            if self._skip_to_index is not None:
                self._mci_send(mci, f"stop {alias}")
                break

            # 暂停/恢复
            if not self._pause_event.is_set() and not mci_paused:
                self._mci_send(mci, f"pause {alias}")
                mci_paused = True
            elif self._pause_event.is_set() and mci_paused:
                self._mci_send(mci, f"resume {alias}")
                mci_paused = False

            # 检查播放完成
            self._mci_send(mci, f"status {alias} mode", status_buf)
            if status_buf.value == "stopped" and not mci_paused:
                break

            time.sleep(0.05)

    def _mci_wait_playing(self, mci, alias: str, timeout: float = 1.0) -> bool:
        """等待 MCI 设备进入 playing 状态（音频真正出声）。"""
        status_buf = ctypes.create_unicode_buffer(256)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop_flag.is_set() or self._skip_to_index is not None:
                return False
            self._mci_send(mci, f"status {alias} mode", status_buf)
            if status_buf.value.lower() == "playing":
                return True
            time.sleep(0.02)
        return False

    # ------------------------------------------------------------------
    # MCI 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _mci_send(mci, command: str, ret_buf=None) -> int:
        """发送 MCI 命令，返回错误码。"""
        if ret_buf is not None:
            return mci.mciSendStringW(command, ret_buf, 256, None)
        return mci.mciSendStringW(command, None, 0, None)

    def _mci_open(self, mci, alias: str, file_path: str):
        """打开 MCI 设备。"""
        self._mci_send(mci, f'open "{file_path}" type mpegvideo alias {alias}')

    def _mci_stop(self, mci, alias: str):
        """停止并关闭 MCI 设备。"""
        self._mci_send(mci, f"stop {alias}")
        self._mci_send(mci, f"close {alias}")

    @staticmethod
    def _cleanup_temp(file_path: str):
        """清理临时文件。"""
        try:
            os.remove(file_path)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _set_state(self, state: ReaderState):
        with self._state_lock:
            self._state = state
        if self._on_state_change:
            try:
                self._on_state_change(state)
            except Exception:
                pass

    def shutdown(self):
        """清理资源。"""
        self.stop()
