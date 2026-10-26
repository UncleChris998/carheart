//
//  CarHeartApp.swift
//  App 入口：配好音频会话 → 起 Python 服务 → 打开内置网页。
//

import SwiftUI
import WebKit

@main
struct CarHeartApp: App {

    init() {
        // 顺序很重要：先把音频会话定死，再让 WebKit 去要麦克风。
        // 反过来的话 WebKit 会先用它自己那套（带语音处理）把 session 占了。
        AudioSessionSetup.configure()
    }

    var body: some Scene {
        WindowGroup {
            WebShellView()
                .ignoresSafeArea()
                .onAppear {
                    PythonBridge.shared.startIfNeeded()
                }
        }
    }
}

/// 包一层 WKWebView：加载内置服务，处理麦克风权限，留好原生桥的口子。
struct WebShellView: UIViewRepresentable {

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.allowsInlineMediaPlayback = true
        cfg.mediaTypesRequiringUserActionForPlayback = []
        // 网页里的 getUserMedia 要能直接过
        cfg.preferences.setValue(true, forKey: "developerExtrasEnabled")

        let web = WKWebView(frame: .zero, configuration: cfg)
        web.uiDelegate = context.coordinator
        web.navigationDelegate = context.coordinator
        web.isOpaque = false
        web.backgroundColor = UIColor(red: 0.03, green: 0.07, blue: 0.10, alpha: 1)
        web.scrollView.bounces = false
        context.coordinator.web = web

        // 服务可能还没起来：轮询 /health，通了再加载
        context.coordinator.waitAndLoad()

        // 切后台也让音频会话活着（连续对话/待机时不断）
        NotificationCenter.default.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil, queue: .main) { _ in
            AudioSessionSetup.keepAliveInBackground()
        }
        return web
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKUIDelegate, WKNavigationDelegate {

        weak var web: WKWebView?
        private var tries = 0

        /// 等内置服务起来再加载页面（最多等 30 秒）
        func waitAndLoad() {
            tries += 1
            let url = URL(string: "http://127.0.0.1:\(PythonBridge.port)/?token=test123")!
            var req = URLRequest(url: url)
            req.timeoutInterval = 2

            URLSession.shared.dataTask(with: req) { [weak self] data, resp, err in
                guard let self else { return }
                let ok = (resp as? HTTPURLResponse)?.statusCode == 200
                DispatchQueue.main.async {
                    if ok {
                        self.web?.load(req)
                    } else if self.tries < 30 {
                        DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                            self.waitAndLoad()
                        }
                    } else {
                        self.loadErrorPage(err?.localizedDescription ?? "内置服务没起来")
                    }
                }
            }.resume()
        }

        private func loadErrorPage(_ why: String) {
            let html = """
            <html><body style="background:#08131b;color:#e8f2f7;font:16px -apple-system;padding:24px">
            <h3>内置服务没起来</h3><p>\(why)</p>
            <p>把 App 杀掉重开一次；还不行就把这台 iPhone 连电脑看日志。</p>
            </body></html>
            """
            web?.loadHTMLString(html, baseURL: nil)
        }

        // MARK: 麦克风权限：不给网页弹系统框，默认允许（App 自己已经有权限了）
        func webView(_ webView: WKWebView,
                     requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                     initiatedByFrame frame: WKFrameInfo,
                     type: WKMediaCaptureType,
                     decisionHandler: @escaping (WKPermissionDecision) -> Void) {
            decisionHandler(.grant)
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            NSLog("[CarHeart] 页面加载完成")
        }

        func webView(_ webView: WKWebView,
                     didFail navigation: WKNavigation!,
                     withError error: Error) {
            NSLog("[CarHeart] 导航失败：\(error)")
        }
    }
}
