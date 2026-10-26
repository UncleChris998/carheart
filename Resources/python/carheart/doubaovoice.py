# -*- coding: utf-8 -*-
"""火山引擎豆包语音：TTS（seed-tts-2.0）+ ASR。

主人指定音色：
  「甜美桃子」= zh_female_tianmeitaozi_uranus_bigtts

鉴权踩过的坑（很重要，别再走回头路）：
  seed-tts-2.0 的 v3 接口用的是 **X-Api-Key 头**，不是 Authorization：
      X-Api-App-Id      AppID（形如 api-key-2026...，**不是纯数字**）
      X-Api-Key         密钥（UUID 形式）
      X-Api-Resource-Id seed-tts-2.0
      X-Api-App-Key     aGjiRDfUWi（固定值）
  把密钥塞进 Authorization: Bearer;xxx 会一直报
      「401 load grant: requested grant not found」
  看起来像 key 无效，其实是位置错了。这个坑绕了很久。

  两代接口也不一样：
      uranus / ICL_uranus -> seed-tts-2.0 的 v3 unidirectional
      *_mars_bigtts       -> 1.0 资源 volc.service_type.10029（本账号没开通，403）

响应是 JSON 行，每行可能带一段 base64 音频，要拼起来。
"""
import base64
import json
import os
import uuid

from . import config

CONF_PATH = os.path.join(config.DATA_DIR, "volc.json")

DEFAULT_SPEAKER = "zh_female_tianmeitaozi_uranus_bigtts"
RESOURCE_ID_2 = "seed-tts-2.0"
FIXED_APP_KEY = "aGjiRDfUWi"
V3_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"

SPEAKERS = [
    ("zh_female_tianmeitaozi_uranus_bigtts", "甜美桃子"),
    ("zh_female_tianmeixiaoyuan_uranus_bigtts", "甜美小源"),
    ("ICL_uranus_zh_female_tianmeijiaoqiao_tob", "甜美娇俏"),
    ("ICL_uranus_zh_female_ruanmengtangtang_tob", "软萌糖糖"),
    ("ICL_uranus_zh_female_chuliannvyou_tob", "初恋女友"),
    ("zh_female_linjianvhai_uranus_bigtts", "邻家女孩"),
    ("zh_female_mengyatou_uranus_bigtts", "萌丫头"),
    ("zh_female_qiaopinv_uranus_bigtts", "俏皮女声"),
    ("ICL_uranus_zh_female_keainvsheng_tob", "可爱女生"),
    ("ICL_uranus_zh_female_tiexinnvyou_tob", "贴心女友"),
    ("zh_female_vv_uranus_bigtts", "Vivi"),
    ("zh_female_qingxinnvsheng_uranus_bigtts", "清新女声"),
    ("zh_female_tianmeiyueyue_uranus_bigtts", "甜美悦悦"),
]


def load_conf():
    c = {"app_id": "", "access_token": "", "speaker": DEFAULT_SPEAKER}
    try:
        if os.path.exists(CONF_PATH):
            with open(CONF_PATH, encoding="utf-8") as f:
                c.update(json.load(f) or {})
    except Exception:
        pass
    c["app_id"] = (os.environ.get("VOLCENGINE_APP_ID")
                   or os.environ.get("DOUBAO_APP_ID")
                   or c.get("app_id") or "").strip()
    c["access_token"] = (os.environ.get("VOLCENGINE_ACCESS_TOKEN")
                         or os.environ.get("DOUBAO_ACCESS_TOKEN")
                         or c.get("access_token") or "").strip()
    if not c.get("speaker"):
        c["speaker"] = DEFAULT_SPEAKER
    return c


def save_conf(conf):
    os.makedirs(os.path.dirname(CONF_PATH), exist_ok=True)
    tmp = CONF_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CONF_PATH)


def available():
    try:
        c = load_conf()
        return bool(c.get("app_id") and c.get("access_token"))
    except Exception:
        return False


def status():
    if not available():
        return "火山语音：未配置"
    c = load_conf()
    return f"火山语音：已配置（{c.get('speaker')}）"


def tts(text, out_path, speaker=None, speed=1.0):
    """合成一段语音到 out_path。返回 (是否成功, 说明)。"""
    if not available():
        return False, "火山凭据没配"
    c = load_conf()
    spk = speaker or c.get("speaker") or DEFAULT_SPEAKER
    headers = {
        "Content-Type": "application/json",
        "X-Api-App-Id": c["app_id"],
        "X-Api-Key": c["access_token"],
        "X-Api-Resource-Id": c.get("resource_id") or RESOURCE_ID_2,
        "X-Api-App-Key": FIXED_APP_KEY,
    }
    audio_params = {"format": "mp3", "sample_rate": 24000}
    # 语速/情绪：主人定的台腔调法，只用接口真有的字段。
    # speech_rate 是**百分比增量**，-18 表示 0.82 倍速。
    rate = speed if speed is not None else None
    if rate is None:
        audio_params["speech_rate"] = int(
            getattr(config, "TTS_SPEECH_RATE", -18))
    elif abs(float(rate) - 1.0) > 1e-6:
        audio_params["speech_rate"] = int((float(rate) - 1.0) * 100)
    emo = getattr(config, "TTS_EMOTION", "") or ""
    if emo:
        audio_params["emotion"] = emo
        audio_params["emotion_scale"] = int(
            getattr(config, "TTS_EMOTION_SCALE", 3))
    dialect = getattr(config, "TTS_DIALECT", "") or ""
    if dialect:
        # 官方只有 dongbei / shaanxi / sichuan，没有台湾；
        # 开着反而跑味，默认空着。
        body_extra = {"additions": json.dumps(
            {"explicit_dialect": dialect}, ensure_ascii=False)}
    else:
        body_extra = {}
    body = {
        "user": {"uid": "carheart"},
        "req_params": {
            "text": text,
            "speaker": spk,
            "audio_params": audio_params,
        },
    }
    body["req_params"].update(body_extra)
    try:
        import requests
        r = requests.post(V3_URL, headers=headers,
                          data=json.dumps(body, ensure_ascii=False).encode(),
                          timeout=90)
    except Exception as e:                                # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code} {r.content[:150]!r}"
    chunks = []
    for line in r.content.decode("utf-8", "ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("data"):
            try:
                chunks.append(base64.b64decode(o["data"]))
            except Exception:
                pass
    if not chunks:
        return False, f"没拿到音频：{r.content[:150]!r}"
    audio = b"".join(chunks)
    if len(audio) < 1000:
        return False, f"音频太小 {len(audio)} 字节"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(audio)
    os.replace(tmp, out_path)
    return True, f"豆包 {spk}"


def asr_available():
    """火山语音识别能用吗 —— 实际走 seedasr（录音文件识别大模型）。

    注意：TTS 那把 api-key 同时也能当 ASR 的 x-api-key 用（实测可行）。
    流式接口(sauc)对这套凭据一直 403，所以不要走流式。
    """
    from . import volcasr
    return volcasr.available()


def asr_file(path, conf=None):
    """整段音频转文字，走火山「录音文件识别大模型」(seedasr.auc)。

    为什么不用流式：sauc 系列资源对这套凭据一直返回
      403 [resource_id=volc.bigasr.sauc.duration] requested resource not granted
    主人账号开通的是录音文件识别，资源 id = volc.seedasr.auc。
    """
    from . import volcasr
    if not volcasr.available():
        return None, "火山 ASR 没配 key"
    return volcasr.transcribe(path)

