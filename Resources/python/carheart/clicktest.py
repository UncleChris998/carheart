PAGE = r'''<html><body style="margin:0;background:#111;color:#0f0;font:11px monospace">
<pre id="out">…</pre>
<iframe id="f" src="/?token=__TOKEN__" style="width:414px;height:700px;border:0"></iframe>
<script>
var L=[]; function w(s){ L.push(s); document.getElementById('out').textContent=L.join('\n'); }
setTimeout(function(){
  var W=document.getElementById('f').contentWindow;
  w('防自激闸已挂: ' + (W.INTERRUPT ? 'INTERRUPT 在' : '没有'));
  // 装一个假的识别回调，模拟"它自己在说话"
  var fed = [];
  W.onNativeASR = function(t, f){ fed.push(t); };
  // 等兜底层重新包一次
  setTimeout(function(){
    var guarded = W.onNativeASR.__guarded;
    w('是否被包过: ' + !!guarded);
    W.__assistantSpeaking = true;
    W.onNativeASR('你刚才说什么', true);      // 播报中 → 应丢弃
    W.__assistantSpeaking = false;
    W.onNativeASR('好', true);                 // 太短 → 丢弃
    W.onNativeASR('我想去吃饭', true);          // 正常 → 放行
    W.onNativeASR('我想去吃饭', true);          // 重复 → 丢弃
    setTimeout(function(){
      w('放行的条数: ' + fed.length + ' 内容: ' + JSON.stringify(fed));
      w('丢弃计数: ' + W.INTERRUPT.dropped());
    }, 300);
  }, 900);
}, 3500);
</script></body></html>'''

def page(tok):
    return PAGE.replace("__TOKEN__", tok)