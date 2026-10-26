# -*- coding: utf-8 -*-
"""把粒子光团作为**独立一层**接到原版界面上。

关键原则：光团只是一个背景层，绝不能影响主界面。
  · 画布单独一个 <canvas id="orb">，放在 body 最前面
  · 它自己的 <script> 单独一块 —— 里面出错只会自己死，按钮照常
  · pointer-events:none，永远不抢点击
  · z-index:0，主界面 #app 提到 z-index:1

原版界面是深色底，所以这里强制用深色配色（亮粒子）。
"""
JS_CSS = """
<style>
  /* 光球占上方约 2/3，文字在下半部分 */
  #orb{position:fixed;left:0;right:0;top:0;width:100vw;height:50vh;z-index:0;
       pointer-events:none;display:block}
  #app,#warn,#inp,#bar,header,#hint2{position:relative;z-index:1}
  #panel,#setup,#mask,#dbg{z-index:40}

  /* 对话区往下压，给光球腾地方（字在下面） */

  /* 旧界面在窄屏上会横向溢出（右边被切掉，"试听/查厂商"看不全）。
     这几条只影响排版，不动功能。 */
  html,body{max-width:100%;overflow-x:hidden}
  #panel .row input,#panel .row select{min-width:0}
  #panel .row{flex-wrap:wrap}
  #panel .row button{flex:none}
  #panel .seg{min-width:0}
  #log,#app{max-width:100%;overflow-x:hidden}

  /* ================= 浅色主题 ================= */
  html.light{
    --bg:#f4f7fb;
    --bg2:#eef3f9;
    --ink:#16202e;
    --dim:#5b6b7d;
    --line:#dbe4ee;
    --accent:#2f7fe0;
  }
  html.light body{background:#f4f7fb;color:#16202e}
  html.light header{background:rgba(244,247,251,.92);
                    border-bottom:1px solid #dbe4ee}
  html.light #log{color:#16202e}
  html.light .msg.sys{color:#6b7c8f}
  html.light .msg.me{background:#dbeafe;color:#12305a}
  html.light .msg.it{background:#ffffff;border-color:#dbe4ee;color:#16202e}
  html.light #bar{background:#eef3f9;border-top:1px solid #dbe4ee}
  /* 注意：这几个按钮的颜色写死在主界面的 CSS 里，浅色下必须盖掉 */
  html.light #talk{background:#ffffff !important;border-color:#cfe0f5 !important;
                   color:#16202e !important}
  html.light #talk.rec{background:#ffe4e1 !important;
                       border-color:#f0a39c !important;color:#8a1f16 !important}
  html.light #txt,html.light #live{background:#ffffff !important;
      border-color:#dbe4ee !important;color:#4a5c70 !important}
  html.light #ti{background:#ffffff;border-color:#dbe4ee;color:#16202e}
  html.light #inp{background:#eef3f9}
  html.light #panel{background:rgba(250,252,255,.98)}
  html.light #panel h2,html.light #panel label{color:#16202e}
  html.light #panel .row label{color:#5b6b7d !important}
  html.light #panel input,html.light #panel select{background:#fff;
      border-color:#dbe4ee;color:#16202e}
  html.light #setup{background:rgba(248,251,255,.98);color:#16202e}
  html.light #setup input{background:#fff;border-color:#dbe4ee;color:#16202e}
  html.light #warn{color:#8a1f16}
  html.light #hint2{color:#5b6b7d}
  html.light #errline{background:rgba(200,40,40,.92)}

  /* 「实时」按钮：颜色交给主题，别再写死 */
  #sasrBtn{background:rgba(14,28,36,.92) !important;color:#8ad9e8 !important;
           border:1px solid #1d3745 !important}
  html.light #sasrBtn{background:#ffffff !important;color:#2f7fe0 !important;
           border:1px solid #cfe0f5 !important;
           box-shadow:0 4px 14px rgba(47,127,224,.18) !important}
  #sasrStrip{background:rgba(6,20,28,.88) !important;color:#cfe9f2 !important;
             border:1px solid #1d3745 !important}
  html.light #sasrStrip{background:rgba(255,255,255,.96) !important;
             color:#16202e !important;border:1px solid #dbe4ee !important;
             box-shadow:0 6px 20px rgba(20,40,70,.10) !important}

  /* 窄屏（手机）下把每一行改成上下排列：
     旧界面是 label + 输入框 + 按钮挤一行，屏幕一窄右边就被切掉，
     "查厂商""试听"看不全。改成竖排后一定装得下。 */
  @media (max-width:560px){
    #panel{box-sizing:border-box !important;width:100vw !important;
           max-width:100vw !important;overflow-x:hidden !important;
           gap:8px !important;padding:16px !important}
    #panel .row{display:block !important;max-width:100% !important}
    #panel .row label{display:block !important;width:auto !important;
                      margin:8px 0 4px !important;font-size:12.5px !important;
                      color:#7f93a3 !important}
    #panel .row input,#panel .row select{display:block !important;
           width:100% !important;max-width:100% !important;min-width:0 !important;
           box-sizing:border-box !important}
    #panel .row button{margin:8px 0 0 !important;max-width:100% !important}
    #panel .seg{display:flex !important;width:100% !important;
                max-width:100% !important;gap:6px !important}
    #panel .seg button{flex:1 1 0 !important;min-width:0 !important;
                       width:auto !important;margin:0 !important}
  }
</style>
"""

CANVAS = '<canvas id="orb"></canvas>'


def inject(html: str, orb_js: str) -> str:
    """在原版页面里挂上光团层。"""
    # 1. CSS 放进 head
    i = html.find("</head>")
    if i != -1:
        html = html[:i] + JS_CSS + html[i:]

    # 2. 画布放在 body 最前面
    j = html.find("<body>")
    if j != -1:
        html = html[:j + 6] + "\n" + CANVAS + html[j + 6:]

    # 3. 光团脚本单独一块，放在 </body> 之前
    k = html.rfind("</body>")
    if k != -1:
        block = ("<script>\n"
                 "/* 光团层（独立脚本，出错不影响界面） */\n"
                 "try{\n"
                 "  window.__BOOT_SETTINGS = window.__BOOT_SETTINGS || {};\n"
                 "  window.__BOOT_SETTINGS.orb = {speed:1.0, color:'#7fc4ff',"
                 " count:3000, theme:'dark'};\n"
                 + orb_js + "\n"
                 "}catch(e){ try{console.error('orb layer failed:', e);}catch(_){} }\n"
                 "/* 地址带 #settings 就自动打开设置面板（方便截图和直接调） */\n"
                 "try{\n"
                 "  if(location.hash === '#settings'){\n"
                 "    setTimeout(function(){\n"
                 "      var g = document.getElementById('gear');\n"
                 "      if(g) g.click();\n"
                 "    }, 700);\n"
                 "  }\n"
                 "}catch(e){}\n"
                 "</script>\n")
        html = html[:k] + block + html[k:]

    return html
