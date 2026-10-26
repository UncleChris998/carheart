# -*- coding: utf-8 -*-
"""
大脑：这一层才是「它」。
路由决定用哪个脑子；记忆与人格永远跟着它走，不跟着模型走。
"""
from datetime import datetime

from . import config, db, llm, persona
from . import cloud

ROUTE_RULES = [
    (config.THINK_TRIGGERS, "think"),
    (config.DEEP_TRIGGERS, "deep"),
]


def record_actual_route(tier, model, where, why=""):
    """把**这次真正用的**脑子记下来。

    为什么要有这个：人格里原来只写"你现在用的语言模型：X"，那是**配置**，
    不是实况。配置说云端、实际回落到本地，它就答错 ——
    主人已经踩过一次，靠看 GPU 使用率才发现的。
    记下实况之后，它答的是"上一次真正发生了什么"。
    """
    try:
        import json
        import os
        p = os.path.join(config.DATA_DIR, "runtime.json")
        data = {}
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                data = {}
        data["last_tier"] = tier
        data["last_model"] = model
        data["last_where"] = where
        data["last_why"] = why
        data["last_at"] = datetime.now().isoformat(timespec="seconds")
        data["calls"] = int(data.get("calls") or 0) + 1
        os.makedirs(config.DATA_DIR, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception:
        pass


def read_actual_route():
    """读回上一次真正用的脑子。没有记录就返回 None。"""
    try:
        import json
        import os
        p = os.path.join(config.DATA_DIR, "runtime.json")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f) or None
    except Exception:
        return None


def pick_model(user_text):
    """返回 (档位名, 模型名, 理由)"""
    for triggers, tier in ROUTE_RULES:
        for t in triggers:
            if t in user_text:
                return tier, config.MODELS[tier], f"你说到「{t}」"
    return "chat", config.MODELS["chat"], "日常"


MEMORY_MARKERS = ("记住", "别忘了", "记一下", "你要记得")


def wants_explicit_memory(text):
    return any(m in text for m in MEMORY_MARKERS)


class Heart:
    """一次会话里的它。"""

    def __init__(self, conn, body_id=None, user_name=None):
        self.conn = conn
        self.body_id = body_id or config.DEFAULT_BODY
        self.user_name = user_name or config.DEFAULT_USER
        self.history = []          # 本进程内的短期上下文
        self.last_recall = []      # 这一轮想起来的记忆（供 /why 查看）

    # ---------- 核心 ----------
    def reply(self, user_text, stream=True, on_delta=None, save=True):
        conn = self.conn
        body = db.get_body(conn, self.body_id)
        body_model = body["model"] if body else self.body_id

        # 1) 想起来
        recalled = db.recall(conn, user_text, body_id=self.body_id)
        self.last_recall = recalled

        # 2) 渲染人格（记忆注入在这里）
        system = persona.render(conn, self.body_id, user_name=self.user_name,
                                recalled=recalled)

        # 3) 拼消息
        messages = [{"role": "system", "content": system}]
        messages += db.recent_turns(conn, limit=6, body_id=self.body_id)
        messages += self.history[-6:]
        messages.append({"role": "user", "content": user_text})

        # 4) 选脑子（本地档位路由，云端模式下全部走云端）
        tier, model, why = pick_model(user_text)

        # 4.5) 让她自己决定要不要上网查。
        #      以前这里是关键词匹配（看到"最新"就搜）—— 那是假的：
        #      换个说法她就不搜，然后回"我不会搜索"。
        #      现在把搜索做成**工具**给她，由她自己发起 tool_call。
        #
        #      为了不多花钱：先带工具问一次（非流式）。
        #        · 她说不用查 -> 这句话本身就是答案，直接返回，只花一次调用
        #        · 她要求查   -> 执行搜索、把结果喂回去、再流式要最终回答
        self.last_search = None
        tool_used = False
        text = None
        used_cloud = False
        if cloud.available():
            try:
                from . import tools_web
                content, calls = cloud.chat_tools(
                    messages, [tools_web.WEB_SEARCH_TOOL])
                if not calls:
                    # 不需要查：她这一步给的就是答案
                    text = content
                    used_cloud = True
                    self.last_route = ("cloud",
                                       cloud.load_conf().get("model", "?"),
                                       "云端")
                    if stream and on_delta and text:
                        on_delta(text)      # 一次性推过去，前端照常显示
                else:
                    messages.append({"role": "assistant",
                                     "content": content or "",
                                     "tool_calls": calls})
                    for call in calls:
                        cid, out = tools_web.run_tool_call(call)
                        messages.append({"role": "tool",
                                         "tool_call_id": cid,
                                         "content": out})
                    tool_used = True
                    self.last_search = "hit"
                    print("  [联网] 她自己决定查了", flush=True)
            except Exception as e:                        # noqa: BLE001
                self.last_search = "error"
                print(f"  [联网] 工具调用跳过：{type(e).__name__}: {e}",
                      flush=True)
                text = None

        # 5) 说 —— 云端优先，云端断了自动回落本地，它不能哑。
        #    架构不变式：人格与记忆在本地下组装完才发出去；
        #    云端只拿「这一轮的上下文」，算完即忘。
        if text is None and cloud.available():
            try:
                text = cloud.chat(messages, stream=stream, on_delta=on_delta)
                used_cloud = True
                self.last_route = ("cloud", cloud.load_conf().get("model", "?"),
                                   "云端（聪明、快、不占显存）")
            except cloud.CloudError as e:
                # 云端断了：退回本地，它不能哑
                try:
                    from . import learn as _l
                    _l._note("cloud_fallback", str(e)[:200])
                except Exception:
                    pass
                text = None
        if text is None:
            # 思考强度：界面/配置里能调（auto/off/low/medium/high）
            use_think = config.resolve_think(tier)
            send = list(messages)
            if not use_think:
                send[-1] = {"role": "user", "content": user_text + config.NO_THINK_SUFFIX}
            text = llm.chat(send, model=model, stream=stream, on_delta=on_delta,
                            think=use_think)
            if used_cloud is False:
                why_fb = why + "（云端回落）" if cloud.available() else why
                self.last_route = (tier, model, why_fb)
        text = (text or "").strip()

        # 5.5) 把**这次真正用的**记下来 —— 实况，不是配置。
        #      这样下次它说自己用什么时，说的是发生过的事。
        try:
            _tier, _model, _why = (self.last_route or (tier, model, why))
            if _tier == "cloud":
                from . import cloud as _c
                _real, _where = _c.active_brain()
                record_actual_route("cloud", _real, "云端", _why)
            else:
                record_actual_route(_tier, _model,
                                    "本机" if "回落" not in str(_why) else "本机（云端回落）",
                                    _why)
        except Exception:
            pass

        # 6) 记（存本地库，云端什么都不存）
        if save:
            db.log_turn(conn, "user", user_text, body_id=self.body_id)
            db.log_turn(conn, "assistant", text, body_id=self.body_id,
                        model=("cloud:" + cloud.load_conf().get("model", "?"))
                        if used_cloud else model)
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": text})

        if wants_explicit_memory(user_text):
            db.add_memory(conn, user_text, kind="EVENT", body_id=self.body_id,
                          provenance="told", salience=0.9,
                          tags=["他说要记住"])

        # 7) 养成：把「他刚教的东西」排队落盘（等他说完安静十几秒才动手，
        #    免得抢下一个请求的算力 —— 见 learn.TurnExtractor 的说明）
        try:
            from . import learn as _learn
            _learn.EXTRACTOR.mark(self.body_id)
        except Exception:
            pass
        return text

    # ---------- 维护 ----------
    def remember(self, text, kind="EVENT", salience=0.8, provenance="told",
                 tags=None, when=None):
        return db.add_memory(self.conn, text, kind=kind, body_id=self.body_id,
                             provenance=provenance, salience=salience,
                             tags=tags or [], t_start=when)

    def switch_body(self, body_id):
        self.body_id = body_id
        self.history = []

    def explain(self):
        """为什么这么回：路由 + 想起了哪些记忆。用来调人格。"""
        tier, model, why = getattr(self, "last_route", ("chat", "?", "?"))
        out = [f"脑子：{tier} -> {model}（{why}）",
               f"身体：{self.body_id}　称呼：{self.user_name}"]
        if not self.last_recall:
            out.append("想起来的：没有（它只能靠人格里那几条锚）")
        else:
            out.append("想起来的：")
            for s, m in self.last_recall:
                out.append(f"  · [{s:.2f}] {m['t_start'][:16]}｜{m['body_id']}｜{m['text'][:60]}")
        return "\n".join(out)


def doctor():
    """自检：模型服务、模型、语音。给你一眼看清缺什么。"""
    lines = []
    lines.append(cloud.status())
    try:
        models = llm.list_models()
        lines.append(f"模型服务：正常  {config.OLLAMA_HOST}")
        have = "、".join(models[:12]) if models else "（一个都没有）"
        lines.append(f"已装模型：{have}")
        for tier in ("chat", "deep", "think", "embed"):
            name = config.MODELS[tier]
            ok = any(name.split(":")[0] in m for m in models)
            lines.append(f"  {tier:6s} {name:38s} {'OK' if ok else '未安装'}")
    except Exception as e:
        lines.append(f"模型服务：连不上  {e}")
        lines.append("  先装 Ollama 并确保它在运行，然后 ollama pull 一个 7B 级模型")

    from . import voice
    lines.append("语音：")
    av = voice.available()
    for k, v in av.items():
        lines.append(f"  {k:14s} {'有' if v else '没有'}")
    if not all(av.values()):
        lines.append("  （用 .venv\\Scripts\\python.exe 跑才有语音；缺了也不影响文字模式）")
    lines.append("")
    lines.append("出声：")
    lines.append(f"  piper   {'可用（首选：离线神经语音，不碰 Windows SAPI）' if voice.piper_available() else '不可用，会退回 SAPI'}")
    if voice.piper_available():
        lines.append(f"    音色文件：{config.PIPER_MODEL}")
    try:
        lines.append("  SAPI 音色：" + "、".join(voice.sapi_voices()))
        ok, how = voice.speak("自检")
        lines.append(f"  出声自检：{'成功' if ok else '失败'}  {how}")
    except Exception as e:
        lines.append(f"  出声自检异常：{e}")
    return "\n".join(lines)
