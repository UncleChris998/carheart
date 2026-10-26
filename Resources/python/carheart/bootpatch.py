# -*- coding: utf-8 -*-
"""页面兜底脚本（单独模块，由 page_bytes 注入）。

为什么单独一个文件：把一大段 JS 直接塞进 webui.py 的字符串里，
每次改动都可能把 Python 字符串写坏（已经坏过好几次）。
放这儿就是一个普通模块，改它不影响页面本体。

做两件事：
  A. 连接自检 —— 自己探一次 /health
     通了：收起"连不上它"的遮罩，顶部显示在线
     不通：把真实错误写到页面上（不再只有一句"连不上"）
  B. 点击留痕 —— 记录"说话"按钮被点、语音桥是否在
     真机上点不动时，右上角那行小字能直接看到卡在哪
"""
JS = r"""
<script>
/* ================= 兜底：连接自检 + 点击留痕 ================= */
(function(){
  var $ = function(id){ return document.getElementById(id); };
  function tok(){
    return new URLSearchParams(location.search).get('token') ||
           (window.localStorage.getItem('ch_token') || '');
  }
  function stamp(txt){
    var s = $('stamp');
    if(s) s.textContent = txt;
  }

  var base = (window.localStorage.getItem('ch_base') || location.origin);
  var u = base.replace(/\/+$/, '') + '/health';
  var t = tok();
  if(t) u += '?token=' + encodeURIComponent(t);

  fetch(u, {cache: 'no-store'}).then(function(r){
    if(!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }).then(function(j){
    // 全都由这里接管：主脚本那边的 health() 会误判成离线，
    // 把顶部写成"离线"、还可能弹出"连不上它"的遮罩挡住设置面板。
    // 这段在它之后执行，直接覆盖它的结果。
    var setup = $('setup');
    if(setup) setup.classList.remove('show');
    var dot = $('dot');
    if(dot) dot.classList.add('on');
    var meta = $('meta');
    if(meta) meta.textContent = '';
    var who = $('who');
    if(who && !who.textContent) who.textContent = '它';
    stamp('在线 · ' + (j.model || '') + ' · 记忆' + (j.memories || 0));

    // 主脚本是异步再跑一次的，可能过后又把遮罩弹出来 —— 多压几次
    [400, 1200, 2500, 5000].forEach(function(ms){
      setTimeout(function(){
        var s2 = $('setup');
        if(s2) s2.classList.remove('show');
      }, ms);
    });
  }).catch(function(e){
    var msg = (e && e.message) ? e.message : String(e);
    stamp('离线 · ' + msg);
    try{
      var log = $('log');
      if(log){
        var d = document.createElement('div');
        d.className = 'msg sys';
        d.textContent = '连不上服务：' + msg + '（' + base + '）';
        log.appendChild(d);
      }
    }catch(_e){}
  });

  var taps = [];
  function tap(s){
    taps.push(s);
    if(taps.length > 3) taps.shift();
    var s0 = $('stamp');
    if(s0) s0.title = taps.join(' | ');
    try{ console.log('[tap]', s); }catch(_e){}
  }

  var talk = $('talk');
  if(talk){
    talk.addEventListener('click', function(){
      tap('点说话');
      stamp('点了说话 · 原生桥=' + (window.NativeASR ? '有' : '无'));
      setTimeout(function(){
        var tk = $('talk');
        var txt = tk ? tk.textContent : '';
        if(txt === '说话' || txt === ''){
          stamp('说话无反应 · 桥=' + (window.NativeASR ? '有' : '无')
                + ' · 按钮=' + (txt || '空'));
        }
      }, 3000);
    }, true);
  }

  var live = $('live');
  if(live){
    live.addEventListener('click', function(){ tap('点连续'); }, true);
  }
})();
</script>
"""


def inject(html: str) -> str:
    """插到 </body> 之前。"""
    i = html.rfind("</body>")
    if i == -1:
        return html
    return html[:i] + JS + html[i:]
