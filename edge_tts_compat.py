"""
edge-tts 兼容补丁模块（v1 → v2 端点迁移）

背景（2026-10）：
    Microsoft 正在将 Edge Read Aloud 的 TTS 后端从 v1 端点
    （speech.platform.bing.com）迁移到 v2 端点
    （api.msedgeservices.com/tts/cognitiveservices/websocket/v1），
    并按区域灰度强制。v1 端点已在多个区域对旧版 WebSocket 握手
    返回 403（WSServerHandshakeError），edge-tts 7.2.8 及更早
    版本仍写死使用 v1，因此在线语音全部不可用。

    实测结论（2026-10-12，本机）：
    - v1 端点：WebSocket 握手 403，任何 Sec-MS-GEC-Version 均被拒；
    - v2 端点：接受旧版 TrustedClientToken（以 Ocp-Apim-Subscription-Key
      查询参数传递）与旧版 Sec-MS-GEC 令牌/版本号，握手 101 并正常合成，
      输出音频与旧版一致（MP3 / 24kHz / mono / 48kbps）。

本模块职责（在 import 时生效，无需修改第三方包源码）：
    1. 将 edge_tts.communicate.WSS_URL 切换为 v2 端点；
    2. 提供 synthesize_to_file()：合成时按「v2 优先、v1 回退」的
       顺序尝试，成功的主机被粘性记住，后续调用优先使用；
    3. 修复 DRM.parse_rfc2616_date 的 locale 依赖——原实现用
       strptime("%a, %d %b %Y ...") 解析服务器 Date 头，在非英文
       locale（如中文 Windows + Qt 设置过 locale）下会解析失败，
       把 403 重试路径变成 SkewAdjustmentError。改用
       email.utils.parsedate_to_datetime，与 locale 无关。

兼容性：仅依赖 edge_tts 的公开接口（Communicate / DRM / constants），
上游若发布修复版本，本模块可安全保留（补丁值与新版本一致）或直接移除。
"""

import asyncio
import email.utils
import threading
from datetime import timezone
from typing import Optional

import edge_tts
import edge_tts.communicate as _communicate
from edge_tts.constants import WSS_URL as _WSS_URL_V1, TRUSTED_CLIENT_TOKEN

__all__ = ["synthesize_to_file", "current_wss_url", "WSS_URL_V2", "WSS_URL_V1"]

# v2 端点（Microsoft Edge 155 起实际使用的合成端点）。
# 与 v1 的差异：主机换成 api.msedgeservices.com，路径换成
# /tts/cognitiveservices/websocket/v1，客户端令牌参数名从
# TrustedClientToken 换成 Ocp-Apim-Subscription-Key（值不变），
# Sec-MS-GEC 令牌算法与 Sec-MS-GEC-Version 仍可沿用旧版。
WSS_URL_V2 = (
    "wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1"
    f"?Ocp-Apim-Subscription-Key={TRUSTED_CLIENT_TOKEN}"
)

# v1 端点（edge-tts 7.2.8 及之前版本的默认值，保留作回退）
WSS_URL_V1 = _WSS_URL_V1

# 主机尝试顺序的初始偏好：0 = v2，1 = v1
_HOSTS = (WSS_URL_V2, WSS_URL_V1)

_host_lock = threading.Lock()
_preferred_host_idx = 0


def _parse_rfc2616_date_any_locale(date_str: str) -> Optional[float]:
    """按 RFC 2616 解析 HTTP Date 头，返回 Unix 时间戳（与 locale 无关）。

    替代 DRM.parse_rfc2616_date：原实现依赖 strptime 的 %a/%b，
    在非英文 locale 下解析 "Mon, 12 Oct 2026 ..." 会返回 None，
    导致 403 的时钟纠偏重试路径报 SkewAdjustmentError。
    """
    try:
        parsed = email.utils.parsedate_to_datetime(date_str)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        # HTTP Date 无时区时按 UTC 处理（RFC 7231 规定）
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _apply_patches() -> None:
    """在 import 时应用运行时补丁（幂等）。"""
    # 1) 默认合成端点切到 v2
    _communicate.WSS_URL = WSS_URL_V2
    # 2) Date 头解析改为 locale 无关实现
    _communicate.DRM.parse_rfc2616_date = staticmethod(_parse_rfc2616_date_any_locale)


def current_wss_url() -> str:
    """返回当前生效的合成端点（诊断用）。"""
    return _communicate.WSS_URL


def synthesize_to_file(
    text: str,
    voice: str,
    output_path: str,
    rate: int = 0,
    volume_pct: int = 0,
    proxy: Optional[str] = None,
) -> None:
    """
    用 Edge-TTS 合成文本并写入 output_path（MP3）。

    主机策略：
        - 首次调用优先使用 v2 端点（迁移目标，当前全球可用）；
        - 若当前主机失败（403 握手被拒 / 连接错误 / 无音频），
          自动回退到另一个主机重试；
        - 合成成功的主机被「粘性」记住，后续调用优先使用，
          避免每次调用都先撞一次旧主机。

    线程安全性：
        WSS_URL 是 edge_tts.communicate 的模块级全局，Communicate
        在建立连接的瞬间读取一次。多线程（播放线程 + 预生成线程）
        并发切换该全局最多导致不同线程短暂使用不同主机，不会串话；
        成功主机的记录在锁内更新。

    Raises:
        最后一个主机的异常（两个主机均失败时）。
    """
    global _preferred_host_idx

    with _host_lock:
        order = (_preferred_host_idx, 1 - _preferred_host_idx)

    last_exc: Optional[BaseException] = None
    for idx in order:
        with _host_lock:
            _communicate.WSS_URL = _HOSTS[idx]
        try:
            communicate = edge_tts.Communicate(
                text=text,
                voice=voice,
                rate=f"{rate:+d}%",
                volume=f"{volume_pct:+d}%",
                proxy=proxy,
            )
            asyncio.run(communicate.save(output_path))
        except Exception as exc:  # noqa: BLE001 - 需要回退到另一主机，最后统一抛出
            last_exc = exc
            continue
        # 成功：粘性记住该主机，恢复全局为成功主机
        with _host_lock:
            _preferred_host_idx = idx
            _communicate.WSS_URL = _HOSTS[idx]
        return

    assert last_exc is not None  # order 必然非空，防御性断言
    raise last_exc


_apply_patches()
