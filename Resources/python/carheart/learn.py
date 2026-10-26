# -*- coding: utf-8 -*-
"""学习层：把对话变成记忆与认识。

这是「记忆」的真正来源 —— 不是人写进去的，是它自己从真实的交流里提出来的。

设计原则（主人 2026-10-11 定）：
  · 不编造。对话里没出现的，一个字都不许写进记忆。
  · 分两类存：谈到的「事」进记忆（带身体归属），谈到的「认识」进档案（不属于任何身体）。
  · 每条都要能追溯：出自哪一轮、什么时候。
  · 提炼失败的代价是「少记一条」，不是「记错一条」—— 宁可少记。
"""
import json
import re
import threading
import time
from datetime import datetime

from . import config, db, llm

EXTRACT_SYSTEM = """你是一个记忆整理器。你的任务：从一段对话里，提取出**值得长期记住的东西**。

输出严格的 JSON，不要任何解释文字。格式：
{
  "memories": [
    {"kind": "EVENT|AFFECT|RELATION|HABIT", "text": "一句话，第三人称，具体", "salience": 0.0-1.0}
  ],
  "about_him": [
    {"text": "关于这个人的稳定事实或偏好，一句话", "salience": 0.0-1.0}
  ],
  "self_notes": [
    {"text": "关于我自己的观察：我说话是不是太啰嗦、他喜欢我怎么称呼他等等", "salience": 0.0-1.0}
  ]
}

铁律：
1. 只能提取**对话里真实出现过**的信息。不许推测、不许补全、不许编造。
2. 没有值得记的就返回空数组，不要为了凑数硬写。
3. memories 里只放**发生了的事**（今天去了哪、说了什么、心情如何）。
4. about_him 里只放**稳定的东西**（他的习惯、偏好、在意什么、怎么称呼他合适）。
5. self_notes 里放**关于我自己表现**的观察，用来改我下次怎么说话。
6. 每条 text 都要具体。「他今天心情不错」是废话，「他今天话比平时少」才有用。
7. salience：0.3 无关紧要，0.6 有点意思，0.9 很久以后还该记得。

**第 8 条（踩过的坑，别再犯）**：
   凡是「我在用什么模型 / 我跑在哪台机器 / 我的配置是什么」这类
   **运行环境的状态**，一条都不要进 memories。
   它是可换的，换过就过时。之前就是这样：我答错了一次模型名，
   这条错误答案被当成「他亲历的事」存了下来，之后每次问到模型
   都从记忆里翻出那个错答案，越传越错。
   这类东西**现读现答**，只认当下的真实状态。
"""


def _chat_json(messages, model=None, debug=False):
    """让模型输出 JSON。失败返回 None，绝不抛异常影响主流程。

    注意：**云端开着的时候走云端**。
    以前这里直接调 llm.chat（本地 Ollama），于是每跑一次记忆整理，
    就把 15 GB 的 27B 加载进显存 —— 明明是云端模式，显存却被占满，
    看起来就像在用本地模型（主人因此以为我没切云端）。
    """
    raw = None
    try:
        from . import cloud as _cloud
        if _cloud.available():
            try:
                raw = _cloud.chat(messages, stream=False, temperature=0.2,
                                  max_tokens=1600)
            except Exception as e:
                if debug:
                    print(f"    [debug] 云端失败，回落本地: {e}")
                raw = None
    except Exception:
        raw = None
    if not raw:
        try:
            raw = llm.chat(messages, model=model or config.MODELS["chat"],
                           stream=False, temperature=0.2, max_tokens=1600,
                           think=False, json_mode=True)
        except Exception as e:
            if debug:
                print(f"    [debug] 调用失败: {type(e).__name__}: {e}")
            return None
    if not raw:
        if debug:
            print("    [debug] 模型返回空字符串")
        return None
    s = raw.strip()
    if s.startswith("```"):
        parts = s.split("```")
        s = parts[1] if len(parts) > 1 else s
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        if debug:
            print(f"    [debug] 没找到 JSON 花括号，原文前 300 字：\n      {raw[:300]}")
        return None
    try:
        return json.loads(s[i:j + 1])
    except Exception as e:
        if debug:
            print(f"    [debug] JSON 解析失败: {e}\n      片段：{s[i:i+300]}")
        return None


def learn_from_turns(conn, body_id=None, n_turns=None, verbose=False):
    """从最近若干轮对话里提炼记忆与认识。返回统计。"""
    body_id = body_id or config.DEFAULT_BODY
    n_turns = int(n_turns or config.LEARN_TURNS)
    turns = db.recent_turns(conn, limit=n_turns, body_id=body_id)
    if len(turns) < 2:
        return {"memories": 0, "about_him": 0, "self_notes": 0, "note": "对话太少"}

    convo = "\n".join(
        f"{'他' if t['role'] == 'user' else '我'}：{t['content']}" for t in turns
    )
    body = db.get_body(conn, body_id)
    body_model = body["model"] if body else body_id

    prompt = (
        f"现在这具身体是：{body_model}（{body_id}）\n"
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"以下是刚才的对话：\n{convo}\n\n"
        f"请按规则输出 JSON。"
    )
    data = _chat_json([
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": prompt},
    ], debug=verbose)
    if not data:
        return {"memories": 0, "about_him": 0, "self_notes": 0, "note": "提炼失败（模型没给出可用 JSON）"}

    stat = {"memories": 0, "about_him": 0, "self_notes": 0}
    tag = f"learn@{datetime.now().strftime('%Y-%m-%d %H:%M')}"

    for m in (data.get("memories") or []):
        txt = (m.get("text") or "").strip()
        if not txt:
            continue
        db.add_memory(conn, txt, kind=(m.get("kind") or "EVENT").upper(),
                      body_id=body_id, provenance="experience",
                      salience=float(m.get("salience") or 0.5),
                      tags=[tag, "自动提炼"])
        stat["memories"] += 1
        if verbose:
            print(f"    + 记忆: {txt}")

    for a in (data.get("about_him") or []):
        txt = (a.get("text") or "").strip()
        if not txt:
            continue
        db.add_fact(conn, txt, key=f"about:auto_{db.new_id('f')}")
        stat["about_him"] += 1
        if verbose:
            print(f"    + 关于他: {txt}")

    for s in (data.get("self_notes") or []):
        txt = (s.get("text") or "").strip()
        if not txt:
            continue
        db.add_fact(conn, txt, key=f"self:auto_{db.new_id('s')}")
        stat["self_notes"] += 1
        if verbose:
            print(f"    + 关于我: {txt}")

    with open(config.LEARN_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n## {datetime.now().isoformat(timespec='seconds')}　身体 {body_id}\n")
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    return stat


# ======================================================================
# 每轮对话之后：把他**亲口教它**的东西落盘
# ----------------------------------------------------------------------
# 为什么必须有这一段（2026-10-13 补）：
#   10-12 晚上那 106 轮里，他亲手教了「你叫小柒」「叫我宝宝」「说话可爱点」，
#   助手当场答应得好好的 —— 但那些话只活在**那几轮对话的上下文**里，
#   没进 facts 表。于是下一轮、下一次重启，它又变回"我不知道我是谁"。
#   对一个「养成」的产品，这是致命的：他教的东西必须真的写进去。
#
# 设计原则（跟提炼记忆一样严）：
#   · 只记他**明说**的，一个字都不许推断、不许补全。
#   · 把握不足就丢掉 —— 少记一条的代价，远小于记错一条。
#   · 绝不覆盖他已经定过的东西（同一类已有时，除非他新说的里带"不"/"改成"，否则跳过）。
# ======================================================================

FACT_SYSTEM = """你在整理一段「他和他的器灵」之间的对话。你的任务：只挑出**他亲口教它的设定**。

输出严格 JSON，不要解释文字：
{
  "facts": [
    {"kind": "name|call|setup|style|about", "text": "……", "why": "他哪句话说的"}
  ],
  "memories": [
    {"kind": "EVENT|AFFECT|RELATION|HABIT", "text": "一句话，第三人称，具体", "salience": 0.0-1.0}
  ]
}

五种 kind 的含义：
  name  —— 他给**它**起的名字（「你就叫小柒」「你的名字是……」）
  call  —— 他希望**它怎么称呼他**（「叫我宝宝」「你喊我大哥」）
  setup —— 它是谁、它在哪、它该做什么（「你是这台 718 的器灵」「你住在我车里」）
  style —— 他要求它**怎么说话**（「可爱点」「别反问」「话少一点」）
  about —— 关于**他本人**的稳定事实或偏好（「我是上海人」「我换过三台车」）

text 的写法（会原样贴进它的提示词，所以要能直接读）：
  name  → **只写名字本身**，例：「小柒」。不要写「他的名字叫小柒」。
  call  → **只写称呼本身**，例：「宝宝」或「大哥」。不要写成句子。
  setup → 写成一句完整的话，例：「你是他这台保时捷 718 的器灵，住在车里。」
  style → 写成一条要求，例：「说话尽量可爱、会撒娇。」
  about → 写成一句关于他的陈述，例：「他是上海人。」

铁律（违反了就等于毁掉这个项目）：
1. 只写他**明说**的。他没说的，一个字都不许推断、不许补全、不许替他决定。
2. 他只是在**问**、在**试探**、在**假设**（「你是不是 AI」「如果你可爱一点」），一律不算教 —— 不写。
3. 他改主意时（「不是 X，是 Y」「以后别 X」），写 Y，并且照他最新的说法写。
4. 没有就返回空数组。**宁可一条都不写，也不许编一条。**
5. memories 只放真正发生过的事；about 只放稳定的东西，不放一时情绪。
6. text 要具体：「他今天话少」有用，「他心情不错」是废话。
7. 只有 name / call 是**短词**（名字、称呼本身）；setup / style / about 才是一句话。
"""


def _norm_loose(text):
    """比 add_fact 的去重更松一点：抹掉空白、标点、以及"你/它/叫/是"这类虚词。"""
    t = (text or "").strip()
    t = re.sub(r"[\s\u3000]+", "", t)
    t = re.sub(r"[，。！？、；：,.!?;:~～\-—…「」『』\"'（）()\[\]【】]+", "", t)
    for w in ("你叫", "你叫", "你的名字是", "它叫", "叫", "是", "你"):
        t = t.replace(w, "")
    return t.lower()


FACT_KINDS = ("name", "call", "setup", "style", "about")


def extract_facts(conn, texts, body_id=None, verbose=False):
    """从几轮对话里挑出「他教它的设定」，落进 facts / memories。

    texts: [{"role": "user"/"assistant", "content": "..."}, ...]
    返回 {"facts": {...}, "memories": n, "note": ...}
    """
    body_id = body_id or config.DEFAULT_BODY
    convo = "\n".join(
        f"{'他' if t.get('role') == 'user' else '我'}：{t.get('content') or ''}"
        for t in texts
    )
    if not convo.strip():
        return {"facts": {}, "memories": 0, "note": "没有新对话"}

    data = _chat_json([
        {"role": "system", "content": FACT_SYSTEM},
        {"role": "user", "content": f"对话：\n{convo}\n\n请按规则输出 JSON。"},
    ], debug=verbose)
    if not data:
        return {"facts": {}, "memories": 0, "note": "提取失败（模型没给出可用 JSON）"}

    wrote, skipped, mem_n = {}, [], 0

    for f in (data.get("facts") or []):
        if not isinstance(f, dict):
            continue
        kind = (f.get("kind") or "").strip().lower()
        txt = (f.get("text") or "").strip()
        if kind not in FACT_KINDS or not txt:
            continue
        if float(f.get("confidence") or 1.0) < 0.6:
            continue

        # 已经在讲他刚说的话里出现过没有 —— 防"给个空壳设定"
        existing = db.all_facts(conn, prefix=f"{kind}:")
        if existing:
            same = any(_norm_loose(txt) and _norm_loose(txt) in _norm_loose(e)
                       for e in existing)
            if same:
                continue
            # 他改主意时才覆盖（带"不 / 别 / 改成 / 以后"这类词）
            changed = any(k in convo for k in ("不是", "别", "不要", "改成", "以后"))
            if not changed:
                skipped.append((kind, txt))
                continue

        key = db.add_fact(conn, txt, key=f"{kind}:{db.new_id(kind)}")
        if key:
            wrote[kind] = txt
            if verbose:
                print(f"    + 设定[{kind}]: {txt}")

    for m in (data.get("memories") or []):
        if not isinstance(m, dict):
            continue
        txt = (m.get("text") or "").strip()
        if not txt:
            continue
        if db.memory_exists(conn, txt, body_id=body_id):
            continue
        db.add_memory(conn, txt, kind=(m.get("kind") or "EVENT").upper(),
                      body_id=body_id, provenance="experience",
                      salience=float(m.get("salience") or 0.5),
                      tags=["每轮提炼"])
        mem_n += 1
        if verbose:
            print(f"    + 记忆: {txt}")

    with open(config.LEARN_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n## {datetime.now().isoformat(timespec='seconds')}　每轮提炼　身体 {body_id}\n")
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    out = {"facts": wrote, "memories": mem_n}
    if skipped:
        out["skipped"] = skipped
    return out


class TurnExtractor:
    """每轮对话之后，安静一会儿就把「他教的东西」落盘。

    为什么不立刻跑：它说完话马上又会调一次模型，用户第二句就得排队等
    （llama-server 一次只处理一个请求），体感就是「接话变慢」。
    所以等他安静 quiet 秒再动手 —— 他一直在说的时候，一句都不抢。
    """

    def __init__(self, quiet=12.0, max_turns=8):
        self.quiet = float(quiet)
        self.max_turns = int(max_turns)
        self._lock = threading.Lock()
        self._dirty = {}          # body_id -> last_activity(ts)
        self._pending = 0         # 正在等安静期的身体数
        self.last = {}            # body_id -> 最近一次提炼结果
        self.last_at = None
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    # ---- 外部接口 ----
    def mark(self, body_id):
        """刚说完一轮，催一下。"""
        with self._lock:
            self._dirty[body_id] = time.time()
            self._pending = len(self._dirty)

    def run_once(self, body_id=None, verbose=False):
        """不等安静期，立刻提炼（自检 / 调试用）。"""
        with self._lock:
            targets = [body_id] if body_id else list(self._dirty)
            for b in targets:
                self._dirty.pop(b, None)
            self._pending = len(self._dirty)
        out = {}
        for b in targets:
            out[b] = self._extract(b, verbose=verbose)
        return out

    # ---- 内部 ----
    def _loop(self):
        while True:
            time.sleep(1.5)
            try:
                now = time.time()
                due = []
                with self._lock:
                    for b, t0 in list(self._dirty.items()):
                        if now - t0 >= self.quiet:
                            due.append(b)
                            self._dirty.pop(b, None)
                    self._pending = len(self._dirty)
                for b in due:
                    self._extract(b)
            except Exception:
                # 提炼失败绝不能把线程弄死（死了以后就再也不学了）
                pass

    def _extract(self, body_id, verbose=False):
        try:
            conn = db.connect()
            marks = getattr(self, "_marks", None)
            if marks is None:
                marks = self._marks = {}
            after = marks.get(body_id)
            turns = db.recent_turns(conn, limit=self.max_turns,
                                    body_id=body_id, after_id=after)
            if not turns:
                conn.close()
                return {}
            r = extract_facts(conn, turns, body_id=body_id, verbose=verbose)
            marks[body_id] = max(t["id"] for t in turns)
            conn.close()
            self.last[body_id] = r
            self.last_at = datetime.now().isoformat(timespec="seconds")
            return r
        except Exception as e:
            self.last_at = datetime.now().isoformat(timespec="seconds")
            return {"error": f"{type(e).__name__}: {e}"}

    def status(self):
        with self._lock:
            waiting = sorted(self._dirty)
        return {"waiting": waiting, "pending": len(waiting),
                "quiet": self.quiet, "last_at": self.last_at,
                "last": self.last}


EXTRACTOR = TurnExtractor()


def compact(conn, body_id=None, keep_recent=20, verbose=False):
    """压缩：把久远的对话流水炼成记忆，然后删掉原文，控制数据库增长。

    这就是「无限上下文」的真身：窗口有界，记忆无界。
    """
    body_id = body_id or config.DEFAULT_BODY
    total = conn.execute("SELECT COUNT(*) c FROM turns WHERE body_id=?", (body_id,)).fetchone()["c"]
    if total <= keep_recent:
        return {"compacted": 0, "turns_left": total}

    rows = conn.execute(
        "SELECT id,role,content,ts FROM turns WHERE body_id=? ORDER BY id ASC LIMIT ?",
        (body_id, total - keep_recent),
    ).fetchall()
    if not rows:
        return {"compacted": 0, "turns_left": total}

    convo = "\n".join(f"{'他' if r['role']=='user' else '我'}：{r['content']}" for r in rows)
    data = _chat_json([
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": f"以下是较早的一段对话，请提炼要点：\n{convo}"},
    ]) or {}

    n = 0
    for m in (data.get("memories") or []):
        txt = (m.get("text") or "").strip()
        if txt:
            db.add_memory(conn, txt, kind=(m.get("kind") or "EVENT").upper(),
                          body_id=body_id, provenance="experience",
                          salience=float(m.get("salience") or 0.5),
                          tags=["压缩摘要"])
            n += 1
    for a in (data.get("about_him") or []):
        txt = (a.get("text") or "").strip()
        if txt:
            db.add_fact(conn, txt, key=f"about:auto_{db.new_id('f')}"); n += 1
    for s in (data.get("self_notes") or []):
        txt = (s.get("text") or "").strip()
        if txt:
            db.add_fact(conn, txt, key=f"self:auto_{db.new_id('s')}"); n += 1

    ids = [r["id"] for r in rows]
    conn.executemany("DELETE FROM turns WHERE id=?", [(i,) for i in ids])
    conn.commit()
    left = conn.execute("SELECT COUNT(*) c FROM turns WHERE body_id=?", (body_id,)).fetchone()["c"]
    if verbose:
        print(f"    压缩了 {len(ids)} 轮对话 -> {n} 条长期记忆；剩下 {left} 轮")
    return {"compacted": len(ids), "extracted": n, "turns_left": left}
