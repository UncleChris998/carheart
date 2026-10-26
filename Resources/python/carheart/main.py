# -*- coding: utf-8 -*-
"""命令行入口：python -m carheart <命令>

  doctor                     自检：模型服务、模型、语音
  chat                       文字对话（默认）
  ask "..."                  问一句就退出
  voice                      语音对话（第 4 周的能力，先放着）
  remember "..."             手写一条记忆
  recall "..."               看它会想起什么
  memories [--body 718]      列出记忆
  forget <id>                删掉一条
  body                       看有哪几具身体
  body --set 718 --mileage 20000 --character "方向沉、声音从后面来"
  seed                       灌入几条示例锚点（会写明是示例，可删）
  stats                      看记忆与对话流水
"""
import os
import sys
import threading
import argparse
import difflib

# 控制台可能是 GBK，输出里带特殊符号会直接崩。强制 UTF-8，失败就算了。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from . import config, db, brain, persona, llm, voice, asr


def banner(conn, body_id):
    s = db.stats(conn)
    b = db.get_body(conn, body_id)
    print("-" * 62)
    print(f"  心石计划 v{__import__('carheart').__version__}"
          f"　名字：{config.HEART_NAME}　身体：{b['model'] if b else body_id}"
          f"　记忆：{s['memories']} 条")
    print(f"  人格文件：{config.PERSONA_PATH}")
    print("  命令：/why 看它为什么这么回　/self 看它的自我陈述　"
          "/teach 教它　/body 换地方　/exit 退出")
    print("-" * 62)


def cmd_doctor(args):
    print(brain.doctor())


def cmd_stats(args):
    conn = db.connect(); db.ensure_bodies(conn)
    s = db.stats(conn)
    print(f"记忆 {s['memories']} 条　对话 {s['turns']} 轮")
    for body_id, c in s["by_body"]:
        b = db.get_body(conn, body_id)
        print(f"  {body_id:8s} {b['model'] if b else '':18s} {c} 条")


def cmd_body(args):
    conn = db.connect(); db.ensure_bodies(conn)
    if args.set:
        changes = []
        if args.model: db.set_body_field(conn, args.set, "model", args.model); changes.append("model")
        if args.mileage is not None:
            db.set_body_field(conn, args.set, "mileage_km", args.mileage); changes.append("mileage")
        if args.character:
            db.set_body_field(conn, args.set, "character", args.character); changes.append("character")
        if args.chapter:
            db.set_body_field(conn, args.set, "chapter", args.chapter); changes.append("chapter")
        if args.year_from: db.set_body_field(conn, args.set, "year_from", args.year_from)
        if args.year_to: db.set_body_field(conn, args.set, "year_to", args.year_to)
        print(f"已更新 {args.set}：{', '.join(changes) or '（没给要改的字段）'}")
    for b in conn.execute("SELECT * FROM bodies ORDER BY seq"):
        print(f"  [{b['seq']}] {b['body_id']:8s} {b['model'] or '':18s} "
              f"{b['year_from'] or '?'}–{b['year_to'] or '至今'}　"
              f"{b['mileage_km'] or '-'} km　{b['chapter'] or ''}")


def cmd_remember(args):
    conn = db.connect(); db.ensure_bodies(conn)
    body_id = args.body or config.DEFAULT_BODY
    mid = db.add_memory(conn, args.text, kind=args.kind, body_id=body_id,
                        provenance=args.provenance, salience=args.salience,
                        tags=args.tag or [], t_start=args.when)
    print(f"记下了：{mid}（{body_id}｜{args.provenance}｜重要度 {args.salience}）")


def cmd_recall(args):
    conn = db.connect(); db.ensure_bodies(conn)
    body_id = args.body or config.DEFAULT_BODY
    hits = db.recall(conn, args.text, body_id=body_id, limit=args.limit)
    if not hits:
        print("什么都没想起来。")
    for s, m in hits:
        print(f"  [{s:.2f}] {m['t_start'][:16]}｜{m['body_id']}｜{m['provenance']}｜{m['text']}")
    print("\n-- 会注入给它的完整上下文 --")
    print(persona.render(conn, body_id, recalled=hits))


def cmd_memories(args):
    conn = db.connect(); db.ensure_bodies(conn)
    sql = "SELECT * FROM memories"
    params = []
    if args.body:
        sql += " WHERE body_id=?"
        params.append(args.body)
    sql += " ORDER BY t_start DESC LIMIT ?"
    params.append(args.limit)
    for m in conn.execute(sql, tuple(params)):
        print(f"  {m['id']}  {m['t_start'][:16]}  {m['body_id']:6s} "
              f"{m['kind']:8s} {m['provenance']:10s} {m['salience']:.2f}  {m['text'][:56]}")


def cmd_forget(args):
    conn = db.connect()
    db.forget_memory(conn, args.id)
    print(f"删了：{args.id}")


def cmd_fact(args):
    """他的档案：心一直知道、不属于任何一具身体的事。"""
    conn = db.connect(); db.ensure_bodies(conn)
    if args.del_key:
        conn.execute("DELETE FROM facts WHERE key=?", (args.del_key,)); conn.commit()
        print(f"删了：{args.del_key}"); return
    if args.text:
        k = db.add_fact(conn, args.text, key=args.key)
        print(f"记进了他的档案：{k}\n  {args.text}")
    rows = conn.execute("SELECT key,value FROM facts WHERE key LIKE 'about:%' ORDER BY key").fetchall()
    if rows:
        print("\n现在他的档案里有：")
        for r in rows:
            print(f"  {r['key']}  {r['value']}")


def cmd_seed(args):
    conn = db.connect(); db.ensure_bodies(conn)
    seeds = [
        ("718", "它是他的第三台车。前面还有一台 A4L、一台 Macan。", 0.95, "BODY"),
        ("718", "它的排气声比前两台都近，是从背后过来的。", 0.7, "BODY"),
        ("718", "方向盘比前两台沉，路感几乎不衰减地传到座椅上。", 0.7, "BODY"),
    ]
    for body_id, text, sal, kind in seeds:
        mid = db.add_memory(conn, text, kind=kind, body_id=body_id,
                            provenance="told", salience=sal, tags=["示例"])
        print(f"  + {mid}  {text}")
    print("\n这些都是示例锚点（标了「示例」标签）。改成真的，或删掉：")
    print("  python -m carheart memories   -> 找到 id")
    print("  python -m carheart forget <id>")


def cmd_ask(args):
    conn = db.connect(); db.ensure_bodies(conn)
    h = brain.Heart(conn, body_id=args.body, user_name=args.user)
    try:
        h.reply(args.text, stream=True, on_delta=lambda s: print(s, end="", flush=True))
        print()
    except llm.OllamaError as e:
        print(f"[出错] {e}")
        print("先跑 `python -m carheart doctor` 看看缺什么。")


def cmd_voice(args):
    conn = db.connect(); db.ensure_bodies(conn)
    av = voice.available()
    missing = [k for k, v in av.items() if not v]
    if missing:
        print("语音依赖没装全，缺：" + "、".join(missing))
        print("装法：.venv\\Scripts\\python.exe -m pip install faster-whisper sounddevice pyttsx3")
        print("（装不全也不影响文字模式：python -m carheart chat）")
        return

    print("先加载语音识别模型（第一次会下载，之后就是本地文件）…")
    try:
        voice.load_model()
        print("模型就绪。")
    except Exception as e:
        print(f"加载失败：{e}")
        return

    h = brain.Heart(conn, body_id=args.body, user_name=args.user)
    print("-" * 62)
    print("  用法：按回车 -> 开始录 -> 说话 -> 再按一次回车结束")
    print("  （不用等它自己停，你说完按回车就行）")
    print("  直接打 q 回车 = 退出　·　打 m 回车 = 改成打字")
    print("-" * 62)

    mic = voice.Mic()
    while True:
        try:
            cmd = input("\n[回车开始录] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n（走了）"); return
        if cmd in ("q", "quit", "exit"):
            return
        if cmd == "m":
            print("（切到打字）")
            line = input("你：").strip()
            if not line:
                continue
        else:
            # 手动控制录音：不靠阈值猜，不等静音，不超时
            stop = threading.Event()
            box = {}

            def _rec():
                try:
                    box["audio"], box["peak"] = mic.record_manual(stop, verbose=True)
                except Exception as e:                    # noqa: BLE001
                    box["err"] = e

            th = threading.Thread(target=_rec, daemon=True)
            th.start()
            print("  ● 正在录 —— 现在说话，说完按回车")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
            stop.set()
            th.join(timeout=10)

            if "err" in box:
                print(f"  录音失败：{box['err']}"); continue
            audio = box.get("audio")
            peak = box.get("peak", 0)
            if audio is None or audio.size == 0:
                print("  没录到任何数据"); continue
            secs = audio.size / 16000.0
            print(f"  ○ 录到 {secs:.1f} 秒，峰值 {peak}。识别中…")

            # 存一份原始录音，专门用来对比识别模型（真人语音才算数）
            saved = voice.save_recording(audio, note="")
            if saved:
                print(f"  （录音已存 {os.path.basename(saved)}）")

            try:
                text = asr.get_engine().transcribe(audio)
            except Exception as e:                    # noqa: BLE001
                import traceback
                log = os.path.join(config.DATA_DIR, "logs", "voice_error.log")
                with open(log, "a", encoding="utf-8") as f:
                    f.write("\n" + "=" * 60 + "\n")
                    f.write(f"峰值 {peak}，时长 {secs:.1f}s，"
                            f"引擎 {config.ASR_ENGINE}\n")
                    f.write(traceback.format_exc())
                print(f"  [识别出错] {type(e).__name__}: {e}")
                print(f"  完整信息已写进：{log}")
                continue

            if not text:
                print("  （没听清 —— 峰值太低说明麦克风没收到声音）")
                continue
            print(f"你：{text}")
            line = text

        print("它：", end="", flush=True)
        # 流式出声：边生成边念。这是「豆包那种快感」的关键一步 ——
        # 第一句一出来就合成、就开始播，不等整段说完。
        spk = voice.StreamSpeaker(enabled=getattr(args, "speak", True))
        spk.start()

        def _on_delta(s):
            print(s, end="", flush=True)
            spk.feed(s)

        try:
            h.reply(line, stream=True, on_delta=_on_delta)
            print()
        except llm.OllamaError as e:
            print(f"\n[出错] {e}")
            spk.stop()
            continue
        spk.finish()
        st = spk.stats()
        if st["句数"]:
            first = st["首句出声延迟"]
            print(f"  （出声 {st['句数']} 句"
                  + (f"，首句 {first}s" if first else "")
                  + f"，共 {st['总耗时']}s）")
        elif getattr(args, "speak", True):
            print("  （没出声 —— 检查 piper 音色文件）")


def cmd_voice_devices(args):
    print("=== 音频设备 ===")
    print(voice.devices_report())
    print("\n=== Windows 自带音色（System.Speech） ===")
    for line in voice.sapi_voices():
        print("  " + line)
    print("\n=== pyttsx3 音色 ===")
    for vid, name in voice.list_voices():
        print(f"  {name}")
    print("\n=== 出声自检 ===")
    ok, how = voice.speak("我在。")
    print(f"  {'[可以] 能出声' if ok else '[不行] 出不了声'}：{how}")


def cmd_learn(args):
    """从最近的对话里提炼记忆与认识 —— 它的记忆是这么长出来的。"""
    from . import learn as learn_mod
    conn = db.connect(); db.ensure_bodies(conn)
    body = args.body or config.DEFAULT_BODY
    n = int(args.turns or config.LEARN_TURNS)
    print(f"从最近 {n} 轮对话里提炼（身体 {body}）…")
    stat = learn_mod.learn_from_turns(conn, body_id=body, n_turns=n,
                                      verbose=True)
    print(f"\n结果：记忆 +{stat.get('memories',0)}　关于他 +{stat.get('about_him',0)}"
          f"　关于我 +{stat.get('self_notes',0)}")
    if stat.get("note"):
        print("  " + stat["note"])
    print(f"原始提炼记录：{config.LEARN_LOG}")


def cmd_compact(args):
    """压缩旧对话成记忆 —— 这就是「窗口有界、记忆无界」。"""
    from . import learn as learn_mod
    conn = db.connect(); db.ensure_bodies(conn)
    body = args.body or config.DEFAULT_BODY
    before = db.stats(conn)
    r = learn_mod.compact(conn, body_id=body, keep_recent=config.COMPACT_KEEP,
                          verbose=True)
    after = db.stats(conn)
    print(f"对话 {before['turns']} -> {after['turns']} 轮；"
          f"记忆 {before['memories']} -> {after['memories']} 条")


def cmd_dedupe(args):
    """合并重复记忆：把「差不多」的合成一条，并累计次数。

    不是字符串相等 —— 是骨架比对（数字/时间/单位全部抹掉后比）。
    """
    conn = db.connect(); db.ensure_bodies(conn)
    rows = conn.execute("SELECT * FROM memories ORDER BY t_start").fetchall()
    kept, merged = [], 0
    for r in rows:
        hit = None
        for k in kept:
            if db.skeleton(k["text"]) == db.skeleton(r["text"]) and k["body_id"] == r["body_id"]:
                hit = k
                break
            if difflib.SequenceMatcher(None, k["text"] or "", r["text"] or "").ratio() >= 0.9 \
                    and k["body_id"] == r["body_id"]:
                hit = k
                break
        if hit is None:
            kept.append(r)
            continue
        db.reinforce(conn, hit["id"], when=r["t_start"])
        db.forget_memory(conn, r["id"])
        merged += 1
        if args.verbose:
            print(f"  合并: {r['text'][:44]}")
            print(f"     -> {hit['text'][:44]}")
    print(f"合并 {merged} 条，剩 {len(kept)} 条")
    return merged


def cmd_brain(args):
    """看它的脑子里现在有什么 —— 记忆、档案、自我观察。"""
    conn = db.connect(); db.ensure_bodies(conn)
    s = db.stats(conn)
    print(f"=== 记忆 {s['memories']} 条，对话 {s['turns']} 轮 ===")
    for body_id, c in s["by_body"]:
        print(f"  {body_id}: {c} 条")
    print("\n=== 他的档案（不属于任何身体） ===")
    for k, v in db.all_facts_full(conn):
        print(f"  [{k.split(':')[0]}] {v}")
    print("\n=== 它对自己的观察 ===")
    notes = db.self_notes(conn)
    if notes:
        for n in notes:
            print(f"  - {n}")
    else:
        print("  （还没有 —— 攒几次对话再跑 learn）")
    print("\n=== 最近 10 条记忆 ===")
    for m in conn.execute(
            "SELECT id,t_start,body_id,kind,provenance,salience,text,tags FROM memories "
            "ORDER BY t_start DESC LIMIT 10"):
        n = db.occurrences(conn, m["id"])
        tag = f"×{n}" if n > 1 else "  "
        print(f"  {m['t_start'][:16]} [{m['body_id']}] {m['kind']}/{m['provenance']} "
              f"{m['salience']:.2f} {tag} {m['text'][:48]}")


def cmd_car(args):
    """让它读这具身体 —— 心石之外，它唯一的真实信息来源。"""
    from . import car as carmod
    conn = db.connect(); db.ensure_bodies(conn)
    body = args.body or config.DEFAULT_BODY
    backend = args.backend or config.OBD_BACKEND
    port = args.port or config.OBD_PORT

    print(f"=== 连接（后端 {backend}）===")
    kw = {}
    if backend == "serial":
        kw["port"] = port
    c = carmod.Car(backend, **kw)
    ok, how = c.connect()
    print(f"  {'成功' if ok else '失败'}：{how}")
    if not ok:
        print("\n  买适配器之前，先用模拟器把链路跑通：")
        print("    python -m carheart car --backend sim")
        return
    print(f"  （后端 {c.mode}）")

    print(f"\n=== 读一轮看看 ===")
    snap = c.snapshot()
    for k, v in snap.items():
        if k != "_ts":
            print(f"  {k:14s} {v}")
    print(f"  （时间戳 {snap['_ts']}）")

    print(f"\n=== 跑一趟（{args.seconds or config.SESSION_SECONDS:.0f} 秒）===")
    sess = carmod.DriveSession(body_id=body, car=c).run(
        seconds=args.seconds, verbose=True)

    print(f"\n=== 这一趟发生了什么（从读数里提出来的）===")
    for e in sess.events:
        print(f"  [{e['kind']}] {e['text']}")

    if args.save:
        n = carmod.write_events(conn, sess, verbose=True)
        print(f"\n写进记忆：{n} 条（身体 {body}）")
        print("  这些都是真实读数提出来的，不是编的。")
    else:
        print("\n（加 --save 就写进记忆）")
    c.close()


def cmd_compare(args):
    """拿真实录音对比三个识别引擎 —— 谁听得准，数据说话。"""
    from carheart import asr as asr_mod
    rec_dir = config.RECORD_DIR
    if not os.path.isdir(rec_dir):
        print(f"还没有录音。先用语音模式说几句话，录音会自动存到：\n  {rec_dir}")
        return
    files = sorted(f for f in os.listdir(rec_dir) if f.lower().endswith(".wav"))
    if args.limit:
        files = files[-args.limit:]
    if not files:
        print("录音目录是空的。先跑 voice 说几句。")
        return

    print(f"引擎：{config.ASR_ENGINE}（当前默认）")
    print(f"对比 {len(files)} 条录音：\n")
    engines = ["sensevoice", "paraformer", "whisper"]
    for f in files:
        p = os.path.join(rec_dir, f)
        a, sr = asr_mod._read_wav(p)
        print(f"── {f}  ({len(a)/sr:.1f} 秒)")
        for e in engines:
            try:
                t0 = time.time()
                txt = asr_mod.get_engine(e).transcribe(a, sr)
                print(f"   {e:12s} {time.time()-t0:5.2f}s  {txt}")
            except Exception as ex:
                print(f"   {e:12s} 失败: {type(ex).__name__}")
        print()


def cmd_mind(args):
    """看/切脑子：本地 Ollama 还是云端大模型。

    架构不变式：人格与记忆永远在本地组装，云端只拿到这一轮的上下文，
    算完即忘。换的是脑子，不换它。云端配错或断了自动回落本地 —— 它不会哑。
    """
    from carheart import cloud

    try:
        if args.set:
            cloud.set_mode(args.set)
            print(f"脑子已切到：{args.set}（已落盘，下次启动也生效）")
        if args.key is not None:
            c = cloud.load_conf()
            c["api_key"] = args.key.strip()
            cloud.save_conf(c)
            print("api_key 已保存（不会打印出来）")
        if args.base:
            c = cloud.load_conf()
            c["base_url"] = args.base.strip()
            cloud.save_conf(c)
            print(f"base_url 已设为 {c['base_url']}")
        if args.model:
            c = cloud.load_conf()
            c["model"] = args.model.strip()
            cloud.save_conf(c)
            print(f"模型已设为 {c['model']}")
    except cloud.CloudError as e:
        print(e)
        return

    print()
    print(cloud.status())
    print()
    c = cloud.load_conf()
    key = c.get("api_key", "")
    print(f"  配置文件：{config.CLOUD_CONF_PATH}")
    print(f"  开关    ：{c.get('mode', 'local')}")
    print(f"  地址    ：{c.get('base_url') or '（没填）'}")
    print(f"  模型    ：{c.get('model') or '（没填）'}")
    print(f"  key     ：{'已填（长度 %d）' % len(key) if key else '没填'}")
    print()
    print("  切到云端：python -m carheart mind --set cloud")
    print("  只用本机：python -m carheart mind --set local")
    print("  填 key  ：python -m carheart mind --key sk-xxx "
          "--base https://api.deepseek.com/v1 --model deepseek-flash")
    print()
    print("  云端不可用时自动回落本地，开了不会把它弄哑。")


def cmd_engine(args):
    """看/切识别引擎。"""
    from carheart import asr as asr_mod
    if args.set:
        if args.set not in asr_mod.ENGINES:
            print(f"不认识的引擎。可选：{list(asr_mod.ENGINES)}")
            return
        config.ASR_ENGINE = args.set
        print(f"这次会话改用：{args.set}")
        print("（要永久生效，改 carheart/config.py 里的 ASR_ENGINE）")
    print("可用的识别引擎：")
    for name, desc in asr_mod.available().items():
        mark = " ← 当前" if name == config.ASR_ENGINE else ""
        print(f"  {name:12s} {desc}{mark}")


def cmd_server(args):
    """起 HTTP 服务，给车里的屏幕/手机用。"""
    from . import server as server_mod
    argv = ["--host", args.host, "--port", str(args.port)]
    if args.token:
        argv += ["--token", args.token]
    if getattr(args, "https", False):
        argv += ["--https", "--https-port", str(args.https_port)]
    if args.verbose:
        argv.append("-v")
    server_mod.main(argv)


def cmd_chat(args):
    conn = db.connect(); db.ensure_bodies(conn)
    h = brain.Heart(conn, body_id=args.body, user_name=args.user)
    persona.ensure_persona_file()
    banner(conn, h.body_id)
    print("（直接打字回车。第一次跑建议先问它一句：「你在哪」）\n")
    while True:
        try:
            line = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n（走了）")
            return
        if not line:
            continue
        if line in ("/exit", "/quit", "exit", "quit"):
            return
        if line == "/why":
            print(h.explain()); continue
        if line == "/body":
            cmd_body(argparse.Namespace(set=None, model=None, mileage=None,
                                        character=None, chapter=None,
                                        year_from=None, year_to=None))
            new = input("换到哪一具？（直接回车不动）：").strip()
            if new:
                h.switch_body(new.upper())
                print(f"它现在是 {h.body_id} 了。上下文清空，但记忆还在。")
            continue
        if line in ("/self", "/我是谁", "/selfinfo"):
            print(persona.self_description(conn, h.body_id))
            print(f"\n（想让它以后照这个说，就把这段贴进 persona.md 当范例；"
                  f"或者直接问它「你是谁」看它自己怎么答）")
            continue
        if line.startswith("/remember "):
            print(f"记下了：{h.remember(line[10:].strip())}"); continue
        # 教它：在对话里直接下指令，落进设定 / 风格 / 档案
        if line.startswith("/教 "):
            line = "/teach " + line[3:]
        if line.startswith("/teach "):
            arg = line[7:].strip()
            parts = arg.split(" ", 1)
            if len(parts) < 2:
                print("用法：/teach setup|style|about 内容")
                print("  setup = 它是谁、在哪、该做什么")
                print("  style = 你要求它怎么说话（撒娇、简短、别问问题…）")
                print("  about = 关于你的事")
                continue
            kind, body_txt = parts[0], parts[1]
            kmap = {"setup": "setup:", "style": "style:", "about": "about:",
                    "name": "name:", "call": "call:"}
            if kind not in kmap:
                print("种类只能是 name / call / setup / style / about")
                print("  name  = 它的名字")
                print("  call  = 它该怎么称呼你")
                print("  setup = 它是谁、在哪、该做什么")
                print("  style = 你要求它怎么说话")
                print("  about = 关于你的事")
                continue
            db.add_fact(conn, body_txt, key=f"{kmap[kind]}{db.new_id(kind)}")
            print(f"（记进「{kind}」了 —— 下一句话它就照这个来）")
            continue

        print("它：", end="", flush=True)
        try:
            h.reply(line, stream=True, on_delta=lambda s: print(s, end="", flush=True))
            print("\n")
        except llm.OllamaError as e:
            print(f"\n[出错] {e}")
            print("先跑 `python -m carheart doctor` 看看缺什么。")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="carheart", description="心石计划 · 第一版")
    p.add_argument("--body", default=None, help="当前身体（A4L/MACAN/718）")
    p.add_argument("--user", default=None, help="它怎么称呼你")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("doctor", help="自检").set_defaults(func=cmd_doctor)
    sub.add_parser("stats", help="统计").set_defaults(func=cmd_stats)
    sub.add_parser("brain", help="看它脑子里有什么").set_defaults(func=cmd_brain)

    lr = sub.add_parser("learn", help="从最近对话提炼记忆")
    lr.set_defaults(func=cmd_learn)
    lr.add_argument("--turns", type=int, default=None)

    cp = sub.add_parser("compact", help="把旧对话压缩成记忆")
    cp.set_defaults(func=cmd_compact)

    dd = sub.add_parser("dedupe", help="把「差不多」的记忆合并成一条")
    dd.set_defaults(func=cmd_dedupe)
    dd.add_argument("--verbose", action="store_true")

    ca = sub.add_parser("car", help="读这台车的数据")
    ca.set_defaults(func=cmd_car)
    ca.add_argument("--backend", default=None, choices=["sim", "serial"])
    ca.add_argument("--port", default=None)
    ca.add_argument("--seconds", type=float, default=None)
    ca.add_argument("--save", action="store_true", help="写进记忆")
    sub.add_parser("seed", help="灌示例锚点").set_defaults(func=cmd_seed)
    sub.add_parser("chat", help="文字对话").set_defaults(func=cmd_chat)

    a = sub.add_parser("ask", help="问一句"); a.set_defaults(func=cmd_ask)
    a.add_argument("text")

    r = sub.add_parser("remember", help="手写记忆（属于某一具身体）"); r.set_defaults(func=cmd_remember)
    r.add_argument("text")
    r.add_argument("--kind", default="EVENT")
    r.add_argument("--provenance", default="told",
                   choices=["experience", "inferred", "told"])
    r.add_argument("--salience", type=float, default=0.8)
    r.add_argument("--tag", action="append")
    r.add_argument("--when", default=None)

    fa = sub.add_parser("fact", help="他的档案（不属于任何一具身体的事）")
    fa.set_defaults(func=cmd_fact)
    fa.add_argument("text", nargs="?", default=None)
    fa.add_argument("--key", default=None)
    fa.add_argument("--del-key", dest="del_key", default=None)

    c = sub.add_parser("recall", help="看它会想起什么"); c.set_defaults(func=cmd_recall)
    c.add_argument("text")
    c.add_argument("--limit", type=int, default=6)

    m = sub.add_parser("memories", help="列出记忆"); m.set_defaults(func=cmd_memories)
    m.add_argument("--limit", type=int, default=50)

    f = sub.add_parser("forget", help="删一条"); f.set_defaults(func=cmd_forget)
    f.add_argument("id")

    b = sub.add_parser("body", help="看/改它所在的地方")
    b.set_defaults(func=cmd_body)
    b.add_argument("--set", default=None)
    b.add_argument("--model", default=None)
    b.add_argument("--mileage", type=int, default=None)
    b.add_argument("--character", default=None)
    b.add_argument("--chapter", default=None)
    b.add_argument("--year-from", dest="year_from", default=None)
    b.add_argument("--year-to", dest="year_to", default=None)

    v = sub.add_parser("voice", help="语音对话"); v.set_defaults(func=cmd_voice)
    v.add_argument("--duration", type=float, default=5.0)
    v.add_argument("--no-speak", dest="speak", action="store_false",
                   help="只出文字，不出声")

    vd = sub.add_parser("voice-devices", help="看麦克风与音色")
    vd.set_defaults(func=cmd_voice_devices)

    cmp_ = sub.add_parser("compare", help="用真实录音对比识别引擎")
    cmp_.set_defaults(func=cmd_compare)
    cmp_.add_argument("--limit", type=int, default=5)

    en = sub.add_parser("engine", help="看/切识别引擎")
    en.set_defaults(func=cmd_engine)
    en.add_argument("--set", default=None)

    md = sub.add_parser("mind", help="看/切脑子（本地 或 云端大模型）")
    md.set_defaults(func=cmd_mind)
    md.add_argument("--set", default=None, choices=["local", "cloud"],
                    help="local = 只用本机 Ollama；cloud = 用云端 API")
    md.add_argument("--key", default=None, help="云端 api_key（不会打印）")
    md.add_argument("--base", default=None, help="如 https://api.deepseek.com/v1")
    md.add_argument("--model", default=None, help="如 deepseek-flash")

    sv = sub.add_parser("server", help="起 HTTP 服务（给车里屏幕/手机用）")
    sv.set_defaults(func=cmd_server)
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=19388)
    sv.add_argument("--token", default=None, help="安卓端要带这个 token")
    sv.add_argument("--https", action="store_true",
                    help="同时开 https（手机/iPhone 要麦克风就必须有它）")
    sv.add_argument("--https-port", type=int, default=19443)
    sv.add_argument("-v", "--verbose", action="store_true")

    args = p.parse_args(argv)
    if not args.cmd:
        args.func = cmd_chat
    if args.body:
        config.DEFAULT_BODY = args.body.upper()
    if args.user:
        config.DEFAULT_USER = args.user
    return args.func(args)


if __name__ == "__main__":
    main()
