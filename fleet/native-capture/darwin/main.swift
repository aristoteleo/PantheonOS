// Fleet's owned-window capture helper. No display/desktop capture operation exists.
// stdout: u32 big-endian length, then JSON (tag 1) or u64 window id + JPEG (tag 2).
// stdin: bounded JSON lines. The owning PID is fixed for this helper's lifetime.
import AppKit
import ScreenCaptureKit
import CoreMedia
import CoreImage
import ApplicationServices

let outputLock = NSLock()
func packet(_ tag: UInt8, _ bytes: Data) {
    outputLock.lock(); defer { outputLock.unlock() }
    var size = UInt32(bytes.count + 1).bigEndian
    var data = Data(bytes: &size, count: 4)
    data.append(tag); data.append(bytes)
    do { try FileHandle.standardOutput.write(contentsOf: data) } catch { exit(0) }
}
func jsonPacket(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value) { packet(1, data) }
}
func failure(_ message: String) -> NSError {
    NSError(domain: "FleetCapture", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
}

@available(macOS 13.0, *)
final class Sink: NSObject, SCStreamOutput, SCStreamDelegate {
    let window: UInt64
    let context = CIContext(options: [.cacheIntermediates: false])
    var stream: SCStream?
    init(window: UInt64) { self.window = window }
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        jsonPacket(["event": "capture_error", "window": window, "error": error.localizedDescription])
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, buffer.isValid,
              let attachments = CMSampleBufferGetSampleAttachmentsArray(buffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let status = attachments.first?[.status] as? Int,
              status == SCFrameStatus.complete.rawValue,
              let pixels = buffer.imageBuffer else { return }
        // This callback is serial. Backpressure is bounded by SCStream's two
        // buffers and the pipe; no unbounded frame queue or stale-frame replay.
        let image = CIImage(cvPixelBuffer: pixels)
        guard let jpeg = context.jpegRepresentation(of: image, colorSpace: CGColorSpaceCreateDeviceRGB(),
            options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.78]) else { return }
        var id = window.bigEndian
        var payload = Data(bytes: &id, count: 8); payload.append(jpeg)
        packet(2, payload)
    }
}

@available(macOS 13.0, *)
@MainActor final class Capture {
    let pid: pid_t
    let application: NSRunningApplication
    var streams: [UInt64: Sink] = [:]
    var inventory: [SCWindow] = []
    var inventoryTime = Date.distantPast
    init(pid: pid_t) throws {
        guard pid > 0, let app = NSRunningApplication(processIdentifier: pid), !app.isTerminated else {
            throw failure("The owned application is no longer running")
        }
        self.pid = pid; application = app
    }
    func windows() async throws -> [SCWindow] {
        guard !application.isTerminated else { throw failure("The owned application exited") }
        guard CGPreflightScreenCaptureAccess() else {
            throw failure("Allow Fleet in System Settings > Privacy & Security > Screen Recording, then restart Fleet")
        }
        if Date().timeIntervalSince(inventoryTime) < 0.1 { return inventory }
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
        inventory = content.windows.filter { $0.owningApplication?.processID == pid && $0.frame.width > 1 && $0.frame.height > 1 }
        inventoryTime = Date()
        return inventory
    }
    func owned(_ id: UInt64) async throws -> SCWindow {
        guard let window = try await windows().first(where: { UInt64($0.windowID) == id }) else {
            throw failure("Window is closed or belongs to another application")
        }
        return window
    }
    func axWindow(_ window: SCWindow) throws -> AXUIElement {
        guard AXIsProcessTrusted() else {
            throw failure("Allow Fleet in System Settings > Privacy & Security > Accessibility to control this app")
        }
        let app = AXUIElementCreateApplication(pid)
        var raw: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXWindowsAttribute as CFString, &raw) == .success,
              let candidates = raw as? [AXUIElement] else { throw failure("App window is not accessible") }
        // Public AX API has no CGWindowID lookup. Match title AND geometry;
        // reject ambiguous matches instead of controlling the wrong window.
        let matching = candidates.filter { candidate in
            var p: CFTypeRef?; var t: CFTypeRef?
            AXUIElementCopyAttributeValue(candidate, kAXPositionAttribute as CFString, &p)
            AXUIElementCopyAttributeValue(candidate, kAXTitleAttribute as CFString, &t)
            guard let p, CFGetTypeID(p) == AXValueGetTypeID() else { return false }
            var point = CGPoint.zero
            AXValueGetValue(p as! AXValue, .cgPoint, &point)
            return abs(point.x - window.frame.minX) < 3 && abs(point.y - window.frame.minY) < 3
                && (window.title?.isEmpty != false || (t as? String) == window.title)
        }
        guard matching.count == 1 else { throw failure("Could not uniquely identify this app window for input") }
        return matching[0]
    }
    func focus(_ window: SCWindow) throws {
        let target = try axWindow(window)
        application.activate(options: [.activateIgnoringOtherApps])
        guard AXUIElementPerformAction(target, kAXRaiseAction as CFString) == .success else {
            throw failure("Could not focus this app window")
        }
    }
    func command(_ a: [String: Any]) async throws -> [String: Any] {
        let op = a["op"] as? String ?? ""
        if op == "list" {
            return ["windows": try await windows().map { w in
                ["id": UInt64(w.windowID), "title": w.title ?? "", "x": w.frame.minX, "y": w.frame.minY,
                 "w": w.frame.width, "h": w.frame.height, "visible": w.isOnScreen, "pid": pid] as [String: Any]
            }]
        }
        let id = (a["window"] as? NSNumber)?.uint64Value ?? 0
        if op == "uncapture" {
            if let sink = streams.removeValue(forKey: id) { try await sink.stream?.stopCapture() }
            return [:]
        }
        let window = try await owned(id)
        if op == "capture" {
            if streams[id] != nil { return [:] }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let config = SCStreamConfiguration()
            config.width = min(3840, max(16, Int(window.frame.width)))
            config.height = min(2160, max(16, Int(window.frame.height)))
            config.minimumFrameInterval = CMTime(value: 1, timescale: 20)
            config.queueDepth = 2; config.showsCursor = false; config.capturesAudio = false
            if #available(macOS 14.0, *) { config.ignoreShadowsSingleWindow = true }
            let sink = Sink(window: id)
            let stream = SCStream(filter: filter, configuration: config, delegate: sink)
            sink.stream = stream
            try stream.addStreamOutput(sink, type: .screen, sampleHandlerQueue: DispatchQueue(label: "fleet.capture.\(id)"))
            try await stream.startCapture(); streams[id] = sink
        } else if op == "focus" {
            try focus(window)
        } else if op == "resize" {
            let target = try axWindow(window)
            var size = CGSize(width: min(3840, max(320, a["w"] as? Int ?? 1280)),
                              height: min(2160, max(200, a["h"] as? Int ?? 800)))
            let value = AXValueCreate(.cgSize, &size)!
            guard AXUIElementSetAttributeValue(target, kAXSizeAttribute as CFString, value) == .success else {
                throw failure("This window cannot be resized")
            }
            if let sink = streams[id], let stream = sink.stream {
                let config = SCStreamConfiguration()
                config.width = Int(size.width); config.height = Int(size.height)
                config.minimumFrameInterval = CMTime(value: 1, timescale: 20)
                config.queueDepth = 2; config.showsCursor = false; config.capturesAudio = false
                if #available(macOS 14.0, *) { config.ignoreShadowsSingleWindow = true }
                try await stream.updateConfiguration(config)
            }
        } else if op == "close" {
            let target = try axWindow(window)
            var button: CFTypeRef?
            guard AXUIElementCopyAttributeValue(target, kAXCloseButtonAttribute as CFString, &button) == .success,
                  let button else { throw failure("This window has no close button") }
            AXUIElementPerformAction(button as! AXUIElement, kAXPressAction as CFString)
        } else if op == "input" {
            try focus(window)
            var flags = CGEventFlags()
            if a["shift"] as? Bool == true { flags.insert(.maskShift) }
            if a["ctrl"] as? Bool == true { flags.insert(.maskControl) }
            if a["alt"] as? Bool == true { flags.insert(.maskAlternate) }
            if a["meta"] as? Bool == true { flags.insert(.maskCommand) }
            let kind = a["kind"] as? String ?? ""
            var event: CGEvent?
            if kind == "key" {
                let code = a["code"] as? String ?? ""
                guard let key = keyCodes[code] else { throw failure("Unsupported physical key: \(code)") }
                event = CGEvent(keyboardEventSource: nil, virtualKey: key, keyDown: a["down"] as? Bool == true)
            } else if kind == "text" {
                let chars = Array((a["text"] as? String ?? "").prefix(4096).utf16)
                for down in [true, false] {
                    let e = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down)!
                    e.keyboardSetUnicodeString(stringLength: chars.count, unicodeString: chars)
                    e.postToPid(pid)
                }
                return [:]
            } else if kind == "wheel" {
                event = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 2,
                    wheel1: Int32(-min(2000, max(-2000, a["dy"] as? Int ?? 0))),
                    wheel2: Int32(-min(2000, max(-2000, a["dx"] as? Int ?? 0))), wheel3: 0)
            } else if kind == "pointer" {
                let x = min(1.0, max(0.0, a["x"] as? Double ?? 0))
                let y = min(1.0, max(0.0, a["y"] as? Double ?? 0))
                let p = CGPoint(x: window.frame.minX + x * window.frame.width, y: window.frame.minY + y * window.frame.height)
                let button: CGMouseButton = (a["button"] as? Int ?? 0) == 2 ? .right : .left
                let phase = a["phase"] as? String ?? "move"
                let type: CGEventType = phase == "down" ? (button == .right ? .rightMouseDown : .leftMouseDown)
                    : phase == "up" ? (button == .right ? .rightMouseUp : .leftMouseUp)
                    : (a["buttons"] as? Int ?? 0) != 0 ? (button == .right ? .rightMouseDragged : .leftMouseDragged) : .mouseMoved
                event = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: p, mouseButton: button)
                event?.setIntegerValueField(.mouseEventClickState, value: Int64(a["clicks"] as? Int ?? 1))
            } else { throw failure("Unknown input event") }
            event?.flags = flags; event?.postToPid(pid)
        } else { throw failure("Unknown capture command") }
        return [:]
    }
    func shutdown() async {
        for sink in streams.values { try? await sink.stream?.stopCapture() }
        streams.removeAll()
    }
}

let keyCodes: [String: CGKeyCode] = [
    "KeyA":0,"KeyS":1,"KeyD":2,"KeyF":3,"KeyH":4,"KeyG":5,"KeyZ":6,"KeyX":7,"KeyC":8,"KeyV":9,
    "KeyB":11,"KeyQ":12,"KeyW":13,"KeyE":14,"KeyR":15,"KeyY":16,"KeyT":17,"Digit1":18,"Digit2":19,
    "Digit3":20,"Digit4":21,"Digit6":22,"Digit5":23,"Equal":24,"Digit9":25,"Digit7":26,"Minus":27,
    "Digit8":28,"Digit0":29,"BracketRight":30,"KeyO":31,"KeyU":32,"BracketLeft":33,"KeyI":34,"KeyP":35,
    "Enter":36,"KeyL":37,"KeyJ":38,"Quote":39,"KeyK":40,"Semicolon":41,"Backslash":42,"Comma":43,
    "Slash":44,"KeyN":45,"KeyM":46,"Period":47,"Tab":48,"Space":49,"Backquote":50,"Backspace":51,
    "Escape":53,"MetaLeft":55,"MetaRight":54,"ShiftLeft":56,"CapsLock":57,"AltLeft":58,"ControlLeft":59,
    "ShiftRight":60,"AltRight":61,"ControlRight":62,"F5":96,"F6":97,"F7":98,"F3":99,"F8":100,"F9":101,
    "F11":103,"F10":109,"F12":111,"Home":115,"PageUp":116,"Delete":117,"F4":118,"End":119,
    "F2":120,"PageDown":121,"F1":122,"ArrowLeft":123,"ArrowRight":124,"ArrowDown":125,"ArrowUp":126]

@main struct Main {
    static func main() async {
        guard #available(macOS 13.0, *) else { print("{\"protocol\":1,\"available\":false,\"error\":\"macOS 13 or newer required\"}"); return }
        let args = CommandLine.arguments
        if args.contains("--probe") || args.contains("--permissions") {
            if args.contains("--permissions") {
                _ = CGRequestScreenCaptureAccess()
                _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
            }
            let result: [String: Any] = ["protocol": 1, "backend": "screencapturekit", "available": true,
                "screen_recording": CGPreflightScreenCaptureAccess(), "input": AXIsProcessTrusted()]
            let data = try! JSONSerialization.data(withJSONObject: result)
            print(String(data: data, encoding: .utf8)!); return
        }
        guard args.count == 3, args[1] == "--pid", let pid = Int32(args[2]) else { exit(2) }
        do {
            let capture = try Capture(pid: pid)
            // Read off the main runloop: ScreenCaptureKit/AX callbacks remain live.
            while let line = await Task.detached(operation: { readLine() }).value {
                guard line.utf8.count <= 65536 else { break }
                var id = ""
                do {
                    guard let a = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else { throw failure("Invalid JSON command") }
                    id = a["id"] as? String ?? ""
                    let value = try await capture.command(a)
                    jsonPacket(value.merging(["id": id, "ok": true]) { _, new in new })
                } catch { jsonPacket(["id": id, "ok": false, "error": error.localizedDescription]) }
            }
            await capture.shutdown()
        } catch { jsonPacket(["event": "fatal", "error": error.localizedDescription]); exit(1) }
    }
}
