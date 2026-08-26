import AppKit
import Foundation
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    private var window: NSWindow!
    private var webView: WKWebView!
    private var backendProcess: Process?
    private var backendLogHandle: FileHandle?
    private var ownsBackend = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        window.title = "镜流工作台"
        window.minSize = NSSize(width: 980, height: 680)
        window.center()
        window.contentView = webView
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        showLoading("正在启动本地执行层…")
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            self?.bootWorkbench()
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        guard ownsBackend, let process = backendProcess, process.isRunning else {
            backendLogHandle?.closeFile()
            return
        }
        process.terminate()
        let deadline = Date().addingTimeInterval(2)
        while process.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if process.isRunning {
            process.interrupt()
        }
        backendLogHandle?.closeFile()
    }

    private func bootWorkbench() {
        let fileManager = FileManager.default
        let appURL = Bundle.main.bundleURL.standardizedFileURL
        let workbenchURL = appURL.deletingLastPathComponent().standardizedFileURL
        let workspaceURL = workbenchURL.deletingLastPathComponent().standardizedFileURL
        let backendURL = workbenchURL.appendingPathComponent("backend/app.py")
        let staticURL = workbenchURL.appendingPathComponent("frontend")
        let dataURL = workbenchURL.appendingPathComponent("data")
        let projectsURL = workspaceURL.appendingPathComponent("work")
        let logsURL = dataURL.appendingPathComponent("logs")

        guard fileManager.fileExists(atPath: backendURL.path),
              fileManager.fileExists(atPath: staticURL.path) else {
            failOnMainThread("应用文件不完整", "请保持“镜流工作台.app”和 video-workbench/backend、frontend 在同一个 video-workbench 文件夹中。")
            return
        }

        do {
            try fileManager.createDirectory(at: logsURL, withIntermediateDirectories: true)
        } catch {
            failOnMainThread("无法创建日志目录", error.localizedDescription)
            return
        }

        if let existingURL = existingWorkbenchURL(projectsRoot: projectsURL.path) {
            loadOnMainThread(existingURL)
            return
        }

        guard let port = firstFreePort() else {
            failOnMainThread("没有可用端口", "本机 8765–8799 端口均被占用，请关闭占用这些端口的程序后重试。")
            return
        }

        guard let pythonURL = locatePython() else {
            failOnMainThread("未找到 Python 3", "镜流工作台优先使用 Codex 自带 Python；若该运行时不存在，请安装 Python 3，或使用 run.command 查看详细信息。")
            return
        }

        let logURL = logsURL.appendingPathComponent("workbench-\(port).log")
        fileManager.createFile(atPath: logURL.path, contents: nil)
        guard let logHandle = FileHandle(forWritingAtPath: logURL.path) else {
            failOnMainThread("无法写入启动日志", logURL.path)
            return
        }
        logHandle.seekToEndOfFile()
        backendLogHandle = logHandle

        let process = Process()
        process.executableURL = pythonURL
        process.arguments = [
            backendURL.path,
            "--host", "127.0.0.1",
            "--port", String(port),
            "--data-root", dataURL.path,
            "--static-root", staticURL.path,
        ] + (fileManager.fileExists(atPath: projectsURL.path) ? ["--projects-root", projectsURL.path] : [])
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONPYCACHEPREFIX"] = "/tmp/jingliu-workbench-pycache"
        process.environment = environment
        process.standardOutput = logHandle
        process.standardError = logHandle

        do {
            try process.run()
            backendProcess = process
            ownsBackend = true
        } catch {
            logHandle.closeFile()
            failOnMainThread("本地执行层启动失败", "\(error.localizedDescription)\n\n日志：\(logURL.path)")
            return
        }

        let targetURL = URL(string: "http://127.0.0.1:\(port)")!
        for _ in 0..<60 {
            if !process.isRunning {
                let excerpt = tailText(logURL, maximumBytes: 12_000)
                failOnMainThread("工作台启动失败", "执行层提前退出。\n\n\(excerpt)\n\n日志：\(logURL.path)")
                return
            }
            if curl(url: targetURL.appendingPathComponent("api/v1/health"), timeout: 0.35) != nil {
                loadOnMainThread(targetURL)
                return
            }
            Thread.sleep(forTimeInterval: 0.2)
        }

        process.terminate()
        let excerpt = tailText(logURL, maximumBytes: 12_000)
        failOnMainThread("工作台启动超时", "本地执行层在 12 秒内没有就绪。\n\n\(excerpt)\n\n日志：\(logURL.path)")
    }

    private func locatePython() -> URL? {
        let fileManager = FileManager.default
        let home = fileManager.homeDirectoryForCurrentUser
        let candidates = [
            home.appendingPathComponent(".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"),
            URL(fileURLWithPath: "/opt/homebrew/bin/python3"),
            URL(fileURLWithPath: "/usr/local/bin/python3"),
            URL(fileURLWithPath: "/usr/bin/python3"),
        ]
        return candidates.first { fileManager.isExecutableFile(atPath: $0.path) }
    }

    private func existingWorkbenchURL(projectsRoot: String) -> URL? {
        for port in 8765...8799 {
            guard let bootstrapURL = URL(string: "http://127.0.0.1:\(port)/api/v1/bootstrap"),
                  let data = curl(url: bootstrapURL, timeout: 0.18),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let server = object["server"] as? [String: Any],
                  let runningRoot = server["projects_root"] as? String else {
                continue
            }
            if URL(fileURLWithPath: runningRoot).standardizedFileURL.path == URL(fileURLWithPath: projectsRoot).standardizedFileURL.path {
                return URL(string: "http://127.0.0.1:\(port)")
            }
        }
        return nil
    }

    private func firstFreePort() -> Int? {
        for port in 8765...8799 where !portIsListening(port) {
            return port
        }
        return nil
    }

    private func portIsListening(_ port: Int) -> Bool {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/lsof")
        process.arguments = ["-nP", "-iTCP:\(port)", "-sTCP:LISTEN"]
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus == 0
        } catch {
            return false
        }
    }

    private func curl(url: URL, timeout: Double) -> Data? {
        let process = Process()
        let pipe = Pipe()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/curl")
        process.arguments = ["-fsS", "--max-time", String(timeout), url.absoluteString]
        process.standardOutput = pipe
        process.standardError = FileHandle.nullDevice
        do {
            try process.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            return process.terminationStatus == 0 ? data : nil
        } catch {
            return nil
        }
    }

    private func tailText(_ url: URL, maximumBytes: Int) -> String {
        guard let data = try? Data(contentsOf: url) else { return "没有可读取的日志内容。" }
        return String(data: data.suffix(maximumBytes), encoding: .utf8) ?? "日志不是有效的 UTF-8 文本。"
    }

    private func showLoading(_ message: String) {
        let escaped = message
            .replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
        webView.loadHTMLString("""
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
          html,body{height:100%;margin:0;background:#090b10;color:#f5f4ef;font:15px -apple-system,BlinkMacSystemFont,sans-serif}
          main{height:100%;display:grid;place-items:center}section{text-align:center}.mark{width:54px;height:54px;margin:0 auto 18px;border:0;background:transparent;color:#eee4b3;display:grid;place-items:center;font-size:27px;font-weight:680;text-shadow:0 0 7px #f7edbd99,0 0 23px #e0d49a66;filter:drop-shadow(0 0 16px #e0d49a44)}p{margin:0;color:#92969d}
        </style><main><section><div class="mark">镜</div><p>\(escaped)</p></section></main>
        """, baseURL: nil)
    }

    private func loadOnMainThread(_ url: URL) {
        DispatchQueue.main.async { [weak self] in
            self?.webView.load(URLRequest(url: url))
            self?.window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
        }
    }

    private func failOnMainThread(_ title: String, _ message: String) {
        DispatchQueue.main.async { [weak self] in
            let alert = NSAlert()
            alert.alertStyle = .critical
            alert.messageText = title
            alert.informativeText = message
            alert.addButton(withTitle: "知道了")
            if let window = self?.window {
                alert.beginSheetModal(for: window)
            } else {
                alert.runModal()
            }
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        failOnMainThread("页面载入失败", error.localizedDescription)
    }

    func webView(
        _ webView: WKWebView,
        runOpenPanelWith parameters: WKOpenPanelParameters,
        initiatedByFrame frame: WKFrameInfo,
        completionHandler: @escaping ([URL]?) -> Void
    ) {
        var hasCompleted = false
        let completeOnce: ([URL]?) -> Void = { urls in
            guard !hasCompleted else { return }
            hasCompleted = true
            completionHandler(urls)
        }

        DispatchQueue.main.async { [weak self] in
            let panel = NSOpenPanel()
            panel.canChooseFiles = !parameters.allowsDirectories
            panel.canChooseDirectories = parameters.allowsDirectories
            panel.allowsMultipleSelection = parameters.allowsMultipleSelection
            panel.canCreateDirectories = false
            panel.resolvesAliases = true

            let handleResult: (NSApplication.ModalResponse) -> Void = { response in
                completeOnce(response == .OK ? panel.urls : nil)
            }

            if let window = self?.window {
                panel.beginSheetModal(for: window, completionHandler: handleResult)
            } else {
                panel.begin(completionHandler: handleResult)
            }
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
