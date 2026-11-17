"""
edge_tts_compat 回归测试（离线，不访问网络）。

覆盖 2026-10 微软 TTS 端点迁移（v1 → v2）的修复逻辑：
1. 端点常量与补丁生效；
2. DRM.parse_rfc2616_date 的 locale 无关解析；
3. synthesize_to_file 的主机回退与粘性偏好（mock Communicate）；
4. tts_engine._generate_edge_audio 向兼容层的参数转发。

运行：python test_edge_tts_compat.py
依赖：venv 内已安装 edge-tts（本项目的运行时依赖）
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import edge_tts
import edge_tts.communicate as _communicate

import edge_tts_compat as compat
from edge_tts.constants import WSS_URL as LEGACY_WSS_URL
from edge_tts.drm import DRM

RESULTS = []


def check(name: str, ok: bool, detail: str = ""):
    RESULTS.append((name, ok))
    mark = "OK  " if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  | {detail}" if detail else ""))


# ---------------------------------------------------------------
# 1. 端点常量与 import 时补丁
# ---------------------------------------------------------------

check("v2 端点常量指向 api.msedgeservices.com",
      compat.WSS_URL_V2.startswith("wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"))
check("v2 端点使用 Ocp-Apim-Subscription-Key 传令牌",
      "Ocp-Apim-Subscription-Key=6A5AA1D4EAFF4E9FB37E23D68491D6F4" in compat.WSS_URL_V2)
check("v1 回退端点与 edge-tts 默认值一致", compat.WSS_URL_V1 == LEGACY_WSS_URL)
check("import 后默认端点已切到 v2", compat.current_wss_url() == compat.WSS_URL_V2)

# ---------------------------------------------------------------
# 2. Date 头解析（locale 无关）
# ---------------------------------------------------------------

ts = DRM.parse_rfc2616_date("Mon, 12 Oct 2026 13:46:57 GMT")
check("标准 GMT Date 解析正确", ts == 1791812817.0, str(ts))
check("带数字时区 +0000 解析正确",
      DRM.parse_rfc2616_date("Mon, 12 Oct 2026 13:46:57 +0000") == 1791812817.0)
check("无时区按 UTC 处理",
      DRM.parse_rfc2616_date("Mon, 12 Oct 2026 13:46:57") == 1791812817.0)
check("非法输入返回 None", DRM.parse_rfc2616_date("not a date") is None)
check("空串返回 None", DRM.parse_rfc2616_date("") is None)

# ---------------------------------------------------------------
# 3. synthesize_to_file 主机回退与粘性偏好（mock Communicate）
# ---------------------------------------------------------------

_ORIG_COMMUNICATE = edge_tts.Communicate


class _MockCommunicate:
    """按当前 WSS_URL 决定成败的假 Communicate，记录调用参数。"""

    #: 这些主机片段命中时 save() 抛错（模拟 403）
    fail_host_fragments: tuple = ()
    calls: list = []

    def __init__(self, text, voice, rate=None, volume=None, proxy=None):
        self.text = text
        self.voice = voice
        self.rate = rate
        self.volume = volume
        self.proxy = proxy

    async def save(self, path):
        type(self).calls.append({
            "host": _communicate.WSS_URL,
            "text": self.text,
            "voice": self.voice,
            "rate": self.rate,
            "volume": self.volume,
        })
        if any(frag in _communicate.WSS_URL for frag in type(self).fail_host_fragments):
            raise RuntimeError("mock 403 handshake")
        with open(path, "wb") as f:
            f.write(b"mock-mp3-bytes")


def _run_synth(text="测试文本。", voice="zh-CN-XiaoxiaoNeural", rate=10, volume_pct=-20):
    tmp = os.path.join(tempfile.gettempdir(), "test_compat_mock.mp3")
    if os.path.exists(tmp):
        os.remove(tmp)
    compat.synthesize_to_file(
        text=text, voice=voice, output_path=tmp, rate=rate, volume_pct=volume_pct,
    )
    return tmp


def _reset_state():
    compat._preferred_host_idx = 0
    _MockCommunicate.calls = []
    _MockCommunicate.fail_host_fragments = ()


# --- 场景 A：v2 直接成功 ---
_reset_state()
edge_tts.Communicate = _MockCommunicate
try:
    path = _run_synth()
    ok_file = os.path.exists(path) and os.path.getsize(path) > 0
    check("场景A: v2 成功时写出文件", ok_file)
    check("场景A: 只调用一次（无多余重试）", len(_MockCommunicate.calls) == 1)
    check("场景A: 使用的正是 v2 端点",
          _MockCommunicate.calls[0]["host"] == compat.WSS_URL_V2)
    check("场景A: 参数正确转发",
          _MockCommunicate.calls[0]["rate"] == "+10%"
          and _MockCommunicate.calls[0]["volume"] == "-20%"
          and _MockCommunicate.calls[0]["text"] == "测试文本。")
    check("场景A: 成功后粘性主机为 v2", compat._preferred_host_idx == 0)
finally:
    edge_tts.Communicate = _ORIG_COMMUNICATE

# --- 场景 B：v2 失败、v1 成功 → 回退并粘性记住 v1 ---
_reset_state()
_MockCommunicate.fail_host_fragments = ("api.msedgeservices.com",)
edge_tts.Communicate = _MockCommunicate
try:
    path = _run_synth()
    check("场景B: 回退到 v1 后写出文件", os.path.exists(path) and os.path.getsize(path) > 0)
    check("场景B: 共尝试 2 个主机", len(_MockCommunicate.calls) == 2)
    check("场景B: 首次尝试 v2、第二次 v1",
          _MockCommunicate.calls[0]["host"] == compat.WSS_URL_V2
          and _MockCommunicate.calls[1]["host"] == compat.WSS_URL_V1)
    check("场景B: 粘性主机切换为 v1", compat._preferred_host_idx == 1)
    check("场景B: 当前端点重置为 v1", compat.current_wss_url() == compat.WSS_URL_V1)

    # --- 场景 C：粘性生效后，v1 仍失败但 v2 恢复 → 自动回 v2 ---
    _MockCommunicate.calls = []
    _MockCommunicate.fail_host_fragments = ("speech.platform.bing.com",)
    path = _run_synth()
    check("场景C: v1 失败时回退 v2 成功", os.path.exists(path))
    check("场景C: 优先尝试粘性的 v1", _MockCommunicate.calls[0]["host"] == compat.WSS_URL_V1)
    check("场景C: 成功后粘性切回 v2", compat._preferred_host_idx == 0)
finally:
    edge_tts.Communicate = _ORIG_COMMUNICATE

# --- 场景 D：两个主机都失败 → 抛出最后一个异常 ---
_reset_state()
_MockCommunicate.fail_host_fragments = ("api.msedgeservices.com", "speech.platform.bing.com")
edge_tts.Communicate = _MockCommunicate
try:
    raised = None
    try:
        _run_synth()
    except RuntimeError as e:
        raised = e
    check("场景D: 双主机失败时抛出异常", raised is not None and "403" in str(raised))
    check("场景D: 两个主机都尝试过", len(_MockCommunicate.calls) == 2)
finally:
    edge_tts.Communicate = _ORIG_COMMUNICATE
    compat._preferred_host_idx = 0
    compat._communicate.WSS_URL = compat.WSS_URL_V2

# ---------------------------------------------------------------
# 4. tts_engine._generate_edge_audio 参数转发
# ---------------------------------------------------------------

import tts_engine
from tts_engine import TTSEngine

_ORIG_SYNTH = compat.synthesize_to_file
captured = {}
compat.synthesize_to_file = lambda **kwargs: captured.update(kwargs)
try:
    # 该方法不访问 self，可直接用 None 调用（离线、不建引擎）
    TTSEngine._generate_edge_audio(
        None, "你好²", "zh-CN-YunxiNeural", "/tmp/x.mp3", -15, 30,
    )
    check("tts_engine: _generate_edge_audio 转发 text/voice/output",
          captured.get("text") == "你好²"
          and captured.get("voice") == "zh-CN-YunxiNeural"
          and captured.get("output_path") == "/tmp/x.mp3")
    check("tts_engine: _generate_edge_audio 转发 rate/volume_pct",
          captured.get("rate") == -15 and captured.get("volume_pct") == 30)
finally:
    compat.synthesize_to_file = _ORIG_SYNTH

check("tts_engine: EDGE_FATAL_ERROR_MESSAGE 常量存在",
      isinstance(tts_engine.EDGE_FATAL_ERROR_MESSAGE, str)
      and "已停止朗读" in tts_engine.EDGE_FATAL_ERROR_MESSAGE)

# ---------------------------------------------------------------
failed = [name for name, ok in RESULTS if not ok]
print(f"\n共 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)} 项，失败 {len(failed)} 项")
if failed:
    for name in failed:
        print(" -", name)
    sys.exit(1)
print("全部通过")
