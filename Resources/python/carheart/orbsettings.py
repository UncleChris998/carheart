# -*- coding: utf-8 -*-
"""光团的设置：动得快/慢、颜色、粒子数 —— 后台可改，前端即时生效。

为什么放服务端存：手机、车机、电脑打开的是同一个页面，
设置必须跟人走，不能存在某个浏览器里。

存储：data/orb.json
  {"speed": 1.0, "color": "#2f7fe0", "count": 3000}
"""
import json
import os

from . import config

PATH = os.path.join(config.DATA_DIR, "orb.json")

DEFAULTS = {
    "speed": 1.0,      # 运动速度倍率：0.2 很慢，2.0 很快
    "color": "#2f7fe0",  # 主色（十六进制）
    "count": 3000,     # 粒子数
    "theme": "light",  # light 白底 / dark 黑灰底
    # 特效倍率，1.0 是默认
    "fx": {"swirl": 1.0, "breath": 1.0, "turb": 1.0,
           "glow": 1.0, "psize": 1.0, "ripple": 1.0, "twinkle": 1.0},
}

SPEED_MIN, SPEED_MAX = 0.15, 2.5
COUNT_MIN, COUNT_MAX = 800, 6000


def load():
    d = dict(DEFAULTS)
    try:
        with open(PATH, encoding="utf-8") as f:
            d.update(json.load(f))
    except Exception:                                     # noqa: BLE001
        pass
    return _clamp(d)


def save(d):
    cur = load()
    for k in ("speed", "color", "count", "theme", "fx"):
        if k in d and d[k] is not None:
            cur[k] = d[k]
    cur = _clamp(cur)
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
    except Exception as e:                                # noqa: BLE001
        print(f"  [orb] 存设置失败: {e}", flush=True)
    return cur


def _clamp(d):
    try:
        d["speed"] = round(max(SPEED_MIN, min(SPEED_MAX, float(d["speed"]))), 2)
    except Exception:                                     # noqa: BLE001
        d["speed"] = DEFAULTS["speed"]
    try:
        d["count"] = int(max(COUNT_MIN, min(COUNT_MAX, int(d["count"]))))
    except Exception:                                     # noqa: BLE001
        d["count"] = DEFAULTS["count"]
    c = str(d.get("color") or DEFAULTS["color"]).strip()
    if not (len(c) == 7 and c.startswith("#")):
        c = DEFAULTS["color"]
    try:
        int(c[1:], 16)
    except ValueError:
        c = DEFAULTS["color"]
    d["color"] = c.lower()
    if d.get("theme") not in ("light", "dark"):
        d["theme"] = DEFAULTS["theme"]
    # 特效：每个 0.2~3.0，缺的补默认
    fx = d.get("fx")
    if not isinstance(fx, dict):
        fx = {}
    out = {}
    for k, v in DEFAULTS["fx"].items():
        try:
            out[k] = round(max(0.2, min(3.0, float(fx.get(k, v)))), 2)
        except Exception:                            # noqa: BLE001
            out[k] = v
    d["fx"] = out
    return d
