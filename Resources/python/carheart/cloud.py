# -*- coding: utf-8 -*-
"""
云端大脑：OpenAI 兼容协议（DeepSeek / 通义 / Kimi / OpenAI 全用这一种）。

架构不变式（主人 2026-10-17 定）：
  人格与记忆永远在本地组装 —— 云端只拿到「这一轮的上下文」，
  算完即忘，不存任何东西。换的是脑子，不换它。

配置放 data/cloud.json（不进代码、不进 git）：
  {
    "mode":     "local",                        // local | cloud
    "base_url": "https://api.deepseek.com/v1",
    "api_key":  "",
    "model":    "deepseek-flash"
  }
没填 key 时自动退回本地 Ollama，它不会哑。
"""
import json
import os
import time

try:
    import requests
    _HAS_REQ = True
except Exception:
    _HAS_REQ = False
    import urllib.request
    import urllib.error

from . import config


class CloudError(RuntimeError):
    pass


# ---------------- 云端预设 ----------------
# 只放**现在真有 key 能用**的。没有 key 的一律不列 —— 列了也用不了，
# 只会让界面变乱。
# model 写官方文档里的真实模型 ID。模型名会过时，所以界面上有「查厂商」
# 按钮：直接问服务端它当下提供什么，比看文档可靠。
PRESETS = [
    {"id": "deepseek", "name": "DeepSeek",
     "base_url": "https://api.deepseek.com/v1",
     "model": "deepseek-flash",
     # real = 官方写的模型真名（带版本号）。接口里用的是 model，
     # 但人问"你用什么模型"时该说真名，不是那个 ID。
     "real": "DeepSeek-V4.1-Flash-1017",
     "verified": True, "reasoning": False},
    {"id": "deepseek-pro", "name": "DeepSeek V4 Pro",
     "base_url": "https://api.deepseek.com/v1",
     "model": "deepseek-v4-pro",
     "real": "DeepSeek-V4-Pro-0813",
     "verified": True, "reasoning": True},
]

# 推理模型的 token 预算要额外加。
# 实测 deepseek-v4-pro + max_tokens=200 -> 200 token 全用在思考上，正文空。
# 这和 Ollama 那边是同一个坑（num_predict 同时管思考和正文）。
REASONING_EXTRA = 1200


def _reply_budget():
    """这一轮回答给多少 token —— 跟着界面上的"说话长短"档位走。

    只靠人格里说"说短点"不够：模型有时会展开，说到一半被截断更难看。
    所以人格提示 + token 上限两道闸一起用。
    """
    try:
        from . import settings as _s
        return int(_s.max_tokens())
    except Exception:
        return config.MAX_REPLY_TOKENS


def preset_by_id(pid):
    for p in PRESETS:
        if p["id"] == pid:
            return p
    return None


def list_remote_models(timeout=25):
    """问云端要**它自己**的模型列表。

    为什么不在预设里写死：模型名会过时 —— Kimi 的 moonshot-v1 整个系列
    2026-08 已下线，智谱也从 GLM-4 换到 GLM-5.3，DeepSeek 的
    deepseek-chat 只是个别名。写死的名字迟早是错的。
    这里直接问服务端，拿它当下真实提供的模型 ID。

    返回 (是否成功, 模型ID列表 或 错误说明)。
    """
    conf = load_conf()
    base = (conf.get("base_url") or "").rstrip("/")
    key = (conf.get("api_key") or "").strip()
    if not base or not key:
        return False, "还没填地址或 key"
    url = base + "/models"
    try:
        if _HAS_REQ:
            r = requests.get(url, headers={"Authorization": "Bearer " + key},
                             timeout=timeout)
            if r.status_code >= 400:
                return False, f"HTTP {r.status_code}：{r.text[:180]}"
            data = r.json()
        else:
            req = urllib.request.Request(
                url, headers={"Authorization": "Bearer " + key})
            data = json.loads(urllib.request.urlopen(req, timeout=timeout)
                              .read().decode("utf-8"))
        ids = []
        for m in (data.get("data") or []):
            mid = m.get("id") if isinstance(m, dict) else None
            if mid:
                ids.append(mid)
        ids.sort()
        return True, ids
    except Exception as e:                                # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


# ---------------- 配置读写 ----------------

def load_conf():
    """读 data/cloud.json。没有就给默认骨架。"""
    path = config.CLOUD_CONF_PATH
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"mode": "local", "base_url": "", "api_key": "", "model": ""}


def save_conf(conf):
    path = config.CLOUD_CONF_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)


def available():
    """云端能不能用：开关开着 + key 填了 + 地址填了。"""
    c = load_conf()
    return (c.get("mode") == "cloud"
            and bool(c.get("api_key", "").strip())
            and bool(c.get("base_url", "").strip()))


def status():
    """给人看的一行状态。绝不打印 key 内容。"""
    c = load_conf()
    mode = c.get("mode", "local")
    has_key = bool(c.get("api_key", "").strip())
    base = c.get("base_url", "") or "（没填）"
    model = c.get("model", "") or "（没填）"
    if mode != "cloud":
        return f"脑子：本地（{config.MODELS['chat']}）　云端开关：关"
    if not has_key or not base.strip():
        return ("脑子：本地（云端开关开着但 key/地址没填，"
                "自动退回本地）")
    return f"脑子：云端 {model} @ {base}（key 已配置）"


def active_brain():
    """**当前真正在用的脑子** —— 给它的自我认知用。

    坑：人格里原来写死 config.MODELS['chat']，于是切到云端之后
    它还坚持说自己是 27B（实测主人问过，答错了）。
    这里统一从真实路由状态取。

    返回 (真名, 在哪)。
    真名用官方写的模型名（DeepSeek-V4.1-Flash-1017），
    不是接口 ID（deepseek-flash）—— 人问"什么模型"要的是真名。
    """
    try:
        c = load_conf()
        if c.get("mode") == "cloud" and available():
            mid = c.get("model") or ""
            p = None
            # 先看当前预设有没有给出真名
            for cand in PRESETS:
                if cand["model"] == mid:
                    p = cand
                    break
            if p and p.get("real"):
                return (p["real"], "云端")
            return (mid or "云端模型", "云端")
    except Exception:
        pass
    return config.MODELS["chat"], "本机"


def set_mode(mode):
    """local / cloud 一键切换，落盘。"""
    if mode not in ("local", "cloud"):
        raise CloudError("mode 只能是 local 或 cloud")
    c = load_conf()
    c["mode"] = mode
    save_conf(c)


# ---------------- 调用 ----------------

def _headers(conf):
    return {
        "Authorization": "Bearer " + conf["api_key"],
        "Content-Type": "application/json",
    }


def chat(messages, stream=True, on_delta=None, timeout=(12, 180),
         temperature=None, max_tokens=None):
    """跟 llm.chat 同一个形状：给它消息，还你一段话。

    - messages 由 brain 在本地组装好（人格+记忆+最近几轮），
      云端只看到这一轮，算完不存。
    - stream=True 时逐块喂 on_delta（语音出声要靠它）。
    - 失败抛 CloudError，上层自动回落本地 —— 它不能哑。
    """
    conf = load_conf()
    if not (conf.get("api_key", "").strip() and conf.get("base_url", "").strip()):
        raise CloudError("云端没配置（data/cloud.json 缺 base_url 或 api_key）")

    url = conf["base_url"].rstrip("/") + "/chat/completions"
    # 出网前脱敏：擦掉本机路径、用户名、数据库文件名。
    # 云端不需要这些，带上就是泄露。
    messages = sanitize_messages(messages)

    # 推理模型（如 deepseek-v4-pro）会先把 token 花在思考上，
    # 预算不够的话正文是空的（实测 max_tokens=200 直接空回复）。
    model_name = conf.get("model") or "deepseek-flash"
    want_pred = max_tokens or _reply_budget()
    is_reasoning = ("reason" in model_name.lower()
                    or "pro" in model_name.lower()
                    or model_name.lower().endswith("-r1"))
    if is_reasoning:
        want_pred += REASONING_EXTRA

    payload = {
        "model": model_name,
        "messages": messages,
        "stream": bool(stream),
        "temperature": config.TEMPERATURE if temperature is None else temperature,
        "max_tokens": want_pred,
    }
    data = json.dumps(payload).encode("utf-8")

    t0 = time.time()
    if _HAS_REQ:
        try:
            r = requests.post(url, data=data, headers=_headers(conf),
                              stream=stream, timeout=timeout)
        except Exception as e:
            raise CloudError(f"连不上云端（{conf.get('base_url')}）：{e}") from e
        if r.status_code >= 400:
            raise CloudError(f"云端 {r.status_code}：{r.text[:300]}")
        if not stream:
            j = r.json()
            return (j.get("choices") or [{}])[0].get("message", {}).get("content", "")
        full = []
        for line in r.iter_lines():
            if not line:
                continue
            s = line.decode("utf-8", errors="replace")
            if not s.startswith("data:"):
                continue
            body = s[5:].strip()
            if body == "[DONE]":
                break
            try:
                j = json.loads(body)
            except Exception:
                continue
            piece = ((j.get("choices") or [{}])[0]
                     .get("delta", {}).get("content", ""))
            if piece:
                full.append(piece)
                if on_delta:
                    on_delta(piece)
        text = "".join(full).strip()
        if not text:
            raise CloudError("云端返回空内容")
        return text
    else:
        # 标准库兜底（没有 requests 时）：只支持非流式
        req = urllib.request.Request(url, data=data,
                                     headers=_headers(conf))
        try:
            resp = urllib.request.urlopen(req, timeout=timeout[1])
        except urllib.error.HTTPError as e:
            raise CloudError(f"云端 {e.code}：{e.read()[:300]}") from e
        except Exception as e:
            raise CloudError(f"连不上云端：{e}") from e
        j = json.loads(resp.read().decode("utf-8"))
        text = ((j.get("choices") or [{}])[0]
                .get("message", {}).get("content", "")).strip()
        if not text:
            raise CloudError("云端返回空内容")
        return text


def chat_tools(messages, tools, timeout=(12, 120), temperature=None,
               max_tokens=None):
    """带工具的调用（OpenAI 兼容的 tool calling）。

    为什么需要这个：模型自己不会上网，也没有手。要让它**自己判断**
    「这题我不知道，得去查」，就必须给它一个工具（web_search），
    让它输出 tool_calls，程序去执行，再把结果喂回去。

    这跟"程序里写死关键词，看到'最新'就去搜"是两回事 ——
    关键词匹配是假智能，换个说法就不搜了。

    返回 (content, tool_calls)。tool_calls 是模型要求调用的工具列表。
    """
    conf = load_conf()
    if not available():
        raise CloudError("云端不可用")
    url = conf["base_url"].rstrip("/") + "/chat/completions"
    model_name = conf.get("model") or "deepseek-flash"
    is_reasoning = ("reason" in model_name.lower()
                    or "pro" in model_name.lower())
    want = max_tokens or config.MAX_REPLY_TOKENS
    if is_reasoning:
        want += REASONING_EXTRA
    payload = {
        "model": model_name,
        "messages": sanitize_messages(messages),
        "stream": False,
        "temperature": (config.TEMPERATURE if temperature is None
                        else temperature),
        "max_tokens": want,
        "tools": tools,
        "tool_choice": "auto",
    }
    try:
        r = requests.post(url, headers=_headers(conf), json=payload,
                          timeout=timeout)
    except Exception as e:                                # noqa: BLE001
        raise CloudError(f"{type(e).__name__}: {e}") from e
    if r.status_code >= 400:
        raise CloudError(f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        j = r.json()
    except Exception as e:                                # noqa: BLE001
        raise CloudError(f"返回不是 JSON: {e}") from e
    msg = ((j.get("choices") or [{}])[0] or {}).get("message") or {}
    return (msg.get("content") or "").strip(), (msg.get("tool_calls") or [])


def usage_note():
    return ("填 key 三步：1) 打开 data/cloud.json  2) 把 api_key 填成 "
            "你在开放平台申请的  3) 跑 python -m carheart mind cloud")


# ---------------- 出网前脱敏 ----------------

# 本机路径、用户名这类东西对云端对话毫无用处，纯属泄露。
# 人格里那段「自我认知」会写出 C:\Users\X\...\carheart.db 这种全路径，
# 一旦走到云端，等于把用户名和目录结构一起发出去。
import re as _re

_PATH_PATTERNS = [
    # Windows 绝对路径（含盘符）：C:\Users\X\...\foo
    _re.compile(r"[A-Za-z]:\\[^\s，。；、）」』\"']*"),
    # UNC 路径 \\server\share
    _re.compile(r"\\\\[^\s，。；、]+"),
    # POSIX 绝对路径 /home/xxx /Users/xxx
    _re.compile(r"(?<![\w])/(?:home|Users|root|var|opt|tmp)/[^\s，。；、]*"),
    # 单独出现的用户名目录
    _re.compile(r"\\Users\\[^\\\s]+"),
]


def sanitize(text):
    """把要发出去的文字里的本机痕迹擦掉。只用于出网，本地照旧。"""
    if not text:
        return text
    out = text
    for pat in _PATH_PATTERNS:
        out = pat.sub("（本机路径）", out)
    # 文件名单独出现也擦
    out = out.replace("carheart.db", "记忆库").replace("persona.md", "人格文件")
    out = out.replace("carheart.crt", "证书").replace("carheart.key", "私钥")
    return out


def sanitize_messages(messages):
    """整条消息列表过一遍。system 和人说的话都过 —— 他说的话里也可能带路径，
    但那是他主动说的，属于"他自己决定要说什么"，这里只擦本机痕迹本身。"""
    clean = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            clean.append({**m, "content": sanitize(c)})
        else:
            clean.append(m)
    return clean
