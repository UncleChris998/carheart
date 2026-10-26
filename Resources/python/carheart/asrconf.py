# -*- coding: utf-8 -*-
"""识别引擎设置：本地模型（免费，默认）/ 火山流式（联网）。

主人要求：默认走本地免费识别，火山的可以后台切换。

存储：data/asr.json
  {"engine": "local"}
"""
import json
import os

from . import config

PATH = os.path.join(config.DATA_DIR, "asr.json")

DEFAULTS = {"engine": "local"}       # local | volcano
CHOICES = ("local", "volcano")


def load():
    d = dict(DEFAULTS)
    try:
        with open(PATH, encoding="utf-8") as f:
            d.update(json.load(f))
    except Exception:                                     # noqa: BLE001
        pass
    if d.get("engine") not in CHOICES:
        d["engine"] = DEFAULTS["engine"]
    return d


def save(d):
    cur = load()
    e = (d or {}).get("engine")
    if e in CHOICES:
        cur["engine"] = e
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
    except Exception as e:                                # noqa: BLE001
        print(f"  [asrconf] 存失败: {e}", flush=True)
    return cur
