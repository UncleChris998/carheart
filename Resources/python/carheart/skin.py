# -*- coding: utf-8 -*-
"""视觉皮肤层：让界面看起来是"成品"，不是"能跑"。

只改外观，不动任何逻辑（独立 <style>，全部 !important 盖过主界面）。

要点（对标微信那种质感）：
  · 顶栏和底栏用毛玻璃（backdrop-blur）+ 半透明，内容从下面透上来
  · 消息做成微信那种气泡：圆角 18px、内侧小圆角、极轻阴影、最大 78% 宽
  · 主按钮用渐变 + 柔和投影，不是一块死色
  · 全局字体字重、行高、字距统一，不要忽大忽小
  · 光球那块单独留白，不摆任何控件，像"壁纸"
"""
CSS = """
<style>
  /* ---------- 全局底色与字体 ---------- */
  html{ -webkit-text-size-adjust:100%; }
  body{
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",
                "Helvetica Neue","Microsoft YaHei",system-ui,sans-serif !important;
    font-size:15px;
    letter-spacing:.2px;
    -webkit-font-smoothing:antialiased;
  }
  html.dark body{
    background:radial-gradient(120% 80% at 50% 0%, #0f2232 0%, #0a141c 55%, #08111a 100%)
                !important;
  }
  html.light body{
    background:radial-gradient(120% 80% at 50% 0%, #ffffff 0%, #f5f8fc 45%, #eef3f9 100%)
                !important;
  }

  /* ---------- 顶栏：毛玻璃，一行 ---------- */
  header{
    background:transparent !important;
    border-bottom:1px solid rgba(255,255,255,.06) !important;
    backdrop-filter:blur(18px) saturate(140%);
    -webkit-backdrop-filter:blur(18px) saturate(140%);
    padding-top:calc(10px + env(safe-area-inset-top, 0px)) !important;
  }
  html.light header{
    background:rgba(255,255,255,.68) !important;
    border-bottom:1px solid rgba(16,24,40,.06) !important;
  }
  #who{ font-size:17px !important; font-weight:650 !important;
        letter-spacing:.5px; }
  #meta{ font-size:11.5px !important; opacity:.72; letter-spacing:.1px; }
  #dot{ width:8px !important; height:8px !important; border-radius:50%;
        box-shadow:0 0 0 3px rgba(79,209,197,.15); }
  #gear{ opacity:.55; transition:opacity .2s; }
  #gear:hover{ opacity:.9; }

  /* ---------- 消息：微信那种气泡 ---------- */
  #log{ background:transparent !important; padding:14px 14px 96px !important; }
  #log .msg{
    max-width:78% !important;
    padding:10px 14px !important;
    border-radius:18px !important;
    margin:7px 0 !important;
    line-height:1.6 !important;
    font-size:15px !important;
    word-break:break-word;
    animation:pop .18s ease-out;
  }
  @keyframes pop{ from{ transform:translateY(4px); opacity:0 } to{ transform:none; opacity:1 } }

  /* 它说的（左） */
  #log .msg.it{
    margin-right:auto !important;
    border-top-left-radius:6px !important;
  }
  html.dark #log .msg.it{
    background:linear-gradient(180deg, rgba(30,46,60,.96), rgba(23,36,48,.96)) !important;
    color:#e8f1f7 !important;
    border:1px solid rgba(255,255,255,.07) !important;
    box-shadow:0 1px 2px rgba(0,0,0,.25), 0 10px 24px rgba(0,0,0,.18);
  }
  html.light #log .msg.it{
    background:#ffffff !important;
    color:#16202e !important;
    border:1px solid rgba(16,24,40,.05) !important;
    box-shadow:0 1px 2px rgba(16,24,40,.05), 0 10px 24px rgba(16,24,40,.05);
  }

  /* 我说的（右） */
  #log .msg.me{
    margin-left:auto !important;
    border-top-right-radius:6px !important;
    color:#ffffff !important;
    border:0 !important;
    background:linear-gradient(135deg,#4f9bff 0%, #2f7fe0 100%) !important;
    box-shadow:0 6px 18px rgba(47,127,224,.28);
  }

  /* 系统提示：居中小字，不要抢戏 */
  #log .msg.sys{
    background:transparent !important;
    border:0 !important;
    box-shadow:none !important;
    max-width:100% !important;
    text-align:center;
    font-size:12.5px !important;
    opacity:.68;
    margin:10px 0 !important;
    padding:2px 8px !important;
  }

  /* ---------- 底栏：毛玻璃 + 胶囊按钮 ---------- */
  #bar{
    background:rgba(10,20,28,.72) !important;
    border-top:1px solid rgba(255,255,255,.06) !important;
    backdrop-filter:blur(20px) saturate(150%);
    -webkit-backdrop-filter:blur(20px) saturate(150%);
    padding:10px 12px calc(10px + env(safe-area-inset-bottom, 0px)) !important;
  }
  html.light #bar{
    background:rgba(255,255,255,.80) !important;
    border-top:1px solid rgba(16,24,40,.07) !important;
  }

  #talk{
    height:50px !important;
    border-radius:999px !important;
    border:0 !important;
    font-size:16.5px !important;
    font-weight:650 !important;
    letter-spacing:1px;
    color:#fff !important;
    background:linear-gradient(135deg,#4f9bff 0%, #2f7fe0 100%) !important;
    box-shadow:0 8px 22px rgba(47,127,224,.32) !important;
    transition:transform .12s ease, box-shadow .2s;
  }
  #talk:active{ transform:scale(.985); box-shadow:0 4px 12px rgba(47,127,224,.30) !important; }
  #talk.rec{
    background:linear-gradient(135deg,#ff7a6b 0%, #e2453a 100%) !important;
    box-shadow:0 8px 22px rgba(226,69,58,.35) !important;
    color:#fff !important;
  }

  #txt,#live{
    width:50px !important; height:50px !important;
    border-radius:999px !important;
    border:1px solid rgba(255,255,255,.10) !important;
    background:rgba(255,255,255,.06) !important;
    color:#cfe1ee !important;
    font-size:19px !important;
    box-shadow:0 4px 14px rgba(0,0,0,.18);
  }
  html.light #txt,html.light #live{
    border:1px solid rgba(16,24,40,.08) !important;
    background:#ffffff !important;
    color:#41556a !important;
    box-shadow:0 4px 14px rgba(16,24,40,.06);
  }

  /* 浅色下主按钮也要是蓝渐变 ——
     主题层为了"白底不刺眼"把它刷成了白色，这里是刻意的覆盖：
     按钮是页面唯一的主色块，刷白就没重点了。 */
  html.light #talk{
    background:linear-gradient(135deg,#4f9bff 0%, #2f7fe0 100%) !important;
    color:#ffffff !important;
    border:0 !important;
    box-shadow:0 8px 22px rgba(47,127,224,.28) !important;
  }
  html.light #talk.rec{
    background:linear-gradient(135deg,#ff7a6b 0%, #e2453a 100%) !important;
    color:#ffffff !important;
    box-shadow:0 8px 22px rgba(226,69,58,.30) !important;
  }

  /* 输入行同样玻璃感 */
  #inp{
    background:rgba(10,20,28,.72) !important;
    backdrop-filter:blur(20px);
    -webkit-backdrop-filter:blur(20px);
    padding:8px 12px 10px !important;
  }
  html.light #inp{ background:rgba(255,255,255,.80) !important; }
  #ti{
    height:46px !important; border-radius:999px !important;
    border:1px solid rgba(255,255,255,.10) !important;
    background:rgba(255,255,255,.07) !important;
    color:#eaf3f9 !important;
    padding:0 18px !important; font-size:15px !important;
  }
  html.light #ti{
    border:1px solid rgba(16,24,40,.08) !important;
    background:#ffffff !important; color:#16202e !important;
  }
  #ts{
    border-radius:999px !important; height:46px !important;
    background:linear-gradient(135deg,#4f9bff,#2f7fe0) !important;
    color:#fff !important; border:0 !important; font-weight:600 !important;
  }

  /* ---------- 实时按钮：浮在上面，跟主题走 ---------- */
  #sasrBtn{
    width:54px !important; height:54px !important;
    border-radius:999px !important;
    font-size:12px !important; font-weight:650 !important;
    letter-spacing:1px;
    border:1px solid rgba(255,255,255,.12) !important;
    background:rgba(16,32,44,.78) !important;
    color:#9fe3f2 !important;
    backdrop-filter:blur(14px);
    -webkit-backdrop-filter:blur(14px);
    box-shadow:0 10px 26px rgba(0,0,0,.28) !important;
    bottom:96px !important;
  }
  html.light #sasrBtn{
    border:1px solid rgba(16,24,40,.08) !important;
    background:rgba(255,255,255,.92) !important;
    color:#2f7fe0 !important;
    box-shadow:0 10px 26px rgba(16,24,40,.10) !important;
  }

  #sasrStrip{
    border-radius:16px !important;
    padding:12px 14px !important;
    font-size:14px !important;
    line-height:1.6 !important;
    box-shadow:0 12px 30px rgba(0,0,0,.28) !important;
  }
  html.light #sasrStrip{ box-shadow:0 12px 30px rgba(16,24,40,.10) !important; }

  /* ---------- 设置面板：整齐、留白、分区分明 ---------- */
  #panel{ padding:20px 18px !important; }
  #panel h2{ font-size:19px !important; font-weight:700 !important;
             letter-spacing:.5px; margin:2px 0 10px !important; }
  #panel .row{ margin-bottom:4px; }
  #panel .row label{ font-size:12.5px !important; font-weight:600 !important;
                     letter-spacing:.4px; text-transform:none;
                     opacity:.72; }

  /* 面板里所有分段按钮统一外观（深色底稿）：
     之前几个按钮各自内联写死颜色，浅色主题下会出现黑一块白一块。 */
  #panel .seg{ display:flex !important; gap:8px !important; }
  #panel .seg button{
    flex:1 1 0 !important; min-width:0 !important;
    height:42px !important; border-radius:12px !important;
    border:1px solid rgba(255,255,255,.12) !important;
    background:rgba(255,255,255,.05) !important;
    color:#c8d8e4 !important;
    font-size:14px !important; font-weight:500 !important;
    transition:background .15s, color .15s, border-color .15s;
  }
  #panel .seg button.on{
    background:linear-gradient(135deg,#4f9bff,#2f7fe0) !important;
    border-color:transparent !important;
    color:#ffffff !important;
    font-weight:650 !important;
    box-shadow:0 6px 16px rgba(47,127,224,.28);
  }
  html.light #panel .seg button{
    border:1px solid rgba(16,24,40,.10) !important;
    background:#ffffff !important;
    color:#41556a !important;
  }
  html.light #panel .seg button.on{
    background:linear-gradient(135deg,#4f9bff,#2f7fe0) !important;
    border-color:transparent !important;
    color:#ffffff !important;
  }

  /* 设置里那些说明文字（我加的）颜色统一 */
  #panel [id^="fxv_"], #panel #orbSpeedVal, #panel #orbCountVal{
    color:#8fa3b3 !important; font-variant-numeric:tabular-nums;
  }
  html.light #panel [id^="fxv_"], html.light #panel #orbSpeedVal,
  html.light #panel #orbCountVal{ color:#6b7c8f !important; }

  #panel input[type=range]{
    -webkit-appearance:none; appearance:none;
    height:4px !important; border-radius:999px !important;
    background:rgba(127,147,163,.30) !important;
    outline:none;
  }
  #panel input[type=range]::-webkit-slider-thumb{
    -webkit-appearance:none; width:18px; height:18px; border-radius:50%;
    background:#4fd1c5; border:2px solid #fff;
    box-shadow:0 2px 8px rgba(0,0,0,.25);
  }
  #panel input[type=color]{ border-radius:10px !important; }
  #panel .seg{ display:flex; gap:8px; }
  #panel .seg button{
    border-radius:12px !important;
    font-size:14px !important;
  }

  /* 说话时草稿气泡末尾的光标 */
  @keyframes blink{ 0%,49%{opacity:.55} 50%,100%{opacity:0} }
  #log .msg.draft{ opacity:.94; }
  #log .msg.draft span{ font-weight:400; }

  /* 滚动条细一点，别那么突兀 */
  ::-webkit-scrollbar{ width:6px; height:6px; }
  ::-webkit-scrollbar-thumb{
    background:rgba(140,160,180,.28); border-radius:999px;
  }
  ::-webkit-scrollbar-track{ background:transparent; }
</style>
"""


def inject(html: str) -> str:
    i = html.find("</head>")
    if i == -1:
        return html
    return html[:i] + CSS + html[i:]
