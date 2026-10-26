# -*- coding: utf-8 -*-
"""光球设置：速度 / 颜色 / 粒子数（独立一层）。

之前这些只存在于"新界面"里，界面回退后就没了。现在补回到设置面板，
仍然是不碰主脚本的独立层。

服务端接口已经有了：
  GET  /orb   -> {speed, color, count, theme}
  POST /orb   -> 改哪个传哪个，返回改完的完整设置
光球那边的接口：
  window.ORB.apply({speed, color, count, theme})  立刻生效
"""
JS = r"""
<script>
/* ================= 光球设置（独立层） ================= */
(function(){
  if(window.__orbSetLoaded) return;
  window.__orbSetLoaded = true;

  var $ = function(id){ return document.getElementById(id); };
  function tok(){
    return new URLSearchParams(location.search).get('token') ||
           (window.localStorage.getItem('ch_token') || '');
  }
  function call(method, body){
    var u = location.origin + '/orb';
    var t = tok();
    if(t) u += (u.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(t);
    var opt = {method: method, cache: 'no-store'};
    if(body !== undefined){
      opt.headers = {'Content-Type': 'application/json'};
      opt.body = JSON.stringify(body);
    }
    return fetch(u, opt).then(function(r){ return r.json(); });
  }

  var st = {speed: 1.0, color: '#2f7fe0', count: 3000, theme: 'dark',
            fx: {swirl: 1.0, breath: 1.0, turb: 1.0, glow: 1.0,
                 psize: 1.0, ripple: 1.0, twinkle: 1.0}};
  var sendTimer = null;

  // 特效：key、中文名、说明
  var FX = [
    ['swirl',   '涡流', '整体旋转的强弱'],
    ['breath',  '呼吸', '一涨一缩的幅度'],
    ['turb',    '扰动', '不规则抖动的幅度'],
    ['glow',    '亮度', '粒子有多亮'],
    ['psize',   '粒子', '粒子的大小'],
    ['ripple',  '涟漪', '想起来时那圈波纹'],
    ['twinkle', '闪烁', '一点点忽明忽暗']
  ];

  function apply(){
    try{ if(window.ORB && window.ORB.apply) window.ORB.apply(st); }catch(e){}
  }
  function saveSoon(){
    if(sendTimer) clearTimeout(sendTimer);
    sendTimer = setTimeout(function(){
      call('POST', {speed: st.speed, color: st.color, count: st.count,
                    fx: st.fx}).catch(function(){});
    }, 350);
  }

  var SWATCHES = ['#2f7fe0', '#7fc4ff', '#17a6c9', '#8b7cf6', '#e8799a'];

  function build(){
    var panel = $('panel');
    if(!panel || $('orbSpeedRange')) return;

    var wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;margin:2px 0 4px';

    wrap.innerHTML =
      '<div style="font-size:13px;color:#7f93a3;margin-top:6px">光球</div>' +

      '<div style="display:flex;align-items:center;gap:10px">' +
      '  <span style="font-size:12.5px;color:#7f93a3;width:56px;flex:none">速度</span>' +
      '  <input id="orbSpeedRange" type="range" min="0.15" max="2.5" step="0.05" value="1"' +
      '     style="flex:1;height:26px">' +
      '  <span id="orbSpeedVal" style="font-size:12.5px;width:44px;text-align:right;flex:none">1.00×</span>' +
      '</div>' +

      '<div style="display:flex;align-items:center;gap:10px">' +
      '  <span style="font-size:12.5px;color:#7f93a3;width:56px;flex:none">数量</span>' +
      '  <input id="orbCountRange" type="range" min="800" max="6000" step="200" value="3000"' +
      '     style="flex:1;height:26px">' +
      '  <span id="orbCountVal" style="font-size:12.5px;width:44px;text-align:right;flex:none">3000</span>' +
      '</div>' +

      '<div style="display:flex;align-items:center;gap:8px">' +
      '  <span style="font-size:12.5px;color:#7f93a3;width:56px;flex:none">颜色</span>' +
      '  <div id="orbSwatches" style="display:flex;gap:8px;flex:1"></div>' +
      '  <input id="orbColorPick" type="color" value="#2f7fe0"' +
      '     style="width:40px;height:32px;padding:0;border:1px solid #1d3745;' +
      '            border-radius:8px;background:transparent;flex:none">' +
      '</div>' +
      '<div style="font-size:13px;color:#7f93a3;margin-top:10px">光球特效</div>' +
      FX.map(function(f){
        return '<div style="display:flex;align-items:center;gap:10px">' +
               '  <span style="font-size:12.5px;color:#7f93a3;width:56px;flex:none">'
                 + f[1] + '</span>' +
               '  <input id="fx_' + f[0] + '" type="range" min="0.2" max="3" step="0.05"' +
               '     value="1" style="flex:1;height:26px">' +
               '  <span id="fxv_' + f[0] + '" style="font-size:12.5px;width:44px;' +
               '     text-align:right;flex:none">1.00</span>' +
               '</div>';
      }).join('') +
      '<div style="font-size:11.5px;color:#5c6b7a;line-height:1.5">' +
      '  1.00 是默认。往左更含蓄，往右更夸张。' +
      '</div>';

    // 插到「外观」那行前面；找不到就加到面板末尾（在按钮之前）
    var rows = panel.querySelectorAll('.row');
    var anchor = null;
    for(var i=0;i<rows.length;i++){
      if((rows[i].textContent || '').indexOf('外观') >= 0){ anchor = rows[i]; break; }
    }
    if(anchor) panel.insertBefore(wrap, anchor);
    else panel.appendChild(wrap);

    var sw = $('orbSwatches');
    SWATCHES.forEach(function(c){
      var b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('data-color', c);
      b.style.cssText = 'width:30px;height:30px;border-radius:50%;border:0;' +
                        'background:' + c + ';flex:none;cursor:pointer';
      b.addEventListener('click', function(){
        st.color = c;
        $('orbColorPick').value = c;
        paint();
        apply(); saveSoon();
      });
      sw.appendChild(b);
    });

    $('orbSpeedRange').addEventListener('input', function(){
      st.speed = parseFloat(this.value);
      $('orbSpeedVal').textContent = st.speed.toFixed(2) + '×';
      apply();
    });
    $('orbSpeedRange').addEventListener('change', function(){ saveSoon(); });

    $('orbCountRange').addEventListener('input', function(){
      st.count = parseInt(this.value, 10);
      $('orbCountVal').textContent = st.count;
      apply();
    });
    $('orbCountRange').addEventListener('change', function(){ saveSoon(); });

    $('orbColorPick').addEventListener('input', function(){
      st.color = this.value;
      paint();
      apply();
    });
    $('orbColorPick').addEventListener('change', function(){ saveSoon(); });

    // ---- 特效滑块 ----
    FX.forEach(function(f){
      var key = f[0];
      var el = $('fx_' + key);
      if(!el) return;
      el.addEventListener('input', function(){
        st.fx[key] = parseFloat(this.value);
        var lab = $('fxv_' + key);
        if(lab) lab.textContent = st.fx[key].toFixed(2);
        apply();
      });
      el.addEventListener('change', function(){ saveSoon(); });
    });
  }

  function paint(){
    var r = $('orbSpeedRange'), c = $('orbCountRange'), p = $('orbColorPick');
    if(r) r.value = st.speed;
    if(c) c.value = st.count;
    if(p) p.value = st.color;
    if($('orbSpeedVal')) $('orbSpeedVal').textContent = Number(st.speed).toFixed(2) + '×';
    if($('orbCountVal')) $('orbCountVal').textContent = st.count;
    FX.forEach(function(f){
      var el = $('fx_' + f[0]);
      if(el) el.value = st.fx[f[0]];
      var lab = $('fxv_' + f[0]);
      if(lab) lab.textContent = Number(st.fx[f[0]]).toFixed(2);
    });
    var bs = document.querySelectorAll('#orbSwatches button');
    for(var i=0;i<bs.length;i++){
      var on = bs[i].getAttribute('data-color').toLowerCase() === String(st.color).toLowerCase();
      bs[i].style.outline = on ? '2px solid #4fd1c5' : 'none';
      bs[i].style.outlineOffset = '2px';
    }
  }

  // 启动：先读服务端，再建界面
  call('GET').then(function(o){
    if(o){
      if(typeof o.speed === 'number') st.speed = o.speed;
      if(typeof o.count === 'number') st.count = o.count;
      if(o.color) st.color = o.color;
      if(o.fx && typeof o.fx === 'object'){
        for(var k in st.fx){
          if(typeof o.fx[k] === 'number') st.fx[k] = o.fx[k];
        }
      }
      if(o.theme) st.theme = o.theme;
    }
    build(); paint(); apply();
  }).catch(function(){ build(); paint(); });

  window.ORBSET = {get: function(){ return st; }, apply: apply};
})();
</script>
"""


def inject(html: str) -> str:
    i = html.rfind("</body>")
    if i == -1:
        return html
    return html[:i] + JS + html[i:]
