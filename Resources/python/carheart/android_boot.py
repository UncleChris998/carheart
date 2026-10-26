# -*- coding: utf-8 -*-
"""Android 启动入口：把原来的 carheart 服务在 App 内部跑起来。

跟桌面的 start_server 差别：
  · 只监听 127.0.0.1 —— 手机/车机内部用，不对外暴露
  · 不用 SSL（localhost 本身就是安全上下文，麦克风能直接拿到）
  · 不加载本地 ASR/TTS 模型（几百 MB，装不下也用不上），
    语音识别和合成全走云：火山 ASR / 火山 TTS
"""
import os
import sys


def _prepare_env():
    """把数据目录指到 App 私有目录（可写、不会被系统清理）。

    APK 里的 python 位于只读 assets，config.py 默认会把 data 建在代码旁边，
    在手机上那一步会失败。所以必须在 import config 之前设好 CARHEART_ROOT。
    Chaquopy 没有 os.getuid 之外的方便办法拿包名，这里用 try 兜一圈。
    """
    home = os.environ.get("CARHEART_HOME")
    if not home:
        cands = [
            "/data/data/com.carheart.app/files",
            "/data/user/0/com.carheart.app/files",
        ]
        for c in cands:
            try:
                os.makedirs(c, exist_ok=True)
                # 试写一下，确认真的可写
                probe = os.path.join(c, ".w")
                with open(probe, "w") as f:
                    f.write("1")
                os.remove(probe)
                home = c
                break
            except Exception:
                continue
    if not home:
        home = os.path.join(os.path.expanduser("~"), "carheart")
        try:
            os.makedirs(home, exist_ok=True)
        except Exception:
            pass
    os.environ["CARHEART_ROOT"] = home
    return home


def _seed_configs(home):
    """首次启动把 API key 写进 App 私有目录。

    为什么写死在代码里：APK 是给主人自己用的，keys 不进 git 也不进公开渠道，
    打进包里是唯一能在无服务器情况下分发的方式。要换 key 就改这里重新打包。
    """
    import json
    data = os.path.join(home, "data")
    try:
        os.makedirs(data, exist_ok=True)
    except Exception:
        pass

    def _put(name, obj, keep_existing=True):
        p = os.path.join(data, name)
        if keep_existing and os.path.exists(p):
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=1)
            print(f"[android] seed {name}", flush=True)
        except Exception as e:                            # noqa: BLE001
            print(f"[android] seed {name} 失败: {e}", flush=True)

    # 云端大脑：DeepSeek
    _put("cloud.json", {
        "mode": "cloud",
        "preset": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": os.environ.get("CARHEART_DEEPSEEK_KEY") or "sk-0ccaac541c724cd5aa0e2bbf1d7d1be1",
        "model": "deepseek-flash",
        "think": "auto",
    })
    # 火山：TTS + ASR
    _put("volc.json", {
        "app_id": "api-key-20261018021612",
        "access_token": "68b08592-d39d-43b4-a3a0-7f6289498b6e",
        "speaker": "zh_female_xiaohe_uranus_bigtts",
    })
    # 音色选择：默认小何
    _put("voice.json", {
        "provider": "volc",
        "id": "zh_female_xiaohe_uranus_bigtts",
    })


def run():
    home = _prepare_env()
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        from . import config
        # 手机上没打包本地模型，这些开关先关掉，省得每条路径都去查文件
        config.PIPER_ENABLED = False
        config.SAVE_RECORDINGS = False
        print(f"[android] root={config.ROOT}", flush=True)
        print(f"[android] data={config.DATA_DIR}", flush=True)
    except Exception as e:                                # noqa: BLE001
        print(f"[android] config 调整失败（继续）: {e}", flush=True)

    _seed_configs(home)

    from . import server
    # 只监听本机回环：WebView 走 http://127.0.0.1:19388，
    # localhost 是安全上下文，麦克风不需要证书。
    #
    # 端口被占用**不算错**。两种情况：
    #   · onCreate 跑了两次，第二个线程来抢同一个端口
    #   · 上一次的实例还没退
    # 两种情况下服务其实都是活的，WebView 连上去就能用。
    # 之前把 EADDRINUSE 当致命错误，界面被报错页盖住 —— App 明明能跑。
    try:
        server.serve(host="127.0.0.1", port=19388, ssl_context=None)
    except OSError as e:                                  # noqa: BLE001
        if getattr(e, "errno", None) in (48, 98):         # EADDRINUSE
            print("[android] 19388 已在监听，实例已存在，直接用", flush=True)
            return
        raise


if __name__ == "__main__":
    run()
