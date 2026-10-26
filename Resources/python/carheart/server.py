# -*- coding: utf-8 -*-
"""carheart 的 HTTP 服务层 —— 给车里的屏幕/手机用。

为什么要有这一层：
  安卓屏直接连 Ollama 只能拿到裸模型，没有人格、没有记忆、没有养成。
  这一层把「它」暴露出去，而安卓端只负责显示和触摸。

设计原则：
  · 只用 Python 标准库，不加依赖（车里那台可能装不上东西）
  · 记忆永远只存在这台机器上，接口只进不出（除了它自己说的话）
  · 流式输出用 SSE，安卓端可以边收边显示

跑：
  python -m carheart.server                  # 监听 0.0.0.0:19388
  python -m carheart.server --port 19388     # 指定端口
  python -m carheart.server --token 密码      # 要求带 token（局域网里更稳）
"""
import json
import os
import re
import socket
import sys
import threading
import threading as _threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db, brain, persona, llm, webui

# 注意：asr / asrlab / asrfix 这三个**故意不在这里 import**。
# 它们依赖 numpy 和本地模型，APK 里没有（手机上语音走火山云，
# 不需要本地识别）。原来在顶部 import，结果整个服务在导入阶段就
# ImportError，App 一打开就是一堆报错 —— 就是这么崩的。
# 现在改成用到时才加载，缺了也只是那一个功能不可用。

# ---------------------------------------------------------------- 会话
_sessions = {}
_sessions_lock = threading.Lock()
# sherpa-onnx 的 recognizer 不是线程安全的，识别必须串行（见 _handle_asr）
_ASR_LOCK = threading.Lock()


def get_heart(session_id="default", body_id=None):
    """拿到这个会话的 Heart，并把一个**属于当前线程**的连接挂上去。

    坑：ThreadingHTTPServer 每个请求一个线程，而 SQLite 连接不能跨线程用
    （ProgrammingError: SQLite objects created in a thread can only be used
     in that same thread）。所以会话里只缓存人格与上下文，不缓存连接。
    """
    with _sessions_lock:
        h = _sessions.get(session_id)
        if h is None:
            conn0 = db.connect()
            db.ensure_bodies(conn0)
            h = brain.Heart(conn0, body_id=body_id or config.DEFAULT_BODY)
            conn0.close()
            _sessions[session_id] = h
    conn = db.connect()
    db.ensure_bodies(conn)
    h.conn = conn                      # 换成当前线程自己的连接
    if body_id:
        h.body_id = body_id
    return h


def drop_session(session_id):
    with _sessions_lock:
        _sessions.pop(session_id, None)


# ---------------------------------------------------------------- 工具
def sse(payload):
    """把一段数据包成 SSE。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _active_model():
    """当前真正在用的模型名（云端开着就是云端那个）。"""
    try:
        from . import cloud
        name, _ = cloud.active_brain()
        return name
    except Exception:
        return config.MODELS["chat"]


def _active_where():
    """在哪儿跑：云端 / 本机。"""
    try:
        from . import cloud
        _, where = cloud.active_brain()
        return where
    except Exception:
        return "本机"


def _free_local_model():
    """把本地模型从显存里卸掉（切到云端时用）。

    走云端还占着 15 GB 显存是浪费，而且看显存会误判在用本地。
    """
    try:
        import subprocess
        name = config.MODELS["chat"]
        subprocess.run(["ollama", "stop", name],
                       capture_output=True, timeout=60)
        print(f"  [显存] 已卸载本地模型 {name}", flush=True)
    except Exception as e:                                # noqa: BLE001
        print(f"  [显存] 卸载失败（不影响云端使用）：{e}", flush=True)


def _warm_local_model():
    """把本地模型加载进显存（切回本地时用），避免第一句话等 8 秒。"""
    try:
        import time as _t
        t0 = _t.time()
        llm.chat([{"role": "user", "content": "在"}],
                 stream=False, think=False, max_tokens=4)
        print(f"  [显存] 本地模型已进显存 {_t.time()-t0:.1f}s", flush=True)
    except Exception as e:                                # noqa: BLE001
        print(f"  [显存] 预热失败（不影响使用）：{e}", flush=True)


def _purge_stale_model_talk():
    """清掉「提到旧模型名」的对话轮次和记忆。

    为什么必须清：她读得到最近几轮对话，里面有她自己以前说过的旧模型名，
    于是换过之后她还照着旧答案念 —— 今天在这件事上反复出错，
    每次都是同一个机制：改对了配置，但历史在拖后腿。
    """
    conn = db.connect()
    keys = ["deepseek-flash", "deepseek-v4-pro", "deepseek-chat",
            "qwen3.8-27b", "qwen3.8-27b-meimeitou", "27B", "27b"]
    n_turn = n_mem = 0
    try:
        for r in list(conn.execute("SELECT id,content FROM turns")):
            tx = (r["content"] or "")
            if sum(k in tx for k in keys) >= 1 and ("模型" in tx or "脑子" in tx):
                conn.execute("DELETE FROM turns WHERE id=?", (r["id"],))
                n_turn += 1
        for r in list(conn.execute("SELECT id,text FROM memories")):
            tx = (r["text"] or "")
            if sum(k in tx for k in keys) >= 1:
                conn.execute("DELETE FROM memories WHERE id=?", (r["id"],))
                n_mem += 1
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if n_turn or n_mem:
        print(f"  [清理] 换模型后清掉旧模型话题：对话 {n_turn} 轮、记忆 {n_mem} 条",
              flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "carheart/0.1"
    protocol_version = "HTTP/1.1"

    # ---------- 基础设施 ----------
    def log_message(self, fmt, *args):
        # 默认的日志太吵，只在 -v 时打
        if getattr(self.server, "verbose", False):
            sys.stderr.write("  %s\n" % (fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _auth_ok(self):
        want = getattr(self.server, "token", None)
        if not want:
            return True
        # 校对台这几条路径免 token：它是本机调试工具，
        # 手机上直接开 /lab 就能用，不用手输 token。
        p = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        if p in ("/lab", "/clips", "/truth", "/mind", "/cloudmodels", "/voice",
                 "/settings", "/orb", "/asrconf") \
                or p.startswith("/clip/"):
            return True
        got = self.headers.get("Authorization", "")
        if got.startswith("Bearer "):
            got = got[7:]
        if not got:
            got = urllib.parse.urlparse(self.path).query
            got = urllib.parse.parse_qs(got).get("token", [""])[0]
        return got == want

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _send(self, code, data, ctype="application/json; charset=utf-8"):
        # 防一类低级错误：传进来 str 的话 wfile.write 会抛 TypeError，
        # 而那时响应头已经发出去了 —— 客户端只看到"200 但正文是空的"。
        # 这里直接转成 bytes，别让那种 bug 再出现。
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 不许缓存。之前没发这个头，安卓 Chrome 会把旧页面一直用下去 ——
        # 改完设置面板，主人刷新还是看到老界面，以为我没改。
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        # 出声用的是谁 —— 前端读这个头显示出来。
        # 主人反馈"说话的声音和试听不一样"，得能看见实际走的是哪条引擎，
        # 否则只能靠猜（我之前就一直在猜）。
        tag = getattr(self, "_tts_tag", None)
        if tag:
            self.send_header("X-Tts-Engine", str(tag))
            self._tts_tag = None
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    # ---------- 路由 ----------
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        """有些客户端会先发 HEAD 探一下。

        以前没实现，http.server 直接回 501 Not Implemented，
        页面上就显示"连不上"—— 实测踩过。
        """
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def do_PUT(self):
        self._send(405, b'{"error":"method not allowed"}')

    def do_DELETE(self):
        self._send(405, b'{"error":"method not allowed"}')

    # ---------- 听：手机录的 WAV 传上来识别 ----------
    def _handle_tts(self):
        """把文字合成成 WAV 音频，让手机直接播。

        为什么不在手机上用 speechSynthesis：安卓上它不稳 ——
        没有中文音色就不出声、要用户手势才解锁、静音键一按就哑。
        服务端用 piper 离线合成，手机只要播个音频文件就行，最可靠。
        """
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            text = (data.get("text") or "").strip()
        except Exception as e:                            # noqa: BLE001
            return self._send(400, json.dumps(
                {"error": f"请求不合法: {e}"}, ensure_ascii=False).encode())
        if not text:
            return self._send(400, json.dumps(
                {"error": "text 是空的"}, ensure_ascii=False).encode())
        # 太长就别合成，省时间
        if len(text) > 600:
            text = text[:600]
        # 纯标点 / 表情 / 空白 的片段直接给一段 40ms 静音。
        # 按句流水线之后，客户端会把「……」这种也当一句话送过来，
        # 豆包会回 "No readable text!"，然后一路退到 piper 再抛
        # wave.Error —— 屏幕上那条 POST /tts → HTTP 500 就是这么来的。
        if not re.search(r"[0-9A-Za-z\u3400-\u9fff]", text):
            import io
            import wave

            sr = 16000
            n = int(sr * 0.04)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(b"\x00\x00" * n)
            self._tts_tag = "silence"
            return self._send(200, buf.getvalue(), ctype="audio/wav")
        # 用哪个音色：配置文件优先，其次 config 默认
        voice_name = config.TTS_VOICE
        engine = config.TTS_ENGINE
        try:
            if os.path.exists(config.TTS_CONF_PATH):
                with open(config.TTS_CONF_PATH, encoding="utf-8") as f:
                    tc = json.load(f) or {}
                voice_name = tc.get("voice") or voice_name
                engine = tc.get("engine") or engine
        except Exception:
            pass

        # 送去合成之前先改成"糯叽叽的台腔写法"：少卷舌、偶尔加喔/啦/诶、长句拆短。
        # 只改这一份，屏幕上的原文和她的记忆都不动。
        try:
            from . import speech_style as _ss
            spoken = _ss.speakable(text)
        except Exception:
            spoken = text

        # 用哪个音色：以 data/voice.json 为准（界面选的 / 说话换的）。
        sel = {"provider": "volc",
               "id": "zh_female_tianmeitaozi_uranus_bigtts"}
        try:
            from . import voices as _voices
            sel = _voices.load()
        except Exception:
            pass

        # 1) 选的是火山豆包
        if sel.get("provider") == "volc":
            try:
                import hashlib

                from . import doubaovoice as _dv
                if not _dv.available():
                    raise RuntimeError("火山凭据没配")
                spk = sel.get("id")
                h = hashlib.md5((spk + "|" + spoken).encode("utf-8")
                                ).hexdigest()[:16]
                d = os.path.join(config.DATA_DIR, "tts")
                os.makedirs(d, exist_ok=True)
                path = os.path.join(d, f"v_{h}.mp3")
                t0 = time.time()
                if not (os.path.exists(path) and os.path.getsize(path) > 2000):
                    ok, msg = _dv.tts(spoken, path, speaker=spk)
                    if not ok:
                        raise RuntimeError(msg)
                    print(f"  [出声] 豆包 {spk[:-11]} 合成 {len(text)} 字 "
                          f"{time.time()-t0:.1f}s", flush=True)
                with open(path, "rb") as f:
                    mp3 = f.read()
                # 告诉前端这段是谁合成的 —— 主人听出差别时能一眼看到
                self._tts_tag = "doubao:" + str(spk)
                # 抬响 + 转 WAV：豆包原始 MP3 峰值只到 -7.9 dBFS，手机上偏小。
                # 增益放服务端做，客户端只负责播一个文件 —— iOS 上再开一个
                # AudioContext 去 decode+gain 会和录音那个打架（实测：变哑，
                # 接着连录音都收不到数据）。所以这里换 16bit WAV 回去。
                try:
                    from . import loud as _loud
                    wav, _sr, _peak = _loud.to_loud_wav(mp3, gain=2.0)
                    self._tts_tag += f" +6dB/wav{_sr}"
                    return self._send(200, wav, ctype="audio/wav")
                except Exception as e:                    # noqa: BLE001
                    print(f"  [出声] 抬响失败，直接给原 mp3："
                          f"{type(e).__name__}: {e}", flush=True)
                return self._send(200, mp3, ctype="audio/mpeg")
            except Exception as e:                        # noqa: BLE001
                print(f"  [出声] 豆包失败，往下退：{type(e).__name__}: {e}",
                      flush=True)

        # 2) 微软神经音色（免费、不要 key）；火山失败时也退到这里
        edge_voice = (sel.get("id") if sel.get("provider") == "edge"
                      else None) or voice_name
        if True:
            try:
                import asyncio
                import hashlib

                import edge_tts
                h = hashlib.md5((edge_voice + "|" + spoken).encode("utf-8")
                                ).hexdigest()[:16]
                d = os.path.join(config.DATA_DIR, "tts")
                os.makedirs(d, exist_ok=True)
                path = os.path.join(d, f"e_{h}.mp3")
                t0 = time.time()
                if not (os.path.exists(path) and os.path.getsize(path) > 2000):
                    tmp = path + ".tmp"

                    async def _gen():
                        c = edge_tts.Communicate(
                            spoken, edge_voice,
                            rate=config.TTS_RATE, pitch=config.TTS_PITCH)
                        await c.save(tmp)

                    asyncio.run(_gen())
                    os.replace(tmp, path)
                    print(f"  [出声] 微软 {edge_voice} 合成 {len(text)} 字 "
                          f"{time.time()-t0:.1f}s", flush=True)
                with open(path, "rb") as f:
                    mp3 = f.read()
                self._tts_tag = "edge:" + str(edge_voice)
                return self._send(200, mp3, ctype="audio/mpeg")
            except Exception as e:                        # noqa: BLE001
                print(f"  [出声] edge 失败，退回 piper："
                      f"{type(e).__name__}: {e}", flush=True)

        # 2) 兜底：本机 piper（离线，保底能出声）
        try:
            import hashlib
            import wave

            from . import voice as _voice
            if not _voice.piper_available():
                return self._send(503, json.dumps(
                    {"error": "没有可用的语音合成"},
                    ensure_ascii=False).encode())
            # 同一句话不重复合成：按内容哈希缓存
            h = hashlib.md5(text.encode("utf-8")).hexdigest()[:16]
            d = os.path.join(config.DATA_DIR, "tts")
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"s_{h}.wav")
            t0 = time.time()
            if not (os.path.exists(path) and os.path.getsize(path) > 2000):
                v = _voice._load_piper()
                tmp = path + ".tmp"
                with wave.open(tmp, "wb") as wf:
                    v.synthesize_wav(text, wf)
                os.replace(tmp, path)
                print(f"  [出声] piper 合成 {len(text)} 字 -> "
                      f"{os.path.getsize(path)} 字节 "
                      f"{time.time()-t0:.1f}s", flush=True)
            with open(path, "rb") as f:
                wav = f.read()
            self._tts_tag = "piper"
            return self._send(200, wav, ctype="audio/wav")
        except Exception as e:                            # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._send(500, json.dumps(
                {"error": f"{type(e).__name__}: {e}"},
                ensure_ascii=False).encode())

    # ---------- 听：手机录的 WAV 传上来识别 ----------
    def _handle_sasr(self):
        """流式识别：start / chunk / end / abort。

        chunk 的 body 是裸 PCM（16k、16bit、单声道），不是 JSON，
        所以这里自己读 body，不能走 _body()。
        """
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        op = (q.get("op") or ["start"])[0]
        sid = (q.get("sid") or [""])[0]

        def out(obj, code=200):
            return self._send(code, json.dumps(obj, ensure_ascii=False)
                              .encode("utf-8"))

        try:
            from . import sasr as _sasr
        except Exception as e:                            # noqa: BLE001
            return out({"error": f"流式识别模块加载失败: {e}"}, 500)

        if not _sasr.available():
            return out({"error": "没有火山凭据，流式识别用不了"}, 503)

        try:
            if op == "start":
                n = int(self.headers.get("Content-Length") or 0)
                cfg = {}
                if n > 0:
                    try:
                        cfg = json.loads(self.rfile.read(n).decode("utf-8"))
                    except Exception:                     # noqa: BLE001
                        cfg = {}
                rate = int(cfg.get("rate") or 16000)
                return out({"sid": _sasr.start(rate=rate)})

            if op == "chunk":
                n = int(self.headers.get("Content-Length") or 0)
                pcm = self.rfile.read(n) if n > 0 else b""
                if not sid:
                    return out({"error": "缺 sid"}, 400)
                text = _sasr.chunk(sid, pcm)
                return out({"text": text})

            if op == "end":
                if not sid:
                    return out({"error": "缺 sid"}, 400)
                return out({"text": _sasr.end(sid)})

            if op == "abort":
                _sasr.abort(sid)
                return out({"ok": True})

            return out({"error": f"不认识的 op: {op}"}, 400)
        except KeyError as e:
            return out({"error": str(e)}, 404)
        except Exception as e:                            # noqa: BLE001
            return out({"error": f"{type(e).__name__}: {e}"}, 500)

    def _handle_asr(self):
        """单独一条路：收二进制 WAV，不走 JSON 解析。"""
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 44:
            return self._send(400, b'{"error":"no audio"}')
        try:
            raw = self.rfile.read(n)
        except Exception as e:                        # noqa: BLE001
            return self._send(400, json.dumps(
                {"error": f"读请求体失败: {e}"}).encode("utf-8"))
        try:
            import io
            import wave
            import numpy as np

            sr = None
            a = None
            if raw[:4] == b"RIFF":
                with wave.open(io.BytesIO(raw), "rb") as w:
                    sr = w.getframerate()
                    ch = w.getnchannels()
                    sw = w.getsampwidth()
                    frames = w.readframes(w.getnframes())
                if sw == 2:
                    a = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                elif sw == 4:
                    a = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
                else:
                    a = np.frombuffer(frames, dtype=np.uint8).astype(np.float32) / 128.0 - 1.0
                if ch > 1:
                    a = a.reshape(-1, ch).mean(axis=1)
            if a is None:
                # 手机用 MediaRecorder 录音了：iOS 给 mp4/aac，别的给 webm/opus。
                # 统一用 PyAV 解成 16k 单声道，后面跟 WAV 走同一条路。
                from . import loud as _loud
                pcm, sr = _loud.decode_pcm16(raw, 16000)
                a = pcm.astype(np.float32) / 32768.0
                ch, sw = 1, 2
                print(f"  [听] 收到非 WAV（{(self.headers.get('Content-Type') or '?')}）"
                      f" 解出 {a.size / sr:.2f}s", flush=True)

            # 存一份**解码后的标准 WAV**（16k 单声道）—— 手机传的可能是
            # mp4/webm，存原文回放不了，存解码后的才方便对比调参。
            saved = None
            if config.SAVE_RECORDINGS:
                try:
                    os.makedirs(config.RECORD_DIR, exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
                    saved = os.path.join(config.RECORD_DIR, f"{stamp}.wav")
                    pcm16 = np.clip(a * 32767.0, -32768, 32767).astype("<i2")
                    with wave.open(saved, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(int(sr))
                        wf.writeframes(pcm16.tobytes())
                except Exception:
                    saved = None

            # 掐掉首尾静音再识别。
            # 实测：手机那条 6.61 秒的录音，前面 2.4 秒全是静音，
            # paraformer 在静音上"编"了两个字（识别结果开头多出「清空」）。
            # 掐掉静音 + 归一化电平，这类开头幻觉基本就没了。
            a_for_asr = a
            try:
                fw2 = max(1, int(sr * 0.02))
                nfr2 = a.size // fw2
                if nfr2 > 4:
                    fr2 = np.sqrt((a[:nfr2 * fw2].reshape(nfr2, fw2)
                                   .astype(np.float64) ** 2).mean(axis=1))
                    thr = max(0.03, float(fr2.max()) * 0.15)
                    keep = fr2 > thr
                    if keep.any():
                        i0 = max(0, int(np.argmax(keep)) - 5)
                        i1 = min(nfr2, nfr2 - int(np.argmax(keep[::-1])) + 5)
                        a_for_asr = a[i0 * fw2:i1 * fw2]
                        cut0 = i0 * fw2 / float(sr)
                        cut1 = (a.size - i1 * fw2) / float(sr)
                        if cut0 > 0.15 or cut1 > 0.15:
                            print(f"  [听] 掐静音：前 {cut0:.2f}s / 后 {cut1:.2f}s",
                                  flush=True)
                pk2 = float(np.max(np.abs(a_for_asr))) if a_for_asr.size else 0.0
                if pk2 > 0.0005:
                    # 手机这条路上的电平低得离谱（实测峰值只有 0.005 左右，
                    # 约 -46 dBFS），必须放开归一化上限才能进识别。
                    # 原来卡 8 倍，等于几乎没抬，白白吃亏。
                    a_for_asr = a_for_asr * min(200.0, 0.9 / pk2)
            except Exception as e:                        # noqa: BLE001
                print(f"  [听] 掐静音失败（不影响识别）：{e}", flush=True)
                a_for_asr = a

            peak_in = int(np.abs(a).max() * 32767) if a.size else 0
            rms_in = float(np.sqrt((a.astype(np.float64) ** 2).mean())) if a.size else 0.0
            # 先把「最响 / 最静」100ms 帧算出来 —— 后面所有闸门都要用它，
            # 而且手机端要靠 loudest 分辨"人说话轻"还是"麦克风是哑的"。
            loudest = quietest = 0.0
            if a.size:
                fw = max(1, int(sr * 0.1))
                nfr = a.size // fw
                if nfr >= 1:
                    blk = a[:nfr * fw].reshape(nfr, fw).astype(np.float64)
                    frms = np.sqrt((blk ** 2).mean(axis=1))
                    loudest = float(frms.max())
                    quietest = float(frms.min())

            # 静音闸：只挡真正的静音（全 0 或只有底噪）。
            # 门槛一降再降的教训：手机这条路的电平极低，实测几条正常说话的
            # 录音整体 rms 只有 0.0015~0.0025，最响帧才 0.004 —— 而 ASR
            # 归一化之后照样能一字不差认出来。用 0.002 去卡，等于每句都
            # 被自己拦掉（"越改越差"就是这么来的）。
            if rms_in < 0.0003:
                return self._send(200, json.dumps({
                    "text": "", "peak": peak_in, "rms": round(rms_in, 4),
                    "loudest": round(loudest, 4),
                    "quietest": round(quietest, 4),
                    "reason": f"太安静了（音量 {rms_in:.4f}），没识别",
                    "engine": config.ASR_ENGINE,
                }, ensure_ascii=False).encode("utf-8"))

            # 背景声判定：只保留"起伏"这一条（说话有停顿，稳态噪声没有）。
            # 原来那条「最响必须 ≥ 0.015」撤掉了 —— 它把上面那种
            # rms 0.0046 的正常说话也误杀了。
            if loudest and loudest < quietest * 2.0:
                return self._send(200, json.dumps({
                    "text": "", "peak": peak_in, "rms": round(rms_in, 4),
                    "loudest": round(loudest, 4),
                    "quietest": round(quietest, 4),
                    "reason": (f"这段像背景声（最响 {loudest:.4f} / 最静 "
                               f"{quietest:.4f}，起伏不够）—— 电视关小再说"),
                    "engine": config.ASR_ENGINE,
                }, ensure_ascii=False).encode("utf-8"))

            # 识别：优先本机引擎（桌面上有 paraformer）。
            # 手机上没打包本地模型，就退到火山的大模型 ASR ——
            # 都是同一段 WAV，先落盘再交给云端。
            raw_text = ""
            engine_used = config.ASR_ENGINE
            try:
                from . import asr as _asr
                from . import asrconf as _ac2
                if _ac2.load().get("engine") == "volcano":
                    raise RuntimeError("按设置走火山")
                # sherpa-onnx 的 recognizer **不是线程安全的**：服务端是
                # ThreadingHTTPServer，两句话挨得近、上一个还在识别时又来一条，
                # 就会抛 RuntimeError（日志里那条「本地 ASR 不可用」）。
                # 串行化一下，识别本来就只有几百毫秒。
                with _ASR_LOCK:
                    raw_text = _asr.get_engine().transcribe(a_for_asr, sr)
            except Exception as e:                        # noqa: BLE001
                print(f"  [听] 本地 ASR 不可用（{type(e).__name__}: {e}），走火山",
                      flush=True)
                try:
                    from . import doubaovoice as _dv
                    if saved and _dv.asr_available():
                        txt, why = _dv.asr_file(saved)
                        if txt:
                            raw_text = txt
                            engine_used = "volcano"
                        else:
                            print(f"  [听] 火山 ASR 失败：{why}", flush=True)
                    else:
                        print("  [听] 火山凭据没配，识别不了", flush=True)
                except Exception as e2:                    # noqa: BLE001
                    print(f"  [听] 火山 ASR 异常：{e2}", flush=True)

            # 领域词纠正：通用模型不认识「器灵」这类词，
            # 实测听成 赤菱/适龄/气零/志玲 等，短句必错。
            text, hits = raw_text, {}
            try:
                from . import asrfix as _fix
                text, hits = _fix.report(raw_text)
            except Exception:
                pass
            out = {"text": text or "", "peak": peak_in,
                   "seconds": round(a.size / float(sr), 2),
                   "engine": engine_used,
                   "saved": os.path.basename(saved) if saved else None}
            if hits:
                out["raw"] = raw_text
                out["fixed"] = hits
            return self._send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"))
        except Exception as e:                        # noqa: BLE001
            import traceback
            log = os.path.join(config.DATA_DIR, "logs", "asr_error.log")
            try:
                with open(log, "a", encoding="utf-8") as f:
                    f.write("\n" + "=" * 60 + "\n" + traceback.format_exc())
            except Exception:
                pass
            return self._send(500, json.dumps(
                {"error": f"{type(e).__name__}: {e}"},
                ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        # 网页界面和图标不要求 token（安卓加主屏幕时带不了）
        if path == "/clicktest":
            from . import clicktest as _ct
            tok = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
            body = _ct.page(tok).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return True

        # ---------------- 光团设置 ----------------
        if path == "/orb":
            from . import orbsettings as _os
            return self._send(200, json.dumps(_os.load()).encode("utf-8"))
        # ---------------- 识别引擎设置 ----------------
        if path == "/asrconf":
            from . import asrconf as _ac
            return self._send(200, json.dumps(_ac.load()).encode("utf-8"))

        # 光团备选对比页（主人要看以前做过的几版）
        if path == "/orbdemo":
            from . import orbdemo as _d
            body = _d.DEMO.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return True

        # 对比页要加载的 WebGL 光团
        if path == "/orbwebgl.js":
            from . import orbwebgl as _o, orbdemo as _d
            body = _d.webgl_for_demo(_o.JS).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return True

        # ---------------- 热重载（只管网页那部分）----------------
        # 改完 webui.py / orbwebgl.py 只要访问一次 /reload 就生效，
        # 不用重启服务、更不会让主人一直看旧页面。
        if path == "/reload":
            import importlib
            from . import webui as _w, orbwebgl as _o
            importlib.reload(_o)
            importlib.reload(_w)
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return True

        if path in ("/", "/index.html"):
            # 网页绝对不能缓存：改了界面以后，手机拿着一份旧 HTML
            # 会让人以为什么都没变。这个坑踩过好几次。
            body = webui.page_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control",
                             "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)
            return True
        if path == "/manifest.webmanifest":
            return self._send(200, webui.manifest_bytes(),
                              "application/manifest+json; charset=utf-8")
        if path == "/icon.png":
            return self._send(200, webui.icon_png(192), "image/png")

        # ---------------- 把模型发给手机 ----------------
        # 手机第一次开识别时来这里拉 paraformer.onnx（232 MB）。
        # 只发 data/asr 里的文件，别的、以及带 ../ 的一概不给。
        if path.startswith("/model/"):
            name = urllib.parse.unquote(path[len("/model/"):])
            ok_name = (name and "/" not in name and "\\" not in name
                       and not name.startswith("."))
            if not ok_name:
                return self._send(400, b'{"error":"bad name"}')
            full = os.path.join(config.ASR_DIR, name)
            if not os.path.isfile(full):
                return self._send(404, b'{"error":"not found"}')
            size = os.path.getsize(full)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                with open(full, "rb") as f:
                    while True:
                        chunk = f.read(1 << 20)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                print("  [model] 手机那边断开了", flush=True)
            return True

        # 诊断用：把固定一句话合成好的 WAV 直接丢给浏览器。
        # 用来分清「破裂声」是文件里本来就有的，还是播放链路（音频会话）
        # 弄出来的 —— 手机 Safari 直接开这个地址听，一听就知道。
        if path in ("/tts.wav", "/diag.wav"):
            try:
                from . import doubaovoice as _dv
                from . import loud as _loud
                from . import voices as _voices
                sel = _voices.load() if hasattr(_voices, "load") else {}
                spk = (sel or {}).get("id")
                d = os.path.join(config.DATA_DIR, "tts")
                os.makedirs(d, exist_ok=True)
                tmp = os.path.join(d, "diag.mp3")
                if not (os.path.exists(tmp) and os.path.getsize(tmp) > 2000):
                    ok, msg = _dv.tts(
                        "这是一段测试音频，用来检查有没有破裂声。", tmp, speaker=spk)
                    if not ok:
                        raise RuntimeError(msg)
                with open(tmp, "rb") as f:
                    wav, sr, peak = _loud.to_loud_wav(f.read(), gain=2.0)
                print(f"  [诊断] 测试音频 {sr}Hz {len(wav)}B 峰值 {peak:.3f}", flush=True)
                return self._send(200, wav, ctype="audio/wav")
            except Exception as e:                        # noqa: BLE001
                return self._send(500, json.dumps(
                    {"error": f"诊断音频生成失败: {type(e).__name__}: {e}"},
                    ensure_ascii=False).encode())

        # 把证书本身发出去 —— 手机用 Safari 打开这个地址就会出现
        # 「安装描述文件」，比从微信传 .cer 文件可靠得多
        # （微信传过去 iOS 常说不认识这种文件）。
        if path in ("/cert", "/cert.cer", "/cert.crt"):
            cer = os.path.join(config.DATA_DIR, "cert", "carheart.cer")
            crt = os.path.join(config.DATA_DIR, "cert", "carheart.crt")
            src, ctype = (cer, "application/x-x509-ca-cert")
            if not os.path.exists(src):
                src, ctype = (crt, "application/x-pem-file")
            if not os.path.exists(src):
                return self._send(404, b'{"error":"no cert, run tools/make_cert.py"}')
            with open(src, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition",
                             'attachment; filename="carheart.cer"')
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            return self.wfile.write(data)

        # 更可靠的装证书入口：走 .mobileconfig 描述文件。
        # iOS 对「Safari 直接下裸 .cer」时好时坏（常常只把文件存进「文件」App，
        # 根本不弹安装），而 application/x-apple-aspen-config 是苹果钦定的入口，
        # Safari 一定会弹「此网站正尝试下载配置描述文件」。
        # 描述文件由 tools/make_profile.py 生成，PayloadType=com.apple.security.root，
        # 只有「根证书」装完才会出现在 证书信任设置 里。
        if path in ("/profile", "/ca.mobileconfig"):
            prof = os.path.join(config.DATA_DIR, "cert", "carheart.mobileconfig")
            if not os.path.exists(prof):
                return self._send(
                    404, b'{"error":"no profile, run tools/make_profile.py"}')
            with open(prof, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-apple-aspen-config")
            self.send_header("Content-Disposition",
                             'attachment; filename="carheart.mobileconfig"')
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            return self.wfile.write(data)

        if not self._auth_ok():
            return self._send(401, b'{"error":"token"}')

        if path == "/api":
            doc = {
                "name": "carheart",
                "what": "心石计划的服务层。记忆与人格都在这台机器上。",
                "endpoints": ["/", "/health", "/self", "/memories",
                              "/chat", "/asr", "/teach", "/remember",
                              "/forget", "/body"],
            }
            return self._send(200, json.dumps(doc, ensure_ascii=False).encode("utf-8"))

        if path == "/health":
            conn = db.connect()
            s = db.stats(conn)
            # 他教过它多少东西 —— 手机界面上要能看见，不然没法判断「养成有没有生效」
            taught = {}
            for k in ("name", "call", "setup", "style", "about"):
                taught[k] = len(db.all_facts(conn, prefix=f"{k}:"))
            info = {
                "ok": True,
                # 注意：这里必须报**当前真正在用的脑子**，不是本地那个。
                # 之前写死 config.MODELS["chat"]，于是切到云端之后
                # 页面顶部还一直显示 27B —— 主人当场以为我骗他。
                "model": _active_model(),
                "model_where": _active_where(),
                "local_model": config.MODELS["chat"],
                "ollama": config.OLLAMA_HOST,
                "memories": s["memories"],
                "turns": s["turns"],
                "taught": taught,
                "taught_total": sum(taught.values()),
                "engine_asr": config.ASR_ENGINE,
                "time": datetime.now().isoformat(timespec="seconds"),
                # 实况：上一次真正用的脑子（不是配置）
                "last_actual": (brain.read_actual_route() or {}),
            }
            try:
                from . import learn as _learn
                info["learning"] = _learn.EXTRACTOR.status()
                info["learning"]["waiting"] = len(info["learning"]["waiting"])
            except Exception:
                pass
            return self._send(200, json.dumps(info, ensure_ascii=False).encode("utf-8"))

        if path == "/self":
            conn = db.connect()
            db.ensure_bodies(conn)
            q = urllib.parse.parse_qs(parsed.query)
            body_id = q.get("body", [config.DEFAULT_BODY])[0]
            out = {"self": persona.self_description(conn, body_id)}
            return self._send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"))

        # ---------------- 识别校对：看录音、填正确答案 ----------------
        if path == "/clips":
            q = urllib.parse.parse_qs(parsed.query)
            limit = int(q.get("limit", ["40"])[0])
            return self._send(200, json.dumps(
                __import_asrlab().load_clips(limit), ensure_ascii=False).encode("utf-8"))

        if path.startswith("/clip/"):
            name = os.path.basename(urllib.parse.unquote(path[6:]))
            fp = os.path.join(config.RECORD_DIR, name)
            if not os.path.isfile(fp) or not name.endswith(".wav"):
                return self._send(404, b'{"error":"no such clip"}')
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            return self.wfile.write(data)

        if path == "/lab":
            return self._send(200, __import_asrlab().page_bytes(),
                              "text/html; charset=utf-8")

        # 说话长短等可调项
        if path == "/settings":
            from . import settings as _settings
            out = {
                "ok": True,
                "reply_length": _settings.load(),
                "max_tokens": _settings.max_tokens(),
                "hint": _settings.hint(),
                "levels": _settings.all_levels(),
            }
            return self._send(200, json.dumps(
                out, ensure_ascii=False).encode("utf-8"))

        # 音色：列出全部、读当前、换当前
        if path == "/voice":
            from . import voices as _voices
            cur = _voices.load()
            out = {
                "ok": True,
                "current": cur,
                "current_name": _voices.current_label(),
                "voices": _voices.list_voices(),
                "volc_ready": False,
            }
            try:
                from . import doubaovoice as _dv
                out["volc_ready"] = _dv.available()
            except Exception:
                pass
            return self._send(200, json.dumps(
                out, ensure_ascii=False).encode("utf-8"))

        # 问云端要它自己真实的模型列表 —— 不要相信写死的名字
        if path == "/cloudmodels":
            from . import cloud
            ok, res = cloud.list_remote_models()
            out = {"ok": ok, "models": res if ok else [], "error": None if ok else res}
            return self._send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"))

        # 脑子状态：本地还是云端。给界面上的开关用。
        if path == "/mind":
            from . import cloud
            c = cloud.load_conf()
            # 本机有哪些模型可以选（给界面下拉用）
            local_models = []
            try:
                local_models = llm.list_models() or []
            except Exception:
                pass
            info = {
                "status": cloud.status(),
                "mode": c.get("mode", "local"),
                "available": cloud.available(),
                "base_url": c.get("base_url", ""),
                "model": c.get("model", ""),
                "has_key": bool(c.get("api_key", "").strip()),
                "local_model": config.MODELS["chat"],
                "local_models": local_models,
                "presets": cloud.PRESETS,
                "preset": c.get("preset", ""),
                "think": config.THINK_LEVEL,
                "think_levels": config.THINK_LEVELS,
            }
            return self._send(200, json.dumps(info, ensure_ascii=False).encode("utf-8"))

        if path == "/memories":
            conn = db.connect()
            db.ensure_bodies(conn)
            q = urllib.parse.parse_qs(parsed.query)
            limit = int(q.get("limit", ["50"])[0])
            body_id = q.get("body", [None])[0]
            sql = ("SELECT id,t_start,body_id,kind,provenance,salience,text "
                   "FROM memories")
            args = []
            if body_id:
                sql += " WHERE body_id=?"
                args.append(body_id)
            sql += " ORDER BY t_start DESC LIMIT ?"
            args.append(limit)
            rows = [dict(r) for r in conn.execute(sql, tuple(args))]
            return self._send(200, json.dumps({"memories": rows},
                                              ensure_ascii=False).encode("utf-8"))

        # ---------------- 看它被教过什么（手机点右上角就是走这里）----------------
        if path == "/facts":
            conn = db.connect()
            out = {}
            for k in ("name", "call", "setup", "style", "about", "self"):
                rows = conn.execute(
                    "SELECT key,value FROM facts WHERE key LIKE ? ORDER BY key",
                    (k + ":%",)).fetchall()
                if rows:
                    out[k] = [{"key": r["key"], "text": r["value"]} for r in rows]
            return self._send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"))

        return self._send(404, b'{"error":"no such path"}')

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/orb":
            from . import orbsettings as _os
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:                             # noqa: BLE001
                body = {}
            out = _os.save(body)
            return self._send(200, json.dumps(out).encode("utf-8"))

        if path == "/asrconf":
            from . import asrconf as _ac
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception:                             # noqa: BLE001
                body = {}
            out = _ac.save(body)
            return self._send(200, json.dumps(out).encode("utf-8"))


        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        if not self._auth_ok():
            return self._send(401, b'{"error":"token"}')

        # 注意：/asr 收的是二进制音频，不能当 JSON 读。
        # 这里必须先分流，否则 _body() 会把 WAV 拿去 json.loads 而卡住。
        if path == "/asr":
            return self._handle_asr()
        # 流式识别：页面边录边传 PCM，这里转给火山，中间结果随时可取
        # 临时测试用：取最近一条录音（流式识别的自动化测试要用）
        if path == "/testwav":
            import glob as _g
            recs = sorted(_g.glob(os.path.join(config.RECORD_DIR, "*.wav")))
            if not recs:
                return self._send(404, b'{"error":"no recording"}')
            with open(recs[-1], "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)

        if path == "/sasr":
            return self._handle_sasr()
        # /tts 自己读 body 并返回音频流，也必须在 _body() 之前分流
        if path == "/tts":
            return self._handle_tts()

        data = self._body()

        # ---------------- 对话（流式 SSE）----------------
        if path == "/chat":
            text = (data.get("text") or "").strip()
            if not text:
                return self._send(400, b'{"error":"empty text"}')
            sid = data.get("session") or "default"
            h = get_heart(sid, body_id=data.get("body"))

            # 说话换音色 / 换长短：先看这句是不是命令。是的话直接回确认，
            # 不进模型 —— 省一次调用，而且响应是即时的。
            try:
                from . import settings as _settings
                from . import voices as _voices
                vmsg = _settings.apply_command(text) or _voices.apply_command(text)
                if vmsg:
                    self.send_response(200)
                    self.send_header("Content-Type",
                                     "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self._cors()
                    self.end_headers()
                    self.close_connection = True
                    self.wfile.write(sse({"t": "delta", "d": vmsg}))
                    self.wfile.write(sse({"t": "done", "text": vmsg,
                                          "voice": _voices.load()}))
                    self.wfile.flush()
                    return
            except Exception as e:                        # noqa: BLE001
                print(f"  [音色] 命令处理失败：{e}", flush=True)

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            # 关键：SSE 结束后必须关连接，否则客户端会一直等下去
            self.send_header("Connection", "close")
            self._cors()
            self.end_headers()
            self.close_connection = True

            t0 = time.time()
            first = [None]
            try:
                def on_delta(s):
                    if first[0] is None:
                        first[0] = time.time() - t0
                        self.wfile.write(sse({"t": "meta",
                                              "first_delta": round(first[0], 2)}))
                    self.wfile.write(sse({"t": "delta", "d": s}))
                    self.wfile.flush()

                # nosave=true：只试一下，不写进对话历史。
                # 我验证白纸状态时自己问了两句，结果那两句被记下来，
                # 她于是知道"刚才两个问题"——自己把白纸弄脏了。
                _nosave = bool(data.get("nosave"))
                reply = h.reply(text, stream=True, on_delta=on_delta,
                                save=not _nosave)
                st = {}
                try:
                    st = h.last_route and {
                        "tier": h.last_route[0], "model": h.last_route[1],
                        "why": h.last_route[2]} or {}
                except Exception:
                    pass
                self.wfile.write(sse({"t": "done", "text": reply,
                                      "seconds": round(time.time() - t0, 2),
                                      "route": st}))
                self.wfile.flush()
            except Exception as e:                    # noqa: BLE001
                try:
                    self.wfile.write(sse({"t": "error",
                                          "msg": f"{type(e).__name__}: {e}"}))
                    self.wfile.flush()
                except Exception:
                    pass
            return

        # ---------------- 说话长短 ----------------
        if path == "/settings":
            from . import settings as _settings
            lv = (data.get("reply_length") or "").strip()
            if lv:
                _settings.save(lv)
                print(f"  [设置] 说话长度 -> {lv}", flush=True)
            return self._send(200, json.dumps({
                "ok": True,
                "reply_length": _settings.load(),
                "max_tokens": _settings.max_tokens(),
                "hint": _settings.hint(),
                "levels": _settings.all_levels(),
            }, ensure_ascii=False).encode("utf-8"))

        # ---------------- 换音色 ----------------
        if path == "/voice":
            from . import voices as _voices
            prov = (data.get("provider") or "").strip()
            vid = (data.get("id") or "").strip()
            if not prov or not vid:
                return self._send(400, json.dumps(
                    {"error": "要 provider 和 id"}, ensure_ascii=False).encode())
            _voices.save(prov, vid)
            cur = _voices.load()
            print(f"  [音色] 切到 {_voices.current_label()} ({prov})",
                  flush=True)
            return self._send(200, json.dumps({
                "ok": True, "current": cur,
                "current_name": _voices.current_label(),
                "voices": _voices.list_voices(),
            }, ensure_ascii=False).encode("utf-8"))

        # ---------------- 切脑子（本地 / 云端）----------------
        if path == "/mind":
            from . import cloud
            mode = (data.get("mode") or "").strip()
            try:
                if mode:
                    cloud.set_mode(mode)
                c = cloud.load_conf()
                changed = False

                # 选预设：自动填地址和模型
                pid = (data.get("preset") or "").strip()
                if pid:
                    p = cloud.preset_by_id(pid)
                    if not p:
                        return self._send(400, json.dumps(
                            {"error": f"没有这个预设：{pid}"},
                            ensure_ascii=False).encode("utf-8"))
                    c["preset"] = pid
                    c["base_url"] = p["base_url"]
                    c["model"] = p["model"]
                    changed = True

                # 手填的字段优先于预设
                for field in ("base_url", "api_key", "model"):
                    v = data.get(field)
                    if isinstance(v, str) and v.strip():
                        c[field] = v.strip()
                        changed = True

                # 本地模型：单独一个字段，落到 config.MODELS['chat']。
                # 之前界面上的「模型名」只改云端模型，本地模式根本不看它 ——
                # 所以"切了模型还是 27b"。这里分开了。
                lm = (data.get("local_model") or "").strip()
                if lm:
                    config.MODELS["chat"] = lm
                    config.MODELS["deep"] = lm
                    config.MODELS["think"] = lm
                    c["local_model"] = lm
                    changed = True

                if changed:
                    cloud.save_conf(c)

                # 思考强度（不全在 cloud.json 里，也允许运行时切）
                tl = (data.get("think") or "").strip().lower()
                if tl:
                    if tl not in config.THINK_LEVELS:
                        return self._send(400, json.dumps(
                            {"error": f"think 只能是 {config.THINK_LEVELS}"},
                            ensure_ascii=False).encode("utf-8"))
                    config.THINK_LEVEL = tl
                    c2 = cloud.load_conf()
                    c2["think"] = tl
                    cloud.save_conf(c2)

                # 切到云端时，把本地模型从显存里卸掉。
                # 不卸的话它会白占 15 GB，而且看显存会以为在用本地
                # —— 主人就是因为这个以为我没切到云端。
                if mode == "cloud" and cloud.available():
                    _threading.Thread(target=_free_local_model,
                                      daemon=True).start()
                elif mode == "local":
                    _threading.Thread(target=_warm_local_model,
                                      daemon=True).start()

                # 模型换了就把「提到旧模型名」的对话和记忆清掉。
                # 不清的话她会从历史里翻出自己以前那句答案照着念，
                # 于是明明换过了还一直说旧的（今天反复出现）。
                if mode or pid or data.get("model") or data.get("local_model"):
                    try:
                        _purge_stale_model_talk()
                    except Exception:
                        pass
            except cloud.CloudError as e:
                return self._send(400, json.dumps(
                    {"error": str(e)}, ensure_ascii=False).encode("utf-8"))
            c = cloud.load_conf()
            local_models = []
            try:
                local_models = llm.list_models() or []
            except Exception:
                pass
            out = {
                "ok": True,
                "status": cloud.status(),
                "mode": c.get("mode", "local"),
                "available": cloud.available(),
                "base_url": c.get("base_url", ""),
                "model": c.get("model", ""),
                "has_key": bool(c.get("api_key", "").strip()),
                "local_model": config.MODELS["chat"],
                "local_models": local_models,
                "presets": cloud.PRESETS,
                "preset": c.get("preset", ""),
                "think": config.THINK_LEVEL,
                "think_levels": config.THINK_LEVELS,
            }
            return self._send(200, json.dumps(out, ensure_ascii=False).encode("utf-8"))

        # ---------------- 校对台：保存正确答案 ----------------
        if path == "/truth":
            items = data.get("items") or []
            n = 0
            for it in items:
                if __import_asrlab().save_truth(it.get("name", ""), it.get("text", "")):
                    n += 1
            return self._send(200, json.dumps({"saved": n},
                                              ensure_ascii=False).encode("utf-8"))

        # ---------------- 教它 ----------------
        if path == "/teach":
            kind = (data.get("kind") or "").strip()
            text = (data.get("text") or "").strip()
            if not text:
                return self._send(400, b'{"error":"empty text"}')
            kmap = {"name": "name:", "call": "call:", "setup": "setup:",
                    "style": "style:", "about": "about:"}
            if kind not in kmap:
                return self._send(400, json.dumps(
                    {"error": f"kind 必须是 {list(kmap)}"},
                    ensure_ascii=False).encode("utf-8"))
            conn = db.connect()
            k = db.add_fact(conn, text, key=f"{kmap[kind]}{db.new_id(kind)}")
            return self._send(200, json.dumps({"ok": True, "key": k},
                                              ensure_ascii=False).encode("utf-8"))

        # ---------------- 记一条记忆 ----------------
        if path == "/remember":
            text = (data.get("text") or "").strip()
            if not text:
                return self._send(400, b'{"error":"empty text"}')
            conn = db.connect()
            db.ensure_bodies(conn)
            r = db.add_memory(conn, text,
                              kind=data.get("kind") or "EVENT",
                              body_id=data.get("body") or config.DEFAULT_BODY,
                              provenance=data.get("provenance") or "experience",
                              salience=float(data.get("salience") or 0.6),
                              dedupe=True)
            return self._send(200, json.dumps({"ok": True, "result": r},
                                              ensure_ascii=False).encode("utf-8"))

        # ---------------- 换地方 / 改它住哪 ----------------
        if path == "/body":
            conn = db.connect()
            db.ensure_bodies(conn)
            bid = data.get("body") or config.DEFAULT_BODY
            for field in ("model", "character", "chapter", "year_from", "year_to"):
                if data.get(field):
                    db.set_body_field(conn, bid, field, data[field])
            b = db.get_body(conn, bid)
            return self._send(200, json.dumps(dict(b) if b else {},
                                              ensure_ascii=False).encode("utf-8"))

        # ---------------- 立刻提炼一遍（不等安静期）----------------
        if path == "/learn":
            from . import learn as _learn
            body_id = data.get("body") or config.DEFAULT_BODY
            out = _learn.EXTRACTOR.run_once(body_id)
            return self._send(200, json.dumps({"ok": True, "result": out},
                                              ensure_ascii=False).encode("utf-8"))

        # ---------------- 忘掉一条 ----------------
        if path == "/forget":
            mid = data.get("id")
            if not mid:
                return self._send(400, b'{"error":"need id"}')
            conn = db.connect()
            db.forget_memory(conn, mid)
            return self._send(200, b'{"ok":true}')

        return self._send(404, b'{"error":"no such path"}')


def local_ips():
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if "." in ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def make_https(port=None):
    """包一层 TLS。用在 --https 上。

    为什么要 https：iOS Safari 只在安全上下文里给麦克风（https 或
    localhost）。局域网 http 下 iPhone 按「按住说话」没反应。
    """
    import ssl
    crt = os.path.join(config.DATA_DIR, "cert", "carheart.crt")
    key = os.path.join(config.DATA_DIR, "cert", "carheart.key")
    if not (os.path.exists(crt) and os.path.exists(key)):
        raise FileNotFoundError(
            f"证书不存在：\n  {crt}\n  {key}\n"
            f"先生成： python tools/make_cert.py")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=crt, keyfile=key)

    # 记下每一次 TLS 握手 —— 包括失败的和证书相关的告警。
    # 手机上连不上时，这里能看到到底是握手被拒还是别的原因。
    def _msg(conn, direction, version, cipher, data):
        try:
            peer = conn.getpeername()
        except Exception:
            peer = "?"
        note = ""
        if isinstance(data, ssl.SSLCertificateError):
            note = " <== 证书相关的错"
        elif isinstance(data, ssl.SSLError):
            note = f" <== TLS 错: {data.reason}"
        print(f"  [TLS] {peer} {direction} ver={version} "
              f"cipher={cipher} {type(data).__name__}{note}", flush=True)

    try:
        ctx.set_msg_callback(_msg)
    except Exception:
        pass
    return ctx


def __import_asrlab():
    """按需加载校对台模块。

    手机上不需要它（那是给我自己校 ASR 用的），而且它依赖 numpy/本地模型。
    顶部直接 import 会让整个服务在手机上 ImportError —— 那就是闪退的原因。
    """
    from . import asrlab as _lab
    return _lab


def serve(host="0.0.0.0", port=19388, token=None, ssl_context=None,
          verbose=False):
    """起服务，阻塞。

    桌面和 Android 都用这个入口 —— Android 那边（android_boot.py）调
    serve(host="127.0.0.1", port=19388)，只监听回环，不开 TLS：
    浏览器把 localhost 当安全上下文，麦克风能直接用，不需要证书。
    """
    conn = db.connect()
    db.ensure_bodies(conn)

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.token = token
    srv.verbose = verbose
    if ssl_context is not None:
        srv.socket = ssl_context.wrap_socket(srv.socket, server_side=True)
    print(f"  [serve] http://{host}:{port}", flush=True)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="carheart.server",
                                 description="心石计划 · 服务层")
    ap.add_argument("--host", default=os.environ.get("CARHEART_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("CARHEART_PORT", "19388")))
    ap.add_argument("--token", default=os.environ.get("CARHEART_TOKEN") or None,
                    help="要求安卓端带这个 token（局域网里建议设一个）")
    ap.add_argument("--https", action="store_true",
                    help="同时开一个 https 端口（手机麦克风要用）")
    ap.add_argument("--https-port", type=int,
                    default=int(os.environ.get("CARHEART_HTTPS_PORT", "19443")))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    # 界面里调过的「思考强度」和「本地模型」存在 cloud.json，启动时读回来
    try:
        from . import cloud as _cloud
        _c = _cloud.load_conf()
        if _c.get("think") in config.THINK_LEVELS:
            config.THINK_LEVEL = _c["think"]
        if _c.get("local_model"):
            config.MODELS["chat"] = _c["local_model"]
            config.MODELS["deep"] = _c["local_model"]
            config.MODELS["think"] = _c["local_model"]
    except Exception:
        pass

    conn = db.connect()
    db.ensure_bodies(conn)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.token = args.token
    srv.verbose = args.verbose

    srv_tls = None
    tls_err = None
    if args.https:
        try:
            srv_tls = ThreadingHTTPServer((args.host, args.https_port), Handler)
            srv_tls.token = args.token
            srv_tls.verbose = args.verbose
            srv_tls.socket = make_https().wrap_socket(
                srv_tls.socket, server_side=True)
        except Exception as e:                        # noqa: BLE001
            tls_err = e
            srv_tls = None

    print("=" * 62)
    print("  心石计划 · 服务层")
    print("=" * 62)
    print(f"  模型      : {config.MODELS['chat']}  （{config.OLLAMA_HOST}）")
    print(f"  记忆      : {db.stats(conn)['memories']} 条")
    print(f"  识别引擎  : {config.ASR_ENGINE}")
    if args.token:
        print(f"  token     : 已启用")
    else:
        print(f"  token     : 没设（局域网里任何人都能连）")
    print()
    print("  手机 / 车里那块屏，连这些地址：")
    ips = local_ips()
    for ip in ips:
        q = f"?token={args.token}" if args.token else ""
        print(f"      http://{ip}:{args.port}{q}")
    if args.https:
        if srv_tls:
            print()
            print("  麦克风要用的 https（iPhone 只有这个能给麦克风）：")
            for ip in ips:
                q = f"?token={args.token}" if args.token else ""
                print(f"      https://{ip}:{args.https_port}{q}")
            print("      （会提示证书不受信任 —— 先点继续；")
            print("        要彻底没警告就把 data/cert/carheart.cer 装到手机并信任）")
        else:
            print()
            print(f"  [!] https 起不来：{tls_err}")
            print(f"      先生成证书： python tools/make_cert.py")
    print(f"      http://127.0.0.1:{args.port}   （本机）")
    print()
    print("  接口：")
    print("      GET  /health              看它活着没、什么模型、几条记忆")
    print("      GET  /self                它的自我陈述")
    print("      GET  /memories?limit=50   它记得什么")
    print("      POST /chat                {text, session}  → 流式 SSE")
    print("      POST /asr                 音频 WAV 上来识别")
    print("      POST /teach               {kind, text}  name/call/setup/style/about")
    print("      POST /remember            {text, salience}")
    print("      POST /forget              {id}")
    print("      POST /body                {model, character, ...}")
    print()
    print("  Ctrl+C 停止")
    print("=" * 62)

    if srv_tls:
        t = threading.Thread(target=srv_tls.serve_forever, daemon=True)
        t.start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  停了")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
