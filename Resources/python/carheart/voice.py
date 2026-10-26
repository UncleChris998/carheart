# -*- coding: utf-8 -*-
"""语音：让它能听你说话，也用自己的声音回你。

两条路：
  - 按回车说话（默认，最省事，不用装额外的唤醒词引擎）
  - 常听模式（可选，需要 openwakeword 一类引擎，先留着口子）
"""
import os
import queue
import threading
import time

from . import config

SAMPLE_RATE = 16000
_model = None
_model_size = None

# ---- 语音识别模型从 HuggingFace 拉。国内直连不通，走镜像。----
# 想换成别的镜像，改这里或设环境变量 HF_ENDPOINT。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")      # xet 在镜像上常失败，关掉
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
os.environ.setdefault("HF_HOME", os.path.join(config.DATA_DIR, "hf"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _pick_device():
    """挑一个能用的输入设备（优先系统默认）。"""
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
        return dev
    except Exception as e:
        return {"error": str(e)}


def devices_report():
    """把输入输出设备列出来，方便排错。"""
    try:
        import sounddevice as sd
        lines = []
        for i, d in enumerate(sd.query_devices()):
            kind = []
            if d.get("max_input_channels", 0) > 0:
                kind.append("输入")
            if d.get("max_output_channels", 0) > 0:
                kind.append("输出")
            if kind:
                lines.append(f"  [{i}] {d['name']}  ({'/'.join(kind)}, "
                             f"默认采样率 {int(d.get('default_samplerate', 0))})")
        try:
            di = sd.default.device
            lines.append(f"  系统默认设备索引：{di}")
        except Exception:
            pass
        return "\n".join(lines) if lines else "  （没找到音频设备）"
    except Exception as e:
        return f"  读不到音频设备：{e}"


def load_model(size=None):
    """按需加载语音识别模型，只加载一次。

    坑：device="auto" 会选 CUDA，然后 CTranslate2 因为找不到 cublas64_12.dll
    直接抛 RuntimeError。所以这里显式按 config 走，并且失败时退到 CPU。
    """
    global _model, _model_size
    size = size or config.VOICE_MODEL_SIZE
    if _model is not None and _model_size == size:
        return _model
    from faster_whisper import WhisperModel

    attempts = [(config.VOICE_DEVICE, config.VOICE_COMPUTE_TYPE)]
    if config.VOICE_DEVICE != "cpu":
        attempts.append(("cpu", "int8"))
    last = None
    for dev, ct in attempts:
        try:
            _model = WhisperModel(size, device=dev, compute_type=ct,
                                  download_root=config.VOICE_MODEL_DIR)
            _model_size = size
            return _model
        except Exception as e:                       # noqa: BLE001
            last = e
            continue
    raise RuntimeError(f"语音模型加载失败（试过 {attempts}）：{last}")


def _resample(audio, src_rate, dst_rate=SAMPLE_RATE):
    """线性重采样。语音够用了，不引入额外依赖。"""
    if src_rate == dst_rate or audio.size == 0:
        return audio
    import numpy as np
    n_out = int(round(audio.size * dst_rate / float(src_rate)))
    if n_out <= 1:
        return audio
    x_old = np.linspace(0.0, 1.0, audio.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


class Mic:
    """麦克风。设备打不开就自动换下一个，采样率不匹配就重采样。"""

    def __init__(self, samplerate=SAMPLE_RATE, device=None, candidates=None):
        import sounddevice as sd
        self.sd = sd
        self.want_rate = samplerate
        self.device = device
        self.candidates = candidates
        self.rate = samplerate
        self.q = queue.Queue()
        self.stream = None
        self.error = None

    def _device_list(self):
        if self.candidates is not None:
            return self.candidates
        out = []
        try:
            di = self.sd.default.device
            if isinstance(di, (list, tuple)) and di:
                out.append(di[0])
            else:
                out.append(di)
        except Exception:
            pass
        try:
            for i, d in enumerate(self.sd.query_devices()):
                if d.get("max_input_channels", 0) > 0 and i not in out:
                    out.append(i)
        except Exception:
            pass
        return [d for d in out if d is not None]

    def _cb(self, indata, frames, time_info, status):
        self.q.put(bytes(indata))

    def start(self):
        if self.stream is not None:
            return True
        import numpy as np
        errs = []
        for dev in self._device_list():
            for rate in (self.want_rate, 48000, 44100, 32000):
                try:
                    s = self.sd.RawInputStream(samplerate=rate, blocksize=0,
                                               dtype="int16", channels=1,
                                               device=dev, callback=self._cb)
                    s.start()
                    self.stream = s
                    self.device = dev
                    self.rate = rate
                    return True
                except Exception as e:
                    errs.append(f"设备{dev}@{rate}: {e}")
                    continue
        self.error = "；".join(errs[:4]) if errs else "没有可用的输入设备"
        return False

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _drain(self):
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def record_manual(self, stop_event, max_seconds=30.0, verbose=False):
        """手动控制：stop_event 置位就停。不猜阈值、不等静音、不超时。

        为什么不用阈值法：实测这台机器环境噪声峰值就有 266-370，
        靠它判断「你在说话」会误触发、会等不到安静、还会因为起步慢而直接放弃。
        """
        import numpy as np
        self._drain()
        if not self.start():
            raise RuntimeError(f"麦克风打不开：{self.error}")
        if verbose:
            print(f"  （设备 {self.device} @ {self.rate} Hz）")
        frames = []
        t = 0.0
        peak = 0
        while t < max_seconds and not stop_event.is_set():
            try:
                data = self.q.get(timeout=0.1)
            except queue.Empty:
                t += 0.1
                continue
            arr = np.frombuffer(data, dtype=np.int16)
            if arr.size == 0:
                continue
            frames.append(arr)
            peak = max(peak, int(np.abs(arr).max()))
            t += arr.size / float(self.rate)
        if not frames:
            return None, peak
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        return _resample(audio, self.rate, SAMPLE_RATE), peak

    def record_until_silence(self, max_seconds=15.0, silence_seconds=1.0,
                             start_timeout=6.0, threshold=None, verbose=False):
        """录到你不说话为止。返回 (音频float32@16k, 峰值) 或 (None, 峰值)。"""
        import numpy as np
        self._drain()
        if not self.start():
            raise RuntimeError(f"麦克风打不开：{self.error}")
        if verbose:
            print(f"  （用设备 {self.device} @ {self.rate} Hz）")
        frames = []
        silent_for = 0.0
        spoke = False
        t = 0.0
        block = 0.1
        peak_seen = 0
        while t < max_seconds:
            try:
                data = self.q.get(timeout=block)
            except queue.Empty:
                t += block
                continue
            arr = np.frombuffer(data, dtype=np.int16)
            if arr.size == 0:
                continue
            peak = int(np.abs(arr).max())
            peak_seen = max(peak_seen, peak)
            thr = threshold if threshold is not None else config.VOICE_SILENCE_RMS
            frames.append(arr)
            dur = arr.size / float(self.rate)
            t += dur
            if peak > thr:
                spoke = True
                silent_for = 0.0
            else:
                silent_for += dur
                if spoke and silent_for >= silence_seconds:
                    break
                if not spoke and t >= start_timeout:
                    return None, peak_seen
        if not frames:
            return None, peak_seen
        audio = np.concatenate(frames).astype(np.float32) / 32768.0
        audio = _resample(audio, self.rate, SAMPLE_RATE)
        return audio, peak_seen

    def record_fixed(self, seconds=5.0):
        import numpy as np
        self._drain()
        if not self.start():
            raise RuntimeError(f"麦克风打不开：{self.error}")
        need = int(seconds * self.rate)
        buf, got = [], 0
        while got < need:
            try:
                data = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            arr = np.frombuffer(data, dtype=np.int16)
            buf.append(arr)
            got += arr.size
        audio = np.concatenate(buf).astype(np.float32) / 32768.0
        return _resample(audio, self.rate, SAMPLE_RATE)


def preprocess_audio(audio, sr=SAMPLE_RATE, trim=True, normalize=True,
                     target_peak=0.9):
    """识别前把音频收拾干净 —— 这一步对准确率的影响可能比换模型还大。

    做三件事：
      1. 去直流偏移（麦克风阵列常见）
      2. 去掉首尾静音（Whisper 遇到长静音开头容易「编话」）
      3. 峰值归一化（录音偏小时模型听不清）
    实测这台的麦克风底噪峰值就有 266-370，不归一化时说话声相对偏小。
    """
    import numpy as np
    a = np.asarray(audio, dtype=np.float32)
    if a.size == 0:
        return a
    a = a - float(np.mean(a))

    if trim:
        win = max(1, int(0.02 * sr))                 # 20ms 窗
        n = a.size // win
        if n >= 3:
            frames = a[:n * win].reshape(n, win)
            rms = np.sqrt(np.mean(frames ** 2, axis=1))
            thr = max(float(np.percentile(rms, 15)) * 2.0,
                      float(rms.max()) * 0.06)
            loud = np.where(rms > thr)[0]
            if loud.size:
                s = max(0, loud[0] - 5) * win        # 前后各留 100ms
                e = min(n, loud[-1] + 6) * win
                a = a[s:e]

    if normalize and a.size:
        peak = float(np.max(np.abs(a)))
        if peak > 1e-6:
            a = a * (target_peak / peak)
    return a.astype(np.float32)


def save_recording(audio, sr=SAMPLE_RATE, tag="", note=""):
    """把这次录音存下来 —— 专门用来以后对比识别模型。

    为什么要存：识别准不准只能拿真人语音判断。
    合成音（piper）本身声调就是错的，拿它测等于自欺欺人。
    """
    if not config.SAVE_RECORDINGS or audio is None or len(audio) == 0:
        return None
    import wave
    import numpy as np
    os.makedirs(config.RECORD_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    name = f"{ts}{('-' + tag) if tag else ''}.wav"
    path = os.path.join(config.RECORD_DIR, name)
    a = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm = (a * 32767.0).astype(np.int16).tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    if note:
        with open(os.path.join(config.RECORD_DIR, "index.tsv"), "a",
                  encoding="utf-8") as f:
            f.write(f"{name}\t{len(a)/sr:.1f}s\t{note}\n")
    return path


def transcribe(audio, language="zh"):
    """音频 -> 文字。先清洗，再用更稳的参数识别。"""
    model = load_model()
    a = preprocess_audio(audio)
    if len(a) < int(0.2 * SAMPLE_RATE):
        return "", None
    segments, info = model.transcribe(
        a, language=language, beam_size=5, best_of=5,
        condition_on_previous_text=False,
        temperature=[0.0, 0.2, 0.4],
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400})
    text = "".join(s.text for s in segments).strip()
    return text, getattr(info, "language_probability", None)


# ---- 注意：这里刻意不去改 TEMP/TMP。
#      踩过的坑：为了绕开 pywin32 的 gen_py 权限问题改掉 TEMP 之后，
#      子进程 PowerShell 的 System.Speech 会报「系统上未安装语音」。
#      pywin32 的缓存路径已经在下面按对象属性单独指好了，不动环境变量。----
def _ensure_writable_temp():
    """只检查 %TEMP% 能不能写，不改它。"""
    import tempfile
    base = tempfile.gettempdir()
    try:
        probe = os.path.join(base, ".ch_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except Exception:
        return False


_TEMP_OK = _ensure_writable_temp()

if _TEMP_OK:
    try:
        import win32com
        win32com.__gen_path__ = os.path.join(config.DATA_DIR, "gen_py")
    except Exception:
        pass


def _sapi():
    """拿一个 SAPI 声音对象。"""
    from win32com.client import dynamic
    return dynamic.Dispatch("SAPI.SpVoice")


_speak_lock = threading.Lock()


def _sapi_wav(text, wav_path, rate_i=0, voice_hint=None):
    """用 System.Speech 把话合成到 WAV 文件。

    为什么要这么绕：
      1. 这台机器的 SAPI 注册表里 AudioOutput 下面没有输出设备 token，
         SpVoice.Speak 直接出声必然「拒绝访问」；合成到文件是好的。
      2. PowerShell 5.1 的 -EncodedCommand 在这台机器上读不到音色，
         但用 -File 跑一个带 BOM 的 .ps1 是好的。
    所以：写 .ps1（UTF-8 BOM）-> -File 执行 -> winsound 播放。
    """
    import subprocess
    import base64

    b64 = base64.b64encode(text.encode("utf-8")).decode()
    # 实测结论：名字必须是完整音色名，且不能用循环从 GetInstalledVoices() 里取
    # （那条路在这台机器上会报「No voice installed」）。写死，失败就退到默认。
    voices = {"Huihui": "Microsoft Huihui Desktop",
              "Zira": "Microsoft Zira Desktop"}
    want = voices.get((voice_hint or config.VOICE_HINT or "Huihui"), None) \
        or (voice_hint or "Microsoft Huihui Desktop")
    ps1 = os.path.join(config.DATA_DIR, "tts", "_say.ps1")
    os.makedirs(os.path.dirname(ps1), exist_ok=True)

    script = "\n".join([
        "$ErrorActionPreference = 'Continue'",
        "Add-Type -AssemblyName System.Speech",
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
        "Write-Output ('音色数: ' + $s.GetInstalledVoices().Count)",
        "foreach ($v in $s.GetInstalledVoices()) { Write-Output ('  ' + $v.VoiceInfo.Name + ' enabled=' + $v.Enabled) }",
        "try { $s.SelectVoice('" + want + "'); Write-Output ('选中: ' + $s.Voice.Name) } catch { Write-Output ('选音色失败: ' + $_.Exception.Message) }",
        "$s.Rate = " + str(rate_i),
        "try { $s.SetOutputToWaveFile('" + wav_path + "'); Write-Output '输出已设' } catch { Write-Output ('设输出失败: ' + $_.Exception.Message); exit 2 }",
        "$t = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('" + b64 + "'))",
        "Write-Output ('文本长度: ' + $t.Length)",
        "try { $s.Speak($t); Write-Output 'Speak 成功' } catch { Write-Output ('Speak 失败: ' + $_.Exception.Message); exit 3 }",
        "$s.Dispose()",
        "if (Test-Path '" + wav_path + "') { Write-Output ('文件: ' + (Get-Item '" + wav_path + "').Length + ' 字节') } else { Write-Output '文件没生成' }",
        "",
    ])
    # 必须带 BOM，否则 PS 5.1 会当 ANSI 读，中文变乱码
    with open(ps1, "w", encoding="utf-8-sig") as f:
        f.write(script)

    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", ps1],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
    if os.path.exists(wav_path) and os.path.getsize(wav_path) > 2000:
        return True, f"System.Speech -> WAV（音色 {want}）"
    err = ((p.stderr or b"") + (p.stdout or b"")).decode("utf-8", "ignore")
    return False, "合成失败: " + err.replace("\r", " ").replace("\n", " ")[:240]


def _play(wav_path):
    """播放 WAV。winsound 在这台机器上已验证可用。"""
    try:
        import winsound
        winsound.PlaySound(wav_path, winsound.SND_FILENAME)
        return True
    except Exception:
        try:
            import subprocess
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"(New-Object System.Media.SoundPlayer '{wav_path}').PlaySync()"],
                           timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            return False


# ---------------- 出声：首选 piper（纯离线神经语音，不碰 Windows SAPI）----------------
_piper_voice = None


def piper_available():
    return bool(config.PIPER_ENABLED and os.path.exists(config.PIPER_MODEL))


def _load_piper():
    global _piper_voice
    if _piper_voice is None:
        from piper import PiperVoice
        _piper_voice = PiperVoice.load(config.PIPER_MODEL)
    return _piper_voice


def piper_say(text, out_wav=None):
    """用 piper 合成并播放（整段）。"""
    import wave
    out_wav = out_wav or os.path.join(config.DATA_DIR, "tts", "piper_say.wav")
    os.makedirs(os.path.dirname(out_wav), exist_ok=True)
    v = _load_piper()
    with wave.open(out_wav, "wb") as wf:
        v.synthesize_wav(text, wf)
    if not os.path.exists(out_wav) or os.path.getsize(out_wav) < 1000:
        return False, "piper 没产出音频"
    return _play(out_wav), "piper（离线神经语音）"


# =====================================================================
#  流式出声：边生成边念
#  动机（主人 2026-10-11）：要「豆包那种快感」。
#  实测 27B 首字 0.18-0.68s、整段不到 1.2s —— 模型不慢，
#  慢的是「等整段生成完才开始合成、才开始播」。改成一句一念。
# =====================================================================
_SENT_END = "。！？!?\n"
_SOFT_END = "，、；;：:"


def split_stream(buffer, force_at=44):
    """从流式文本里切出可以立刻念的整句。返回 (句子列表, 剩余缓冲)。"""
    out = []
    start = 0
    i = 0
    while i < len(buffer):
        ch = buffer[i]
        if ch in _SENT_END:
            seg = buffer[start:i + 1].strip()
            if seg:
                out.append(seg)
            start = i + 1
        elif ch in _SOFT_END and (i - start) >= force_at:
            # 长句太长会让人等 —— 在逗号处先念一截
            seg = buffer[start:i + 1].strip()
            if seg:
                out.append(seg)
            start = i + 1
        i += 1
    return out, buffer[start:]


class StreamSpeaker:
    """边生成边念。

    两个线程：
      · 合成线程：句子进来就合成 WAV，排进播放队列
      · 播放线程：一个接一个播，**在播当前句的时候，下一句已经在合成了**

    这样你听到的第一个字，比「等整段说完再合再播」早了大约 2 秒。
    """

    def __init__(self, voice_hint=None, enabled=True):
        self.enabled = enabled and piper_available()
        self.voice_hint = voice_hint
        self.buf = ""
        self.said = []
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._synth_t = None
        self._play_t = None
        self._n = 0
        self.first_audio_at = None
        self._t0 = None
        self.error = None
        self.total_chars = 0

    # ---------- 内部线程 ----------
    def _synth(self):
        import wave
        d = os.path.join(config.DATA_DIR, "tts")
        os.makedirs(d, exist_ok=True)
        while True:
            item = self._q.get()
            if item is None:
                break
            idx, text = item
            try:
                v = _load_piper()
                p = os.path.join(d, f"seg_{idx:03d}.wav")
                with wave.open(p, "wb") as wf:
                    v.synthesize_wav(text, wf)
                self._ready.put((idx, text, p, time.time()))
            except Exception as e:                    # noqa: BLE001
                self._ready.put((idx, text, None, time.time()))

    def _play_loop(self):
        while True:
            item = self._ready.get()
            if item is None:
                break
            idx, text, path, ts = item
            if self.first_audio_at is None and self._t0 is not None:
                self.first_audio_at = ts - self._t0
            if path and os.path.exists(path):
                self._play_blocking(path)

    def _play_blocking(self, path):
        """播完这一段才返回。"""
        try:
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            import wave
            with wave.open(path) as w:
                dur = w.getnframes() / float(w.getframerate())
            t0 = time.time()
            while time.time() - t0 < dur + 0.15:
                if self._stop.is_set():
                    winsound.PlaySound(None, winsound.SND_PURGE)
                    return
                time.sleep(0.02)
        except Exception:
            _play(path)

    # ---------- 对外 ----------
    def start(self):
        self._t0 = time.time()
        self._ready = queue.Queue()
        if not self.enabled:
            return False
        self._synth_t = threading.Thread(target=self._synth, daemon=True)
        self._play_t = threading.Thread(target=self._play_loop, daemon=True)
        self._synth_t.start()
        self._play_t.start()
        return True

    def feed(self, delta):
        """把模型流出来的字喂进来。凑够一句就送去合成。"""
        if not self.enabled:
            return
        self.buf += delta
        self.total_chars += len(delta)
        sents, self.buf = split_stream(self.buf)
        for s in sents:
            self._n += 1
            self.said.append(s)
            self._q.put((self._n, s))

    def finish(self):
        """模型说完了：把剩下的尾巴也念掉，然后等他听完。"""
        if not self.enabled:
            return
        tail = self.buf.strip()
        if tail:
            self._n += 1
            self.said.append(tail)
            self._q.put((self._n, tail))
        self.buf = ""
        self._q.put(None)
        if self._synth_t:
            self._synth_t.join(timeout=90)
        self._ready.put(None)
        if self._play_t:
            self._play_t.join(timeout=90)

    def stop(self):
        """打断：立刻停声。"""
        self._stop.set()
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass

    def stats(self):
        return {
            "句数": self._n,
            "首句出声延迟": (round(self.first_audio_at, 2)
                             if self.first_audio_at else None),
            "整段字数": self.total_chars,
            "总耗时": (round(time.time() - self._t0, 2) if self._t0 else None),
        }


def speak(text, rate=None, voice_hint=None):
    """让它出声。

    顺序：piper -> SAPI 直出 -> 合成 WAV 再播 -> pyttsx3。
    这台机器的 SAPI 是坏的（AudioOutput 注册表下没有设备 token），
    所以正常情况下都会走第一条。
    """
    if not text:
        return False, "空文本"
    errs = []
    r = int(rate or config.VOICE_RATE)
    rate_i = max(-10, min(10, int((175 - r) / 12)))
    tts_dir = os.path.join(config.DATA_DIR, "tts")
    os.makedirs(tts_dir, exist_ok=True)
    wav = os.path.join(tts_dir, "say.wav")

    with _speak_lock:
        # 路 1：piper
        if piper_available():
            try:
                ok, how = piper_say(text)
                if ok:
                    return True, how
                errs.append(how)
            except Exception as e:                    # noqa: BLE001
                errs.append(f"piper: {type(e).__name__}: {str(e)[:80]}")

        # 路 2：SAPI 直出
        try:
            sp = _sapi()
            sp.Rate = rate_i
            if voice_hint:
                for tok in sp.GetVoices():
                    if voice_hint.lower() in tok.GetDescription().lower():
                        sp.Voice = tok
                        break
            sp.Speak(text)
            return True, "SAPI 直出"
        except Exception as e:
            errs.append(f"直出: {str(e)[:60]}")

        # 路 3：合成 WAV 再播放
        try:
            if os.path.exists(wav):
                os.remove(wav)
            ok, how = _sapi_wav(text, wav, rate_i, voice_hint)
            if ok and _play(wav):
                return True, how
            errs.append(how)
        except Exception as e:
            errs.append(f"WAV: {str(e)[:80]}")

        # 路 4：pyttsx3
        try:
            import pyttsx3
            eng = pyttsx3.init()
            eng.setProperty("rate", r)
            eng.say(text)
            eng.runAndWait()
            return True, "pyttsx3"
        except Exception as e:
            errs.append(f"pyttsx3: {str(e)[:60]}")

    return False, " | ".join(errs)


def sapi_voices():
    """列 Windows SAPI 的音色。"""
    try:
        sp = _sapi()
        return [tok.GetDescription() for tok in sp.GetVoices()]
    except Exception as e:
        return [f"读不到：{e}"]


def list_voices():
    try:
        import pyttsx3
        eng = pyttsx3.init()
        return [(v.id, v.name) for v in eng.getProperty("voices")]
    except Exception as e:
        return [(None, f"读不到：{e}")]


def available():
    info = {}
    for name in ("sounddevice", "faster_whisper", "pyttsx3", "numpy"):
        try:
            __import__(name)
            info[name] = True
        except Exception:
            info[name] = False
    return info
