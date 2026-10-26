# -*- coding: utf-8 -*-
"""火山「豆包流式语音识别 2.0」—— 边说边出字。

按官方协议实现（之前两处错，已修）：
  1. 末帧 flags = 0b0010 表示「结束帧且不带序号」，不能再塞 4 字节序号
  2. 服务端响应帧是 header + 序号(4) + 长度(4) + 负载；
     客户端请求帧没有那个序号 —— 收发结构不同，不能共用一套解析

用法：
  s = Stream(); s.start()
  s.feed(pcm)                  # 反复灌 16k/16bit/单声道 裸 PCM
  for text, final in s.finish(): ...
"""
import gzip
import json
import time
import uuid

import websockets.sync.client as wsc

from . import doubaovoice

URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
RES_ID = "volc.seedasr.sauc.duration"      # 2.0 小时版

VER = 0b0001
HDR = 0b0001

MSG_FULL_CLIENT = 0b0001
MSG_AUDIO = 0b0010
MSG_FULL_SERVER = 0b1001
MSG_ERROR = 0b1111

SER_JSON = 0b0001
SER_NONE = 0b0000
CMP_GZIP = 0b0001
CMP_NONE = 0b0000

FLAG_NONE = 0b0000
FLAG_FINAL = 0b0010


def _key():
    c = doubaovoice.load_conf()
    return c.get("asr_api_key") or c.get("access_token") or ""


def available():
    return bool(_key())


def _pack(msg_type, flags, serial, comp, payload):
    head = bytes([(VER << 4) | HDR, (msg_type << 4) | flags,
                  (serial << 4) | comp, 0x00])
    return head + len(payload).to_bytes(4, "big") + payload


def _unpack_server(data):
    if len(data) < 4:
        return None, None, b""
    msg_type = data[1] >> 4
    flags = data[1] & 0x0F
    comp = data[2] & 0x0F

    if msg_type == MSG_ERROR:
        code = int.from_bytes(data[4:8], "big") if len(data) >= 8 else 0
        size = int.from_bytes(data[8:12], "big") if len(data) >= 12 else 0
        msg = data[12:12 + size].decode("utf-8", "replace")
        return msg_type, flags, json.dumps(
            {"code": code, "message": msg}).encode("utf-8")

    if len(data) < 12:
        return msg_type, flags, b""
    size = int.from_bytes(data[8:12], "big")
    payload = data[12:12 + size]
    if comp == CMP_GZIP:
        try:
            payload = gzip.decompress(payload)
        except Exception:
            pass
    return msg_type, flags, payload


class Stream:
    def __init__(self, rate=16000, punc=True, nonstream=False,
                 end_window=800, force_to_speech=1000,
                 show_utterances=True, result_type="full"):
        self.rate = rate
        self.punc = punc
        self.nonstream = nonstream
        self.end_window = end_window
        self.force_to_speech = force_to_speech
        self.show_utterances = show_utterances
        self.result_type = result_type
        self.ws = None
        self.last = ""

    def start(self):
        h = {
            "X-Api-Key": _key(),
            "X-Api-Resource-Id": RES_ID,
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        self.ws = wsc.connect(URL, additional_headers=h, open_timeout=12,
                              close_timeout=3, max_size=None)
        req = {
            "user": {"uid": "carheart"},
            "audio": {"format": "pcm", "codec": "raw", "rate": self.rate,
                      "bits": 16, "channel": 1},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": bool(self.punc),
                "enable_ddc": False,
                "enable_nonstream": bool(self.nonstream),
                "show_utterances": bool(self.show_utterances),
                "end_window_size": int(self.end_window),
                "force_to_speech_time": int(self.force_to_speech),
                "result_type": self.result_type,
            },
        }
        self.ws.send(_pack(MSG_FULL_CLIENT, FLAG_NONE, SER_JSON, CMP_GZIP,
                           gzip.compress(json.dumps(req).encode("utf-8"))))

    def feed(self, pcm):
        if not pcm:
            return
        self.ws.send(_pack(MSG_AUDIO, FLAG_NONE, SER_NONE, CMP_GZIP,
                           gzip.compress(pcm)))

    def finish(self, timeout_s=20):
        try:
            self.ws.send(_pack(MSG_AUDIO, FLAG_FINAL, SER_NONE, CMP_GZIP,
                               gzip.compress(b"")))
        except Exception:
            pass

        end = time.time() + timeout_s
        while time.time() < end:
            try:
                raw = self.ws.recv(timeout=max(1, end - time.time()))
            except Exception:
                break
            if not raw:
                continue
            mt, flags, pl = _unpack_server(raw)
            if mt == MSG_ERROR:
                try:
                    err = json.loads(pl.decode("utf-8", "replace"))
                except Exception:
                    err = {"raw": str(pl[:200])}
                yield ("__ERROR__", err)
                break
            if mt != MSG_FULL_SERVER:
                continue
            try:
                j = json.loads(pl.decode("utf-8"))
            except Exception:
                continue

            res = j.get("result") or {}
            text = ""
            if isinstance(res, dict):
                text = res.get("text") or ""
                if not text:
                    text = "".join(u.get("text", "")
                                   for u in (res.get("utterances") or []))

            done = bool(flags & 0b0010) or bool(j.get("is_last_package"))
            if text and text != self.last:
                self.last = text
                yield (text, done)
            if done:
                yield (self.last, True)
                break

        try:
            self.ws.close()
        except Exception:
            pass

    # ---------- 边灌边收（给 /sasr 用）----------
    def pump(self, timeout=0.05):
        """把当前已经回来的结果收掉，返回最新文字。

        和 finish() 的区别：不发结束帧，随时可以再灌音频。
        超时没有任何结果就返回空字符串，不抛异常。
        """
        got = ""
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                break
            try:
                raw = self.ws.recv(timeout=max(0.01, left))
            except Exception:
                break
            if not raw:
                continue
            mt, flags, pl = _unpack_server(raw)
            if mt == MSG_ERROR:
                return self.last
            if mt != MSG_FULL_SERVER:
                continue
            try:
                j = json.loads(pl.decode("utf-8"))
            except Exception:
                continue
            res = j.get("result") or {}
            text = ""
            if isinstance(res, dict):
                text = res.get("text") or ""
                if not text:
                    text = "".join(u.get("text", "")
                                   for u in (res.get("utterances") or []))
            if text and text != self.last:
                self.last = text
                got = text
        return got

    def end(self, timeout_s=6.0):
        """发结束帧并把剩余结果收完，返回最终文字。"""
        try:
            self.ws.send(_pack(MSG_AUDIO, FLAG_FINAL, SER_NONE, CMP_GZIP,
                               gzip.compress(b"")))
        except Exception:
            pass
        end = time.time() + timeout_s
        while time.time() < end:
            try:
                raw = self.ws.recv(timeout=max(0.2, end - time.time()))
            except Exception:
                break
            if not raw:
                continue
            mt, flags, pl = _unpack_server(raw)
            if mt == MSG_ERROR:
                break
            if mt != MSG_FULL_SERVER:
                continue
            try:
                j = json.loads(pl.decode("utf-8"))
            except Exception:
                continue
            res = j.get("result") or {}
            text = ""
            if isinstance(res, dict):
                text = res.get("text") or ""
                if not text:
                    text = "".join(u.get("text", "")
                                   for u in (res.get("utterances") or []))
            if text:
                self.last = text
            if bool(flags & 0b0010) or bool(j.get("is_last_package")):
                break
        try:
            self.ws.close()
        except Exception:
            pass
        return self.last
