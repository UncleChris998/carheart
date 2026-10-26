# -*- coding: utf-8 -*-
"""识别校对台 —— 把录音、各引擎的识别结果、正确答案放一起。

为什么需要：说"识别不准"没法改，因为没有标准答案，就不知道错在哪、
错多少、是哪个引擎更差。这个页面让你给每条录音填一次正确答案，
之后就能算出真实的字准确率，而不是靠感觉。
"""
import glob
import json
import os
import threading
import time

from . import config

# 正确答案存这里
TRUTH_PATH = os.path.join(config.DATA_DIR, "asr_truth.json")
_lock = threading.Lock()

# 缓存的识别结果：{文件名: {引擎名: 文本}}
_cache = {}
_cache_lock = threading.Lock()


def _read_truth():
    if not os.path.exists(TRUTH_PATH):
        return {}
    try:
        with open(TRUTH_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_truth(d):
    os.makedirs(os.path.dirname(TRUTH_PATH), exist_ok=True)
    tmp = TRUTH_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, TRUTH_PATH)


def save_truth(name, text):
    with _lock:
        d = _read_truth()
        text = (text or "").strip()
        if text:
            d[name] = text
        else:
            d.pop(name, None)
        _write_truth(d)
    return d.get(name)


def _wav_info(path):
    import wave
    try:
        with wave.open(path, "rb") as w:
            return round(w.getnframes() / float(w.getframerate()), 2)
    except Exception:
        return None


def transcribe_all(name):
    """对一条录音，用所有引擎各识别一遍（结果缓存起来，别每次重算）。"""
    with _cache_lock:
        if name in _cache:
            return _cache[name]
    path = os.path.join(config.RECORD_DIR, name)
    out = {}
    try:
        import wave
        import numpy as np
        import sherpa_onnx
        with wave.open(path, "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            raw = w.readframes(w.getnframes())
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if ch > 1:
            a = a.reshape(-1, ch).mean(axis=1)

        d = config.ASR_DIR
        engines = [
            ("paraformer", sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=os.path.join(d, "paraformer.onnx"),
                tokens=os.path.join(d, "paraformer_tokens.txt"),
                num_threads=config.ASR_THREADS, sample_rate=16000,
                feature_dim=80, decoding_method="greedy_search", debug=False)),
            ("sensevoice", sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=os.path.join(d, "sensevoice.onnx"),
                tokens=os.path.join(d, "sensevoice_tokens.txt"),
                language="zh", use_itn=True,
                num_threads=config.ASR_THREADS, debug=False)),
        ]
        for label, rec in engines:
            t0 = time.time()
            st = rec.create_stream()
            st.accept_waveform(16000, a.astype(np.float32))
            rec.decode_stream(st)
            out[label] = {"text": (st.result.text or "").strip(),
                          "sec": round(time.time() - t0, 3)}
    except Exception as e:                                  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
    with _cache_lock:
        _cache[name] = out
    return out


def load_clips(limit=40):
    files = sorted(glob.glob(os.path.join(config.RECORD_DIR, "*.wav")))
    files = files[-limit:] if limit else files
    truth = _read_truth()
    clips = []
    for p in files:
        name = os.path.basename(p)
        clips.append({
            "name": name,
            "sec": _wav_info(p),
            "mtime": time.strftime("%H:%M:%S",
                                   time.localtime(os.path.getmtime(p))),
            "truth": truth.get(name, ""),
            "engines": transcribe_all(name),
        })
    return {"clips": clips, "has_truth": bool(truth),
            "truth_count": len(truth)}


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>识别校对台</title>
<style>
:root{--bg:#0b141a;--card:#13212a;--line:#22343f;--ink:#e6eef3;--dim:#7d94a2;
      --ok:#4fd1c5;--warn:#e8a33d;--bad:#e05c5c}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  font-size:15px;line-height:1.55}
header{position:sticky;top:0;background:#0d1a22;border-bottom:1px solid var(--line);
  padding:12px 14px;z-index:5}
h1{margin:0 0 4px;font-size:16px;font-weight:600}
#sum{color:var(--dim);font-size:12.5px}
#bar{margin-top:8px;display:flex;gap:8px}
button{border:0;border-radius:9px;padding:9px 14px;font-size:14px;font-weight:600;
  background:#1d3745;color:var(--ink)}
button.p{background:var(--ok);color:#04222a}
#list{padding:12px 12px 60px;display:flex;flex-direction:column;gap:12px}
.clip{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:11px 12px}
.hd{display:flex;align-items:center;gap:9px;margin-bottom:9px}
.play{width:38px;height:38px;border-radius:50%;background:#1d3745;flex:none;
  font-size:15px;display:flex;align-items:center;justify-content:center;cursor:pointer}
.play.on{background:var(--ok);color:#04222a}
.meta{color:var(--dim);font-size:12px}
.eng{margin:3px 0;font-size:14px}
.eng b{color:var(--dim);font-weight:600;font-size:12px;display:inline-block;
  width:86px}
.eng.pf b{color:var(--ok)}
.eng.sv b{color:var(--warn)}
input{width:100%;margin-top:9px;height:42px;border-radius:9px;
  border:1px solid var(--line);background:#0e1c24;color:var(--ink);
  padding:0 12px;font-size:15px;outline:none}
input:focus{border-color:var(--ok)}
input.done{border-color:var(--ok);background:#0f2622}
</style>
</head>
<body>
<header>
  <h1>识别校对台</h1>
  <div id="sum">加载中…</div>
  <div id="bar">
    <button class="p" id="save">保存全部</button>
    <button id="reload">重新识别</button>
  </div>
</header>
<div id="list"></div>
<script>
var clips = [];
var audio = null;

function esc(s){ return (s||'').replace(/[&<>]/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c]; }); }

function render(){
  var L = document.getElementById('list');
  L.innerHTML = '';
  clips.forEach(function(c, i){
    var d = document.createElement('div');
    d.className = 'clip';
    var pft = (c.engines.paraformer||{}).text || '';
    var svt = (c.engines.sensevoice||{}).text || '';
    d.innerHTML =
      '<div class="hd">' +
        '<div class="play" data-i="'+i+'">▶</div>' +
        '<div class="meta">'+ c.mtime +' · '+ (c.sec||'?') +'s</div>' +
      '</div>' +
      '<div class="eng pf"><b>Paraformer</b>'+ esc(pft) +'</div>' +
      '<div class="eng sv"><b>SenseVoice</b>'+ esc(svt) +'</div>' +
      '<input data-i="'+i+'" placeholder="你原本说的是什么？" value="'+ esc(c.truth) +'">';
    L.appendChild(d);
  });
  document.querySelectorAll('.play').forEach(function(b){
    b.onclick = function(){ play(parseInt(b.dataset.i,10), b); };
  });
  document.querySelectorAll('input[data-i]').forEach(function(inp){
    if(inp.value.trim()) inp.classList.add('done');
    inp.oninput = function(){
      var i = parseInt(inp.dataset.i,10);
      clips[i].truth = inp.value;
      inp.classList.toggle('done', !!inp.value.trim());
    };
  });
  updateSum();
}

function updateSum(){
  var done = clips.filter(function(c){ return (c.truth||'').trim(); }).length;
  document.getElementById('sum').textContent =
    clips.length + ' 条录音，已标 ' + done + ' 条';
}

function play(i, btn){
  if(audio){ audio.pause(); audio = null; }
  document.querySelectorAll('.play').forEach(function(x){
    x.classList.remove('on'); x.textContent = '▶';
  });
  audio = new Audio('/clip/' + encodeURIComponent(clips[i].name));
  audio.onended = function(){ btn.classList.remove('on'); btn.textContent='▶'; };
  audio.play();
  btn.classList.add('on'); btn.textContent = '❚❚';
}

function load(){
  fetch('/clips?limit=40').then(function(r){ return r.json(); }).then(function(o){
    clips = o.clips || [];
    render();
  }).catch(function(e){
    document.getElementById('sum').textContent = '加载失败：' + e.message;
  });
}

document.getElementById('save').onclick = function(){
  var items = clips.filter(function(c){ return (c.truth||'').trim(); })
                   .map(function(c){ return {name:c.name, text:c.truth.trim()}; });
  if(!items.length){ alert('还没填任何一条'); return; }
  fetch('/truth', {method:'POST', headers:{'Content-Type':'application/json'},
                   body: JSON.stringify({items: items})})
    .then(function(r){ return r.json(); })
    .then(function(o){ alert('保存了 ' + o.saved + ' 条'); })
    .catch(function(e){ alert('保存失败：' + e.message); });
};

document.getElementById('reload').onclick = function(){ load(); };
load();
</script>
</body>
</html>
"""


def warm(limit=60):
    """后台预热：把最近这些录音的识别结果先算好。

    不预热的话，手机上第一次打开 /lab 要等所有录音都识别完
    （一条两条还行，40 条就是几十秒的转圈）。
    """
    files = sorted(glob.glob(os.path.join(config.RECORD_DIR, "*.wav")))
    files = files[-limit:] if limit else files
    for p in files:
        try:
            transcribe_all(os.path.basename(p))
        except Exception:
            pass
    return len(files)


def warm_async(limit=60):
    t = threading.Thread(target=warm, args=(limit,), daemon=True)
    t.start()
    return t


def page_bytes():
    return PAGE.encode("utf-8")
