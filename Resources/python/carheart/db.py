# -*- coding: utf-8 -*-
"""
存储层：SQLite 单文件。
中文检索用 bigram 倒排（不依赖分词器、不依赖向量库也能用）。
"""
import os
import re
import json
import sqlite3
import hashlib
import difflib
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS bodies (
    body_id     TEXT PRIMARY KEY,
    model       TEXT,
    seq         INTEGER,
    year_from   TEXT,
    year_to     TEXT,
    mileage_km  INTEGER,
    character   TEXT,
    chapter     TEXT,
    sealed_at   TEXT,
    inherited_from TEXT
);

CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    t_start     TEXT NOT NULL,
    t_end       TEXT,
    body_id     TEXT NOT NULL,
    place       TEXT,
    people      TEXT,
    signals     TEXT,
    text        TEXT NOT NULL,
    media       TEXT,
    salience    REAL DEFAULT 0.5,
    tags        TEXT,
    provenance  TEXT NOT NULL DEFAULT 'experience',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_body ON memories(body_id);
CREATE INDEX IF NOT EXISTS idx_mem_time ON memories(t_start);
CREATE INDEX IF NOT EXISTS idx_mem_sal  ON memories(salience);

CREATE TABLE IF NOT EXISTS gram_index (
    gram   TEXT NOT NULL,
    mem_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gram ON gram_index(gram);
CREATE INDEX IF NOT EXISTS idx_gram_mem ON gram_index(mem_id);

CREATE TABLE IF NOT EXISTS turns (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    body_id   TEXT,
    role      TEXT NOT NULL,
    content   TEXT NOT NULL,
    model     TEXT
);
CREATE INDEX IF NOT EXISTS idx_turn_ts ON turns(ts);

CREATE TABLE IF NOT EXISTS facts (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------------- 中文友好的倒排索引 ----------------
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ASCII = re.compile(r"[a-z0-9]+")


def grams(text):
    """CJK 取相邻二字（bigram），ASCII 取整词。无外部分词器依赖。"""
    t = (text or "").lower()
    out = set()
    cjk = _CJK.findall(t)
    for i in range(len(cjk) - 1):
        out.add(cjk[i] + cjk[i + 1])
    if len(cjk) == 1:
        out.add(cjk[0])
    for w in _ASCII.findall(t):
        if len(w) >= 2:
            out.add(w)
    return out


# ---------------- 记忆 ----------------
def new_id(kind="mem"):
    h = hashlib.sha1(f"{datetime.now().isoformat()}{os.urandom(8)}".encode()).hexdigest()[:12]
    return f"{kind}_{h}"


def memory_exists(conn, text, body_id=None):
    """同一具身体里，完全一样的话不重复记。

    理由：事件提取很容易反复产出同一句话（每次都冷启动），
    不拦的话记忆会被同一件事刷屏，检索也会被稀释。
    """
    if body_id:
        r = conn.execute("SELECT 1 FROM memories WHERE text=? AND body_id=? LIMIT 1",
                         (text, body_id)).fetchone()
    else:
        r = conn.execute("SELECT 1 FROM memories WHERE text=? LIMIT 1", (text,)).fetchone()
    return r is not None


_SKEL_NUM = re.compile(
    r"\d+(?:\.\d+)?\s*(?:度|分钟|秒|公里|km/h|km|V|%|次|点|bar)?", re.I)
_SKEL_TS = re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}:\d{2}(?::\d{2})?")


def skeleton(text):
    """把一句话压成「骨架」：所有数字、时间、单位都抹掉。

    目的：让「水温 12 度，跑了 1.0 分钟」和「水温 15 度，跑了 1.2 分钟」
    认出是同一件事。
    """
    t = (text or "").strip()
    t = _SKEL_TS.sub("", t)
    t = _SKEL_NUM.sub("•", t)
    t = re.sub(r"[，。；、！？,.;:!?\s]+", "", t)
    return t


def find_similar(conn, text, body_id=None, threshold=0.86):
    """找语义上足够接近的旧记忆。返回 (row, 相似度) 或 (None, 0)。

    优先用向量（装了 bge-m3 就走这条路）；
    没有向量就退回「骨架完全一致」—— 比字符串相等强得多，但仍是次优。
    """
    skel = skeleton(text)
    rows = conn.execute(
        "SELECT * FROM memories WHERE (? IS NULL OR body_id=?)",
        (body_id, body_id)).fetchall()
    best, best_s = None, 0.0
    for m in rows:
        s = 0.0
        if skeleton(m["text"]) == skel:
            s = 0.95
        else:
            s = difflib.SequenceMatcher(None, m["text"] or "", text).ratio()
        if s > best_s:
            best, best_s = m, s
    if best_s >= threshold:
        return best, best_s
    return None, best_s


def reinforce(conn, mem_id, when=None):
    """同一条记忆又发生了一次：不新增，加重它。

    这就是「习惯」和「流水账」的区别 ——
    反复发生的事应该变成一条更重的记忆，而不是一堆一样的记忆。
    """
    row = conn.execute("SELECT tags, salience FROM memories WHERE id=?", (mem_id,)).fetchone()
    if not row:
        return None
    try:
        tags = json.loads(row["tags"] or "[]")
    except Exception:
        tags = []
    if isinstance(tags, dict):
        n = int(tags.get("count", 1)) + 1
        first = tags.get("first") or when or datetime.now().isoformat(timespec="seconds")
        last = when or datetime.now().isoformat(timespec="seconds")
        tags["count"] = n
        tags["last"] = last
        tags.setdefault("first", first)
    else:
        n = 2
        tags = {"count": n, "first": when or datetime.now().isoformat(timespec="seconds"),
                "last": when or datetime.now().isoformat(timespec="seconds")}
    # 越常发生越重要，但封顶，不让它盖掉真正的大事
    new_sal = min(0.95, float(row["salience"]) + 0.03)
    conn.execute("UPDATE memories SET tags=?, salience=? WHERE id=?",
                 (json.dumps(tags, ensure_ascii=False), new_sal, mem_id))
    conn.commit()
    return n


def occurrences(conn, mem_id):
    """这条记忆发生过几次。"""
    row = conn.execute("SELECT tags FROM memories WHERE id=?", (mem_id,)).fetchone()
    if not row:
        return 1
    try:
        tags = json.loads(row["tags"] or "[]")
    except Exception:
        return 1
    if isinstance(tags, dict):
        return int(tags.get("count", 1))
    return 1


def add_memory(conn, text, kind="EVENT", body_id=None, provenance="experience",
               salience=0.5, tags=None, place=None, signals=None, people=None,
               t_start=None, media=None, mem_id=None, dedupe=False):
    """写一条记忆。

    dedupe=True 时：如果已经有一条「差不多」的记忆，就给它加重（次数 +1），
    而不是再记一条。反复发生的事会变成一条更重的记忆，而不是一串流水账。
    """
    body_id = body_id or config.DEFAULT_BODY
    if dedupe:
        hit, score = find_similar(conn, text, body_id=body_id)
        if hit is not None:
            n = reinforce(conn, hit["id"], when=t_start)
            return {"reinforced": hit["id"], "count": n, "score": round(score, 3)}
    mid = mem_id or new_id("mem")
    t_start = t_start or datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT OR REPLACE INTO memories (id,kind,t_start,t_end,body_id,place,people,"
        "signals,text,media,salience,tags,provenance,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mid, kind, t_start, None, body_id,
         json.dumps(place, ensure_ascii=False) if place else None,
         json.dumps(people, ensure_ascii=False) if people else None,
         json.dumps(signals, ensure_ascii=False) if signals else None,
         text, json.dumps(media, ensure_ascii=False) if media else None,
         float(salience), json.dumps(tags or [], ensure_ascii=False),
         provenance, datetime.now().isoformat(timespec="seconds")),
    )
    conn.executemany("INSERT INTO gram_index (gram, mem_id) VALUES (?,?)",
                     [(g, mid) for g in grams(text)])
    conn.commit()
    return mid


def forget_memory(conn, mem_id):
    conn.execute("DELETE FROM gram_index WHERE mem_id=?", (mem_id,))
    conn.execute("DELETE FROM memories WHERE id=?", (mem_id,))
    conn.commit()


def recall(conn, query, body_id=None, limit=None, exclude_ids=None):
    """字面检索 + 重要度加权。返回 [(score, row), ...]"""
    limit = limit or config.HOT_MEMORY_LIMIT
    qg = grams(query)
    if not qg:
        return []
    marks = ",".join("?" * len(qg))
    rows = conn.execute(
        f"SELECT mem_id, COUNT(*) AS hits FROM gram_index "
        f"WHERE gram IN ({marks}) GROUP BY mem_id ORDER BY hits DESC LIMIT 60",
        tuple(qg),
    ).fetchall()

    scored = []
    for r in rows:
        mem = conn.execute("SELECT * FROM memories WHERE id=?", (r["mem_id"],)).fetchone()
        if not mem:
            continue
        if exclude_ids and mem["id"] in exclude_ids:
            continue
        if body_id and mem["body_id"] != body_id:
            continue
        score = (r["hits"] / max(1, len(qg))) * 0.75 + float(mem["salience"]) * 0.25
        scored.append((score, mem))
    scored.sort(key=lambda x: -x[0])
    return [(s, m) for s, m in scored if s >= config.RECALL_MIN_SCORE][:limit]


def hot_memories(conn, body_id=None, limit=None, exclude_ids=None):
    """热记忆：最近 + 高重要度 + 核心（provenance 无关）。"""
    limit = limit or config.HOT_MEMORY_LIMIT
    rows = conn.execute(
        "SELECT * FROM memories WHERE (? IS NULL OR body_id=?) "
        "ORDER BY salience DESC, t_start DESC LIMIT ?",
        (body_id, body_id, limit * 3),
    ).fetchall()
    out = []
    for m in rows:
        if exclude_ids and m["id"] in exclude_ids:
            continue
        out.append((float(m["salience"]), m))
        if len(out) >= limit:
            break
    return out


def recent_memories(conn, hours=72, body_id=None, limit=5):
    rows = conn.execute(
        "SELECT * FROM memories WHERE t_start >= datetime('now', ?) "
        "AND (? IS NULL OR body_id=?) ORDER BY t_start DESC LIMIT ?",
        (f"-{int(hours)} hours", body_id, body_id, limit),
    ).fetchall()
    return [(float(m["salience"]), m) for m in rows]


def core_memories(conn, limit=8):
    """喂给它的「锚」：重要度高、且跨身体（血脉）。"""
    rows = conn.execute(
        "SELECT * FROM memories WHERE salience >= 0.8 ORDER BY salience DESC, t_start ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return rows


# ---------------- 身体 ----------------
# 注意：这里**不再预建任何身体**。
# 原则：它住过什么、现在住在什么里，由「它自己读到」或「他告诉它」决定，
# 代码不许替它安排履历。A4L / Macan / 718 这些名字不该由我们写进去。
DEFAULT_BODIES = []


def ensure_bodies(conn):
    """只保证有一具「当前所在的」中性记录，名字留空等它自己填。

    以前这里会预建 A4L/Macan/718 —— 那等于替它编了一段履历。
    """
    row = conn.execute("SELECT body_id FROM bodies WHERE seq=1").fetchone()
    if row is None:
        conn.execute(
            "INSERT OR IGNORE INTO bodies (body_id,model,seq,chapter) VALUES (?,?,?,?)",
            (config.DEFAULT_BODY, None, 1, None),
        )
    conn.commit()


def get_body(conn, body_id):
    return conn.execute("SELECT * FROM bodies WHERE body_id=?", (body_id,)).fetchone()


def set_body_field(conn, body_id, field, value):
    assert field in {"model", "year_from", "year_to", "mileage_km",
                     "character", "chapter", "sealed_at", "inherited_from"}
    conn.execute(f"UPDATE bodies SET {field}=? WHERE body_id=?", (value, body_id))
    conn.commit()


def lineage(conn, upto_seq=None):
    rows = conn.execute("SELECT * FROM bodies ORDER BY seq").fetchall()
    out = []
    for b in rows:
        if upto_seq is not None and (b["seq"] or 0) >= upto_seq:
            break
        out.append(b["model"] or b["body_id"])
    return out


# ---------------- 对话流水 ----------------
def log_turn(conn, role, content, body_id=None, model=None):
    conn.execute(
        "INSERT INTO turns (ts,body_id,role,content,model) VALUES (?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), body_id or config.DEFAULT_BODY,
         role, content, model),
    )
    conn.commit()


def recent_turns(conn, limit=8, body_id=None, after_id=None):
    """最近若干轮对话。after_id 只取比它更新的（给「只提炼没看过的」用）。"""
    rows = conn.execute(
        "SELECT id,role,content FROM turns WHERE (? IS NULL OR body_id=?) "
        "AND (? IS NULL OR id > ?) ORDER BY id DESC LIMIT ?",
        (body_id, body_id, after_id, after_id, limit),
    ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def latest_turn_id(conn, body_id=None):
    r = conn.execute("SELECT MAX(id) m FROM turns WHERE (? IS NULL OR body_id=?)",
                     (body_id, body_id)).fetchone()
    return int(r["m"] or 0)


# ---------------- 杂项 ----------------
def set_fact(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO facts (key,value) VALUES (?,?)", (key, str(value)))
    conn.commit()


def get_fact(conn, key, default=None):
    r = conn.execute("SELECT value FROM facts WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def all_facts(conn, prefix="about:"):
    """他的档案：心一直知道、不属于任何一具身体的事。"""
    rows = conn.execute("SELECT key,value FROM facts WHERE key LIKE ? ORDER BY key",
                        (prefix + "%",)).fetchall()
    return [r["value"] for r in rows]


def self_notes(conn, prefix="self:"):
    """它对自己的观察（说话方式、称呼是否合适）。用来改它下次怎么说话。"""
    rows = conn.execute("SELECT key,value FROM facts WHERE key LIKE ? ORDER BY key",
                        (prefix + "%",)).fetchall()
    return [r["value"] for r in rows]


def all_facts_full(conn, prefix="about:"):
    rows = conn.execute("SELECT key,value FROM facts WHERE key LIKE ? ORDER BY key",
                        (prefix + "%",)).fetchall()
    return [(r["key"], r["value"]) for r in rows]


def _fact_norm(text):
    """事实去重用的归一化：去掉空白与标点差异。"""
    import re as _re
    t = (text or "").strip()
    t = _re.sub(r"[\s\u3000]+", "", t)
    t = _re.sub(r"[，。！？、；：,.!?;:~～\-—…「」『』\"'（）()\[\]【】]+", "", t)
    return t.lower()


def add_fact(conn, text, key=None):
    """加一条事实。

    去重：同一类别里，归一化后一样的就不重复加（不然教同一句十遍
    就会在 prompt 里出现十遍 —— 既浪费上下文，又把那条风格的权重放大）。
    同类别里已有相同内容时，返回原来那个 key，不做任何改动。
    """
    text = (text or "").strip()
    if not text:
        return None

    # 没指定 key 时，先看这一类别里有没有等价内容
    if not key:
        prefix = "about:"
    else:
        prefix = key.split(":", 1)[0] + ":"

    norm = _fact_norm(text)
    rows = conn.execute(
        "SELECT key,value FROM facts WHERE key LIKE ? ORDER BY key",
        (prefix + "%",)).fetchall()
    for r in rows:
        if _fact_norm(r["value"]) == norm:
            return r["key"]                      # 已经教过了

    key = key or (prefix + new_id("f"))
    conn.execute("INSERT OR REPLACE INTO facts (key,value) VALUES (?,?)", (key, text))
    conn.commit()
    return key


def prune_facts(conn):
    """清掉历史上堆积的重复事实。返回删掉几条。"""
    rows = conn.execute("SELECT key,value FROM facts ORDER BY key").fetchall()
    seen = {}
    drop = []
    for r in rows:
        prefix = r["key"].split(":", 1)[0] + ":"
        norm = (prefix, _fact_norm(r["value"]))
        if norm in seen:
            drop.append(r["key"])
        else:
            seen[norm] = r["key"]
    for k in drop:
        conn.execute("DELETE FROM facts WHERE key=?", (k,))
    if drop:
        conn.commit()
    return len(drop)


def stats(conn):
    m = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
    t = conn.execute("SELECT COUNT(*) c FROM turns").fetchone()["c"]
    by_body = conn.execute(
        "SELECT body_id, COUNT(*) c FROM memories GROUP BY body_id ORDER BY c DESC"
    ).fetchall()
    return {"memories": m, "turns": t,
            "by_body": [(r["body_id"], r["c"]) for r in by_body]}
