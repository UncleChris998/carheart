# -*- coding: utf-8 -*-
"""统一的语音识别接口：whisper 与 sherpa-onnx 都从这里走。

为什么要有这一层：
  · 实测 faster-whisper small 对中文同音字很差（器灵 -> 次灵 -> 气），
    而 SenseVoice / Paraformer 是专门为中文训练的。
  · 换引擎不该动上层代码。
"""
import os
import wave

# numpy 不是每个环境都有 —— APK 里没打包它（手机上语音走火山云，
# 不需要本地识别）。这里做成可选，别让 import 这一步就把整个程序带崩。
# 之前就是这里抛 ModuleNotFoundError，App 一打开满屏报错。
try:
    import numpy as np
except ImportError:                                       # noqa: BLE001
    np = None

from . import config

_cache = {}


def _read_wav(path):
    with wave.open(path, "rb") as w:
        sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if sw == 2:
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        a = np.frombuffer(raw, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def _resample(a, sr, dst=16000):
    if sr == dst or a.size == 0:
        return a
    n = int(round(a.size * dst / float(sr)))
    if n <= 1:
        return a
    xo = np.linspace(0.0, 1.0, a.size, endpoint=False)
    xn = np.linspace(0.0, 1.0, n, endpoint=False)
    return np.interp(xn, xo, a).astype(np.float32)


# =====================================================================
#  引擎
# =====================================================================
def _engine_whisper(size=None):
    key = ("whisper", size or config.VOICE_MODEL_SIZE)
    if key in _cache:
        return _cache[key]

    from faster_whisper import WhisperModel

    class W:
        name = f"faster-whisper {size or config.VOICE_MODEL_SIZE}"

        def __init__(self):
            self.m = WhisperModel(size or config.VOICE_MODEL_SIZE,
                                  device=config.VOICE_DEVICE,
                                  compute_type=config.VOICE_COMPUTE_TYPE,
                                  download_root=config.VOICE_MODEL_DIR)

        def transcribe(self, audio, sr=16000):
            if sr != 16000:
                audio = _resample(audio, sr, 16000)
            segs, _info = self.m.transcribe(
                audio, language="zh", beam_size=5, best_of=5,
                condition_on_previous_text=False,
                temperature=[0.0, 0.2, 0.4],
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400})
            return "".join(s.text for s in segs).strip()

    _cache[key] = W()
    return _cache[key]


def _engine_sherpa(kind="sensevoice"):
    key = ("sherpa", kind)
    if key in _cache:
        return _cache[key]
    import sherpa_onnx

    d = config.ASR_DIR
    if kind == "sensevoice":
        model = os.path.join(d, "sensevoice.onnx")
        tokens = os.path.join(d, "sensevoice_tokens.txt")
        if not (os.path.exists(model) and os.path.exists(tokens)):
            raise FileNotFoundError("SenseVoice 模型不在，先下载")
        rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=model, tokens=tokens, language="zh", use_itn=True,
            num_threads=config.ASR_THREADS, debug=False)
        name = "sherpa-onnx SenseVoice"
    elif kind == "paraformer":
        model = os.path.join(d, "paraformer.onnx")
        tokens = os.path.join(d, "paraformer_tokens.txt")
        if not (os.path.exists(model) and os.path.exists(tokens)):
            raise FileNotFoundError("Paraformer 模型不在，先下载")
        rec = sherpa_onnx.OfflineRecognizer.from_paraformer(
            paraformer=model, tokens=tokens, num_threads=config.ASR_THREADS,
            sample_rate=16000, feature_dim=80, decoding_method="greedy_search",
            debug=False)
        name = "sherpa-onnx Paraformer-zh"
    else:
        raise ValueError(f"不认识的引擎：{kind}")

    class S:
        def __init__(self):
            self.r = rec
            self.name = name

        def transcribe(self, audio, sr=16000):
            if sr != 16000:
                audio = _resample(audio, sr, 16000)
            st = self.r.create_stream()
            st.accept_waveform(16000, audio.astype(np.float32))
            self.r.decode_stream(st)
            return (st.result.text or "").strip()

    _cache[key] = S()
    return _cache[key]


ENGINES = {
    "sensevoice": lambda: _engine_sherpa("sensevoice"),
    "paraformer": lambda: _engine_sherpa("paraformer"),
    "whisper": _engine_whisper,
}


def get_engine(name=None):
    name = name or config.ASR_ENGINE
    if name not in ENGINES:
        raise ValueError(f"不认识的识别引擎：{name}（可选 {list(ENGINES)}）")
    return ENGINES[name]()


def transcribe_file(path, engine=None):
    a, sr = _read_wav(path)
    return get_engine(engine).transcribe(a, sr)


def available():
    """哪些引擎现在就能用。"""
    out = {}
    for name in ENGINES:
        try:
            e = get_engine(name)
            out[name] = e.name
        except Exception as ex:
            out[name] = f"不可用：{type(ex).__name__}"
    return out
