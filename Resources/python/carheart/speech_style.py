# -*- coding: utf-8 -*-
"""把要说出口的话，改成"糯叽叽的台腔"写法。

只改**送去合成的那份文本**，屏幕上显示的原文不动 ——
不然你看着别扭，而且她的记忆也会被改过的句子污染。

三条规则（来自主人的调法，全部只用接口支持的能力）：
  1. 少卷舌：事儿→事情、这儿→这里、哪儿→哪里 …（台腔咬字偏平舌，卷舌重就硬）
  2. 句尾偶尔加软尾：喔 / 啦 / 诶，配一个「～」让 TTS 自然拖长（两三句加一次，
     加了当点缀，句句都加就假了）
  3. 长句拆短，中间给逗号停顿（语速本来就慢，加停顿才黏糊）

不做什么：不改意思、不加感叹号、不把疑问句改成陈述句。
"""
import re

# ---- 1. 软化咬字 ----
# 台腔里儿化音和重卷舌很轻，这些词换掉之后舌头不用往后卷
SOFTEN = [
    ("事儿", "事情"),
    ("这儿", "这里"),
    ("那儿", "那里"),
    ("哪儿", "哪里"),
    ("这会儿", "现在"),
    ("那会儿", "那时候"),
    ("一会儿", "一下"),
    ("待会儿", "等一下"),
    ("一点儿", "一点"),
    ("有点儿", "有点"),
    ("玩意儿", "东西"),
    ("压根", "根本"),
    ("闹心", "烦"),
    ("整明白", "搞懂"),
    ("啥", "什么"),
    ("咋", "怎么"),
]

# ---- 2. 软尾词 ----
TAILS = ["喔", "啦", "诶"]
# 只有陈述句才加尾；问句和感叹句后面加会怪
END_STATEMENT = ("。", "！")
END_OTHER = ("？", "?", "…", "~", "～")

# ---- 3. 拆长句用的分隔 ----
CLAUSE_SPLIT = re.compile(r"(?<=[。！？])\s*")


def soften_words(text):
    out = text
    for a, b in SOFTEN:
        out = out.replace(a, b)
    return out


def split_long(text, max_len=28):
    """把很长的分句在逗号处断开一点，给它喘气的地方。

    只处理超长的分句，短句不动 —— 否则整段变成碎渣，反而不自然。
    """
    parts = []
    for sent in re.split(r"(?<=[。！？])", text):
        if not sent:
            continue
        if len(sent) <= max_len:
            parts.append(sent)
            continue
        # 在逗号处切成两半
        chunks = re.split(r"(?<=，)", sent)
        buf = ""
        for c in chunks:
            if len(buf) + len(c) <= max_len:
                buf += c
            else:
                if buf:
                    parts.append(buf)
                buf = c
        if buf:
            parts.append(buf)
    return "".join(parts)


def add_tails(text, every=3, seed_shift=0):
    """句尾偶尔加个软尾。

    every=3 表示大约每三句加一次。不是每句都加 —— 句句加就成装的了。
    只加在陈述句后面；问句、感叹句、已经有波浪号的句子跳过。
    """
    sentences = [s for s in re.split(r"(?<=[。！？])", text) if s.strip()]
    if not sentences:
        return text
    out = []
    n = 0
    for i, s in enumerate(sentences):
        s = s.rstrip()
        if not s:
            continue
        tail_char = s[-1]
        skip = (tail_char in END_OTHER or "～" in s[-2:]
                or len(s) < 4)
        if not skip and (i + seed_shift) % every == 1:
            pick = TAILS[n % len(TAILS)]
            n += 1
            # 去掉原来的句号，换成「软尾～」
            body = s[:-1] if tail_char in END_STATEMENT else s
            out.append(body + pick + "～")
        else:
            out.append(s)
    return "".join(out)


def speakable(text):
    """完整流程：软化 -> 拆短 -> 加软尾。返回送去合成的文本。"""
    if not text or not text.strip():
        return text
    t = soften_words(text)
    t = split_long(t)
    t = add_tails(t)
    return t
