# -*- coding: utf-8 -*-
"""布局层：光球占上半屏，聊天区固定在下方，文字永远不会压到光球上。

做法（全部是独立一层，不碰主界面）：
  · 光球画布固定在上面 50vh
  · 聊天区改成固定定位，从 50vh 到屏幕底部，自己带不透明背景
    → 消息再多也只是在自己区域内滚动，不可能盖到光球
  · 底栏和输入框钉在底部，聊天区底部留出它们的高度
  · 顶栏压成一行（长了就省略号），不再折行显得破

为什么不用原来的 flex 布局：原来 #log 是 flex:1，只要消息一多就会
往上滚到光球上面 —— 主人明确说"文字不能碰到光团"。
"""
CSS = """
<style>
  /* 光球：上半屏 */
  #orb{height:50vh !important}

  /* 聊天区：固定在光球下方，自带背景，滚动不外溢 */
  #log{
    position:fixed !important;
    left:0; right:0; top:50vh; bottom:0;
    z-index:1 !important;
    overflow-y:auto !important;
    background:var(--bg);
    padding:10px 14px 96px !important;
    box-sizing:border-box;
  }

  /* 顶栏与底栏压在聊天区之上 */
  header{z-index:3 !important}
  #bar{position:fixed !important;left:0;right:0;bottom:0;
       z-index:3 !important}
  #inp{position:fixed !important;left:0;right:0;bottom:76px;
       z-index:3 !important}

  /* 顶栏一行显示，太长就省略号 */
  header{display:flex !important;align-items:center !important;
         gap:8px !important;padding:10px 14px !important;
         min-height:52px;box-sizing:border-box}
  #who{flex:none;font-size:17px;font-weight:700;white-space:nowrap}
  #meta{flex:1;min-width:0;font-size:12px !important;
        white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
        color:var(--dim) !important}
  #gear{flex:none}

  /* 消息气泡收紧一点，别显得散 */
  #log .msg{margin:6px 0;line-height:1.55}
  #log .msg.sys{font-size:13px;opacity:.9;margin:8px 0}
</style>
"""

JS = r"""
<script>
/* ============ 布局：底栏高度动态补偿（独立层） ============ */
(function(){
  function fit(){
    var bar = document.getElementById('bar');
    var inp = document.getElementById('inp');
    var log = document.getElementById('log');
    if(!bar || !log) return;

    var barH = bar.offsetHeight || 76;
    var inpShowing = inp && getComputedStyle(inp).display !== 'none';
    var inpH = inpShowing ? (inp.offsetHeight || 0) : 0;

    // 输入框在底栏上方
    if(inp) inp.style.bottom = barH + 'px';

    // 聊天区底部让出底栏 + 输入框
    log.style.paddingBottom = (barH + inpH + 16) + 'px';
  }

  window.addEventListener('resize', fit);
  document.addEventListener('DOMContentLoaded', fit);

  // #inp 显示/隐藏是靠 style 改的，用观察器盯住
  function watch(){
    var inp = document.getElementById('inp');
    if(inp && window.MutationObserver){
      new MutationObserver(fit).observe(inp, {attributes:true,
        attributeFilter:['style', 'class']});
    }
    fit();
    setInterval(fit, 1500);        // 兜底：高度变了也能跟上
  }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', watch);
  } else {
    watch();
  }
})();
</script>
"""


def inject(html: str) -> str:
    i = html.find("</head>")
    if i != -1:
        html = html[:i] + CSS + html[i:]
    j = html.rfind("</body>")
    if j != -1:
        html = html[:j] + JS + html[j:]
    return html
