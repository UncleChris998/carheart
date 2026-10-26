# -*- coding: utf-8 -*-
"""光团备选对比页：把做过的几种风格摆在一起，一屏看完。

主人要看以前的版本。与其让他一版版扫码，不如做一页五宫格：
每种风格各一个小画布，同时跑，方向一眼比出来。

访问：http://<主机>:19388/orbdemo
"""
DEMO = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>光团备选</title>
<style>
  html,body{margin:0;background:#06070a;color:#cfe0f0;
    font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
  h1{font-size:15px;font-weight:500;padding:16px 16px 6px;margin:0;letter-spacing:.08em}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:10px 12px 24px}
  .cell{position:relative;border:1px solid rgba(120,180,255,.14);
    border-radius:14px;overflow:hidden;background:#05070b;aspect-ratio:3/4}
  .cell canvas{width:100%;height:100%;display:block}
  .cap{position:absolute;left:10px;bottom:8px;font-size:11px;
    color:#8fb0cc;letter-spacing:.06em;text-shadow:0 1px 8px #000}
  .cap b{color:#dceeff;font-weight:600}
  .last{grid-column:1 / -1;aspect-ratio:3/2}
</style></head><body>
<h1>光团 · 做过的几版</h1>
<div class="grid">
  <div class="cell"><canvas id="c1"></canvas><div class="cap"><b>① 点阵球</b> 暖白颗粒</div></div>
  <div class="cell"><canvas id="c2"></canvas><div class="cap"><b>② 螺旋光丝</b> 蒲公英感</div></div>
  <div class="cell"><canvas id="c3"></canvas><div class="cap"><b>③ 流体实心</b> 大模糊</div></div>
  <div class="cell"><canvas id="c4"></canvas><div class="cap"><b>④ 流动光丝</b> 蓝线游走</div></div>
  <div class="cell last"><canvas id="c5"></canvas><div class="cap"><b>⑤ 粒子生命体</b> WebGL 两万四千粒子（现在用的）</div></div>
</div>
<script>
/* ---------- ① 点阵球 ---------- */
(function(){
  var cv=document.getElementById('c1'), ctx=cv.getContext('2d');
  var P=[],N=520,DPR=Math.min(devicePixelRatio||1,2);
  for(var i=0;i<N;i++){
    var t=(i+.5)/N, phi=Math.acos(1-2*t), th=Math.PI*(1+Math.sqrt(5))*i;
    P.push({x:Math.sin(phi)*Math.cos(th), y:Math.sin(phi)*Math.sin(th),
            z:Math.cos(phi), s:.5+Math.random()});
  }
  function sz(){cv.width=cv.clientWidth*DPR;cv.height=cv.clientHeight*DPR;
    ctx.setTransform(DPR,0,0,DPR,0,0);}
  sz(); addEventListener('resize',sz);
  var t0=0;
  function f(){
    var W=cv.clientWidth,H=cv.clientHeight,CX=W/2,CY=H*.44,R=Math.min(W,H)*.30;
    t0+=.016; var rot=t0*.25;
    ctx.clearRect(0,0,W,H);
    ctx.globalCompositeOperation='lighter';
    var cy=Math.cos(rot),sy=Math.sin(rot);
    for(var i=0;i<N;i++){
      var p=P[i];
      var x=p.x*cy-p.z*sy, z=p.x*sy+p.z*cy;
      var d=(z+1)/2, sc=1/(1+(z+1)*.45);
      var px=CX+x*R*sc, py=CY+p.y*R*sc;
      var g=ctx.createRadialGradient(px,py,0,px,py,(2+d*5)*p.s);
      g.addColorStop(0,'rgba(255,236,214,'+(.10+d*d*.55)+')');
      g.addColorStop(1,'rgba(232,150,110,0)');
      ctx.fillStyle=g; ctx.beginPath();
      ctx.arc(px,py,(2+d*5)*p.s,0,6.283); ctx.fill();
    }
    requestAnimationFrame(f);
  }
  requestAnimationFrame(f);
})();

/* ---------- ② 螺旋光丝 ---------- */
(function(){
  var cv=document.getElementById('c2'), ctx=cv.getContext('2d');
  var DPR=Math.min(devicePixelRatio||1,2);
  function sz(){cv.width=cv.clientWidth*DPR;cv.height=cv.clientHeight*DPR;
    ctx.setTransform(DPR,0,0,DPR,0,0);}
  sz(); addEventListener('resize',sz);
  var F=[];
  for(var m=0;m<14;m++) F.push({tilt:(m/14-.5)*.62, turn:4.2+(m%3)*.9,
      ph:(m/14)*6.283, sp:.0021+(m%5)*.00016});
  var t0=0;
  function f(){
    var W=cv.clientWidth,H=cv.clientHeight,CX=W/2,CY=H*.44,R=Math.min(W,H)*.28;
    t0+=.016; var rad=R*(1+.02*Math.sin(t0*.8));
    ctx.fillStyle='rgba(6,7,10,.18)'; ctx.fillRect(0,0,W,H);
    ctx.globalCompositeOperation='lighter'; ctx.lineCap='round';
    for(var k=0;k<F.length;k++){
      var th=F[k], prev=null;
      for(var p=0;p<=70;p++){
        var u=p/70, ang=u*th.turn*6.283+th.ph+t0*th.sp*60;
        var lat=Math.sin(u*Math.PI)*2-1;
        var rr=Math.sqrt(Math.max(0,1-lat*lat));
        var x=Math.cos(ang)*rr, z=Math.sin(ang)*rr, y=lat;
        var x2=x*Math.cos(th.tilt)-y*Math.sin(th.tilt);
        var y2=x*Math.sin(th.tilt)+y*Math.cos(th.tilt);
        var sc=1/(1+(z+1)*.45);
        var px=CX+x2*rad*sc, py=CY+y2*rad*sc, d=(z+1)/2;
        if(prev){
          ctx.globalAlpha=Math.min(.8,(.03+Math.pow(d,2)*.34));
          ctx.lineWidth=.45+d*1.0;
          ctx.strokeStyle=d>.7?'rgba(232,246,255,1)':(d>.38?'rgba(150,205,255,1)':'rgba(70,125,215,1)');
          ctx.beginPath();ctx.moveTo(prev.px,prev.py);ctx.lineTo(px,py);ctx.stroke();
        }
        prev={px:px,py:py};
      }
    }
    ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';
    requestAnimationFrame(f);
  }
  requestAnimationFrame(f);
})();

/* ---------- ③ 流体实心 ---------- */
(function(){
  var cv=document.getElementById('c3'), ctx=cv.getContext('2d');
  var DPR=Math.min(devicePixelRatio||1,2);
  function sz(){cv.width=cv.clientWidth*DPR;cv.height=cv.clientHeight*DPR;
    ctx.setTransform(DPR,0,0,DPR,0,0);}
  sz(); addEventListener('resize',sz);
  var t0=0;
  function f(){
    var W=cv.clientWidth,H=cv.clientHeight,CX=W/2,CY=H*.44,R=Math.min(W,H)*.26;
    t0+=.016; var rad=R*(1+.02*Math.sin(t0*.7));
    ctx.clearRect(0,0,W,H);
    ctx.save(); ctx.filter='blur('+(rad*.19)+'px)';
    var g=ctx.createRadialGradient(CX-rad*.18,CY-rad*.24,rad*.05,CX,CY,rad);
    g.addColorStop(0,'rgba(236,248,255,.95)');
    g.addColorStop(.22,'rgba(180,220,255,.82)');
    g.addColorStop(.52,'rgba(110,170,250,.50)');
    g.addColorStop(1,'rgba(50,95,190,0)');
    ctx.fillStyle=g;ctx.beginPath();ctx.arc(CX,CY,rad,0,6.283);ctx.fill();
    ctx.restore();
    ctx.save(); ctx.filter='blur('+(rad*.1)+'px)';
    ctx.beginPath();ctx.arc(CX,CY,rad*.97,0,6.283);ctx.clip();
    var p1=t0*.22;
    ctx.strokeStyle='rgba(210,236,255,1)';ctx.globalAlpha=.3;ctx.lineWidth=rad*.3;ctx.lineCap='round';
    ctx.beginPath();ctx.arc(CX,CY,rad*.62,p1,p1+2.3);ctx.stroke();
    ctx.strokeStyle='rgba(150,205,255,1)';ctx.globalAlpha=.24;ctx.lineWidth=rad*.24;
    ctx.beginPath();ctx.arc(CX,CY,rad*.46,-p1*1.35,-p1*1.35+1.85);ctx.stroke();
    ctx.globalAlpha=1; ctx.restore();
    requestAnimationFrame(f);
  }
  requestAnimationFrame(f);
})();

/* ---------- ④ 流动光丝 ---------- */
(function(){
  var cv=document.getElementById('c4'), ctx=cv.getContext('2d');
  var DPR=Math.min(devicePixelRatio||1,2);
  function sz(){cv.width=cv.clientWidth*DPR;cv.height=cv.clientHeight*DPR;
    ctx.setTransform(DPR,0,0,DPR,0,0);}
  sz(); addEventListener('resize',sz);
  var S=[];
  for(var i=0;i<120;i++){
    var nz=Math.random()*2-1, nph=Math.random()*6.283;
    var nx=Math.sqrt(Math.max(0,1-nz*nz))*Math.cos(nph);
    var ny=Math.sqrt(Math.max(0,1-nz*nz))*Math.sin(nph);
    var ax=Math.abs(nx)<.9?1:0, ay=Math.abs(nx)<.9?0:1;
    var ux=ny*0-nz*ay, uy=nz*ax-nx*0, uz=nx*ay-ny*ax;
    var ul=Math.sqrt(ux*ux+uy*uy+uz*uz)||1; ux/=ul;uy/=ul;uz/=ul;
    var vx=ny*uz-nz*uy, vy=nz*ux-nx*uz, vz=nx*uy-ny*ux;
    S.push({ux:ux,uy:uy,uz:uz,vx:vx,vy:vy,vz:vz,
      ph:Math.random()*6.283, sp:(0.10+Math.random()*.22)*(Math.random()<.5?1:-1),
      len:.9+Math.random()*1.6, w:.7+Math.random()*1.3, br:.42+Math.random()*.58});
  }
  var t0=0;
  function f(){
    var W=cv.clientWidth,H=cv.clientHeight,CX=W/2,CY=H*.44,R=Math.min(W,H)*.27;
    t0+=.016; var rad=R;
    ctx.clearRect(0,0,W,H);
    ctx.globalCompositeOperation='lighter';ctx.lineCap='round';
    ctx.save();ctx.beginPath();ctx.arc(CX,CY,rad*.99,0,6.283);ctx.clip();
    for(var k=0;k<S.length;k++){
      var s=S[k], head=s.ph+t0*s.sp*1.6, pts=[];
      for(var q=0;q<=18;q++){
        var ff=q/18, a=head-(1-ff)*s.len;
        var ca=Math.cos(a),sa=Math.sin(a);
        var x=s.ux*ca+s.vx*sa, y=s.uy*ca+s.vy*sa, z=s.uz*ca+s.vz*sa;
        var sc=1/(1+(z+1)*.45);
        pts.push({x:CX+x*rad*sc, y:CY+y*rad*sc, d:(z+1)/2});
      }
      var SEC=[[0,6,.16],[6,12,.44],[12,18,.95]];
      for(var si=0;si<3;si++){
        var a0=SEC[si][0],a1=SEC[si][1],g0=SEC[si][2];
        var d=pts[Math.floor((a0+a1)/2)].d;
        var al=g0*(.30+d*.70)*s.br; if(al<.012) continue;
        ctx.globalAlpha=Math.min(.95,al);
        ctx.lineWidth=s.w*(.55+d*.75);
        ctx.strokeStyle=d>.6?'rgba(240,250,255,1)':(d>.3?'rgba(155,210,255,1)':'rgba(85,145,230,1)');
        ctx.beginPath();ctx.moveTo(pts[a0].x,pts[a0].y);
        for(var q2=a0+1;q2<=a1;q2++)ctx.lineTo(pts[q2].x,pts[q2].y);
        ctx.stroke();
      }
    }
    ctx.restore();ctx.globalCompositeOperation='source-over';
    requestAnimationFrame(f);
  }
  requestAnimationFrame(f);
})();
</script>
<script src="/orbwebgl.js"></script>
</body></html>
"""

# ⑤ 用当前线上那套 WebGL，但挂到 c5 这个画布上
def webgl_for_demo(js: str) -> str:
    return js.replace("document.getElementById('orb')",
                      "document.getElementById('c5')")
