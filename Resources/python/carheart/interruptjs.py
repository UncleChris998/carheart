# -*- coding: utf-8 -*-
"""打断机制：它说话的时候，你随时能插进去。

以前是"必须等它把话说完"—— 主人明确说这不行。

三种打断方式（都是独立一层，不碰主界面逻辑）：
  1. 点「说话」按钮：先掐掉声音，再开始录
  2. 点「实时」按钮：同上
  3. 直接点屏幕上的光球/对话区：只掐声音，不开始录（想先听完再决定）

技术上：主脚本的 speak() 里注册了 window.__speakCancel，
这里只管在合适的时机调它。
"""
JS = r"""
<script>
/* ================= 打断（独立层） ================= */
(function(){
  if(window.__interruptLoaded) return;
  window.__interruptLoaded = true;

  var $ = function(id){ return document.getElementById(id); };

  function isSpeaking(){
    if(window.__speakCancel) return true;
    try{
      if(window.speechSynthesis && window.speechSynthesis.speaking) return true;
    }catch(e){}
    return false;
  }

  function cut(){
    var did = false;
    if(window.__speakCancel){
      try{ window.__speakCancel(); did = true; }catch(e){}
    }
    try{
      if(window.speechSynthesis) window.speechSynthesis.cancel();
    }catch(e){}
    // 兜底：把所有还在播的 <audio> 都暂停（主脚本可能换过元素）
    try{
      var as = document.getElementsByTagName('audio');
      for(var i=0;i<as.length;i++){
        if(!as[i].paused && as[i].src){ as[i].pause(); did = true; }
      }
    }catch(e){}
    if(did) hint('打断了');
    return did;
  }

  var hintTimer = null;
  function hint(t){
    var el = $('cutHint');
    if(!el){
      el = document.createElement('div');
      el.id = 'cutHint';
      el.style.cssText = 'position:fixed;left:50%;top:56px;transform:translateX(-50%);' +
        'z-index:30;padding:6px 14px;border-radius:999px;font-size:12.5px;' +
        'background:rgba(20,32,44,.82);color:#cfe9f2;pointer-events:none;' +
        'opacity:0;transition:opacity .2s;backdrop-filter:blur(8px)';
      document.body.appendChild(el);
    }
    el.textContent = t;
    el.style.opacity = '1';
    if(hintTimer) clearTimeout(hintTimer);
    hintTimer = setTimeout(function(){ el.style.opacity = '0'; }, 1200);
  }

  // 按钮：先掐声音，再让原逻辑继续（不 preventDefault）
  ['talk', 'sasrBtn'].forEach(function(id){
    var b = $(id);
    if(!b) return;
    b.addEventListener('click', function(){
      if(isSpeaking()) cut();
    }, true);
  });

  // 点对话区 / 光球：只掐声音
  var log = $('log');
  if(log){
    log.addEventListener('click', function(){
      if(isSpeaking()) cut();
    }, true);
  }
  var orb = $('orb');
  if(orb){
    // 画布是 pointer-events:none，这里只是为了以后放开时也能用
    orb.addEventListener('click', function(){ if(isSpeaking()) cut(); }, true);
  }

  // Esc：打断
  document.addEventListener('keydown', function(e){
    if(e.key === 'Escape' && isSpeaking()) cut();
  });

  /* ============ 连续对话防自激 ============
     它自己说话的时候，麦克风会听见自己 → 又识别出一句话 → 又答一遍。
     这里在识别结果入口加一道闸：
       · 正在播报（__assistantSpeaking）→ 丢弃
       · 刚刚说过的一模一样 / 高度相似 → 丢弃（回声）
       · 一句话不到 2 个字 → 丢弃（噪声）
     注意：必须包在原生桥的上游，所以直接替换 window.onNativeASR。 */
  var lastSentText = '';
  var lastSentAt = 0;
  var droppedCount = 0;

  function similar(a, b){
    a = (a || '').replace(/[\s。，、？！,.?!]/g, '');
    b = (b || '').replace(/[\s。，、？！,.?!]/g, '');
    if(!a || !b) return false;
    if(a === b) return true;
    if(a.length >= 4 && b.length >= 4){
      // 一方包含另一方，超过六成就算回声
      var shortA = a.length <= b.length ? a : b;
      var longB  = a.length <= b.length ? b : a;
      var hit = 0;
      for(var i = 0; i < shortA.length; i++){
        if(longB.indexOf(shortA.charAt(i)) >= 0) hit++;
      }
      return hit / shortA.length >= 0.6;
    }
    return false;
  }

  function installGuard(){
    var raw = window.onNativeASR;
    if(typeof raw !== 'function' || raw.__guarded){ return false; }
    var wrapped = function(text, isFinal){
      var t = (text || '').trim();
      if(window.__assistantSpeaking){
        droppedCount++;
        try{ console.log('[guard] 丢弃（正在播报）: ' + t); }catch(e){}
        return;
      }
      if(t.length < 2){
        droppedCount++;
        return;
      }
      if(Date.now() - lastSentAt < 8000 && similar(t, lastSentText)){
        droppedCount++;
        try{ console.log('[guard] 丢弃（疑似回声）: ' + t); }catch(e){}
        return;
      }
      if(isFinal && t){ lastSentText = t; lastSentAt = Date.now(); }
      return raw.apply(this, arguments);
    };
    wrapped.__guarded = true;
    window.onNativeASR = wrapped;
    return true;
  }

  // 必须一直盯着：连续对话开始时会自己重新赋值 window.onNativeASR，
  // 包一次就被绕过（踩过：自检显示"没被包过"）。
  // 这个检查很轻（一次 typeof 判断），长期跑没关系。
  setInterval(installGuard, 700);

  window.INTERRUPT = {
    cut: cut, isSpeaking: isSpeaking,
    dropped: function(){ return droppedCount; },
    resetLast: function(){ lastSentText = ''; lastSentAt = 0; }
  };
})();
</script>
"""


def inject(html: str) -> str:
    i = html.rfind("</body>")
    if i == -1:
        return html
    return html[:i] + JS + html[i:]
