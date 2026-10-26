# -*- coding: utf-8 -*-
"""识别结果纠正 —— 把领域词改回来。

为什么需要：通用识别模型不认识「器灵」「小柒」这类词。
实测它会把「器灵」听成 赤菱 / 适龄 / 四零 / 气零 / 欺灵 / 志玲 / 刺灵。
长句有上下文时能蒙对，短句必错 —— 而车里说话大量是短句。

做法：识别完，按词表把常见错听改回正确写法。
词表在 data/asr_fix.json，你可以随时加。改完不用重启。

注意：这是"事后纠正"，不是"识别得更准"。
     它只治已知的错，不认识的新错还得靠更好的模型。
     所以这个和"换更强的识别模型"是两件事，都要做。
"""
import json
import os
import re

from . import config

FIX_PATH = os.path.join(config.DATA_DIR, "asr_fix.json")

# 默认词表：左边是正确写法，右边是可能听错的写法。
# 原则：**只收录那些基本不可能是正经词的错听**。
# 像「四零」「七零」这种本身是正常中文的（七十年代、四零后），
# 一律不收 —— 收了就会出现「我没有说器灵啊我说器灵」这种自相矛盾的句子。
DEFAULT_FIX = {
    # 这个项目的核心词。下面这些写法基本不会是别的意思。
    "器灵": ["赤菱", "赤灵", "适龄", "气零", "欺灵", "欺凌", "志玲",
             "刺灵", "气灵", "起灵", "启灵", "契灵", "麒麟",
             "器0", "4灵", "4零"],
    "保时捷": ["保时杰", "宝时捷", "保时节"],
    "二号车机": ["二号机", "2号车机"],
}

_cache = {"mtime": None, "rules": None}


def _load():
    """读词表。文件改过就重读，不用重启服务。"""
    rules = {}
    for right, wrongs in DEFAULT_FIX.items():
        for w in wrongs:
            if w != right:
                rules[w] = right
    try:
        if os.path.exists(FIX_PATH):
            mt = os.path.getmtime(FIX_PATH)
            if _cache["mtime"] != mt:
                with open(FIX_PATH, encoding="utf-8") as f:
                    user = json.load(f)
                # 用户词表：{ "正确写法": ["错法1","错法2"] }
                # 用户加的优先，可以覆盖默认
                for right, wrongs in (user or {}).items():
                    if isinstance(wrongs, str):
                        wrongs = [wrongs]
                    for w in wrongs:
                        # 用户如果写了默认里有的错法，先从旧的正确写法上摘掉
                        rules.pop(w, None)
                        rules[w] = right
                _cache["mtime"] = mt
            _cache["rules"] = rules
        else:
            _cache["rules"] = rules
    except Exception:
        _cache["rules"] = rules
    return _cache["rules"]


def ensure_file():
    """第一次运行时写一份默认词表出来，方便主人自己加。"""
    if os.path.exists(FIX_PATH):
        return FIX_PATH
    try:
        os.makedirs(os.path.dirname(FIX_PATH), exist_ok=True)
        with open(FIX_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_FIX, f, ensure_ascii=False, indent=1)
            f.write("\n")
    except Exception:
        pass
    return FIX_PATH


def fix(text):
    """把识别结果里的常见错听改回来。"""
    if not text:
        return text
    rules = _load()
    if not rules:
        return text
    out = text
    # 长的先替换，避免「四零」把「一百四零」之类的误伤顺序搞乱
    for wrong in sorted(rules, key=len, reverse=True):
        if wrong in out:
            out = out.replace(wrong, rules[wrong])
    return out


def report(text):
    """返回 (纠正后的文本, 改了哪些)。给调试用。"""
    if not text:
        return text, []
    rules = _load()
    hits = []
    out = text
    for wrong in sorted(rules, key=len, reverse=True):
        if wrong in out:
            out = out.replace(wrong, rules[wrong])
            hits.append(f"{wrong}→{rules[wrong]}")
    return out, hits
