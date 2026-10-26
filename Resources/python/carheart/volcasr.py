# -*- coding: utf-8 -*-
"""火山「录音文件识别大模型」(seedasr.auc) —— 真正能被主人账号用的那套。

为什么不是流式（sauc）：
  实测 sauc 各种头都返回
    [resource_id=volc.bigasr.sauc.duration] requested resource not granted
  主人的账号开通的是**录音文件识别大模型**，资源 id 是 volc.seedasr.auc。

鉴权用主人的 api-key 直接当 x-api-key —— 实测可行：
  x-api-key: 68b0...
  X-Api-Resource-Id: volc.seedasr.auc

流程：先 submit 提交，再 query 取结果（同一个 X-Api-Request-Id）。
整段录音离线识别，最长支持几分钟，适合手机录一段再传上来。
"""
import base64
import json
import time
import uuid

import requests

from . import config, doubaovoice

SUBMIT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
QUERY = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
RES_ID = "volc.seedasr.auc"

# 实测第一次偶尔会 45000030（瞬时未授权），重试一次就好
RETRIES = 3
POLLS = 25


def _key():
    c = doubaovoice.load_conf()
    # 用 TTS 那把 api-key 就行；也允许单独覆盖
    return c.get("asr_api_key") or c.get("access_token") or ""


def available():
    return bool(_key())


def _headers(rid):
    return {
        "Content-Type": "application/json",
        "x-api-key": _key(),
        "X-Api-Resource-Id": RES_ID,
        "X-Api-Request-Id": rid,
        "X-Api-Sequence": "-1",
    }


def transcribe(path, rate=16000, timeout_s=35):
    """整段音频转文字。返回 (text, why)。"""
    if not available():
        return None, "火山 ASR 没配 key"

    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    body = {
        "user": {"uid": "carheart"},
        "audio": {"data": b64, "format": "wav", "codec": "raw",
                  "rate": rate, "bits": 16, "channel": 1},
        "request": {"model_name": "bigmodel", "enable_itn": True,
                    "enable_punc": False, "enable_ddc": False,
                    "enable_speaker_info": False,
                    "enable_channel_split": False,
                    "show_utterances": False, "vad_segment": False,
                    "sensitive_words_filter": ""},
    }

    last_err = ""
    for attempt in range(RETRIES):
        rid = str(uuid.uuid4())
        try:
            r = requests.post(SUBMIT, headers=_headers(rid),
                              data=json.dumps(body).encode(), timeout=30)
        except Exception as e:                            # noqa: BLE001
            last_err = f"submit 异常 {type(e).__name__}"
            time.sleep(0.6)
            continue

        code = r.headers.get("X-Api-Status-Code") or ""
        msg = r.headers.get("X-Api-Message") or ""
        if r.status_code != 200 or code not in ("20000000", "20000003"):
            last_err = f"submit {r.status_code} {code} {msg}"
            # 45000030 = 瞬时未授权，等一下重试
            if code == "45000030" or r.status_code == 403:
                time.sleep(0.8)
                continue
            return None, last_err

        # 轮询结果
        for _ in range(POLLS):
            time.sleep(1.0)
            try:
                q = requests.post(QUERY, headers=_headers(rid),
                                  data=b"{}", timeout=30)
            except Exception as e:                        # noqa: BLE001
                last_err = f"query 异常 {type(e).__name__}"
                continue

            qc = q.headers.get("X-Api-Status-Code") or ""
            qm = q.headers.get("X-Api-Message") or ""
            if qc in ("20000000", "20000003"):
                try:
                    j = q.json()
                except Exception:                         # noqa: BLE001
                    return None, "结果不是 JSON"
                res = j.get("result") or {}
                txt = ""
                if isinstance(res, dict):
                    txt = res.get("text") or ""
                    if not txt:
                        utts = res.get("utterances") or []
                        txt = "".join(u.get("text", "") for u in utts)
                return (txt or "").strip(), "seedasr"
            if qc and (qc.startswith("45") or qc.startswith("4")):
                last_err = f"query {qc} {qm}"
                break
            # 20000001/20000002 = 处理中，继续等
        else:
            last_err = last_err or "等待超时"

        if attempt + 1 < RETRIES:
            time.sleep(0.8)

    return None, last_err or "未知失败"
