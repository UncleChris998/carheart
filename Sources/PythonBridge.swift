//
//  PythonBridge.swift
//  在 App 里起一个 CPython 解释器，把 carheart.server 跑起来（127.0.0.1:19388）。
//
//  为什么要这么写：
//    · Python-Apple-support 的 xcframework 里，标准库是随包带的 python-stdlib；
//      运行时必须把 PYTHONHOME 指到 App 沙盒里那份拷贝，否则 import 全崩。
//    · App 的 bundle 是只读的，Python 会往 __pycache__ 里写字节码 —— 必须禁掉，
//      否则第一次 import 就报 Read-only file system。
//    · 服务要活到 App 结束，所以放在独立线程里跑。
//

import Foundation

final class PythonBridge {

    static let shared = PythonBridge()
    private init() {}

    private(set) var isRunning = false
    private var serverStarted = false

    /// 服务监听的端口 —— 跟网页、跟电脑上那套完全一致
    static let port = 19388

    /// App 沙盒里的工作根目录（人格、记忆、录音、缓存都落在这儿）
    static var rootDir: String {
        let base = FileManager.default.urls(for: .applicationSupportDirectory,
                                            in: .userDomainMask)[0]
        let dir = base.appendingPathComponent("carheart", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir,
                                                 withIntermediateDirectories: true)
        return dir.path
    }

    /// 启动解释器 + 起服务。可重复调用，只会真正起一次。
    func startIfNeeded() {
        guard !isRunning else { return }
        isRunning = true

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.bootstrap()
        }
    }

    // MARK: - 核心

    private func bootstrap() {
        guard let bundlePath = Bundle.main.resourcePath else {
            NSLog("[CarHeart] 找不到资源目录")
            return
        }

        // 1) 把 bundle 里的 Python 标准库 / 我们的 python 包拷到可写沙盒
        let home = preparePythonHome(bundlePath: bundlePath)

        // 2) 初始化解释器
        if !PythonStarted.shared.once {
            Py_Initialize()
            PythonStarted.shared.once = true
        }
        guard Py_IsInitialized() != 0 else {
            NSLog("[CarHeart] Py_Initialize 失败")
            return
        }

        // 3) 环境：PYTHONHOME + 不写字字节码 + 我们的代码路径
        setEnv("PYTHONHOME", home)
        setEnv("PYTHONDONTWRITEBYTECODE", "1")
        setEnv("PYTHONUTF8", "1")
        setEnv("PYTHONIOENCODING", "utf-8")
        setEnv("CARHEART_ROOT", Self.rootDir)
        setEnv("CARHEART_TOKEN", "test123")
        setEnv("CARHEART_HOST", "127.0.0.1")

        // 4) sys.path：标准库 + 我们的包
        let pkgPath = bundlePath + "/python"
        run("import sys; sys.path.insert(0, \(repr(pkgPath)))")
        run("import sys; sys.path.insert(0, \(repr(home + "/lib/python3.12")))")
        run("import sys; sys.path.insert(0, \(repr(home + "/lib/python3.12/lib-dynload")))")

        // 5) 真起服务（阻塞，所以在这个后台线程里跑）
        NSLog("[CarHeart] 启动 carheart.server …")
        DispatchQueue.main.async {
            self.serverStarted = true
            NotificationCenter.default.post(name: .carHeartServerReady, object: nil)
        }
        run("""
from carheart.server import main as _srv
_srv(['--host','127.0.0.1','--port','\(Self.port)','--token','test123'])
""")
        NSLog("[CarHeart] carheart.server 退出了（不应发生）")
    }

    /// 把 python-stdlib 和我们的包拷进沙盒，返回拷贝后的 PYTHONHOME
    private func preparePythonHome(bundlePath: String) -> String {
        let fm = FileManager.default
        let home = Self.rootDir + "/pythonhome"
        let marker = home + "/.prepared"

        if fm.fileExists(atPath: marker) { return home }

        try? fm.createDirectory(atPath: home, withIntermediateDirectories: true)
        // bundle 里的 python-stdlib → 沙盒
        let src = bundlePath + "/python-stdlib"
        if fm.fileExists(atPath: src) {
            if let items = try? fm.contentsOfDirectory(atPath: src) {
                for item in items {
                    let s = src + "/" + item
                    let d = home + "/" + item
                    if fm.fileExists(atPath: d) { continue }
                    try? fm.copyItem(atPath: s, toPath: d)
                }
            }
        }
        fm.createFile(atPath: marker, contents: Data())
        return home
    }

    // MARK: - 小工具

    private func setEnv(_ key: String, _ value: String) {
        setenv(key, value, 1)
    }

    private func run(_ code: String) {
        let ret = code.withCString { cstr in
            PyRun_SimpleStringFlags(cstr, nil)
        }
        if ret != 0 {
            NSLog("[CarHeart] Python 执行失败（ret=\(ret)）：\(code.prefix(120))")
        }
    }

    private func repr(_ s: String) -> String {
        // 够用的转义：路径里不会有引号
        return "'" + s.replacingOccurrences(of: "\\", with: "\\\\")
                      .replacingOccurrences(of: "'", with: "\\'") + "'"
    }
}

/// Py_Initialize 只能调一次
final class PythonStarted {
    static let shared = PythonStarted()
    var once = false
}

extension Notification.Name {
    static let carHeartServerReady = Notification.Name("carHeartServerReady")
}
