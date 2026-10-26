# -*- coding: utf-8 -*-
"""屏幕上的一行报错地址（独立层，不碰主界面）。

用处：出问题的时候不用猜。
它把页面里所有失败的请求都记下来，显示成一行小字：

    ✗ POST /asr → 501

这样"连不上"这种含糊的提示，就能立刻看出来是谁返回了什么。
平时不显示，一旦有失败才出现。
"""
JS = r"""
<script>
/* ============ 请求失败留痕（独立层） ============ */
(function(){
  if(window.__errlineLoaded) return;
  window.__errlineLoaded = true;

  var box = document.createElement('div');
  box.id = 'errline';
  box.style.cssText = [
    'position:fixed','left:8px','right:8px','top:4px','z-index:60',
    'padding:6px 10px','border-radius:8px',
    'background:rgba(120,20,20,.92)','color:#ffe2e0',
    'font-size:11.5px','line-height:1.45','display:none',
    'white-space:pre-wrap','pointer-events:none','max-height:32vh','overflow:hidden'
  ].join(';');
  if(document.body) document.body.appendChild(box);

  var lines = [];
  function short(u){
    try{
      var a = new URL(u, location.origin);
      return a.pathname + (a.search ? '?' + a.search.replace(/token=[^&]+/, 'token=…') : '');
    }catch(e){ return String(u).slice(0, 60); }
  }
  function log(s){
    lines.push(s);
    if(lines.length > 3) lines.shift();
    box.style.display = 'block';
    box.textContent = '✗ ' + lines.join('\n✗ ');
  }

  var orig = window.fetch;
  window.fetch = function(input, init){
    var url = (typeof input === 'string') ? input : ((input && input.url) || '');
    var method = ((init && init.method) || 'GET').toUpperCase();
    return orig.apply(this, arguments).then(function(r){
      if(!r.ok && r.status !== 0 && r.status !== 401){
        log(method + ' ' + short(url) + ' → HTTP ' + r.status);
      }
      return r;
    }).catch(function(e){
      log(method + ' ' + short(url) + ' → 网络失败 '
          + ((e && e.message) ? e.message : e));
      throw e;
    });
  };

  // 顺手记下没抓住的异常
  window.addEventListener('error', function(ev){
    if(ev && ev.message) log('JS 异常：' + ev.message);
  });

  window.__errline = {log: log, clear: function(){
    lines = []; box.style.display = 'none'; box.textContent = '';
  }};
})();
</script>
"""


def inject(html: str) -> str:
    i = html.rfind("</body>")
    if i == -1:
        return html
    return html[:i] + JS + html[i:]
