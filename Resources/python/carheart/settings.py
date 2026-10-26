# -*- coding: utf-8 -*-
"""可调的说话风格设置：说多长。

主人的诉求是"她一次说多少字"。这有两道闸，都要动：
  1. 人格里告诉她的长度感 —— 光限 token 她会说到一半被截断，很难看
  2. max_tokens 上限 —— 光说"说短点"她不一定听

只有把两道闸一起调，才是"她说得短而且说完整"。

注意：这里**不写"最多三五句"这种硬规定** —— 早先那么写过，
结果她的语气变成客服腔。改成描述"他说话的方式 + 你该有的比例感"。
"""
import json
import os

from . import config

STORE = os.path.join(config.DATA_DIR, "settings.json")

# 档位 -> (人格里那句话, 输出 token 上限)
LEVELS = {
    "short": (
        "他平时说话就短。你也短 —— 一两句，把话说完整，不要凑。"
        "想多说的时候先忍住，等他追问。",
        200,
    ),
    "medium": (
        "正常长度就好：两三句到五六句，看他想不想听下去。",
        512,
    ),
    "long": (
        "可以多说几句，把前因后果、你的想法都讲出来，别怕长。",
        900,
    ),
}
DEFAULT = "medium"


def load():
    try:
        if os.path.exists(STORE):
            with open(STORE, encoding="utf-8") as f:
                c = json.load(f) or {}
            lv = c.get("reply_length")
            if lv in LEVELS:
                return lv
    except Exception:
        pass
    return DEFAULT


def save(level):
    if level not in LEVELS:
        return load()
    c = {}
    try:
        if os.path.exists(STORE):
            with open(STORE, encoding="utf-8") as f:
                c = json.load(f) or {}
    except Exception:
        pass
    c["reply_length"] = level
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)
    return level


def hint():
    """给人格用的那句话。"""
    return LEVELS[load()][0]


def max_tokens():
    """这里只给建议值；真正的上限由 brain 和 config 决定。"""
    return LEVELS[load()][1]


def all_levels():
    return [{"id": k, "name": {"short": "短", "medium": "中", "long": "长"}[k]}
            for k in LEVELS]


# ---------------- 说话就能改长度 ----------------
# 主人说"长短我跟他直接说"，所以这句要能认出来。
# 宁可漏判不要误判：普通聊天里出现"短"字不该被当成命令。
_CMD = [
    ("short", ["说短", "短一点", "短点", "简短", "少说", "别啰嗦", "啰嗦",
               "太长", "话太多", "简洁"]),
    ("long", ["说长", "长一点", "长点", "多说", "详细", "展开", "讲清楚",
              "多说几句"]),
    ("medium", ["正常长度", "适中", "不用太长", "一般长度", "恢复长度"]),
]


def parse_command(text):
    """这句话是不是在要求改长度。返回档位或 None。"""
    if not text:
        return None
    t = text.strip()
    if len(t) > 24:          # 太长基本是聊天，不是命令
        return None
    for level, keys in _CMD:
        for k in keys:
            if k in t:
                # 要带一点"要求"的语气，避免"今天话说少了"这种被误判。
                # "长度"两个字出现就算命令 —— 「正常长度就好」这种说法很常见。
                if ("长度" in t) or any(
                        w in t for w in ("说", "点", "别", "要", "改", "以后",
                                         "太", "一点", "些", "就好")):
                    return level
    return None


def apply_command(text):
    """执行改长度。返回给用户看的一句确认，或 None。"""
    lv = parse_command(text)
    if not lv:
        return None
    save(lv)
    name = {"short": "短（一两句）", "medium": "中（三五句）",
            "long": "长（多说几句）"}[lv]
    return f"好，以后说话长度改成{name}。"
