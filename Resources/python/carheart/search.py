# -*- coding: utf-8 -*-
"""联网：让她能查当下的真事。

为什么要有这个：她的知识停在训练那一刻，问「现在最新款 iPhone 是什么」
她只能答不知道。主人要她能上网。

**默认用免费的**：直接抓搜狗 / Bing 的结果页，不花钱。
（主人原话：「模型不是本身就能搜索吗，为什么额外的钱」——
 说得对。聊天模型自己不会上网，真正去搜的是程序；
 搜索这一步没必要走收费接口。）

如果哪天免费的被封了，把 free=False 传给 lookup() 就切回
DeepSeek 的 /v1/tools/search（5 元 / 1000 次）。

设计原则：
  · 搜到的东西是**给模型读的素材**，不是直接念给主人听的。
    她应该用自己的话讲，并且说清"这是我刚查到的"。
  · 搜不到就说搜不到，不许拿训练记忆冒充刚查到的。
"""
import json
import re
import urllib.request

from . import config, cloud

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"

# 什么时候该去查：问当下的、会变的事。
# 不查的：闲聊、情绪、他说过的事、常识（这些查了反而慢）。
_TRIGGERS = (
    "最新", "现在", "目前", "今年", "本月", "这周", "今天", "昨天", "刚才",
    "多少钱", "价格", "几点", "几号", "什么时候", "涨", "跌",
    "发布", "上市", "出了", "新闻", "热搜", "股票", "汇率", "天气",
    "新款", "哪一款", "哪个好", "推荐", "对比", "怎么样",
    "是谁", "谁是", "在哪", "怎么走", "多远",
)

# 明显的闲聊/内部话题，不要浪费搜索
_SKIP = (
    "你是谁", "你叫什么", "你用什么模型", "你记得", "我叫什么",
    "你累不累", "你开心", "喜欢我", "想我",
)


def enabled():
    """能不能联网（云端开着、有 key 才行）。"""
    try:
        return bool(cloud.available())
    except Exception:
        return False


def should_search(text):
    """这句该不该去查。"""
    if not enabled() or not text:
        return False
    t = text.strip()
    if len(t) < 3:
        return False
    for s in _SKIP:
        if s in t:
            return False
    return any(k in t for k in _TRIGGERS)


def _post(url, payload, timeout=45):
    conf = cloud.load_conf()
    key = (conf.get("api_key") or "").strip()
    if not key:
        raise RuntimeError("没有 key")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def search(query, count=5, ori_question=None, free=False):
    """搜网页。返回结果列表；失败返回空列表，绝不抛异常影响对话。

    为什么默认走**收费**的那个（真的比较过）：
      收费（DeepSeek /v1/tools/search）搜「最新款 iPhone 型号」
        -> iPhone 18 Pro Max（2026年）、iPhone Duo 折叠屏（刚开预购）—— 准
      免费（抓搜狗页）同一个问题
        -> iPhone 15、iPhone XS、2018 年的文章 —— 旧且乱
      价格是 5 元 / 1000 次，也就是**半分钱一次**，不值得为省这个牺牲准确性。
    免费的仍然保留作兜底：收费接口挂了、或者没 key 的时候用它。
    """
    if not free:
        payload = {"query": query[:120], "count": max(1, min(50, count)),
                   "need_content": False}
        if ori_question:
            payload["ori_question"] = ori_question[:120]
        try:
            data = _post(SEARCH_URL, payload)
            res = data.get("results") or []
            if res:
                return res
        except Exception as e:                            # noqa: BLE001
            print(f"  [联网] 收费搜索失败：{type(e).__name__}: {e}", flush=True)
        print("  [联网] 收费搜索没结果，改用免费抓取", flush=True)
    # 免费兜底
    try:
        from . import websearch
        return websearch.web(query, count=count)
    except Exception as e:                                # noqa: BLE001
        print(f"  [联网] 免费搜索也失败：{type(e).__name__}: {e}", flush=True)
        return []


def fetch(url):
    """抓一个网页的正文（Markdown）。失败返回空串。"""
    try:
        data = _post(FETCH_URL, {"url": url})
        return data.get("title") or "", data.get("content") or ""
    except Exception as e:                                # noqa: BLE001
        print(f"  [联网] 抓取失败：{type(e).__name__}: {e}", flush=True)
        return "", ""


def as_context(results, max_items=5):
    """把搜索结果整理成给模型读的一段文字。

    注意：这里给的是**素材**，不是结论。要让她自己判断、
    用自己的话说，并且标明这是查来的。
    """
    if not results:
        return ""
    lines = ["【你刚刚上网查到的（下面的东西不是你本来就知道的，是刚查的）】"]
    for i, it in enumerate(results[:max_items], 1):
        title = (it.get("title") or "").strip()
        summ = (it.get("summary") or it.get("snippet") or "").strip()
        url = (it.get("url") or "").strip()
        date = (it.get("date") or "")[:10]
        auth = it.get("authority")
        flag = "（权威站）" if isinstance(auth, int) and auth >= 3 else ""
        lines.append(f"{i}. {title}{flag}")
        if date:
            lines.append(f"   时间：{date}")
        if summ:
            # 摘要可能很长，掐到 600 字，别把上下文撑爆
            lines.append(f"   {summ[:600]}")
        if url:
            lines.append(f"   来源：{url}")
    lines.append("")
    lines.append("**怎么说这些**：用你自己的话讲，像刚看到的人那样。")
    lines.append("该说「我刚查了一下」就说，别装成本来就知道。")
    lines.append("**摘要里常混着旧年份的旧消息**（比如问最新款却跳出 2023 年的文章），")
    lines.append("看到日期就按日期判断新旧，别把旧的当最新。这点最重要。")
    lines.append("查到的如果互相矛盾，或者看着不像真的，就把这个疑问告诉他，别硬下结论。")
    return "\n".join(lines)


def lookup(text, count=5):
    """一步到位：判断该不该查、查、整理成上下文。返回 (是否查了, 上下文)。"""
    if not should_search(text):
        return False, ""
    # 搜索词：把口语里的虚词去掉一点，但保留原句信息量
    q = re.sub(r"^[你您]\s*(知道|能|会|帮我|给我|告诉我)?\s*", "", text)
    q = q.strip(" ?？。！!，,") or text
    results = search(q, count=count, ori_question=text[:120])
    if not results:
        return True, ""
    return True, as_context(results)
