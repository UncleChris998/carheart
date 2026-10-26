# -*- coding: utf-8 -*-
"""AI 生命体的粒子光团 —— 纯 WebGL（支持浅色/深色、可调速度/颜色/粒子数）。

依赖：无（不引 Three.js，离线可用）

注意两条硬规则（都踩过坑）：
  1. 着色器源码里**只能有 ASCII** —— 中文注释会让驱动直接编译失败，
     而编译失败不会抛 JS 异常，只是画面空白，极难查。
  2. 这个文件里函数顺序要小心：applyBootSettings() 必须放在所有
     var 初始化之后调用，否则设置会被后面的默认值覆盖。

对外接口：
  window.setAIState(mood, isSpeaking, memoryTrigger)
  window.ORB.set / level / look / memory / apply / setTheme / state
"""
JS = r"""
/* ================= AI 生命体 · 粒子光团（WebGL）================= */
(function(){
  var cv = document.getElementById('orb');
  if(!cv) return;

  var gl = cv.getContext('webgl', {
      alpha: true, antialias: false, depth: false,
      premultipliedAlpha: false, powerPreference: 'high-performance'
  }) || cv.getContext('experimental-webgl');
  if(!gl) return;

  var CONFIG = {
    MAX_COUNT: 6000,
    COUNT: 3000,
    SIZE: 2.7,
    DIAMETER: 0.72,
    CENTER_Y: 0.50,   // 0.5 = 正好在画布（空白区）中间
    RIPPLE_TIME: 1.7,
    MOODS: {
      calm:    { a:[0.20,0.42,0.84], b:[0.52,0.70,0.97], swirl:0.60, breath:0.055, turb:0.35, size:1.10, bright:3.00 },
      gentle:  { a:[0.30,0.42,0.88], b:[0.62,0.70,0.98], swirl:0.48, breath:0.050, turb:0.30, size:1.12, bright:2.85 },
      curious: { a:[0.16,0.44,0.90], b:[0.46,0.70,1.00], swirl:1.00, breath:0.042, turb:0.62, size:1.12, bright:3.30 },
      happy:   { a:[0.12,0.40,0.92], b:[0.44,0.68,1.00], swirl:1.30, breath:0.070, turb:0.58, size:1.16, bright:3.60 },
      tired:   { a:[0.36,0.42,0.62], b:[0.66,0.70,0.86], swirl:0.30, breath:0.014, turb:0.18, size:1.02, bright:2.05 }
    }
  };

  /* ---------- 顶点着色器（ASCII only）---------- */
  var VS = [
    'attribute vec3 aDir;',
    'attribute vec3 aSeed;',
    'uniform float uTime, uSwirl, uBreath, uTurb, uSize, uBright;',
    'uniform float uRipple, uAudio, uHalfW, uHalfH, uRadiusPx, uDpr, uOffsetY;',
    'uniform float uTwk;',
    'uniform vec3 uColA, uColB;',
    'varying vec3 vColor;',
    'varying float vAlpha;',
    '',
    'void main(){',
    '  float t = uTime;',
    '  vec3 d = normalize(aDir);',
    '',
    '  float layer = aSeed.y;',
    '  float ang = t * (0.10 + layer * 0.30 + aSeed.x * 0.10) * uSwirl;',
    '  float ca = cos(ang), sa = sin(ang);',
    '  vec3 p = vec3(d.x*ca - d.z*sa, d.y, d.x*sa + d.z*ca);',
    '',
    '  float r = mix(0.34, 1.06, pow(aSeed.y, 0.92));',
    '  r *= 1.0 + uBreath * sin(t*0.90 + aSeed.z*6.2831);',
    '  r *= 1.0 + 0.10 * sin(t*0.20 + aSeed.x*3.0) * uTurb;',
    '',
    '  float edge = step(0.55, aSeed.z);',
    '  r *= 1.0 + edge * 0.62 * (0.5 + 0.5*sin(t*0.16 + aSeed.x*2.2));',
    '',
    '  p += 0.09 * uTurb * vec3(',
    '     sin(t*0.24 + aSeed.x*3.0),',
    '     sin(t*0.21 + aSeed.y*3.6),',
    '     sin(t*0.27 + aSeed.z*4.2));',
    '',
    '  float tiltA = 0.13 * sin(t * 0.12);',
    '  float cA = cos(tiltA), sA = sin(tiltA);',
    '  p = vec3(p.x, p.y*cA - p.z*sA, p.y*sA + p.z*cA);',
    '',
    '  float tiltB = 0.08 * cos(t * 0.09);',
    '  float cB = cos(tiltB), sB = sin(tiltB);',
    '  p = vec3(p.x*cB - p.y*sB, p.x*sB + p.y*cB, p.z);',
    '',
    '  vec3 pos = p * r;',
    '',
    '  float band = 0.0;',
    '  if(uRipple >= 0.0){',
    '    float rr = mix(-0.15, 1.9, uRipple);',
    '    band = exp(-pow((r - rr) * 4.0, 2.0)) * (1.0 - uRipple);',
    '  }',
    '',
    '  gl_Position = vec4(pos.x * uRadiusPx / uHalfW,',
    '                     pos.y * uRadiusPx / uHalfH + uOffsetY, 0.0, 1.0);',
    '',
    '  float depth = clamp(0.5 + 0.5*pos.z, 0.0, 1.0);',
    '  // 所有粒子一样大 —— 只保留远近造成的轻微变化，不再随机',
    '  gl_PointSize = uSize * uDpr * (0.92 + 0.16*depth)',
    '                 * (1.0 + band*1.8 + uAudio*0.45);',
    '',
    '  float core = 1.0 - smoothstep(0.0, 1.05, r);',
    '  float edgeFade = 1.0 - smoothstep(0.85, 1.85, r);',
    '  float grain = 0.55 + 0.45*aSeed.x;',
    '  float twinkle = mix(1.0, 0.95 + 0.05*sin(t*0.45 + aSeed.y*8.0), uTwk);',
    '',
    '  vAlpha = uBright * (0.24 + 0.52*core + 0.85*band + 0.12*uAudio)',
    '           * edgeFade * grain * twinkle;',
    '',
    '  vColor = mix(uColB, uColA, depth);',
    '  vec3 tintC = vec3(0.82,0.94,1.00);',
    '  vec3 tintV = vec3(0.88,0.86,1.00);',
    '  vec3 tint = mix(tintC, tintV, aSeed.y);',
    '  vColor = mix(vColor, vColor * tint, 0.45);',
    '  vColor = mix(vColor, vec3(1.0,1.0,1.0), band*0.55);',
    '}'
  ].join('\n');

  /* ---------- 片元着色器：亮芯 + 柔边 ---------- */
  var FS = [
    'precision mediump float;',
    'varying vec3 vColor;',
    'varying float vAlpha;',
    'void main(){',
    '  vec2 q = gl_PointCoord - vec2(0.5);',
    '  float d = length(q);',
    '  if(d > 0.5) discard;',
    '  float halo = smoothstep(0.5, 0.0, d);',
    '  float core = pow(halo, 3.0);',
    '  float a = core * 1.00 + halo * 0.10;',
    '  gl_FragColor = vec4(vColor, a * vAlpha);',
    '}'
  ].join('\n');

  function compile(type, src){
    var sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if(!gl.getShaderParameter(sh, gl.COMPILE_STATUS)){
      console.error('shader error: ' + gl.getShaderInfoLog(sh));
      return null;
    }
    return sh;
  }
  var vs = compile(gl.VERTEX_SHADER, VS);
  var fs = compile(gl.FRAGMENT_SHADER, FS);
  if(!vs || !fs) return;

  var prog = gl.createProgram();
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if(!gl.getProgramParameter(prog, gl.LINK_STATUS)){
    console.error('link error: ' + gl.getProgramInfoLog(prog));
    return;
  }
  gl.useProgram(prog);

  /* ---------- 粒子 ---------- */
  var MAXN = CONFIG.MAX_COUNT;
  var activeCount = Math.min(CONFIG.COUNT, MAXN);
  var dir = new Float32Array(MAXN * 3);
  var seed = new Float32Array(MAXN * 3);
  for(var i = 0; i < MAXN; i++){
    var u = (i + 0.5) / MAXN;
    var phi = Math.acos(1 - 2 * u);
    var theta = Math.PI * (1 + Math.sqrt(5)) * i;
    dir[i*3]   = Math.sin(phi) * Math.cos(theta);
    dir[i*3+1] = Math.sin(phi) * Math.sin(theta);
    dir[i*3+2] = Math.cos(phi);
    seed[i*3]   = Math.random();
    seed[i*3+1] = Math.random();
    seed[i*3+2] = Math.random();
  }
  function bindBuf(data, name, size){
    var b = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    var loc = gl.getAttribLocation(prog, name);
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, 0, 0);
    return b;
  }
  bindBuf(dir, 'aDir', 3);
  bindBuf(seed, 'aSeed', 3);

  var U = {};
  ['uTime','uSwirl','uBreath','uTurb','uSize','uBright','uRipple','uAudio',
   'uHalfW','uHalfH','uRadiusPx','uDpr','uOffsetY','uColA','uColB','uTwk'
  ].forEach(function(k){ U[k] = gl.getUniformLocation(prog, k); });

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
  gl.clearColor(0, 0, 0, 0);

  var DPR = Math.min(window.devicePixelRatio || 1, 2);

  /* ---------- 状态 ---------- */
  var cur = JSON.parse(JSON.stringify(CONFIG.MOODS.calm));
  var tgt = JSON.parse(JSON.stringify(CONFIG.MOODS.calm));
  var mood = 'calm', speaking = false;
  var audio = 0, audioTarget = 0, fakeAudio = 0;
  var ripple = -1, rippleT0 = 0;
  var speed = 1.0;
  var simT = 0;
  var theme = 'light';
  // 特效倍率（1.0 = 默认），由设置面板控制
  var fx = {swirl: 1.0, breath: 1.0, turb: 1.0, glow: 1.0,
            psize: 1.0, ripple: 1.0, twinkle: 1.0};
  var baseColor = '#2f7fe0';
  var customColor = null;

  /* 按主题把主色算成内/外两层颜色 */
  function recomputeColor(){
    if(!baseColor || baseColor.length !== 7) return;
    var h = baseColor.replace('#','');
    var r = parseInt(h.substr(0,2),16)/255;
    var g = parseInt(h.substr(2,2),16)/255;
    var b = parseInt(h.substr(4,2),16)/255;
    if(theme === 'dark'){
      customColor = {
        a: [Math.min(1, r + (1-r)*0.55), Math.min(1, g + (1-g)*0.50), Math.min(1, b + (1-b)*0.25)],
        b: [r*0.45, g*0.50, b*0.70]
      };
    } else {
      // 白底上粒子要更有存在感：中心压深、边缘少往白里化
      customColor = {
        a: [r*0.58, g*0.58, b*0.86],
        b: [r + (1-r)*0.22, g + (1-g)*0.22, b + (1-b)*0.10]
      };
    }
  }

  function setAIState(m, isSpeaking, memoryTrigger){
    if(m && CONFIG.MOODS[m]){ mood = m; tgt = JSON.parse(JSON.stringify(CONFIG.MOODS[m])); }
    speaking = !!isSpeaking;
    if(memoryTrigger){ ripple = 0; rippleT0 = performance.now(); }
  }

  /* 服务端写进来的启动设置 */
  function applyBootSettings(){
    var cfg = (window.__BOOT_SETTINGS && window.__BOOT_SETTINGS.orb) || null;
    if(!cfg) return;
    if(cfg.theme === 'dark' || cfg.theme === 'light') theme = cfg.theme;
    if(typeof cfg.speed === 'number') speed = Math.max(0.1, Math.min(3, cfg.speed));
    if(typeof cfg.count === 'number') activeCount = Math.max(200, Math.min(MAXN, Math.floor(cfg.count)));
    if(typeof cfg.color === 'string' && cfg.color.length === 7) baseColor = cfg.color;
    if(cfg.fx && typeof cfg.fx === 'object'){
      for(var k in fx){
        if(typeof cfg.fx[k] === 'number'){
          fx[k] = Math.max(0.2, Math.min(3.0, cfg.fx[k]));
        }
      }
    }
    recomputeColor();
  }

  function resize(){
    var W = cv.clientWidth || window.innerWidth;
    var H = cv.clientHeight || window.innerHeight;
    cv.width = Math.round(W * DPR);
    cv.height = Math.round(H * DPR);
    gl.viewport(0, 0, cv.width, cv.height);
    gl.uniform1f(U.uHalfW, W / 2);
    gl.uniform1f(U.uHalfH, H / 2);
    gl.uniform1f(U.uRadiusPx, Math.min(W, H) * CONFIG.DIAMETER * 0.5);
    gl.uniform1f(U.uDpr, DPR);
    gl.uniform1f(U.uOffsetY, (0.5 - CONFIG.CENTER_Y) * 2.0);
  }
  window.addEventListener('resize', resize);

  /* ---------- 主循环 ---------- */
  var last = performance.now();

  function frame(now){
    var dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    simT += dt * speed;
    var t = simT;

    var k = 1 - Math.pow(0.001, dt);
    ['swirl','breath','turb','size','bright'].forEach(function(key){
      cur[key] += (tgt[key] - cur[key]) * k * 0.6;
    });
    for(var i = 0; i < 3; i++){
      cur.a[i] += (tgt.a[i] - cur.a[i]) * k * 0.6;
      cur.b[i] += (tgt.b[i] - cur.b[i]) * k * 0.6;
    }

    fakeAudio = speaking
      ? 0.30 + 0.32*Math.abs(Math.sin(t*3.4)) + 0.28*Math.abs(Math.sin(t*7.1))
      : 0;
    audio += ((audioTarget > 0 ? audioTarget : fakeAudio) - audio) * 0.15;

    var rip = -1;
    if(ripple >= 0){
      rip = (now - rippleT0) / 1000 / CONFIG.RIPPLE_TIME;
      if(rip >= 1){ ripple = -1; rip = -1; }
    }

    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform1f(U.uTime, t);
    gl.uniform1f(U.uSwirl, cur.swirl * fx.swirl);
    gl.uniform1f(U.uBreath, cur.breath * fx.breath);
    gl.uniform1f(U.uTurb, cur.turb * fx.turb);
    gl.uniform1f(U.uTwk, fx.twinkle);
    gl.uniform1f(U.uRipple, rip < 0 ? -1 : rip * fx.ripple);
    gl.uniform1f(U.uAudio, audio);

    if(customColor){
      gl.uniform3fv(U.uColA, customColor.a);
      gl.uniform3fv(U.uColB, customColor.b);
    } else {
      gl.uniform3fv(U.uColA, cur.a);
      gl.uniform3fv(U.uColB, cur.b);
    }

    // 三层叠加：外轮廓雾 / 光的体积 / 粒子本体
    gl.uniform1f(U.uSize, CONFIG.SIZE * cur.size * 6.5 * fx.psize);
    gl.uniform1f(U.uBright, cur.bright * 0.012 * fx.glow);
    gl.drawArrays(gl.POINTS, 0, activeCount);

    gl.uniform1f(U.uSize, CONFIG.SIZE * cur.size * 3.4 * fx.psize);
    gl.uniform1f(U.uBright, cur.bright * 0.055 * fx.glow);
    gl.drawArrays(gl.POINTS, 0, activeCount);

    gl.uniform1f(U.uSize, CONFIG.SIZE * cur.size * fx.psize);
    gl.uniform1f(U.uBright, cur.bright * fx.glow);
    gl.drawArrays(gl.POINTS, 0, activeCount);

    requestAnimationFrame(frame);
  }

  /* ---------- 对外接口 ---------- */
  window.setAIState = setAIState;
  window.ORB = {
    set: function(s){
      if(s === 'listen') setAIState('curious', false, false);
      else if(s === 'think') setAIState('curious', false, false);
      else if(s === 'speak') setAIState('gentle', true, false);
      else setAIState('calm', false, false);
    },
    level: function(v){ audioTarget = Math.max(0, Math.min(1, v || 0)); },
    look: function(){},
    memory: function(){ ripple = 0; rippleT0 = performance.now(); },
    setTheme: function(t){ theme = (t === 'dark') ? 'dark' : 'light'; recomputeColor(); },
    apply: function(o){
      if(!o) return;
      if(typeof o.speed === 'number') speed = Math.max(0.1, Math.min(3, o.speed));
      if(typeof o.count === 'number') activeCount = Math.max(200, Math.min(MAXN, Math.floor(o.count)));
      if(typeof o.color === 'string' && o.color.length === 7){
        baseColor = o.color;
        recomputeColor();
      }
      if(o.theme === 'dark' || o.theme === 'light'){
        theme = o.theme;
        recomputeColor();
      }
      if(o.fx && typeof o.fx === 'object'){
        for(var k in fx){
          if(typeof o.fx[k] === 'number'){
            fx[k] = Math.max(0.2, Math.min(3.0, o.fx[k]));
          }
        }
      }
    },
    state: function(){ return mood; }
  };

  /* ---------- 启动：所有变量都就绪后才套设置 ---------- */
  applyBootSettings();
  resize();
  requestAnimationFrame(frame);
})();
"""
