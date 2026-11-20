"""
本地神经语音引擎（sherpa-onnx）

能力：
1. 模型目录扫描：`%APPDATA%\\PDFVoiceReader\\models\\<id>\\manifest.json`
2. 模型下载：从 sherpa-onnx 官方 release 拉取 tar.bz2，支持断点续传、
   失败重试、安全解压，并在解压后自动生成 manifest.json
3. 语音合成：按 manifest 构建 OfflineTts，输出 WAV（24kHz 等按模型采样率）

依赖说明：
- 需要 `sherpa-onnx`（可选依赖）。版本必须 < 2.0：2.x 起废弃了
  `data_dir`（espeak-ng 音素数据），Piper 系列模型会无法正确发音。
- 模型全部本地推理，不联网、不上传文本。

对外接口：
- is_available() -> bool
- models_dir() -> str
- catalog() -> list[CatalogEntry]
- discover_models() -> list[LocalModel]
- download_and_install(spec_id, progress_cb, cancel_event) -> LocalModel
- remove_model(model_id) -> bool
- synthesize(model_id, text, out_wav, speed=1.0) -> None
"""

import bz2
import json
import os
import shutil
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from typing import Callable, Optional

__all__ = [
    "is_available", "models_dir", "catalog", "discover_models",
    "download_and_install", "remove_model", "synthesize",
    "LocalModel", "CatalogEntry",
]

# sherpa-onnx 官方模型发布根地址（tar.bz2 打包，含 tokens / lexicon / espeak 数据）
_RELEASE_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models"


@dataclass(frozen=True)
class CatalogEntry:
    """可下载模型条目。"""
    id: str
    name: str
    language: str
    size_mb: int
    url: str
    note: str = ""


# 精选模型目录：覆盖中文 / 英文 / 中英混读，均提供 int8 量化的小体积版本
# （int8 体积约为 fp32 的 1/3，CPU 推理更快，音质损失很小）
_CATALOG = [
    CatalogEntry(
        id="zh-xiaoya",
        name="小雅 中文女声（int8，推荐）",
        language="zh-CN",
        size_mb=14,
        url=f"{_RELEASE_BASE}/vits-piper-zh_CN-xiao_ya-medium-int8.tar.bz2",
        note="纯中文书籍，体积小、加载快",
    ),
    CatalogEntry(
        id="zh-chaowen",
        name="超文 中文男声（int8）",
        language="zh-CN",
        size_mb=14,
        url=f"{_RELEASE_BASE}/vits-piper-zh_CN-chaowen-medium-int8.tar.bz2",
        note="纯中文书籍，男声",
    ),
    CatalogEntry(
        id="en-amy",
        name="Amy 英文女声（int8，推荐）",
        language="en-US",
        size_mb=21,
        url=f"{_RELEASE_BASE}/vits-piper-en_US-amy-medium-int8.tar.bz2",
        note="纯英文书籍，女声",
    ),
    CatalogEntry(
        id="en-ryan",
        name="Ryan 英文男声（int8）",
        language="en-US",
        size_mb=21,
        url=f"{_RELEASE_BASE}/vits-piper-en_US-ryan-medium-int8.tar.bz2",
        note="纯英文书籍，男声",
    ),
    CatalogEntry(
        id="melo-zh-en",
        name="Melo 中英混读（女声，高质量）",
        language="zh-CN + en-US",
        size_mb=159,
        url=f"{_RELEASE_BASE}/vits-melo-tts-zh_en.tar.bz2",
        note="中英文混排朗读最自然，体积较大，适合中英夹杂的技术书",
    ),
]


@dataclass
class LocalModel:
    """已安装的本地模型。"""
    id: str
    name: str
    language: str
    kind: str                 # vits / matcha
    path: str                 # 模型目录
    model_file: str           # 声学模型 onnx
    tokens: str
    lexicon: str = ""
    dict_dir: str = ""
    data_dir: str = ""
    vocoder: str = ""
    sample_rate: int = 24000
    num_speakers: int = 1


# ----------------------------------------------------------------------
# 基础路径与可用性
# ----------------------------------------------------------------------

def is_available() -> bool:
    """sherpa-onnx 是否可用（未安装时为 False，应用其余功能不受影响）。"""
    try:
        import importlib.util
        return importlib.util.find_spec("sherpa_onnx") is not None
    except (ImportError, ValueError):
        return False


def _app_data_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
    return os.path.join(base, "PDFVoiceReader")


def models_dir() -> str:
    """模型根目录（不存在则创建）。"""
    path = os.path.join(_app_data_dir(), "models")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def catalog() -> list[CatalogEntry]:
    return list(_CATALOG)


# ----------------------------------------------------------------------
# 模型发现
# ----------------------------------------------------------------------

def _load_manifest(model_dir: str) -> Optional[dict]:
    path = os.path.join(model_dir, "manifest.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def discover_models() -> list[LocalModel]:
    """扫描模型目录，返回已安装模型（按目录名排序）。"""
    root = models_dir()
    models: list[LocalModel] = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return models

    for name in entries:
        mdir = os.path.join(root, name)
        if not os.path.isdir(mdir):
            continue
        manifest = _load_manifest(mdir)
        if not manifest:
            continue
        model_file = os.path.join(mdir, manifest.get("model", ""))
        tokens = os.path.join(mdir, manifest.get("tokens", ""))
        if not (os.path.isfile(model_file) and os.path.isfile(tokens)):
            continue
        models.append(LocalModel(
            id=manifest.get("id") or name,
            name=manifest.get("name") or name,
            language=manifest.get("language", ""),
            kind=manifest.get("kind", "vits"),
            path=mdir,
            model_file=model_file,
            tokens=tokens,
            lexicon=_abs_if(mdir, manifest.get("lexicon")),
            dict_dir=_abs_if(mdir, manifest.get("dict_dir")),
            data_dir=_abs_if(mdir, manifest.get("data_dir")),
            vocoder=_abs_if(mdir, manifest.get("vocoder")),
            sample_rate=int(manifest.get("sample_rate", 24000)),
            num_speakers=int(manifest.get("num_speakers", 1)),
        ))
    return models


def _abs_if(base: str, rel: Optional[str]) -> str:
    if not rel:
        return ""
    path = os.path.join(base, rel)
    return path if os.path.exists(path) else ""


# ----------------------------------------------------------------------
# 下载 / 解压 / 安装
# ----------------------------------------------------------------------

def _http_download(url: str, dest: str, progress_cb, cancel_event, retries: int = 3) -> None:
    """带断点续传与重试的下载。"""
    for attempt in range(1, retries + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("下载已取消")
        existing = os.path.getsize(dest) if os.path.exists(dest) else 0
        headers = {"User-Agent": "PDFVoiceReader/2.0"}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length") or 0) + existing
                mode = "ab" if resp.status == 206 else "wb"
                if mode == "wb":
                    existing = 0
                with open(dest, mode) as f:
                    downloaded = existing
                    while True:
                        if cancel_event is not None and cancel_event.is_set():
                            raise RuntimeError("下载已取消")
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb:
                            progress_cb(downloaded, total)
            return
        except urllib.error.HTTPError as exc:
            # 416: 断点位置已到文件末尾（本地文件已完整），直接视为完成
            if exc.code == 416:
                return
            if attempt >= retries:
                raise RuntimeError(f"下载失败（HTTP {exc.code}）：{exc}") from exc
            time.sleep(2 * attempt)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            if attempt >= retries:
                raise RuntimeError(f"下载失败（已重试 {retries} 次）：{exc}") from exc
            time.sleep(2 * attempt)


def _safe_extract(archive: str, dest: str) -> str:
    """安全解压 tar.bz2（拒绝路径穿越），返回解压出的顶层目录。"""
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(archive, "r:bz2") as tar:
        members = tar.getmembers()
        for m in members:
            target = os.path.abspath(os.path.join(dest, m.name))
            if not target.startswith(os.path.abspath(dest) + os.sep) and target != os.path.abspath(dest):
                raise RuntimeError(f"压缩包包含非法路径：{m.name}")
        try:
            # Python 3.12+ 提供内置安全过滤器；旧版本回退为普通解压（已手动校验路径）
            tar.extractall(dest, filter="data")
        except TypeError:
            tar.extractall(dest)

    entries = [e for e in os.listdir(dest) if os.path.isdir(os.path.join(dest, e))]
    if len(entries) == 1:
        return os.path.join(dest, entries[0])
    return dest


def _build_manifest(spec: CatalogEntry, model_dir: str) -> dict:
    """根据解压出的实际文件推断 manifest（不同模型包结构略有差异）。"""
    onnx_files = []
    tokens = ""
    lexicon = ""
    dict_dir = ""
    data_dir = ""
    for root, _dirs, files in os.walk(model_dir):
        for fn in files:
            low = fn.lower()
            full = os.path.relpath(os.path.join(root, fn), model_dir)
            if low.endswith(".onnx"):
                onnx_files.append((os.path.getsize(os.path.join(root, fn)), full))
            elif fn == "tokens.txt" and not tokens:
                tokens = full
            elif fn == "lexicon.txt" and not lexicon:
                lexicon = full
        for d in _dirs:
            rel = os.path.relpath(os.path.join(root, d), model_dir)
            if d == "dict" and not dict_dir:
                dict_dir = rel
            if d == "espeak-ng-data" and not data_dir:
                data_dir = rel

    if not onnx_files or not tokens:
        raise RuntimeError("模型包结构无法识别：缺少 onnx 模型或 tokens.txt")

    onnx_files.sort(reverse=True)
    names = [name for _size, name in onnx_files]
    vocoder = ""
    acoustic = onnx_files[0][1]
    kind = "vits"

    # 声码器命名特征：hifigan / vocos / vocoder。命中则视为 Matcha 双模型结构，
    # 声学模型取「除声码器外体积最大的 onnx」。
    for name in names:
        low = os.path.basename(name).lower()
        if "hifigan" in low or "vocos" in low or "vocoder" in low:
            vocoder = name
            break
    if vocoder:
        others = [n for n in names if n != vocoder]
        if others:
            kind = "matcha"
            acoustic = others[0]

    manifest = {
        "id": spec.id,
        "name": spec.name,
        "language": spec.language,
        "kind": kind,
        "model": acoustic,
        "tokens": tokens,
        "lexicon": lexicon,
        "dict_dir": dict_dir,
        "data_dir": data_dir,
        "vocoder": vocoder,
        "sample_rate": 0,   # 加载时由 OfflineTts.sample_rate 补全
        "num_speakers": 1,
    }
    return manifest


def download_and_install(
    spec_id: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> LocalModel:
    """
    下载并安装指定模型。

    Args:
        spec_id: catalog() 中的模型 id
        progress_cb: 进度回调 (downloaded_bytes, total_bytes)，total 可能为 0
        cancel_event: 取消信号

    Returns:
        安装完成的 LocalModel
    """
    spec = next((s for s in _CATALOG if s.id == spec_id), None)
    if spec is None:
        raise ValueError(f"未知模型：{spec_id}")

    root = models_dir()
    downloads = os.path.join(root, "_downloads")
    os.makedirs(downloads, exist_ok=True)
    archive = os.path.join(downloads, f"{spec.id}.tar.bz2")
    staging = os.path.join(downloads, f"{spec.id}_extract")

    _http_download(spec.url, archive, progress_cb, cancel_event)

    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)
    extracted = _safe_extract(archive, staging)

    manifest = _build_manifest(spec, extracted)
    with open(os.path.join(extracted, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    target = os.path.join(root, spec.id)
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    os.replace(extracted, target)

    # 清理临时文件（保留失败时的 archive 以便续传）
    try:
        os.remove(archive)
    except OSError:
        pass
    shutil.rmtree(staging, ignore_errors=True)

    installed = [m for m in discover_models() if m.id == spec.id]
    if not installed:
        raise RuntimeError("模型安装后无法识别，请检查模型包是否完整")
    return installed[0]


def remove_model(model_id: str) -> bool:
    """删除已安装模型（含模型目录）。"""
    target = os.path.join(models_dir(), model_id)
    if not os.path.isdir(target):
        return False
    try:
        shutil.rmtree(target)
        return True
    except OSError:
        return False


# ----------------------------------------------------------------------
# 合成
# ----------------------------------------------------------------------

_tts_cache: dict[str, object] = {}
_tts_lock = threading.Lock()


def _get_tts(model: LocalModel):
    """按模型路径缓存 OfflineTts（加载一次约 1~3 秒，避免每句重建）。"""
    key = model.path
    with _tts_lock:
        cached = _tts_cache.get(key)
    if cached is not None:
        return cached

    import sherpa_onnx

    if model.kind == "matcha":
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                matcha=sherpa_onnx.OfflineTtsMatchaModelConfig(
                    acoustic_model=model.model_file,
                    vocoder=model.vocoder,
                    tokens=model.tokens,
                    lexicon=model.lexicon,
                    data_dir=model.data_dir,
                    dict_dir=model.dict_dir,
                ),
            ),
        )
    else:
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=model.model_file,
                    tokens=model.tokens,
                    lexicon=model.lexicon,
                    data_dir=model.data_dir,
                    dict_dir=model.dict_dir,
                ),
            ),
        )

    tts = sherpa_onnx.OfflineTts(cfg)
    with _tts_lock:
        _tts_cache[key] = tts
    return tts


def synthesize(model_id: str, text: str, out_wav: str, speed: float = 1.0) -> None:
    """
    合成一句话到 WAV 文件。

    Args:
        model_id: 模型 id（LocalModel.id）
        text: 待朗读文本
        out_wav: 输出 wav 路径
        speed: 语速倍率（0.5~2.0），1.0 为原速

    Raises:
        RuntimeError: 模型未安装 / 合成失败
    """
    model = next((m for m in discover_models() if m.id == model_id), None)
    if model is None:
        raise RuntimeError(f"本地模型未安装：{model_id}")

    tts = _get_tts(model)
    speed = max(0.5, min(2.0, float(speed)))
    audio = tts.generate(text, sid=0, speed=speed)
    if audio is None or not len(audio.samples):
        raise RuntimeError("本地语音合成未返回音频")

    with wave.open(out_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(audio.sample_rate)
        # sherpa-onnx 返回 float32 [-1, 1]，转 16-bit PCM
        import array
        pcm = array.array("h", (
            int(max(-1.0, min(1.0, s)) * 32767) for s in audio.samples
        ))
        wf.writeframes(pcm.tobytes())


def model_sample_rate(model_id: str) -> int:
    """返回模型实际采样率（加载后可知；未安装返回 0）。"""
    model = next((m for m in discover_models() if m.id == model_id), None)
    if model is None:
        return 0
    try:
        return int(_get_tts(model).sample_rate)
    except Exception:
        return model.sample_rate


def clear_cache() -> None:
    """释放已加载模型（切换模型/退出时调用）。"""
    with _tts_lock:
        _tts_cache.clear()
