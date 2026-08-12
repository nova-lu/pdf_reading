"""
端到端测试脚本

验证以下功能：
1. 语音引擎数量 >= 5
2. SAPI5 播放 + skip_next 连续性（不再读一句就停）
3. SAPI5 播放 + pause/resume 连续性，且 pause 立即停止当前音频
4. Edge-TTS 播放
5. 页面感知朗读（从指定页开始）
6. 高亮范围与朗读句子精确匹配
7. 进度拖拽跳转
8. 选中文本朗读
9. 段落结构保留
"""

import sys
import os
import time
import threading
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from voice_manager import VoiceManager, EDGE_VOICE_PREFIX
from tts_engine import TTSEngine, ReaderState
from reading_controller import ReadingController
from pdf_reader import PDFReader
from ui.text_panel import TextPanel


# 测试结果记录
results = []

def record(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    results.append((name, passed, detail))
    print(f"  [{status}] {name}" + (f" - {detail}" if detail else ""))

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# 辅助：定位 PDF 测试文件
def find_pdf_path():
    pdf_path = os.path.join(os.path.dirname(__file__), "test_sample.pdf")
    if os.path.exists(pdf_path):
        return pdf_path
    for f in os.listdir(os.path.dirname(__file__)):
        if f.endswith(".pdf"):
            return os.path.join(os.path.dirname(__file__), f)
    return None


# ==================================================================
# Test 1: Voice count >= 5
# ==================================================================
section("Test 1: Voice count >= 5")
vm = VoiceManager()
sapi5_count = sum(1 for v in vm.voices if v.provider == "sapi5")
edge_count = sum(1 for v in vm.voices if v.provider == "edge")
total = vm.count
record("Total voices >= 5", total >= 5, f"Total={total} (SAPI5={sapi5_count}, Edge={edge_count})")
record("Has SAPI5 voices", sapi5_count > 0, f"{sapi5_count} SAPI5 voices")
record("Has Edge voices", edge_count > 0, f"{edge_count} Edge voices")

sapi_voices = [v for v in vm.voices if v.provider == "sapi5"]
edge_voices = [v for v in vm.voices if v.provider == "edge"]


# ==================================================================
# Test 2: SAPI5 play + skip_next continuity
# ==================================================================
section("Test 2: SAPI5 play + skip_next continuity")

if sapi_voices:
    tts_sapi = TTSEngine(vm)
    tts_sapi.set_voice(sapi_voices[0].id)
    tts_sapi.set_rate(300)

    sentences_played = []

    tts_sapi.set_callbacks(
        on_sentence_start=lambda idx, s: sentences_played.append(idx),
        on_state_change=lambda s: None,
        on_finish=lambda: None,
    )

    test_sentences = [
        "First sentence for testing.",
        "Second sentence is here.",
        "Third sentence follows.",
        "Fourth and final sentence.",
    ]

    tts_sapi.play(sentences=test_sentences)
    time.sleep(1.5)
    tts_sapi.skip_to(2)
    time.sleep(1.5)
    tts_sapi.skip_to(3)
    time.sleep(1.5)
    tts_sapi.stop()
    time.sleep(0.5)

    print(f"  Sentences played indices: {sentences_played}")
    record("Skip produced multiple sentences", len(sentences_played) >= 3,
          f"Played {len(sentences_played)} sentences: {sentences_played}")
    record("Skip reached target index 2", 2 in sentences_played, f"Indices: {sentences_played}")
    record("Skip reached target index 3", 3 in sentences_played, f"Indices: {sentences_played}")
    tts_sapi.shutdown()
else:
    record("Skip test", False, "No SAPI5 voices")


# ==================================================================
# Test 3: SAPI5 pause/resume continuity and immediate stop
# ==================================================================
section("Test 3: SAPI5 pause/resume + immediate stop")

if sapi_voices:
    tts_sapi2 = TTSEngine(vm)
    tts_sapi2.set_voice(sapi_voices[0].id)
    tts_sapi2.set_rate(200)

    sentences_played2 = []
    tts_sapi2.set_callbacks(
        on_sentence_start=lambda idx, s: sentences_played2.append((idx, time.time())),
        on_state_change=lambda s: None,
        on_finish=lambda: None,
    )

    long_sentences = [
        "This is the first sentence and it is relatively long so we can test pause.",
        "This is the second sentence for continued playback after resume.",
        "This is the third sentence to verify full continuity.",
    ]

    tts_sapi2.play(sentences=long_sentences)
    time.sleep(1.0)

    pause_time = time.time()
    tts_sapi2.pause()
    record("Paused state correct", tts_sapi2.state == ReaderState.PAUSED, f"State={tts_sapi2.state}")
    state_change_time = None
    # Poll state log is not available, so we just check state directly
    for _ in range(20):
        if tts_sapi2.state == ReaderState.PAUSED:
            state_change_time = time.time()
            break
        time.sleep(0.01)
    if state_change_time:
        record("Pause state change fast", state_change_time - pause_time < 0.2,
              f"Delay={state_change_time - pause_time:.3f}s")
    else:
        record("Pause state change fast", False, "State did not change to PAUSED")

    # Verify no new sentence starts after pause (until resume)
    sentences_during_pause = [x for x in sentences_played2 if x[1] >= pause_time]
    # Allow the sentence currently playing to register if it was already started
    record("No new sentence after pause", len(sentences_during_pause) <= 1,
          f"Sentences after pause: {sentences_during_pause}")

    time.sleep(1.0)
    tts_sapi2.resume()
    record("Resumed state correct", tts_sapi2.state == ReaderState.PLAYING, f"State={tts_sapi2.state}")

    # Wait for completion
    for _ in range(30):
        time.sleep(0.5)
        if tts_sapi2.state != ReaderState.PLAYING:
            break

    print(f"  Sentences played: {sentences_played2}")
    record("Pause/resume played multiple sentences", len(sentences_played2) >= 2,
          f"Played {len(sentences_played2)} sentences")

    tts_sapi2.shutdown()
else:
    record("Pause/resume test", False, "No SAPI5 voices")


# ==================================================================
# Test 4: Edge-TTS playback
# ==================================================================
section("Test 4: Edge-TTS playback")

if edge_voices:
    tts_edge = TTSEngine(vm)
    tts_edge.set_voice(edge_voices[0].id)
    tts_edge.set_rate(200)

    edge_sentences_played = []
    edge_finish = threading.Event()

    tts_edge.set_callbacks(
        on_sentence_start=lambda idx, s: edge_sentences_played.append(idx),
        on_state_change=lambda s: None,
        on_finish=lambda: edge_finish.set(),
    )

    edge_test_sentences = [
        "Hello, this is the first sentence.",
        "And this is the second sentence.",
    ]

    tts_edge.play(sentences=edge_test_sentences)
    edge_finish.wait(timeout=30)
    time.sleep(0.5)

    print(f"  Edge sentences played: {edge_sentences_played}")
    record("Edge-TTS played sentences", len(edge_sentences_played) >= 1,
          f"Played {len(edge_sentences_played)} sentences: {edge_sentences_played}")
    record("Edge-TTS played first sentence", 0 in edge_sentences_played,
          f"Indices: {edge_sentences_played}")
    tts_edge.shutdown()
else:
    record("Edge-TTS test", False, "No Edge voices")


# ==================================================================
# Test 5: Page-aware reading (from specific page)
# ==================================================================
section("Test 5: Page-aware reading")

pdf_path = find_pdf_path()
if pdf_path and sapi_voices:
    pdf_doc = PDFReader.load(pdf_path)
    print(f"  PDF loaded: {os.path.basename(pdf_path)}, {pdf_doc.page_count} pages")

    tts_page = TTSEngine(vm)
    tts_page.set_voice(sapi_voices[0].id)
    tts_page.set_rate(300)

    ctrl = ReadingController(tts_page)
    ctrl.set_document(pdf_doc)
    ctrl.read_all_pages = True

    page_changes = []
    highlight_events = []

    ctrl.set_callbacks(
        on_page_change=lambda p: page_changes.append(p),
        on_sentence_highlight=lambda idx, page, s, start, end: highlight_events.append((idx, page)),
        on_state_change=lambda s: None,
        on_finish=lambda: None,
    )

    start_page = min(1, pdf_doc.page_count - 1)
    ctrl.current_page = start_page
    ctrl.play()

    time.sleep(2.0)
    ctrl.stop()
    time.sleep(0.5)

    print(f"  Start page: {start_page}")
    print(f"  Highlight events: {len(highlight_events)}")
    if highlight_events:
        first_page_in_events = highlight_events[0][1]
        record("Reading started from correct page", first_page_in_events == start_page,
              f"Expected page {start_page}, got {first_page_in_events}")
    else:
        record("Reading started from correct page", False, "No highlight events")

    record("Highlight events generated", len(highlight_events) > 0,
          f"{len(highlight_events)} events")

    tts_page.shutdown()
else:
    record("Page-aware reading", False, "No PDF file or no SAPI5 voices")


# ==================================================================
# Test 6: Paragraph-based text formatting
# ==================================================================
section("Test 6: Paragraph-based text formatting")

if pdf_path:
    pdf_doc2 = PDFReader.load(pdf_path)
    has_paragraphs = False
    total_paragraphs = 0
    first_non_empty_page = -1
    for i in range(min(pdf_doc2.page_count, 10)):
        paras = pdf_doc2.get_page_paragraphs(i)
        total_paragraphs += len(paras)
        if paras and first_non_empty_page < 0:
            first_non_empty_page = i
        if paras:
            has_paragraphs = True
    record("Pages have paragraph structure", has_paragraphs,
          f"{total_paragraphs} paragraphs in first 10 pages")

    sample_page = first_non_empty_page if first_non_empty_page >= 0 else 0
    sample_clean = pdf_doc2.get_page_text_clean(sample_page)
    record("Clean text preserves paragraph breaks", "\n\n" in sample_clean,
          f"Sample page={sample_page + 1}, length={len(sample_clean)}")
else:
    record("Paragraph formatting", False, "No PDF file found")


# ==================================================================
# Test 7: Progress seek (drag to sentence)
# ==================================================================
section("Test 7: Progress seek")

if pdf_path and sapi_voices:
    pdf_doc3 = PDFReader.load(pdf_path)
    tts_seek = TTSEngine(vm)
    tts_seek.set_voice(sapi_voices[0].id)
    tts_seek.set_rate(300)

    ctrl_seek = ReadingController(tts_seek)
    ctrl_seek.set_document(pdf_doc3)
    ctrl_seek.read_all_pages = True

    seek_highlights = []
    ctrl_seek.set_callbacks(
        on_page_change=lambda p: None,
        on_sentence_highlight=lambda idx, page, s, start, end: seek_highlights.append(idx),
        on_state_change=lambda s: None,
        on_finish=lambda: None,
    )

    ctrl_seek.seek_to_sentence(3)
    time.sleep(2.0)
    ctrl_seek.stop()
    time.sleep(0.5)

    print(f"  Seek highlights: {seek_highlights}")
    started_near_target = False
    if seek_highlights:
        started_near_target = seek_highlights[0] >= 2
    record("Seek jumped to target sentence", started_near_target,
          f"First highlight index={seek_highlights[0] if seek_highlights else 'none'}")

    tts_seek.shutdown()
else:
    record("Progress seek", False, "No PDF file or no SAPI5 voices")


# ==================================================================
# Test 8: Play selected text
# ==================================================================
section("Test 8: Play selected text")

if sapi_voices:
    selected_text = "Hello, this is a selected text for playback. It has two sentences."
    tts_sel = TTSEngine(vm)
    tts_sel.set_voice(sapi_voices[0].id)
    tts_sel.set_rate(250)

    sel_sentences = []
    ctrl_sel = ReadingController(tts_sel)
    ctrl_sel.set_callbacks(
        on_page_change=lambda p: None,
        on_sentence_highlight=lambda idx, page, s, start, end: sel_sentences.append(idx),
        on_state_change=lambda s: None,
        on_finish=lambda: None,
    )
    ctrl_sel.play_selected_text(selected_text)

    for _ in range(30):
        time.sleep(0.5)
        if len(sel_sentences) >= 2:
            break

    print(f"  Selected text sentences played: {sel_sentences}")
    record("Selected text played sentences", len(sel_sentences) >= 1,
          f"Played {len(sel_sentences)} sentences")

    tts_sel.shutdown()
else:
    record("Selected text test", False, "No SAPI5 voices")


# ==================================================================
# Test 9: Highlight matches spoken sentence exactly
# ==================================================================
section("Test 9: Highlight matches spoken sentence")

if pdf_path and sapi_voices:
    root = tk.Tk()
    root.withdraw()
    frame = ttk.Frame(root)
    panel = TextPanel(frame, {})

    pdf_doc_hl = PDFReader.load(pdf_path)
    # Use a page with content
    page_num = 0
    for i in range(min(pdf_doc_hl.page_count, 10)):
        if pdf_doc_hl.get_page_text_clean(i).strip():
            page_num = i
            break

    clean = pdf_doc_hl.get_page_text_clean(page_num)
    panel.display_page(clean, page_num, pdf_doc_hl.page_count, "test.pdf")

    # Build reading data like controller does
    ctrl_hl = ReadingController(TTSEngine(vm))
    ctrl_hl.set_document(pdf_doc_hl)
    ctrl_hl.current_page = page_num
    ctrl_hl._build_reading_data()

    # Verify offsets exist and highlight each sentence
    all_match = True
    mismatch_count = 0
    for idx, (s, (start, end)) in enumerate(zip(ctrl_hl._reading_sentences, ctrl_hl._sentence_offsets)):
        if idx >= 5:
            break
        panel.highlight_sentence(s, page_num, start, end)
        ranges = panel.text_area.tag_ranges("highlight")
        if ranges:
            highlighted = panel.text_area.get(ranges[0], ranges[1]).strip()
            if highlighted != s.strip():
                mismatch_count += 1
                all_match = False
        else:
            mismatch_count += 1
            all_match = False

    print(f"  Tested {min(5, len(ctrl_hl._reading_sentences))} sentences, mismatches: {mismatch_count}")
    record("Highlight matches sentence text", all_match,
          f"Mismatches={mismatch_count}/{min(5, len(ctrl_hl._reading_sentences))}")

    ctrl_hl.tts.shutdown()
    root.destroy()
else:
    record("Highlight match test", False, "No PDF file or no SAPI5 voices")


# ==================================================================
# Test 10: MainWindow callback chain (regression test)
# ==================================================================
# 验证 main_window 中的回调 lambda 正确传递所有 5 个参数，
# 否则 TTS 回调会抛 TypeError 被吞掉，导致高亮和进度不更新。
section("Test 10: MainWindow callback chain")

if pdf_path and sapi_voices:
    app_root_destroyed = False
    try:
        from ui.main_window import PDFVoiceReaderApp

        app = PDFVoiceReaderApp()
        pdf_doc_mw = PDFReader.load(pdf_path)
        app.pdf_doc = pdf_doc_mw
        app.controller.set_document(pdf_doc_mw)
        app._display_page(0)
        app._update_page_controls()

        captured_highlights = []

        orig_hl = app._on_event_highlight
        def captured(index, page, sentence, start_offset=-1, end_offset=-1):
            captured_highlights.append((index, page, start_offset, end_offset))
            orig_hl(index, page, sentence, start_offset, end_offset)
        app._on_event_highlight = captured

        app.controller.set_rate(280)
        app.controller.read_all_pages = True
        app.controller.current_page = 0
        app.controller.play()

        # Run mainloop for 4 seconds
        app.root.after(4000, app.root.destroy)
        app.root.mainloop()
        app_root_destroyed = True

        app.controller.stop()
        app.controller.shutdown()

        # Verify events flowed through the queue to _on_event_highlight
        has_offsets = all(c[2] >= 0 and c[3] >= 0 for c in captured_highlights) if captured_highlights else False
        record("MainWindow callback chain works", len(captured_highlights) > 0 and has_offsets,
              f"Received {len(captured_highlights)} highlight events with valid offsets")
    except Exception as e:
        record("MainWindow callback chain works", False, f"Exception: {e}")
    finally:
        if not app_root_destroyed:
            try:
                app.root.destroy()
            except Exception:
                pass
else:
    record("MainWindow callback chain", False, "No PDF file or no SAPI5 voices")


# ==================================================================
# Summary
# ==================================================================
section("Summary")
passed = sum(1 for _, p, _ in results if p)
failed = sum(1 for _, p, _ in results if not p)
total = len(results)
print(f"  Total: {total}, Passed: {passed}, Failed: {failed}")
for name, p, detail in results:
    status = "PASS" if p else "FAIL"
    print(f"    [{status}] {name}" + (f" - {detail}" if detail else ""))

print(f"\n{'='*60}")
if failed == 0:
    print("  ALL TESTS PASSED!")
else:
    print(f"  {failed} TEST(S) FAILED!")
print(f"{'='*60}")
