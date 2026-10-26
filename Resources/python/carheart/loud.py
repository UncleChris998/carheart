# -*- coding: utf-8 -*-
"""把豆包回来的 MP3 抬响 + 转成 WAV，手机直接播。

为什么在服务端抬响（而不是浏览器里解码加增益）：
  iOS 上再开一个 AudioContext 去 decodeAudioData + GainNode，会和录音那个
  上下文打架 —— 实测：播放变哑，接着连录音都收不到一块数据（只录到 0 秒）。
  所以增益改成服务端算好，客户端只播一个文件，最不容易出事。

豆包原始输出实测：峰值 0.402（-7.9 dBFS），RMS -21.3 dBFS。
乘 2.0（+6 dB）后峰值约 -1.9 dBFS，还有余量，不会削波。
"""
import io
import wave

import numpy as np


def decode_any(data):
    """任意容器（mp3/ogg/m4a…）解成单声道 float32 + 采样率。依赖 PyAV。"""
    import av
    with av.open(io.BytesIO(data)) as c:
        st = c.streams.audio[0]
        sr = st.codec_context.sample_rate
        chunks = []
        for frame in c.decode(st):
            a = frame.to_ndarray()
            if a.ndim > 1:
                a = a.mean(axis=0)
            chunks.append(np.asarray(a, dtype=np.float32))
    if not chunks:
        raise ValueError("没有解出音频帧")
    x = np.concatenate(chunks)
    mx = float(np.max(np.abs(x))) if x.size else 0.0
    if mx > 1.5:            # 有些解码路径给的是 16bit 整数值
        x = x / 32768.0
    return x, int(sr)


def decode_pcm16(data, sr=16000):
    """把任意容器（mp4/aac、webm/opus、mp3…）解成 16k 单声道 int16。

    手机端现在用 MediaRecorder 录音，iOS 给的是 mp4/aac，
    Android/桌面给的是 webm/opus —— 服务端统一用 PyAV 解。
    """
    import av
    out = []
    with av.open(io.BytesIO(data)) as c:
        st = c.streams.audio[0]
        res = av.AudioResampler(format="s16", layout="mono", rate=sr)
        frames = []
        for frame in c.decode(st):
            frames.extend(res.resample(frame))
        frames.extend(res.resample(None))
        for f2 in frames:
            arr = np.asarray(f2.to_ndarray(), dtype=np.int16).reshape(-1)
            out.append(arr.copy())
    if not out:
        raise ValueError("没有解出音频帧")
    return np.concatenate(out), int(sr)


def to_loud_wav(data, gain=2.0, ceiling=0.97):
    """返回 (wav_bytes, sample_rate, peak)。失败就抛异常，让调用方退回原始文件。

    注意这里**不是硬削波**：先乘增益，再看峰值。超过 ceiling 就整体
    等比缩下来（归一化），而不是把尖峰压平 —— 硬削波在响字上会出
    「破裂声」（实测第一版：3.46 秒那句有 12 个样本被削平）。
    """
    x, sr = decode_any(data)
    y = x * float(gain)
    pk = float(np.max(np.abs(y))) if y.size else 0.0
    if pk > ceiling and pk > 0:
        y = y * (ceiling / pk)
    # 首尾各做 8ms 淡入淡出：按句流水线之后，一句话就是一个独立音频，
    # 直接硬起硬收会在句子边界"啪"一下 —— 主人听到的破裂声有一半是这个。
    n = int(sr * 0.008)
    if y.size > 2 * n > 0:
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        y[:n] *= ramp
        y[-n:] *= ramp[::-1]
    pcm = (y * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    return buf.getvalue(), sr, peak
