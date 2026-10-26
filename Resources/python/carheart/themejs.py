# -*- coding: utf-8 -*-
"""深色 / 浅色主题切换（独立一层，不碰主界面）。

- 主题存在服务端 data/orb.json 的 theme 字段（跟光团设置放一起）
- 页面打开时按服务端的值套上，切了就立刻生效并存盘
- 设置面板里加一行「外观」
- 光团颜色也跟着换（深色底亮粒子，浅色底深粒子）
"""
JS = r"""
<script>
/* ================= 深浅主题（独立层） ================= */
(function(){
  if(window.__themeLoaded) return;
  window.__themeLoaded = true;

  var $ = function(id){ return document.getElementById(id); };
  function tok(){
    return new URLSearchParams(location.search).get('token') ||
           (window.localStorage.getItem('ch_token') || '');
  }
  function api(method, path, body){
    var u = location.origin + path;
    var t = tok();
    if(t) u += (path.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(t);
    var opt = {method: method, cache: 'no-store'};
    if(body !== undefined){
      opt.headers = {'Content-Type': 'application/json'};
      opt.body = JSON.stringify(body);
    }
    return fetch(u, opt).then(function(r){ return r.json(); });
  }

  var theme = 'dark';

  function paint(t){
    theme = (t === 'light') ? 'light' : 'dark';
    var de = document.documentElement;
    de.classList.toggle('light', theme === 'light');
    de.classList.toggle('dark', theme === 'dark');
    try{
      if(window.ORB && window.ORB.setTheme) window.ORB.setTheme(theme);
    }catch(e){}
    var d = $('themeDarkBtn'), l = $('themeLightBtn');
    // 颜色交给皮肤层（skin.py 的 #panel .seg button / .on），
    // 之前内联写死颜色，浅色主题下会一块黑一块白。
    if(d) d.className = (theme === 'dark') ? 'on' : '';
    if(l) l.className = (theme === 'light') ? 'on' : '';
  }

  function save(t){
    paint(t);
    api('POST', '/orb', {theme: t}).catch(function(){});
  }

  // ---------- 设置面板里加一行 ----------
  function addRow(){
    var panel = $('panel');
    if(!panel || $('themeDarkBtn')) return;
    var row = document.createElement('div');
    row.className = 'row';
    row.innerHTML =
      '<label>外观</label>' +
      '<div class="seg" style="display:flex;gap:8px;flex:1">' +
      '  <button type="button" id="themeDarkBtn">深色</button>' +
      '  <button type="button" id="themeLightBtn">浅色</button>' +
      '</div>';
    var rows = panel.querySelectorAll('.row');
    var anchor = null;
    for(var i=0;i<rows.length;i++){
      if((rows[i].textContent || '').indexOf('语音识别') >= 0){ anchor = rows[i]; break; }
    }
    if(anchor) panel.insertBefore(row, anchor);
    else panel.insertBefore(row, panel.firstChild && panel.firstChild.nextSibling);
    $('themeDarkBtn').addEventListener('click', function(){ save('dark'); });
    $('themeLightBtn').addEventListener('click', function(){ save('light'); });
    paint(theme);
  }

  // ---------- 启动 ----------
  api('GET', '/orb').then(function(o){
    if(o && o.theme) paint(o.theme);
    addRow();
  }).catch(function(){ addRow(); });

  window.THEME = {set: paint, get: function(){ return theme; }};
})();
</script>
"""


def inject(html: str) -> str:
    i = html.rfind("</body>")
    if i == -1:
        return html
    return html[:i] + JS + html[i:]
