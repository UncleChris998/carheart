# -*- coding: utf-8 -*-
"""把搜索做成**工具**交给模型，由它自己判断什么时候查。

这才是「像豆包那样不会就自动去搜」的正确做法。
之前那套关键词匹配（看到"最新""价格"就搜）是假的智能 ——
换个说法就失灵，而且她会在别的问法下说"我不会搜索"。

模型自己不会上网。它能做的是输出一个 tool_call（"我要用 web_search"），
程序执行后把结果喂回去，它再组织成人话。
"""
import json

# 给模型看的工具说明。写得越清楚，它判断得越准。
WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "上网搜索，拿到当下的真实信息。"
            "什么时候该用：问题涉及**会变的事实**或**你不确定的当下信息** —— "
            "最新款、价格、新闻、谁当权、某人在做什么、天气、比分、"
            "某个产品现在怎么样、某件事的进展。"
            "你的训练知识有截止日期，这类问题必须查，不能凭印象答。"
            "什么时候不该用：闲聊、情绪、他告诉过你的事、"
            "通用常识（水沸点、首都在哪）、以及关于你自己的问题。"
            "查完之后再回答，并且说明这是刚查到的。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索词，要具体。「最新款 iPhone 型号 价格」比「手机」好。",
                }
            },
            "required": ["query"],
        },
    },
}


def run_web_search(args):
    """执行模型要求的搜索，返回给它看的文本。"""
    from . import search as _search
    q = ""
    if isinstance(args, dict):
        q = (args.get("query") or "").strip()
    elif isinstance(args, str):
        q = args.strip()
    if not q:
        return "搜索词是空的，没搜。"
    try:
        results = _search.search(q, count=5)
    except Exception as e:                                # noqa: BLE001
        return f"搜索出错：{type(e).__name__}: {e}"
    if not results:
        return f"搜「{q}」没搜到任何结果。你可以直接告诉他没查到。"
    return _search.as_context(results)


def run_tool_call(call):
    """执行一个 tool_call 对象，返回 (调用 id, 结果文本)。"""
    try:
        cid = call.get("id") or "call_0"
        fn = call.get("function") or {}
        name = fn.get("name") or ""
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            args = {}
        if name == "web_search":
            return cid, run_web_search(args)
        return cid, f"没有这个工具：{name}"
    except Exception as e:                                # noqa: BLE001
        return "call_0", f"工具执行出错：{type(e).__name__}: {e}"
