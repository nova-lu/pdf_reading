"""
TTS 语音合成引擎模块（重构版）

核心改进：
1. 双后端支持：SAPI5（pyttsx3 本地语音）+ Edge-TTS（在线神经语音）
2. skip_to 不再杀线程重建，用 _skip_to_index 信号在 worker 内跳转
3. pause/resume 使用 engine.pause()/resume() 真正暂停 SAPI5 音频
4. Edge-TTS 通过 MCI (Windows Media Control Interface) 播放 MP3
5. 持久 worker 线程，play/skip/pause 不需要重建引擎

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
from typing import Callable, Optional

# 确保能导入同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voice_manager import VoiceManager, VoiceInfo, EDGE_VOICE_PREFIX

# 尝试导入 edge-tts（可选依赖）
try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    _EDGE_TTS_AVAILABLE = False


class ReaderState(Enum):
    """朗读状态。"""
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"


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

        # 引擎参数（主线程设置，worker 线程应用）
        self._voice_id: Optional[str] = None
        self._rate: int = 200
        self._volume: float = 1.0

        # 当前播放使用的后端和语音
        self._current_provider: str = "sapi5"
        self._current_edge_short_name: str = ""

        # SAPI5 引擎实例（仅在 worker 线程中使用）
        self._sapi5_engine: Optional[pyttsx3.Engine] = None

        # 回调
        self._on_sentence_start: Optional[Callable[[int, str], None]] = None
        self._on_state_change: Optional[Callable[[ReaderState], None]] = None
        self._on_finish: Optional[Callable[[], None]] = None

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
        return self._current_index

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

    # ------------------------------------------------------------------
    # 回调注册
    # ------------------------------------------------------------------

    def set_callbacks(
        self,
        on_sentence_start: Optional[Callable[[int, str], None]] = None,
        on_state_change: Optional[Callable[[ReaderState], None]] = None,
        on_finish: Optional[Callable[[], None]] = None,
    ):
        self._on_sentence_start = on_sentence_start
        self._on_state_change = on_state_change
        self._on_finish = on_finish

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

    def set_volume(self, volume: float):
        self._volume = max(0.0, min(1.0, volume))
        if self._sapi5_engine and self._current_provider == "sapi5":
            try:
                self._sapi5_engine.setProperty("volume", self._volume)
            except Exception:
                pass

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

        Returns:
            list of (sentence_text, start_offset, end_offset)
        """
        if not text or not text.strip():
            return []

        text = text.strip()
        pattern = r"[^。！？；.!?\n]+[。！？；.!?]*"
        matches = list(re.finditer(pattern, text))

        result = []
        for m in matches:
            s = m.group().strip()
            if s:
                result.append((s, m.start(), m.end()))
        return result

    # ------------------------------------------------------------------
    # 播放控制
    # ------------------------------------------------------------------

    def load_text(self, text: str, start_index: int = 0):
        """加载要朗读的文本。"""
        self.stop()
        self._sentences = self.split_sentences(text)
        self._current_index = max(0, min(start_index, len(self._sentences) - 1)) if self._sentences else 0

    def load_sentences(self, sentences: list[str], start_index: int = 0):
        """直接加载句子列表（避免重复拆分）。"""
        self.stop()
        self._sentences = list(sentences)
        self._current_index = max(0, min(start_index, len(self._sentences) - 1)) if self._sentences else 0

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

        self._set_state(ReaderState.PLAYING)
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

        self._set_state(ReaderState.STOPPED)

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=3.0)

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
        else:
            # 未在播放，仅更新索引
            self._current_index = index

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
        if completed:
            self._utterance_done.set()

    def _run_sapi5_worker(self):
        """SAPI5 后端工作循环。"""
        pythoncom.CoInitialize()
        try:
            self._run_sapi5_loop()
        except Exception:
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

        # 注册永久的 finished-utterance 回调
        self._sapi5_engine.connect("finished-utterance", self._on_utterance_finished)

        # 启动非阻塞手动事件循环
        self._sapi5_engine.startLoop(False)

        while self._current_index < len(self._sentences):
            if self._stop_flag.is_set():
                break

            # 处理立即暂停请求：SAPI5 用 stop() 已终止音频，这里等待恢复
            if self._pause_request.is_set():
                self._pause_request.clear()
                self._pause_event.wait()
                if self._stop_flag.is_set():
                    break
                # 恢复后重播当前句
                continue

            # 处理 skip 信号
            if self._skip_to_index is not None:
                self._current_index = self._skip_to_index
                self._skip_to_index = None
                try:
                    self._sapi5_engine.stop()
                except Exception:
                    pass
                continue

            self._pause_event.wait()
            if self._stop_flag.is_set():
                break

            sentence = self._sentences[self._current_index]
            if not sentence.strip():
                self._current_index += 1
                continue

            # 通知 UI：开始朗读这句
            if self._on_sentence_start:
                try:
                    self._on_sentence_start(self._current_index, sentence)
                except Exception:
                    pass

            # 重置完成信号，开始朗读
            self._utterance_done.clear()
            try:
                self._sapi5_engine.say(sentence)

                # 手动泵送事件循环，直到该句朗读完成
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
                self._pause_event.wait()
                if self._stop_flag.is_set():
                    break
                # 恢复后重播当前句
                continue

            # skip 信号处理：跳到目标句子
            if self._skip_to_index is not None:
                self._current_index = self._skip_to_index
                self._skip_to_index = None
                try:
                    self._sapi5_engine.stop()
                except Exception:
                    pass
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
        """Edge-TTS 后端工作循环。"""
        mci = ctypes.windll.winmm
        alias = self._MCI_ALIAS

        # Edge-TTS 参数转换
        edge_rate = int((self._rate - 200) / 2)  # 200 -> 0, 400 -> 100, 50 -> -75
        edge_rate = max(-100, min(100, edge_rate))
        # Edge-TTS volume 是相对百分比：1.0 -> "+0%", 0.5 -> "-50%", 0.0 -> "-100%"
        edge_volume_pct = int((self._volume - 1.0) * 100)
        edge_volume_pct = max(-100, min(100, edge_volume_pct))

        while self._current_index < len(self._sentences):
            if self._stop_flag.is_set():
                break

            # 处理 skip 信号
            if self._skip_to_index is not None:
                self._current_index = self._skip_to_index
                self._skip_to_index = None
                self._mci_stop(mci, alias)
                continue

            self._pause_event.wait()
            if self._stop_flag.is_set():
                break

            sentence = self._sentences[self._current_index]
            if not sentence.strip():
                self._current_index += 1
                continue

            # 生成音频（先生成，成功后再通知 UI）
            temp_file = os.path.join(tempfile.gettempdir(), f"edge_tts_{threading.get_ident()}.mp3")
            try:
                self._generate_edge_audio(sentence, self._current_edge_short_name, temp_file, edge_rate, edge_volume_pct)
            except Exception:
                self._current_index += 1
                continue

            if self._stop_flag.is_set():
                self._cleanup_temp(temp_file)
                break

            # 通知 UI：音频已生成，即将播放
            if self._on_sentence_start:
                try:
                    self._on_sentence_start(self._current_index, sentence)
                except Exception:
                    pass

            # 用 MCI 播放
            self._mci_open(mci, alias, temp_file)
            self._mci_send(mci, f"play {alias}")

            # 轮询播放状态
            self._poll_edge_playback(mci, alias)

            # 关闭并清理
            self._mci_stop(mci, alias)
            self._cleanup_temp(temp_file)

            # skip 信号处理
            if self._skip_to_index is not None:
                self._current_index = self._skip_to_index
                self._skip_to_index = None
                continue

            if self._stop_flag.is_set():
                break

            self._current_index += 1

        # 朗读完成回调
        if not self._stop_flag.is_set() and self._current_index >= len(self._sentences):
            self._set_state(ReaderState.IDLE)
            if self._on_finish:
                try:
                    self._on_finish()
                except Exception:
                    pass

    def _generate_edge_audio(self, text: str, voice_short_name: str, output_path: str, rate: int, volume_pct: int):
        """使用 Edge-TTS 生成音频文件。"""
        communicate = edge_tts.Communicate(
            text,
            voice_short_name,
            rate=f"{rate:+d}%",
            volume=f"{volume_pct:+d}%",
        )
        asyncio.run(communicate.save(output_path))

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
