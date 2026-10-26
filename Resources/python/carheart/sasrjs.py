# -*- coding: utf-8 -*-
"""页面端「实时识别」：边说边出字。

单独一个 <script> 注入，不碰界面原有代码 —— 之前把 JS 塞进主脚本里
已经把整个界面搞死过两次，这次一律新开一层，错了也不影响别的。

做法：
  麦克风 → AudioContext(16k) → ScriptProcessor → Int16 PCM
  每 200ms POST 一块给 /sasr?op=chunk，把返回的中间文字显示在屏幕下方
  再点一次：发结束，拿到最终文字，自动填进输入框并点发送
"""
JS = r"""
<script>
/* ================= 实时识别（独立层） ================= */
(function(){
  if(window.__sasrLoaded) return;
  window.__sasrLoaded = true;

  var $ = function(id){ return document.getElementById(id); };
  var tok = function(){
    return new URLSearchParams(location.search).get('token') ||
           (window.localStorage.getItem('ch_token') || '');
  };
  var api = function(op, sid, body, ctype){
    var u = location.origin + '/sasr?op=' + op;
    if(sid) u += '&sid=' + encodeURIComponent(sid);
    var t = tok();
    if(t) u += '&token=' + encodeURIComponent(t);
    return fetch(u, {
      method: 'POST',
      headers: {'Content-Type': ctype || 'application/octet-stream'},
      body: body || ''
    }).then(function(r){ return r.json(); });
  };

  // ---------- 界面：一颗按钮 + 一条字幕 ----------
  var bar = $('bar');
  if(!bar){ return; }

  /* 识别引擎：默认本地（免费），只有手动切到「流式」才会花火山的钱。
     设置存在服务端 data/asr.json，页面打开时读一次。 */
  var engine = 'local';                 // local | volcano
  function loadEngine(){
    var u = location.origin + '/asrconf';
    var t = tok();
    if(t) u += '?token=' + encodeURIComponent(t);
    fetch(u, {cache:'no-store'}).then(function(r){ return r.json(); })
      .then(function(o){
        if(o && o.engine) { engine = o.engine; paintEngine(); }
      }).catch(function(){});
  }
  function setEngine(e){
    engine = (e === 'volcano') ? 'volcano' : 'local';
    paintEngine();
    var u = location.origin + '/asrconf';
    var t = tok();
    if(t) u += '?token=' + encodeURIComponent(t);
    fetch(u, {method:'POST', headers:{'Content-Type':'application/json'},
              body: JSON.stringify({engine: engine})}).catch(function(){});
  }
  function paintEngine(){
    var lb = $('asrLocalBtn'), vb = $('asrVolcBtn'), hint = $('asrHintLine');
    // 颜色交给皮肤层（skin.py），这里只切 class
    if(lb) lb.className = (engine === 'local') ? 'on' : '';
    if(vb) vb.className = (engine === 'volcano') ? 'on' : '';
    if(hint){
      hint.textContent = (engine === 'local')
        ? '本地识别：不联网、不花钱。'
        : '火山流式：边说边出字，按量计费（花钱）。';
    }
  }

  var btn = document.createElement('button');
  btn.id = 'sasrBtn';
  btn.type = 'button';
  btn.textContent = '实时';
  // 悬浮在底栏右上方：底栏已经排满，塞进去会被挤出屏幕
  // 颜色交给皮肤层（skin.py），这里只管位置和大小
  btn.style.cssText = 'position:fixed;right:12px;bottom:96px;z-index:9';
  document.body.appendChild(btn);

  var strip = document.createElement('div');
  strip.id = 'sasrStrip';
  strip.style.cssText = [
    'position:fixed','left:12px','right:12px','bottom:86px','z-index:9',
    'padding:8px 12px','border-radius:12px','background:rgba(6,20,28,.88)',
    'border:1px solid #1d3745','color:#cfe9f2','font-size:13px','line-height:1.5',
    'display:none','max-height:30vh','overflow:auto','white-space:pre-wrap'
  ].join(';');
  document.body.appendChild(strip);

  function show(t){
    if(!t){ strip.style.display = 'none'; strip.textContent = ''; return; }
    strip.style.display = 'block';
    strip.textContent = t;
  }

  /* 往设置面板里加一行「语音识别」：本地（免费） / 流式（火山，花钱）。
     找得到 #panel 才加，找不到就算了，不影响别的。 */
  (function addPanelRow(){
    var panel = $('panel');
    if(!panel || $('asrLocalBtn')) return;
    var row = document.createElement('div');
    row.className = 'row';
    row.id = 'asrEngineRow';
    row.innerHTML =
      '<label>语音识别</label>' +
      '<div class="seg" style="display:flex;gap:8px;flex:1">' +
      '  <button type="button" id="asrLocalBtn">本地（免费）</button>' +
      '  <button type="button" id="asrVolcBtn">流式（花钱）</button>' +
      '</div>';
    var hint = document.createElement('div');
    hint.id = 'asrHintLine';
    hint.style.cssText = 'font-size:12.5px;color:#7f93a3;line-height:1.5;margin-top:2px';
    // 插在「思考强度」那组前面；找不到就放最后
    var rows = panel.querySelectorAll('.row');
    var anchor = null;
    for(var i=0;i<rows.length;i++){
      var t = (rows[i].textContent || '');
      if(t.indexOf('思考强度') >= 0){ anchor = rows[i]; break; }
    }
    if(anchor){ panel.insertBefore(row, anchor); }
    else { panel.appendChild(row); }
    panel.insertBefore(hint, row.nextSibling);

    $('asrLocalBtn').addEventListener('click', function(){ setEngine('local'); });
    $('asrVolcBtn').addEventListener('click', function(){
      // 花钱的事，点之前说清楚
      if(engine !== 'volcano'){
        var ok = window.confirm('流式识别用的是火山豆包，按用量计费（要花钱）。\n确定切换？');
        if(!ok) return;
      }
      setEngine('volcano');
    });
    paintEngine();
  })();
  loadEngine();

  // ---------- 录音 ----------
  var ctx = null, stream = null, node = null, src = null;
  var sid = null, on = false, buf = [], bufLen = 0, target = 3200;   // 200ms @16k
  var latest = '', sending = false, queue = [];

  function f32toi16(f){
    var out = new Int16Array(f.length);
    for(var i=0;i<f.length;i++){
      var s = Math.max(-1, Math.min(1, f[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return out;
  }

  /* 对话区里的「草稿气泡」：边说边出现、边被修正。
     豆包那种效果 —— 不是等说完才蹦出一整句，
     而是先说出来的字先出现，后面识别得更准了就改掉前面的。 */
  var draftEl = null;
  function draft(text){
    var log = $('log');
    if(!log) return;
    if(!draftEl){
      draftEl = document.createElement('div');
      draftEl.className = 'msg me draft';
      draftEl.style.opacity = '.92';
      draftEl.style.boxShadow = 'none';
      log.appendChild(draftEl);
    }
    // 末尾加一个光标，表示"还在说"
    draftEl.textContent = text;
    var cur = document.createElement('span');
    cur.textContent = '▌';
    cur.style.opacity = '.55';
    cur.style.animation = 'blink 1s steps(1) infinite';
    draftEl.appendChild(cur);
    log.scrollTop = log.scrollHeight;
  }
  function dropDraft(){
    if(draftEl && draftEl.parentNode) draftEl.parentNode.removeChild(draftEl);
    draftEl = null;
  }

  function flush(){
    if(!queue.length || sending || !sid) return;
    sending = true;
    var data = queue.shift();
    api('chunk', sid, data).then(function(r){
      sending = false;
      if(r && r.text && r.text !== latest){
        latest = r.text;
        show(latest);      // 小字幕留着（便于看历史）
        draft(latest);     // 主要是这个：对话区里边说边长、边修正
      }
      if(queue.length) flush();
    }).catch(function(e){
      sending = false;
      show('（上传失败：' + (e && e.message ? e.message : e) + '）');
    });
  }

  function push(int16){
    // 同上：按字节范围拷一份，避免把整块缓冲发出去
    queue.push(int16.buffer.slice(int16.byteOffset,
                                 int16.byteOffset + int16.byteLength));
    if(queue.length > 12) queue.shift();      // 网络慢时别积太多
    flush();
  }

  function start(){
    if(on) return;
    on = true;
    latest = '';
    show('正在打开麦克风…');
    btn.style.background = '#b3261e';
    btn.style.color = '#fff';
    btn.textContent = '停止';

    navigator.mediaDevices.getUserMedia({
      audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}
    }).then(function(s){
      stream = s;
      var AC = window.AudioContext || window.webkitAudioContext;
      ctx = new AC({sampleRate: 16000});
      src = ctx.createMediaStreamSource(s);
      node = ctx.createScriptProcessor(4096, 1, 1);

      var bufF = [];
      node.onaudioprocess = function(ev){
        if(!on) return;
        var ch = ev.inputBuffer.getChannelData(0);
        bufF.push(new Float32Array(ch));
        var have = 0;
        for(var i=0;i<bufF.length;i++) have += bufF[i].length;
        while(have >= 3200){
          var need = 3200, merged = [];
          while(need > 0 && bufF.length){
            var head = bufF[0];
            if(head.length <= need){
              merged.push(head); need -= head.length; have -= head.length;
              bufF.shift();
            } else {
              merged.push(head.subarray(0, need));
              bufF[0] = head.subarray(need);
              have -= need; need = 0;
            }
          }
          var all = new Float32Array(3200), off = 0;
          for(var k=0;k<merged.length;k++){ all.set(merged[k], off); off += merged[k].length; }
          push(f32toi16(all));           // 送 200ms
        }
      };
      src.connect(node);
      node.connect(ctx.destination);

      return api('start', '', JSON.stringify({rate: 16000}),
                 'application/json').then(function(r){
        if(!r || !r.sid) throw new Error((r && r.error) || '服务端没给会话号');
        sid = r.sid;
        show('（开始说话）');
      });
    }).catch(function(e){
      show('打不开麦克风：' + (e && e.message ? e.message : e));
      stop(true);
    });
  }

  function stop(silent){
    if(!on) return;
    on = false;
    btn.style.background = '#0e1c24';
    btn.style.color = '#8ad9e8';
    btn.textContent = '实时';
    try{ if(node) node.disconnect(); }catch(e){}
    try{ if(src) src.disconnect(); }catch(e){}
    try{ if(stream) stream.getTracks().forEach(function(t){ t.stop(); }); }catch(e){}
    try{ if(ctx) ctx.close(); }catch(e){}
    node = src = stream = ctx = null;

    if(!sid){ show(''); dropDraft(); return; }
    var s = sid;
    sid = null;
    if(silent){ api('abort', s); show(''); dropDraft(); return; }

    show(latest || '（没听到）');
    api('end', s).then(function(r){
      var txt = (r && r.text) ? r.text.trim() : '';
      // 把草稿换成最终结果，再交给主界面发送（草稿先撤掉，免得重复一条）
      if(txt){
        draft(txt);
        setTimeout(dropDraft, 80);
        show(txt);
        var ti = $('ti');
        if(ti){
          ti.value = txt;
          var ts = $('ts');
          if(ts) setTimeout(function(){ ts.click(); }, 140);
        }
      } else {
        dropDraft();
        show('（没识别出内容）');
        setTimeout(function(){ show(''); }, 1500);
      }
    }).catch(function(e){
      dropDraft();
      show('结束失败：' + (e && e.message ? e.message : e));
    });
  }

  btn.addEventListener('click', function(){
    if(on){ stop(false); return; }
    // 默认本地（免费）。没切到流式就别偷偷花钱。
    if(engine !== 'volcano'){
      show('实时识别用的是火山流式，按量计费。\n'
         + '设置里「语音识别」切到「流式（花钱）」才会用。\n'
         + '平时说话走本地模型，不花钱。');
      setTimeout(function(){ if(!on) show(''); }, 4500);
      return;
    }
    start();
  });

  /* 不用麦克风的入口：开一个会话，然后自己往里喂 PCM。
     两个用处：
       1. 电脑上验证整条链路（无头浏览器拿不到麦克风）
       2. 以后安卓那边原生录的 PCM 也能直接喂进来 */
  function testStart(){
    if(on) return Promise.resolve(sid);
    on = true;
    latest = '';
    btn.style.background = '#b3261e';
    btn.style.color = '#fff';
    btn.textContent = '停止';
    show('（测试模式：直接喂 PCM）');
    return api('start', '', JSON.stringify({rate: 16000}),
               'application/json').then(function(r){
      if(!r || !r.sid) throw new Error((r && r.error) || '没会话号');
      sid = r.sid;
      return sid;
    });
  }

  function feed(buf){
    // buf: Int16Array 或 ArrayBuffer
    // 注意：subarray 和原数组共用同一块内存，直接取 .buffer 会把整段都发出去
    // （踩过：录音重复识别了 13 遍）
    var ab;
    if(buf instanceof ArrayBuffer){
      ab = buf;
    } else if(buf && buf.buffer){
      ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    } else {
      return;
    }
    queue.push(ab);
    if(queue.length > 24) queue.shift();
    flush();
  }

  window.SASR = {
    start: start, stop: stop, isOn: function(){ return on; },
    testStart: testStart, feed: feed,
    text: function(){ return latest; }
  };
})();
</script>
"""


def inject(html: str) -> str:
    """插到 </body> 之前。"""
    i = html.rfind("</body>")
    if i == -1:
        return html
    return html[:i] + JS + html[i:]
