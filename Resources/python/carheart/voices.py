# -*- coding: utf-8 -*-
"""统一音色目录 + 当前选择 + 用说话改音色。

为什么要有这一层：
  现在两套引擎各有各的音色（火山豆包、微软 edge），
  界面上要一个下拉能选全部，而且**说一句话就能换**。
  以前音色是写死在 config 里的，改一次要改代码重启，太笨。

存储：data/voice.json
  {"provider": "volc" | "edge", "id": "zh_female_tianmeitaozi_uranus_bigtts"}

说话换音色的识别放在这里，不改 brain —— 保持 brain 只关心"说什么"。
"""
import json
import os
import re

from . import config

STORE = os.path.join(config.DATA_DIR, "voice.json")

# ---------------- 目录 ----------------
# (provider, id, 显示名, 别名列表) —— 别名给"说话换音色"匹配用
CATALOG = [
    # 火山豆包 seed-tts-2.0
    ("volc", "zh_female_tianmeitaozi_uranus_bigtts", "甜美桃子", ["桃子", "甜美桃子"]),
    ("volc", "zh_female_tianmeixiaoyuan_uranus_bigtts", "甜美小源", ["小源"]),
    ("volc", "ICL_uranus_zh_female_tianmeijiaoqiao_tob", "甜美娇俏", ["娇俏"]),
    ("volc", "ICL_uranus_zh_female_ruanmengtangtang_tob", "软萌糖糖", ["糖糖", "软萌"]),
    ("volc", "ICL_uranus_zh_female_chuliannvyou_tob", "初恋女友", ["初恋"]),
    ("volc", "zh_female_linjianvhai_uranus_bigtts", "邻家女孩", ["邻家"]),
    ("volc", "zh_female_mengyatou_uranus_bigtts", "萌丫头", ["萌丫"]),
    ("volc", "zh_female_qiaopinv_uranus_bigtts", "俏皮女声", ["俏皮"]),
    ("volc", "ICL_uranus_zh_female_keainvsheng_tob", "可爱女生", ["可爱"]),
    ("volc", "ICL_uranus_zh_female_tiexinnvyou_tob", "贴心女友", ["贴心"]),
    ("volc", "zh_female_vv_uranus_bigtts", "Vivi", ["vivi", "薇薇"]),
    ("volc", "zh_female_qingxinnvsheng_uranus_bigtts", "清新女声", ["清新"]),
    ("volc", "zh_female_tianmeiyueyue_uranus_bigtts", "甜美悦悦", ["悦悦"]),
    # 这两个是实测能出声音、且气质接近"阳光甜美"的
    ("volc", "ICL_uranus_zh_female_yuanqitianmei_tob", "元气甜妹", ["元气", "甜妹"]),
    ("volc", "zh_female_xiaohe_uranus_bigtts", "小何", ["小何"]),
    # 微软 edge —— 台湾腔是真的台湾国语
    ("edge", "zh-TW-HsiaoChenNeural", "曉臻（台湾）", ["晓臻", "曉臻", "台湾腔", "台湾"]),
    ("edge", "zh-TW-HsiaoYuNeural", "曉雨（台湾）", ["晓雨", "曉雨"]),
    ("edge", "zh-CN-XiaoyiNeural", "晓伊", ["晓伊"]),
    ("edge", "zh-CN-XiaoxiaoNeural", "晓晓", ["晓晓"]),
    ("edge", "zh-CN-XiaobeiNeural", "晓北（东北）", ["晓北"]),
]

# "用台湾腔" 这种说法直接映射到台湾音色
ACCENT_HINT = {
    "台湾腔": ("edge", "zh-TW-HsiaoYuNeural"),
    "台腔": ("edge", "zh-TW-HsiaoYuNeural"),
    "台湾": ("edge", "zh-TW-HsiaoYuNeural"),
    "东北腔": ("edge", "zh-CN-XiaobeiNeural"),
}


def list_voices():
    """给界面用的列表。"""
    out = []
    for prov, vid, name, _ in CATALOG:
        out.append({"provider": prov, "id": vid, "name": name,
                    "label": f"{name}",
                    "engine": "火山豆包" if prov == "volc" else "微软"})
    return out


def _match(prov, vid):
    for p, v, name, _ in CATALOG:
        if p == prov and v == vid:
            return name
    return vid


def load():
    """当前选的是哪个音色。"""
    try:
        if os.path.exists(STORE):
            with open(STORE, encoding="utf-8") as f:
                c = json.load(f) or {}
            if c.get("provider") and c.get("id"):
                return c
    except Exception:
        pass
    # 默认：火山甜美桃子（如果配了凭据），否则台湾曉雨
    try:
        from . import doubaovoice
        if doubaovoice.available():
            return {"provider": "volc",
                    "id": "zh_female_tianmeitaozi_uranus_bigtts"}
    except Exception:
        pass
    return {"provider": "edge", "id": "zh-TW-HsiaoYuNeural"}


def save(prov, vid):
    c = {"provider": prov, "id": vid}
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)
    return c


def current_label():
    c = load()
    return _match(c["provider"], c["id"])


# ---------------- 说话换音色 ----------------
# 他要能说：「换个台湾腔」「用晓雨的声音」「声音换成甜美桃子」
_CMD_PAT = re.compile(
    r"(换|改|切|变|用|要|说)"
    r".{0,8}?"
    r"(音色|声音|嗓子|腔调|腔)"
    r"|"
    r"(换个|换成|改用|用)"
    r".{0,6}?"
    r"(的声音)"
)


def parse_command(text):
    """看这句话是不是在要求换音色。

    返回 (provider, id, 显示名) 或 None。
    宁可漏判也不要误判 —— 把普通聊天当成换音色会很怪。
    """
    if not text:
        return None
    t = text.strip()
    if len(t) > 60:          # 太长的话基本不是命令，是聊天
        return None

    # 命中口音别名（台湾腔 / 东北腔）
    for hint, target in ACCENT_HINT.items():
        if hint in t and re.search(r"(换|改|切|用|要|说)", t):
            return target[0], target[1], _match(*target)

    looks_like_cmd = bool(_CMD_PAT.search(t))
    # 或者直接点到某个音色名，且带"换/用/说"
    named = None
    for prov, vid, name, aliases in CATALOG:
        for a in [name] + aliases:
            if a and a in t:
                named = (prov, vid, name)
                break
        if named:
            break

    if named and (looks_like_cmd or re.search(r"(换|改|切|用|要|说)", t)):
        return named
    return None


def apply_command(text):
    """执行换音色。返回给用户看的一句话，或 None（不是命令）。"""
    hit = parse_command(text)
    if not hit:
        return None
    prov, vid, name = hit
    save(prov, vid)
    engine = "火山豆包" if prov == "volc" else "微软"
    return f"好，声音换成「{name}」了（{engine}）。"
