# -*- coding: utf-8 -*-
"""配置：模型档位、路径、默认身份。改这里，不改代码。"""
import os
import sys

# ---------------- 控制台编码 ----------------
# 中文 Windows 的控制台默认 GBK，打印非 GBK 字符会直接抛 UnicodeEncodeError，
# 表现就是「跑一半莫名其妙退出」。这里在所有模块之前把输出改成 UTF-8。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------- 路径 ----------------
# 为什么要有 CARHEART_ROOT：
#   打包进 APK 之后，代码是从只读的 assets 里解出来的，__file__ 那个位置
#   写不了东西。数据和记忆必须落到 App 的私有目录。
#   所以：有 CARHEART_ROOT 就用它，没有就按原来的（代码目录的上一层）。
PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("CARHEART_ROOT") or os.path.dirname(PKG_DIR)
DATA_DIR = os.path.join(ROOT, "data")
MEDIA_DIR = os.path.join(DATA_DIR, "media")
DB_PATH = os.path.join(DATA_DIR, "carheart.db")
PERSONA_PATH = os.path.join(ROOT, "persona.md")
LOG_DIR = os.path.join(DATA_DIR, "logs")
# 云端大脑的配置文件（base_url / api_key / model / mode）。
# key 只进这个文件，不进代码。
CLOUD_CONF_PATH = os.path.join(DATA_DIR, "cloud.json")

# 只读环境下不能因为建目录失败就崩 —— 手机上首次启动时目录可能还不存在
for _d in (DATA_DIR, MEDIA_DIR, LOG_DIR):
    try:
        os.makedirs(_d, exist_ok=True)
    except Exception:
        pass

# ---------------- 模型档位（脑子可以换，它不能换）----------------
# 支持用环境变量覆盖 —— 搬到另一台机器（比如车里那台）不用改代码：
#   set CARHEART_CHAT_MODEL=qwen2.5:7b
#   set OLLAMA_HOST=http://192.168.31.119:11434      ← 连回家里这台
def _fix_ollama_host(raw):
    """把 OLLAMA_HOST 整成能当客户端 URL 用的形式。

    坑：Ollama 服务端拿 OLLAMA_HOST 当**监听地址**，写成 "0.0.0.0:11434"
    没问题（它自己认）。但客户端要完整 URL，少了 http:// 就报
    "unknown url type: 0.0.0.0"。这里统一补上，并把连不通的 0.0.0.0
    换成 127.0.0.1。
    """
    s = (raw or "").strip() or "http://127.0.0.1:11434"
    if "://" not in s:
        s = "http://" + s
    head, _, tail = s.partition("://")
    hostport = tail.split("/")[0]
    host = hostport.split(":")[0]
    if host in ("0.0.0.0", "::", "[::]", ""):
        port = hostport.split(":", 1)[1] if ":" in hostport else "11434"
        s = f"{head}://127.0.0.1:{port}"
    return s.rstrip("/")


OLLAMA_HOST = _fix_ollama_host(os.environ.get("OLLAMA_HOST"))

# 服务端监听哪个地址（部署到车里那台时用）。
# 刻意换个变量名，避免 OLLAMA_HOST 那个坑：
# OLLAMA_HOST 同时是 Ollama 的"监听地址"和客户端的"连接地址"，
# 写成 0.0.0.0:11434 服务端能跑、客户端却会崩。分开就不会再混。
OLLAMA_BIND = os.environ.get("OLLAMA_BIND", "0.0.0.0:11434")

# 启动 Ollama 服务时该带的环境变量（给启动器用）
OLLAMA_SERVER_ENV = {
    "OLLAMA_MODELS": os.environ.get("OLLAMA_MODELS", r"D:\OllamaModels"),
    "OLLAMA_HOST": OLLAMA_BIND,
}

MODELS = {
    # 日常对话：这台机器上已经有的那个（27B Q4_K_M，16.2 GB）
    "chat": os.environ.get("CARHEART_CHAT_MODEL", "qwen3.8-27b:latest"),
    # 认真想一下：没有更小的就先同一个，以后拉了 14B 再换
    "deep": os.environ.get("CARHEART_DEEP_MODEL", "qwen3.8-27b:latest"),
    # 离线深度思考
    "think": os.environ.get("CARHEART_THINK_MODEL", "qwen3.8-27b:latest"),
    # 记忆检索用的嵌入模型（可选；没有就退回字面检索）
    "embed": os.environ.get("CARHEART_EMBED_MODEL", "bge-m3"),
}

# 迷你主机 / 车里那台的小模型（跑不动 27B 时用这个）
SMALL_MODEL = os.environ.get("CARHEART_SMALL_MODEL", "qwen2.5:7b-instruct-q4_K_M")

# 触发「换脑子」的口令
DEEP_TRIGGERS = ["认真想", "好好想", "想清楚", "仔细想", "深入想"]
THINK_TRIGGERS = ["深度思考", "慢慢想"]

# ---------------- 生成参数 ----------------
TEMPERATURE = 0.9
TOP_P = 0.9
NUM_CTX = 8192          # 上下文窗口
MAX_REPLY_TOKENS = 512  # 它一次最多说这么多，说太多就不像人了

# 思考模式开关（实测：这台机器上的 27B 是思考模型，必须关掉才能秒接话）
#   "chat"  -> 关（日常对话要的是不假思索地接话）
#   "deep"  -> 开（认真想一下的时候才值得等）
#   "think" -> 开
THINK_MODE = {"chat": False, "deep": True, "think": True}

# 思考强度（界面可调）。auto = 按档位用上面的 THINK_MODE。
#   off    -> 完全不思考（最快，日常对话）
#   low / medium / high -> Ollama 的分级思考（越高质量越好、越慢）
# 实测这台机器：off 首字 0.2–0.7s；开思考 5–9s。
THINK_LEVEL = os.environ.get("CARHEART_THINK_LEVEL", "auto")
THINK_LEVELS = ["auto", "off", "low", "medium", "high"]

# 开思考时给「思考块」预留的 token 预算。
# 不加这个的话，思考会把 num_predict 吃光，正文返回空
# （实测 think=medium + 120 上限 -> 思考用了 169，正文 0 字）。
THINK_BUDGET = 1024


def resolve_think(tier="chat"):
    """把「思考强度」换算成该传给 Ollama 的值。"""
    lvl = (THINK_LEVEL or "auto").lower()
    if lvl == "off":
        return False
    if lvl in ("low", "medium", "high"):
        return lvl
    # auto：按档位
    return THINK_MODE.get(tier, False)
# 关掉之后：1–2 句，短
NO_THINK_SUFFIX = "\n\n（直接回答，不要展开推理过程。最多两句话。）"

# 保持模型常驻显存的时间，避免每次都重载十几秒
OLLAMA_KEEP_ALIVE = "30m"

# ---------------- 身份：一律留空 ----------------
# 原则（主人 2026-10-11 定）：它是谁、叫什么、在哪、是什么、怎么称呼你，
# 全部由「他亲口告诉它」决定。代码不许填默认值 —— 那就不叫养成了。
HEART_NAME = None           # 它叫什么。None = 还不知道，它会问
DEFAULT_USER = None         # 它怎么称呼你。None = 还没告诉它
DEFAULT_BODY = "body"       # 内部记账用的中性标识，不是名字。真实名字它自己读或你告诉它

# ---------------- 记忆与学习 ----------------
HOT_MEMORY_LIMIT = 6        # 每轮注入几条「热记忆」
RECALL_MIN_SCORE = 0.06     # 检索低于这个分数就当没想起来（宁可少提，不可提错）

# 学习：对话结束后自动提炼记忆与认识
AUTO_LEARN = True           # 退出对话时自动跑一次提炼
LEARN_TURNS = 12            # 每次提炼看最近多少轮
COMPACT_KEEP = 24           # 每个身体保留多少轮原始对话，更早的压缩成记忆
LEARN_LOG = os.path.join(DATA_DIR, "logs", "learn.jsonl")   # 提炼原始记录，可复查

# ---------------- 车（OBD）----------------
# 后端：sim = 模拟器（没硬件也能跑通）；serial = 真适配器走串口
OBD_BACKEND = "sim"
OBD_PORT = None                 # 例："COM5"。买了适配器后填这里
OBD_BAUD = 38400                # ELM327 蓝牙虚拟串口常见 38400 / 115200
OBD_PIDS = ["0C", "0D", "05", "11", "04", "10", "2F",
            "46", "5C", "42", "1F", "33"]   # 读哪些
SAMPLE_INTERVAL = 1.0           # 采样间隔（秒）
SESSION_SECONDS = 360.0         # 一次行程采样多久（模拟器用）

# ---------------- 语音 ----------------
VOICE_MODEL_SIZE = "medium"     # whisper 档位；已知 small 对中文同音字很差
# 实测踩坑：device="auto" 会去用 CUDA，但 CTranslate2 找不到 cublas64_12.dll 直接崩。
# 语音识别用 CPU 就好：而且不跟 27B 抢显存。
VOICE_DEVICE = "cpu"            # cpu / cuda / auto
VOICE_COMPUTE_TYPE = "int8"     # int8（CPU 最快）/ float32 / float16（GPU）
VOICE_MODEL_DIR = os.path.join(DATA_DIR, "whisper")   # 模型下到这，不进系统目录
VOICE_SILENCE_RMS = 500         # 手动录音模式下已不用；留着给阈值模式备用
VOICE_RATE = 175                # 语速
VOICE_HINT = "Huihui"           # 优先用哪个音色（名字片段）

# ---------------- 语音识别引擎 ----------------
# whisper    —— 通用，但中文同音字差（实测「器灵」听成「次灵」「气」）
# sensevoice —— 专门为中文/粤语训练，中文明显更好
# paraformer —— 阿里，纯中文，识别率通常最高
ASR_ENGINE = "paraformer"
ASR_DIR = os.path.join(DATA_DIR, "asr")
ASR_THREADS = 8                 # 这颗 CPU 有 12 核 24 线程
SAVE_RECORDINGS = True          # 把每次录音存下来，专门用来对比模型
RECORD_DIR = os.path.join(DATA_DIR, "recordings")

# ---------------- 出声（TTS）----------------
# 实测：这台机器的 Windows SAPI 是坏的（AudioOutput 注册表下没有设备 token），
# 直接出声一律报「拒绝访问」。所以首选 piper：纯离线神经语音，不碰 SAPI。
PIPER_DIR = os.path.join(DATA_DIR, "piper")
PIPER_MODEL = os.path.join(PIPER_DIR, "zh_CN-huayan-medium.onnx")
PIPER_ENABLED = True            # 关掉就退回 SAPI

# ---- 更好听的音色：微软神经语音（edge-tts）----
# 主人说 piper 的声音"太假"。piper 的中文音色确实机械。
# edge-tts 走微软的神经网络音色，自然得多，而且**不要 key**。
# 实测可连，中文有 8 个音色，合成 1.7~3 秒。
# 策略：edge 优先（自然），失败自动退回 piper（离线、保底能出声）。
TTS_ENGINE = "edge"                 # edge / piper
TTS_VOICE = "zh-CN-XiaoyiNeural"    # 年轻活泼女声
TTS_VOICES = [
    ("zh-CN-XiaoyiNeural", "晓伊 · 年轻活泼（默认）"),
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 温柔标准"),
    ("zh-CN-liaoning-XiaobeiNeural", "晓北 · 东北口音"),
    ("zh-CN-shaanxi-XiaoniNeural", "晓妮 · 陕西口音"),
    ("zh-CN-YunxiNeural", "云希 · 男声"),
]
# 语速/音调微调（edge-tts 语法：+8% / -4Hz）
TTS_RATE = "+6%"
TTS_PITCH = "+2Hz"

TTS_CONF_PATH = os.path.join(DATA_DIR, "tts.json")

# ---- 豆包（火山）专用发声参数 ----
# 主人定的调法，只用接口真支持的字段：
#   语速锁 0.82（speech_rate=‑18）—— 慢是台腔黏糊感的基础
#   情绪用 happy 但靠文本收敛，读出来会自然偏高偏软，替代"音调抬高"
#   方言参数关掉 —— 官方只有东北/陕西/四川，开了跑味
#   台腔的咬字和尾韵靠文本端做（见 speech_style.py）
TTS_SPEECH_RATE = -18          # -18% ≈ 0.82 倍速
TTS_EMOTION = "happy"
TTS_EMOTION_SCALE = 3          # 1~5；给 3 是"轻轻带一点"，不是亢奋
TTS_DIALECT = ""               # 空 = 不开方言（开了会跑味）
