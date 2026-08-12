"""
朗读控制器模块

负责页面感知的朗读状态管理：
1. 构建句子列表和句子-页面映射（从当前页开始，而非总是第 1 页）
2. 朗读时自动跨页翻页高亮
3. 页面切换时更新朗读位置（切换后在当前页重新开始朗读）
4. 朗读范围控制（全部页面 / 仅当前页）

将 TTS 引擎和 UI 解耦：UI 通过控制器间接操作 TTS，
控制器负责维护"当前朗读到哪一页哪一句"的全局状态。
"""

import os
import sys
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdf_reader import PDFDocument
from tts_engine import TTSEngine, ReaderState


class ReadingController:
    """
    页面感知的朗读控制器。

    职责：
    - 根据 current_page 和 read_all_pages 构建朗读数据
    - 维护句子索引 -> 页码的映射
    - TTS 回调中检测翻页需求，通知 UI
    - 页面切换时重置朗读数据
    """

    def __init__(self, tts_engine: TTSEngine):
        self.tts = tts_engine
        self.pdf_doc: Optional[PDFDocument] = None
        self.current_page: int = 0
        self.read_all_pages: bool = True

        # 句子到页面的映射（全局索引 -> 页码）
        self._sentence_page_map: list[int] = []
        # 句子在页面 clean_text 中的起止偏移（(start, end)）
        self._sentence_offsets: list[tuple[int, int]] = []
        # 当前朗读的句子列表
        self._reading_sentences: list[str] = []
        # 朗读数据对应的起始页
        self._start_page: int = 0

        # UI 回调
        self._on_page_change: Optional[Callable[[int], None]] = None
        self._on_sentence_highlight: Optional[Callable[[int, int, str], None]] = None
        self._on_state_change: Optional[Callable[[ReaderState], None]] = None
        self._on_finish: Optional[Callable[[], None]] = None

        # 注册 TTS 回调
        self.tts.set_callbacks(
            on_sentence_start=self._on_tts_sentence_start,
            on_state_change=self._on_tts_state_change,
            on_finish=self._on_tts_finish,
        )

    # ------------------------------------------------------------------
    # 回调注册
    # ------------------------------------------------------------------

    def set_callbacks(
        self,
        on_page_change: Optional[Callable[[int], None]] = None,
        on_sentence_highlight: Optional[Callable[[int, int, str], None]] = None,
        on_state_change: Optional[Callable[[ReaderState], None]] = None,
        on_finish: Optional[Callable[[], None]] = None,
    ):
        """
        注册 UI 回调。

        Args:
            on_page_change: 翻页回调，参数为目标页码 (0-based)
            on_sentence_highlight: 高亮回调，参数为 (sentence_index, page_num, sentence_text, start_offset, end_offset)
            on_state_change: 状态变化回调
            on_finish: 朗读完成回调
        """
        self._on_page_change = on_page_change
        self._on_sentence_highlight = on_sentence_highlight
        self._on_state_change = on_state_change
        self._on_finish = on_finish

    # ------------------------------------------------------------------
    # 文档管理
    # ------------------------------------------------------------------

    def set_document(self, pdf_doc: Optional[PDFDocument]):
        """设置 PDF 文档。"""
        self.stop()
        self.pdf_doc = pdf_doc
        self.current_page = 0
        self._sentence_page_map = []
        self._reading_sentences = []

    @property
    def page_count(self) -> int:
        return self.pdf_doc.page_count if self.pdf_doc else 0

    @property
    def state(self) -> ReaderState:
        return self.tts.state

    @property
    def current_sentence_index(self) -> int:
        return self.tts.current_index

    @property
    def total_sentences(self) -> int:
        return self.tts.total_sentences

    # ------------------------------------------------------------------
    # 朗读数据构建
    # ------------------------------------------------------------------

    def _build_reading_data(self):
        """
        构建朗读数据：句子列表 + 句子-页面映射 + 句子在页面 clean_text 中的偏移。

        - read_all_pages=True: 从当前页到文档末尾
        - read_all_pages=False: 仅当前页
        """
        self._reading_sentences = []
        self._sentence_page_map = []
        self._sentence_offsets = []
        self._start_page = self.current_page

        if not self.pdf_doc:
            return

        if self.read_all_pages:
            start = self.current_page
            end = self.pdf_doc.page_count
        else:
            start = self.current_page
            end = self.current_page + 1

        for page_num in range(start, end):
            page_text = self.pdf_doc.get_page_text_clean(page_num)
            for s, s_start, s_end in TTSEngine.split_sentences_with_positions(page_text):
                self._reading_sentences.append(s)
                self._sentence_page_map.append(page_num)
                self._sentence_offsets.append((s_start, s_end))

    # ------------------------------------------------------------------
    # 播放控制（委托给 TTS 引擎）
    # ------------------------------------------------------------------

    def play(self):
        """开始朗读（从当前页开始）。"""
        if not self.pdf_doc:
            return

        # 如果正在暂停，直接恢复
        if self.tts.state == ReaderState.PAUSED:
            self.tts.resume()
            return

        # 构建朗读数据
        self._build_reading_data()

        if not self._reading_sentences:
            return

        self.tts.play(sentences=self._reading_sentences)

    def pause(self):
        """暂停。"""
        self.tts.pause()

    def resume(self):
        """恢复。"""
        self.tts.resume()

    def stop(self):
        """停止。"""
        self.tts.stop()

    def skip_next(self):
        """跳到下一句。"""
        if self.tts.total_sentences > 0:
            self.tts.skip_to(self.tts.current_index + 1)

    def skip_prev(self):
        """跳到上一句。"""
        if self.tts.total_sentences > 0:
            self.tts.skip_to(max(0, self.tts.current_index - 1))

    def seek_to_sentence(self, index: int):
        """拖拽进度条跳转到指定句子索引。"""
        if not self.pdf_doc:
            return
        # 确保朗读数据已构建（如果未在播放则构建）
        if self.tts.total_sentences == 0:
            self._build_reading_data()
            if not self._reading_sentences:
                return
            self.tts.load_sentences(self._reading_sentences, index)
            self.tts.play()
        else:
            self.tts.skip_to(max(0, min(index, self.tts.total_sentences - 1)))

    def play_selected_text(self, text: str):
        """朗读用户选中的一段文本（独立于页面朗读模式）。"""
        if not text or not text.strip():
            return
        self.stop()
        self._reading_sentences = []
        self._sentence_page_map = []
        # 直接让 TTS 朗读这段文字
        self.tts.play(text=text)

    def toggle_play_pause(self):
        """播放/暂停切换。"""
        state = self.tts.state
        if state == ReaderState.PLAYING:
            self.pause()
        elif state == ReaderState.PAUSED:
            self.resume()
        else:
            self.play()

    # ------------------------------------------------------------------
    # 页面导航
    # ------------------------------------------------------------------

    def goto_page(self, page_num: int) -> bool:
        """
        跳转到指定页面。

        如果正在朗读，停止当前朗读。
        更新 current_page，但不自动开始朗读。
        返回是否成功跳转。
        """
        if not self.pdf_doc or not (0 <= page_num < self.pdf_doc.page_count):
            return False

        self.stop()
        self.current_page = page_num
        return True

    def next_page(self) -> bool:
        """下一页。"""
        return self.goto_page(self.current_page + 1)

    def prev_page(self) -> bool:
        """上一页。"""
        return self.goto_page(self.current_page - 1)

    # ------------------------------------------------------------------
    # 语音参数（委托给 TTS 引擎）
    # ------------------------------------------------------------------

    def set_voice(self, voice_id: str):
        self.tts.set_voice(voice_id)

    def set_rate(self, rate: int):
        self.tts.set_rate(rate)

    def set_volume(self, volume: float):
        self.tts.set_volume(volume)

    def shutdown(self):
        """清理资源。"""
        self.tts.shutdown()

    # ------------------------------------------------------------------
    # TTS 回调处理
    # ------------------------------------------------------------------

    def _on_tts_sentence_start(self, index: int, sentence: str):
        """TTS 开始朗读某句时的回调（在 worker 线程中被调用）。"""
        # 检查是否需要翻页
        target_page = self.current_page
        start_offset = -1
        end_offset = -1
        if self._sentence_page_map and index < len(self._sentence_page_map):
            target_page = self._sentence_page_map[index]
            if index < len(self._sentence_offsets):
                start_offset, end_offset = self._sentence_offsets[index]
            if target_page != self.current_page:
                self.current_page = target_page
                if self._on_page_change:
                    try:
                        self._on_page_change(target_page)
                    except Exception:
                        pass

        # 高亮回调
        if self._on_sentence_highlight:
            try:
                self._on_sentence_highlight(index, target_page, sentence, start_offset, end_offset)
            except Exception:
                pass

    def _on_tts_state_change(self, state: ReaderState):
        """TTS 状态变化回调。"""
        if self._on_state_change:
            try:
                self._on_state_change(state)
            except Exception:
                pass

    def _on_tts_finish(self):
        """TTS 朗读完成回调。"""
        if self._on_finish:
            try:
                self._on_finish()
            except Exception:
                pass
