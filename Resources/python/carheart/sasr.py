# -*- coding: utf-8 -*-
"""流式识别的服务端会话（给页面用）。

页面负责录音，每 200ms 把一小块 16k/16bit/单声道 PCM POST 上来，
这里转给火山，把中间结果攒着；页面轮询就能看到文字在长。

接口（都在 /sasr 下，用 op 区分）：
  POST /sasr?op=start                    -> {"sid": "..."}
  POST /sasr?op=chunk&sid=...（body=PCM）-> {"text": "当前识别结果"}
  POST /sasr?op=end&sid=...              -> {"text": "最终结果"}
  POST /sasr?op=abort&sid=...            -> {"ok": true}

线程安全：每个会话一把锁；会话超时 120 秒自动清掉，不会积一堆连接。
"""
import threading
import time
import uuid

from . import volcstream

_sessions = {}
_lock = threading.Lock()
MAX_SESSIONS = 4
IDLE_TIMEOUT = 120

KEEP = {}          # 已经结束但还没被取走结果的


def available():
    return volcstream.available()


def _sweep():
    now = time.time()
    dead = []
    with _lock:
        for sid, s in _sessions.items():
            if now - s.get("touched", 0) > IDLE_TIMEOUT:
                dead.append(sid)
        for sid in dead:
            s = _sessions.pop(sid, None)
            if s:
                try:
                    s["stream"].end(timeout_s=1.0)
                except Exception:                        # noqa: BLE001
                    pass
    return dead


def start(rate=16000):
    _sweep()
    with _lock:
        if len(_sessions) >= MAX_SESSIONS:
            raise RuntimeError("同时进行的会话太多（%d）" % len(_sessions))
    sid = uuid.uuid4().hex[:12]
    st = volcstream.Stream(rate=rate, punc=True, nonstream=False,
                           end_window=800, force_to_speech=1000)
    st.start()
    with _lock:
        _sessions[sid] = {"stream": st, "text": "", "touched": time.time(),
                          "lock": threading.Lock()}
    return sid


def chunk(sid, pcm):
    with _lock:
        s = _sessions.get(sid)
    if not s:
        raise KeyError("没有这个会话")
    with s["lock"]:
        s["stream"].feed(pcm)
        got = s["stream"].pump(timeout=0.12)
        if got:
            s["text"] = got
        s["touched"] = time.time()
        return s["text"]


def end(sid):
    with _lock:
        s = _sessions.pop(sid, None)
    if not s:
        raise KeyError("没有这个会话")
    with s["lock"]:
        try:
            final = s["stream"].end(timeout_s=6.0)
        except Exception:                            # noqa: BLE001
            final = s["text"]
    return final or s["text"]


def abort(sid):
    with _lock:
        s = _sessions.pop(sid, None)
    if not s:
        return False
    try:
        s["stream"].end(timeout_s=0.5)
    except Exception:                                # noqa: BLE001
        pass
    return True
