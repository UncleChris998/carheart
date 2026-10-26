//
//  AudioSessionSetup.swift
//  网页版做不到、而 App 必须做的第一件事：把音频会话定死。
//
//  为什么关键：
//    网页里 iOS 会因为「播放过音频」把麦克风压到 -38 dBFS（实测峰值只有
//    0.005），而且 readyState 还是 live —— 这几天所有「太轻/0 秒/哑流」
//    都是它造成的。原生层可以自己选 category / mode / 路由，不用被
//    WebKit 的语音处理接管。
//

import AVFoundation

enum AudioSessionSetup {

    static func configure() {
        let session = AVAudioSession.sharedInstance()
        do {
            // .playAndRecord 同时要麦克风和扬声器；
            // .defaultToSpeaker 强制走扬声器（不插耳机时不会跑到听筒）；
            // .allowBluetooth 留给耳机。
            try session.setCategory(.playAndRecord,
                                    mode: .spokenAudio,
                                    options: [.defaultToSpeaker,
                                              .allowBluetooth,
                                              .allowBluetoothA2DP,
                                              .duckOthers])
            // 不要打电话那套语音处理（它会压麦克风电平）
            try session.setMode(.spokenAudio)
            try session.setPreferredSampleRate(48000)
            try session.setPreferredIOBufferDuration(0.02)
            try session.setActive(true, options: [])
            NSLog("[CarHeart] AVAudioSession ok: \(session.category.rawValue) / \(session.mode.rawValue)")
        } catch {
            NSLog("[CarHeart] AVAudioSession 配置失败：\(error)")
        }
    }

    /// 切后台时保持活跃（配合 Info.plist 的 UIBackgroundModes: audio）
    static func keepAliveInBackground() {
        let session = AVAudioSession.sharedInstance()
        try? session.setActive(true)
    }
}
