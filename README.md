# CarHeart iOS（方案②：Python 塞进 App）

目标：**一个装在自己 iPhone 上的真 App，出门也能用，不依赖电脑。**

```
iPhone App
 ├── 内嵌 CPython 3.12（Python-Apple-support 的 iOS xcframework）
 ├── 跑现有的 carheart.server（127.0.0.1:19388，stdlib，业务代码不改）
 ├── WKWebView 加载现有那套 60KB 界面（不改）
 └── 原生层：AVAudioSession 定死路由 + 麦克风权限放行 + 后台保活
```

## 为什么不用把识别模型也塞进来

`carheart/asr.py`、`asrlab.py`、`asrfix.py` 在手机端**故意不导入**（源码注释写着：手机上语音走火山云）。
所以 iOS 里不需要 sherpa-onnx、不需要 numpy、不需要那 232MB 模型。
App 体积预计 **80MB 上下**。

## 目录

```
carheart-ios/
  project.yml            XcodeGen 工程定义（CI 里生成 .xcodeproj）
  codemagic.yaml         云端构建：出**未签名** .ipa，给 AltStore 签
  Sources/
    CarHeartApp.swift    App 入口 + WKWebView + 等内置服务
    PythonBridge.swift   起 CPython、拷 stdlib、跑 carheart.server
    AudioSessionSetup.swift  AVAudioSession（网页版做不到的那部分）
    Bridging-Header.h    #include <Python.h>
  Frameworks/            CI 下载的 Python.xcframework（不进仓库）
  Resources/python/      从 ../car-heart/carheart 同步过来的包（不进仓库）
  Resources/python-stdlib/  CI 下载
```

## 本地先做一次同步

```powershell
cd carheart-ios
powershell -File sync-python.ps1
```

把 `..\car-heart\carheart` 拷进 `Resources\python\`，去掉 `__pycache__`。

## 云端构建（不需要 Mac）

1. 把 `carheart-ios` 推到一个 **GitHub 私库**。
2. Codemagic 用 GitHub 登录 → Add application → 选这个库。
3. 用仓库里的 `codemagic.yaml` 跑 `ios-unsigned` 工作流。
4. 产物：`CarHeart-unsigned.ipa`（未签名）。

> `codemagic.yaml` 里 Python-Apple-support 的下载地址按版本号拼装，
> 第一次跑如果 404，打开 https://github.com/beeware/Python-Apple-support/releases
> 找到当前 3.12 的 tag，改 `PY_SUPPORT_TAG` 即可。

## 装到手机（AltStore，免费签名，7 天续签）

1. Windows 上装 **AltServer**（altstore.io），装好后托盘常驻。
2. iPhone 用数据线接一次电脑，AltServer → Install AltStore → 输入 Apple ID（要双重认证）。
3. 把 `CarHeart-unsigned.ipa` 传到手机（AirDrop / 文件 App 都行）。
4. AltStore → My Apps → `+` → 选这个 ipa → 用同一个 Apple ID 签名安装。
5. 7 天后：手机和电脑同一个 Wi-Fi，打开 AltStore → Refresh。

## 已知风险（照实写）

| 风险 | 说明 |
|---|---|
| 云端编译要迭代 | 我没有 Mac，Swift 只能靠 CI 编译。第一次大概率有几个报错，一轮一轮修（每轮几分钟）。 |
| Python-Apple-support 版本 | CI 里按 tag 下载，版本对不上会 404，改 tag 即可。 |
| CPython 在 iOS 上写字节码 | 已用 `PYTHONDONTWRITEBYTECODE=1` 规避（沙盒只读会崩）。 |
| 免费签名 7 天 | 到期 App 打不开，要刷新；漏了就重装。 |
| 音频会话 | 原生层设了 `.playAndRecord + .spokenAudio + defaultToSpeaker`，**不能保证**一定解决电平被压的问题 —— 要真机验证。 |
| 大模型/豆包 | 本来就在云端，所以 App **必须联网**才能聊天；“离线”只覆盖人格+记忆。 |
