# -*- coding: utf-8 -*-
"""
模型层：只做一件事 —— 把话说出来。
它不知道什么叫记忆，也不知道自己是谁。人格与记忆由上层注入。
只有 requests 可选；没有就用标准库。
"""
import json

try:
    import requests
    _HAS_REQ = True
except Exception:
    _HAS_REQ = False
    import urllib.request
    import urllib.error

from . import config


class OllamaError(RuntimeError):
    pass


def _post(path, payload, stream=False, timeout=600):
    url = config.OLLAMA_HOST.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8")
    if _HAS_REQ:
        try:
            r = requests.post(url, data=data, stream=stream, timeout=timeout)
        except Exception as e:
            raise OllamaError(f"连不上本地模型服务（{url}）：{e}") from e
        if r.status_code >= 400:
            raise OllamaError(f"{r.status_code} {r.text[:300]}")
        if stream:
            for line in r.iter_lines():
                if line:
                    yield json.loads(line.decode("utf-8"))
        else:
            # 注意：这是生成器，必须 yield，return 的返回值会被吞掉
            yield r.json()
    else:
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except Exception as e:
            raise OllamaError(f"连不上本地模型服务（{url}）：{e}") from e
        if stream:
            for raw in resp:
                if raw.strip():
                    yield json.loads(raw.decode("utf-8"))
        else:
            yield json.loads(resp.read().decode("utf-8"))


def list_models():
    url = config.OLLAMA_HOST.rstrip("/") + "/api/tags"
    try:
        if _HAS_REQ:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        else:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return [m["name"] for m in json.loads(resp.read().decode()).get("models", [])]
    except Exception as e:
        raise OllamaError(f"读不到模型列表：{e}") from e


def chat(messages, model=None, stream=True, temperature=None, num_ctx=None,
         max_tokens=None, on_delta=None, think=None, keep_alive=None, json_mode=False):
    """返回完整回复文本。on_delta 用于流式输出。

    think: True 开思考模式；False 关；None 用 config 的默认。
    json_mode: True 时要求服务端强制输出合法 JSON（Ollama 的 format 参数）。
    """
    # num_predict 同时限制「思考」和「正文」。开思考时如果预算不够，
    # 会有思考块但正文是空的（实测 think=medium + 120 tokens 直接空回复）。
    # 所以开思考时给思考留出额外预算。
    think_on = think is not None and (
        (isinstance(think, str) and think not in ("", "off", "auto"))
        or (not isinstance(think, str) and bool(think)))
    base_pred = max_tokens or config.MAX_REPLY_TOKENS
    num_pred = base_pred + (config.THINK_BUDGET if think_on else 0)

    payload = {
        "model": model or config.MODELS["chat"],
        "messages": messages,
        "stream": bool(stream),
        "keep_alive": config.OLLAMA_KEEP_ALIVE if keep_alive is None else keep_alive,
        "options": {
            "temperature": config.TEMPERATURE if temperature is None else temperature,
            "top_p": config.TOP_P,
            "num_ctx": num_ctx or config.NUM_CTX,
            "num_predict": num_pred,
        },
    }
    if json_mode:
        payload["format"] = "json"
    if think is not None:
        # 注意：不能 bool(think)。Ollama 支持分级思考
        # （"low"/"medium"/"high"），bool() 会把它压成 True 就丢了分级。
        if isinstance(think, str):
            payload["think"] = think if think not in ("", "off", "auto") else False
        else:
            payload["think"] = bool(think)
    full = []
    if stream:
        for chunk in _post("/api/chat", payload, stream=True):
            piece = (chunk.get("message") or {}).get("content", "")
            if piece:
                full.append(piece)
                if on_delta:
                    on_delta(piece)
            if chunk.get("done"):
                break
        return "".join(full)
    else:
        # 注意：_post 因为带 yield 永远是生成器，非流式时只有一个元素。
        out = None
        for chunk in _post("/api/chat", payload, stream=False):
            out = chunk
            break
        if not isinstance(out, dict):
            raise OllamaError(f"非流式返回类型异常：{type(out).__name__}")
        msg = out.get("message") or {}
        text = msg.get("content") or ""
        if not text:
            # 空回复基本都是被长度截断（思考模式尤其容易）
            raise OllamaError(
                f"模型返回空内容（done_reason={out.get('done_reason')}，"
                f"eval_count={out.get('eval_count')}，"
                f"思考块长度={len(msg.get('thinking') or '')}）"
            )
        return text


def embed(text, model=None):
    """可选：嵌入向量。失败返回 None，上层自动退回字面检索。"""
    model = model or config.MODELS["embed"]
    try:
        out = _post("/api/embeddings", {"model": model, "prompt": text}, stream=False, timeout=60)
        vec = out.get("embedding")
        return vec if isinstance(vec, list) and vec else None
    except Exception:
        return None
