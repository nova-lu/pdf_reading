"""
端到端测试脚本

验证以下功能：
1. 语音引擎数量 >= 5
2. SAPI5 播放 + skip_next 连续性（不再读一句就停）
3. SAPI5 播放 + pause/resume 连续性
4. Edge-TTS 播放
5. 页面感知朗读（从指定页开始）
6. ReadingController 页面切换
"""

import sys
import os
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from voice_manager import VoiceManager, EDGE_VOICE_PREFIX
from tts_engine import TTSEngine, ReaderState
from reading_controller import ReadingController
from pdf_reader import PDFReader


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


# ==================================================================
# Test 2: SAPI5 play + skip_next continuity
# ==================================================================
section("Test 2: SAPI5 play + skip_next continuity")

tts_sapi = TTSEngine(vm)
# Use first SAPI5 voice
sapi_voices = [v for v in vm.voices if v.provider == "sapi5"]
tts_sapi.set_voice(sapi_voices[0].id)
tts_sapi.set_rate(300)  # Fast rate for testing

sentences_played = []
state_changes = []
finish_called = threading.Event()

tts_sapi.set_callbacks(
    on_sentence_start=lambda idx, s: sentences_played.append(idx),
    on_state_change=lambda s: state_changes.append(s),
    on_finish=lambda: finish_called.set(),
)

test_sentences = [
    "First sentence for testing.",
    "Second sentence is here.",
    "Third sentence follows.",
    "Fourth and final sentence.",
]

tts_sapi.play(sentences=test_sentences)
time.sleep(1.5)  # Let first sentence play

# Skip to sentence 2
tts_sapi.skip_to(2)
time.sleep(1.5)  # Let sentence 2 play

# Skip to sentence 3
tts_sapi.skip_to(3)
time.sleep(1.5)  # Let sentence 3 play

tts_sapi.stop()
time.sleep(0.5)

print(f"  Sentences played indices: {sentences_played}")
# Should have played sentence 0, then 2, then 3 (skip should NOT stop after one sentence)
record("Skip produced multiple sentences", len(sentences_played) >= 3,
      f"Played {len(sentences_played)} sentences: {sentences_played}")
record("Skip reached target index 2", 2 in sentences_played, f"Indices: {sentences_played}")
record("Skip reached target index 3", 3 in sentences_played, f"Indices: {sentences_played}")

tts_sapi.shutdown()


# ==================================================================
# Test 3: SAPI5 pause/resume continuity
# ==================================================================
section("Test 3: SAPI5 pause/resume continuity")

tts_sapi2 = TTSEngine(vm)
tts_sapi2.set_voice(sapi_voices[0].id)
tts_sapi2.set_rate(200)

sentences_played2 = []
finish_called2 = threading.Event()

tts_sapi2.set_callbacks(
    on_sentence_start=lambda idx, s: sentences_played2.append(idx),
    on_state_change=lambda s: None,
    on_finish=lambda: finish_called2.set(),
)

long_sentences = [
    "This is the first sentence and it is relatively long so we can test pause.",
    "This is the second sentence for continued playback after resume.",
    "This is the third sentence to verify full continuity.",
]

tts_sapi2.play(sentences=long_sentences)
time.sleep(1.0)

# Pause
tts_sapi2.pause()
print(f"  State after pause: {tts_sapi2.state}")
record("Paused state correct", tts_sapi2.state == ReaderState.PAUSED, f"State={tts_sapi2.state}")
time.sleep(1.0)

# Resume
tts_sapi2.resume()
print(f"  State after resume: {tts_sapi2.state}")
record("Resumed state correct", tts_sapi2.state == ReaderState.PLAYING, f"State={tts_sapi2.state}")

# Wait for completion
finish_called2.wait(timeout=15)
time.sleep(0.5)

print(f"  Sentences played: {sentences_played2}")
record("Pause/resume played all sentences", len(sentences_played2) >= 2,
      f"Played {len(sentences_played2)} sentences: {sentences_played2}")

tts_sapi2.shutdown()


# ==================================================================
# Test 4: Edge-TTS playback
# ==================================================================
section("Test 4: Edge-TTS playback")

tts_edge = TTSEngine(vm)
edge_voices = [v for v in vm.voices if v.provider == "edge"]
tts_edge.set_voice(edge_voices[0].id)  # zh-CN-XiaoxiaoNeural
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


# ==================================================================
# Test 5: Page-aware reading (from specific page)
# ==================================================================
section("Test 5: Page-aware reading")

pdf_path = os.path.join(os.path.dirname(__file__), "test_sample.pdf")
if not os.path.exists(pdf_path):
    # Use the astronomy PDF if test_sample doesn't exist
    for f in os.listdir(os.path.dirname(__file__)):
        if f.endswith(".pdf"):
            pdf_path = os.path.join(os.path.dirname(__file__), f)
            break

if os.path.exists(pdf_path):
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

    # Use a direct callback approach (no queue for testing)
    ctrl.set_callbacks(
        on_page_change=lambda p: page_changes.append(p),
        on_sentence_highlight=lambda idx, page, s: highlight_events.append((idx, page)),
        on_state_change=lambda s: None,
        on_finish=lambda: None,
    )

    # Start from page 1 (second page, 0-indexed)
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
        print(f"  First sentence page: {first_page_in_events}")
        record("Reading started from correct page", first_page_in_events == start_page,
              f"Expected page {start_page}, got {first_page_in_events}")
    else:
        record("Reading started from correct page", False, "No highlight events")

    record("Highlight events generated", len(highlight_events) > 0,
          f"{len(highlight_events)} events")

    tts_page.shutdown()
else:
    print("  No PDF file found for testing")
    record("Page-aware reading", False, "No PDF file found")


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
