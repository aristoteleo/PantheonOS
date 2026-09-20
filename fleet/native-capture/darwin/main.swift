// Fleet's owned-window capture helper. No display/desktop capture operation exists.
// stdout: u32 big-endian length, then JSON (tag 1) or u64 window id + JPEG (tag 2).
// stdin: bounded JSON lines. The owning PID is fixed for this helper's lifetime.
import AppKit
import ScreenCaptureKit
import CoreMedia
import CoreImage
import ApplicationServices
import VideoToolbox

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
func desktopAwake() -> Bool {
    let session = CGSessionCopyCurrentDictionary() as? [String: Any]
    guard session?[kCGSessionOnConsoleKey as String] as? Bool == true else { return false }
    var displays = [CGDirectDisplayID](repeating: 0, count: 32)
    var count: UInt32 = 0
    guard CGGetOnlineDisplayList(32, &displays, &count) == .success else { return false }
    return displays.prefix(Int(count)).contains { CGDisplayIsAsleep($0) == 0 }
}

// VideoToolbox is part of macOS. Require hardware and fall back to JPEG when
// unavailable; a failed encoder never prevents the owned window from opening.
final class H264Encoder {
    var session: VTCompressionSession?
    let window: UInt64
    var width = 0, height = 0
    var forceKey = true
    init(window: UInt64) { self.window = window }
    deinit { stop() }
    func stop() {
        if let session { VTCompressionSessionCompleteFrames(session, untilPresentationTimeStamp: .invalid); VTCompressionSessionInvalidate(session) }
        session = nil
    }
    func prepare(_ w: Int, _ h: Int) -> Bool {
        stop(); width = w; height = h; forceKey = true
        let spec: [CFString: Any] = [kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder: true,
            kVTVideoEncoderSpecification_EnableLowLatencyRateControl: true]
        let result = VTCompressionSessionCreate(allocator: nil, width: Int32(w), height: Int32(h), codecType: kCMVideoCodecType_H264,
            encoderSpecification: spec as CFDictionary, imageBufferAttributes: nil, compressedDataAllocator: nil,
            outputCallback: { ref, _, status, _, sample in
                guard status == noErr, let ref, let sample else { return }
                Unmanaged<H264Encoder>.fromOpaque(ref).takeUnretainedValue().output(sample)
            }, refcon: Unmanaged.passUnretained(self).toOpaque(), compressionSessionOut: &session)
        guard result == noErr, let session else { return false }
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_RealTime, value: kCFBooleanTrue)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_AllowFrameReordering, value: kCFBooleanFalse)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_ProfileLevel, value: kVTProfileLevel_H264_ConstrainedBaseline_AutoLevel)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_ExpectedFrameRate, value: 60 as CFNumber)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_AverageBitRate, value: min(12_000_000, max(2_000_000, w*h*5)) as CFNumber)
        VTSessionSetProperty(session, key: kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration, value: 1 as CFNumber)
        return VTCompressionSessionPrepareToEncodeFrames(session) == noErr
    }
    func encode(_ pixels: CVPixelBuffer) {
        let w = CVPixelBufferGetWidth(pixels), h = CVPixelBufferGetHeight(pixels)
        if w != width || h != height { if !prepare(w,h) { return } }
        guard let session else { return }
        let properties: [CFString: Any] = [kVTEncodeFrameOptionKey_ForceKeyFrame: forceKey]
        forceKey = false
        VTCompressionSessionEncodeFrame(session, imageBuffer: pixels, presentationTimeStamp: CMClockGetTime(CMClockGetHostTimeClock()),
            duration: CMTime(value: 1, timescale: 60), frameProperties: properties as CFDictionary, sourceFrameRefcon: nil, infoFlagsOut: nil)
    }
    func output(_ sample: CMSampleBuffer) {
        guard let description = CMSampleBufferGetFormatDescription(sample), let block = CMSampleBufferGetDataBuffer(sample) else { return }
        let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[CFString: Any]]
        let key = attachments?.first?[kCMSampleAttachmentKey_NotSync] as? Bool != true
        var annex = Data()
        let prefix = Data([0,0,0,1])
        var header: Int32 = 0
        var count = 0
        if key {
            var parameter: UnsafePointer<UInt8>?, length = 0
            guard CMVideoFormatDescriptionGetH264ParameterSetAtIndex(description, parameterSetIndex: 0,
                parameterSetPointerOut: &parameter, parameterSetSizeOut: &length, parameterSetCountOut: &count, nalUnitHeaderLengthOut: &header) == noErr else { return }
            for i in 0..<count {
                if CMVideoFormatDescriptionGetH264ParameterSetAtIndex(description, parameterSetIndex: i,
                    parameterSetPointerOut: &parameter, parameterSetSizeOut: &length, parameterSetCountOut: nil, nalUnitHeaderLengthOut: &header) == noErr,
                   let parameter { annex.append(prefix); annex.append(parameter, count: length) }
            }
        } else {
            guard CMVideoFormatDescriptionGetH264ParameterSetAtIndex(description, parameterSetIndex: 0,
                parameterSetPointerOut: nil, parameterSetSizeOut: nil, parameterSetCountOut: nil, nalUnitHeaderLengthOut: &header) == noErr else { return }
        }
        guard header == 4 else { return }
        let size = CMBlockBufferGetDataLength(block)
        var bytes = [UInt8](repeating: 0, count: size)
        guard CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: size, destination: &bytes) == noErr else { return }
        var offset = 0
        while offset + 4 <= size {
            let n = bytes[offset..<offset+4].reduce(0) { ($0 << 8) | Int($1) }; offset += 4
            guard n > 0, n <= size-offset else { return }
            annex.append(prefix); annex.append(contentsOf: bytes[offset..<offset+n]); offset += n
        }
        guard !annex.isEmpty else { return }
        var id = window.bigEndian
        var stamp = UInt64(max(0, CMTimeGetSeconds(sample.presentationTimeStamp)*1_000_000)).bigEndian
        var payload = Data(bytes: &id, count: 8); payload.append(key ? 1 : 0)
        payload.append(Data(bytes: &stamp, count: 8)); payload.append(annex)
        packet(3, payload)
    }
}

@available(macOS 13.0, *)
final class Sink: NSObject, SCStreamOutput, SCStreamDelegate {
    let window: UInt64
    let context = CIContext(options: [.cacheIntermediates: false])
    var stream: SCStream?
    let queue = DispatchQueue(label: "fleet.capture.frames")
    let encoder: H264Encoder
    var videoEnabled = false
    var lastPixels: CVPixelBuffer?
    var lastJPEG = 0.0
    var jpegInterval = 0.05
    init(window: UInt64) { self.window = window; encoder = H264Encoder(window: window) }
    func video(_ enabled: Bool, _ interval: Double) {
        queue.async { let changed = self.videoEnabled != enabled
            self.videoEnabled = enabled; self.jpegInterval = min(0.5, max(0.05, interval))
            if changed && enabled, let pixels = self.lastPixels { self.encoder.forceKey = true; self.encoder.encode(pixels) }
        }
    }
    func keyframe() {
        queue.async { self.encoder.forceKey = true
            if self.videoEnabled, let pixels = self.lastPixels { self.encoder.encode(pixels) }
        }
    }
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        jsonPacket(["event": "capture_error", "window": window, "error": error.localizedDescription])
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen, buffer.isValid,
              let attachments = CMSampleBufferGetSampleAttachmentsArray(buffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let status = attachments.first?[.status] as? Int,
              status == SCFrameStatus.complete.rawValue,
              let pixels = buffer.imageBuffer else { return }
        lastPixels = pixels
        if videoEnabled { encoder.encode(pixels) }
        let now = ProcessInfo.processInfo.systemUptime
        guard now - lastJPEG >= jpegInterval else { return }; lastJPEG = now
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
        guard desktopAwake() else { throw failure("Unlock the macOS desktop and wake its display before streaming") }
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
            if let sink = streams.removeValue(forKey: id) {
                try await sink.stream?.stopCapture(); sink.stream = nil
                sink.queue.sync { sink.encoder.stop(); sink.lastPixels = nil }
            }
            return [:]
        }
        let window = try await owned(id)
        if op == "capture" {
            if streams[id] != nil { return [:] }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let config = SCStreamConfiguration()
            config.width = min(3840, max(16, Int(window.frame.width))) / 2 * 2
            config.height = min(2160, max(16, Int(window.frame.height))) / 2 * 2
            config.minimumFrameInterval = CMTime(value: 1, timescale: 60)
            config.queueDepth = 2; config.showsCursor = false; config.capturesAudio = false
            if #available(macOS 14.0, *) { config.ignoreShadowsSingleWindow = true }
            let sink = Sink(window: id)
            let stream = SCStream(filter: filter, configuration: config, delegate: sink)
            sink.stream = stream
            try stream.addStreamOutput(sink, type: .screen, sampleHandlerQueue: sink.queue)
            let video = sink.encoder.prepare(config.width, config.height)
            try await stream.startCapture(); streams[id] = sink
            return video ? ["videoCodec": "h264"] : [:]
        } else if op == "video" {
            streams[id]?.video(a["enabled"] as? Bool == true, a["jpeg_interval"] as? Double ?? 0.05)
        } else if op == "keyframe" {
            streams[id]?.keyframe()
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
                config.width = Int(size.width) / 2 * 2; config.height = Int(size.height) / 2 * 2
                config.minimumFrameInterval = CMTime(value: 1, timescale: 60)
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
            guard AXIsProcessTrusted() else { throw failure("Allow Fleet Accessibility permission for native input") }
            if a["kind"] as? String != "pointer" || a["phase"] as? String == "down" { try focus(window) }
            let modifiers = a["modifiers"] as? [String] ?? []
            var flags = CGEventFlags()
            if modifiers.contains("shift") { flags.insert(.maskShift) }
            if modifiers.contains("ctrl") { flags.insert(.maskControl) }
            if modifiers.contains("alt") { flags.insert(.maskAlternate) }
            if modifiers.contains("meta") { flags.insert(.maskCommand) }
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
                let button: CGMouseButton = (a["button"] as? Int ?? 0) == 2 ? .right : (a["button"] as? Int ?? 0) == 1 ? .center : .left
                let phase = a["phase"] as? String ?? "move"
                let type: CGEventType = phase == "down" ? (button == .right ? .rightMouseDown : button == .center ? .otherMouseDown : .leftMouseDown)
                    : phase == "up" ? (button == .right ? .rightMouseUp : button == .center ? .otherMouseUp : .leftMouseUp)
                    : (a["buttons"] as? Int ?? 0) != 0 ? (button == .right ? .rightMouseDragged : button == .center ? .otherMouseDragged : .leftMouseDragged) : .mouseMoved
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

// Permission requests are user actions, never a side effect of --probe or
// starting the node. A separate process also avoids stale per-process TCC caches.
@MainActor final class PermissionSetup: NSObject, NSWindowDelegate {
    private let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 420),
        styleMask: [.titled, .closable], backing: .buffered, defer: false)
    private let recording = NSTextField(labelWithString: "Checking…")
    private let input = NSTextField(labelWithString: "Checking…")
    private let summary = NSTextField(wrappingLabelWithString: "")
    private let recordingButton = NSButton(title: "Allow Screen Recording", target: nil, action: nil)
    private let inputButton = NSButton(title: "Allow Input Control", target: nil, action: nil)
    private let done = NSButton(title: "Done", target: nil, action: nil)
    private var timer: Timer?
    private var checking = false
    private var requestedRecording = false
    private var requestedInput = false
    private var recordingAllowed = false
    private var inputAllowed = false
    private let preferences = UserDefaults(suiteName: "org.pantheonos.fleet.capture")!
    private let shownKey = "permissionGuideShown.v1"

    func show(automatic: Bool) -> Bool {
        guard desktopAwake() else { return false }
        if automatic && (preferences.bool(forKey: shownKey) ||
            (CGPreflightScreenCaptureAccess() && AXIsProcessTrusted())) { return false }
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)
        window.title = "Fleet · Streaming setup"
        window.level = .floating
        window.isReleasedWhenClosed = false
        window.delegate = self
        let content = window.contentView!
        let stack = NSStackView()
        stack.orientation = .vertical; stack.alignment = .leading; stack.spacing = 18
        stack.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 24),
            stack.bottomAnchor.constraint(equalTo: content.bottomAnchor, constant: -24),
        ])
        let heading = NSTextField(labelWithString: "Use this Mac for streamed apps")
        heading.font = .boldSystemFont(ofSize: 20)
        stack.addArrangedSubview(heading)
        let intro = NSTextField(wrappingLabelWithString: "Allow Fleet to show and control app windows from this Mac in Atrium. macOS will ask you to approve each permission.")
        intro.textColor = .secondaryLabelColor
        stack.addArrangedSubview(intro)
        recordingButton.target = self; recordingButton.action = #selector(allowRecording)
        inputButton.target = self; inputButton.action = #selector(allowInput)
        stack.addArrangedSubview(permissionRow("Screen Recording", detail: "Show the windows of apps started by Fleet.", status: recording, button: recordingButton))
        stack.addArrangedSubview(permissionRow("Accessibility", detail: "Control those apps with the mouse and keyboard.", status: input, button: inputButton))
        summary.textColor = .secondaryLabelColor
        summary.font = .systemFont(ofSize: 12)
        stack.addArrangedSubview(summary)
        let later = NSButton(title: "Set Up Later", target: self, action: #selector(finish))
        later.bezelStyle = .rounded; later.keyEquivalent = "\u{1b}"
        done.target = self; done.action = #selector(finish); done.bezelStyle = .rounded
        done.keyEquivalent = "\r"
        let footer = NSStackView(views: [later, NSView(), done])
        footer.orientation = .horizontal
        stack.addArrangedSubview(footer)
        for view in stack.arrangedSubviews {
            view.widthAnchor.constraint(equalTo: stack.widthAnchor).isActive = true
        }
        update(recording: CGPreflightScreenCaptureAccess(), input: AXIsProcessTrusted())
        window.center()
        window.makeKeyAndOrderFront(nil)
        application.activate(ignoringOtherApps: true)
        preferences.set(true, forKey: shownKey)
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        return true
    }

    private func permissionRow(_ title: String, detail: String, status: NSTextField, button: NSButton) -> NSView {
        let name = NSTextField(labelWithString: title)
        name.font = .boldSystemFont(ofSize: 13)
        let detailLabel = NSTextField(wrappingLabelWithString: detail)
        detailLabel.textColor = .secondaryLabelColor; detailLabel.font = .systemFont(ofSize: 12)
        status.font = .systemFont(ofSize: 12)
        let labels = NSStackView(views: [name, detailLabel, status])
        labels.orientation = .vertical; labels.alignment = .leading; labels.spacing = 4
        button.bezelStyle = .rounded
        button.setContentHuggingPriority(.required, for: .horizontal)
        let row = NSStackView(views: [labels, button])
        row.orientation = .horizontal; row.alignment = .centerY; row.spacing = 16
        labels.widthAnchor.constraint(greaterThanOrEqualToConstant: 250).isActive = true
        return row
    }

    private func update(recording allowedRecording: Bool, input allowedInput: Bool) {
        recordingAllowed = allowedRecording; inputAllowed = allowedInput
        recording.stringValue = allowedRecording ? "✓ Allowed" : "Permission needed"
        input.stringValue = allowedInput ? "✓ Allowed" : "Permission needed"
        recording.textColor = allowedRecording ? .systemGreen : .secondaryLabelColor
        input.textColor = allowedInput ? .systemGreen : .secondaryLabelColor
        recordingButton.isEnabled = !allowedRecording; inputButton.isEnabled = !allowedInput
        recordingButton.title = allowedRecording ? "Allowed" : (requestedRecording ? "Open Settings" : "Allow Screen Recording")
        inputButton.title = allowedInput ? "Allowed" : (requestedInput ? "Open Settings" : "Allow Input Control")
        done.isEnabled = allowedRecording && allowedInput
        summary.stringValue = done.isEnabled
            ? "Streaming is ready. Fleet will update this node automatically; no restart is needed."
            : "You can set this up later and keep using Files and other apps. To return, run fleet capture permissions."
    }

    @objc private func allowRecording() {
        if requestedRecording { openSettings("Privacy_ScreenCapture") }
        else {
            requestedRecording = true
            if !CGRequestScreenCaptureAccess() { openSettings("Privacy_ScreenCapture") }
        }
        update(recording: recordingAllowed, input: inputAllowed); refresh()
    }
    @objc private func allowInput() {
        if requestedInput { openSettings("Privacy_Accessibility") }
        else {
            requestedInput = true
            _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
        }
        update(recording: recordingAllowed, input: inputAllowed); refresh()
    }
    private func openSettings(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?" + pane) {
            NSWorkspace.shared.open(url)
        }
    }
    private func refresh() {
        guard !checking else { return }
        checking = true
        // A fresh probe sees grants even when macOS caches an earlier denial in
        // the guide process. It never requests permission or starts a capture.
        let executable = URL(fileURLWithPath: CommandLine.arguments[0])
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let process = Process(), output = Pipe()
            process.executableURL = executable; process.arguments = ["--probe"]
            process.standardOutput = output; process.standardError = FileHandle.nullDevice
            var status: [String: Any]?
            do {
                try process.run()
                let data = output.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                status = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            } catch { }
            let recording = status?["screen_recording"] as? Bool ?? false
            let input = status?["input"] as? Bool ?? false
            DispatchQueue.main.async {
                guard let self else { return }
                self.checking = false
                self.update(recording: recording, input: input)
            }
        }
    }
    @objc private func finish() { window.close() }
    func windowWillClose(_ notification: Notification) {
        timer?.invalidate()
        NSApplication.shared.stop(nil)
        NSApplication.shared.postEvent(NSEvent.otherEvent(with: .applicationDefined, location: .zero,
            modifierFlags: [], timestamp: 0, windowNumber: 0, context: nil, subtype: 0, data1: 0, data2: 0)!, atStart: true)
    }
}

@main struct Main {
    @MainActor static func main() {
        guard #available(macOS 13.0, *) else { print("{\"protocol\":1,\"available\":false,\"error\":\"macOS 13 or newer required\"}"); return }
        let args = CommandLine.arguments
        // Deterministic synthetic pixels only: exercises hardware encoding
        // without recording the desktop or requiring privacy permissions.
        if args.contains("--test-encoder") {
            let encoder = H264Encoder(window: 1)
            guard encoder.prepare(320, 240) else { exit(77) }
            var pixel: CVPixelBuffer?
            guard CVPixelBufferCreate(nil, 320, 240, kCVPixelFormatType_32BGRA,
                [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &pixel) == kCVReturnSuccess,
                let pixel else { exit(1) }
            CVPixelBufferLockBaseAddress(pixel, [])
            memset(CVPixelBufferGetBaseAddress(pixel), 0x66, CVPixelBufferGetDataSize(pixel))
            CVPixelBufferUnlockBaseAddress(pixel, [])
            for _ in 0..<30 { encoder.encode(pixel); Thread.sleep(forTimeInterval: 1.0/60) }
            encoder.stop(); return
        }
        if args.contains("--setup") || args.contains("--permissions") {
            let setup = PermissionSetup()
            if setup.show(automatic: args.contains("--setup")) {
                withExtendedLifetime(setup) { NSApplication.shared.run() }
            }
            return
        }
        if args.contains("--probe") {
            let result: [String: Any] = ["protocol": 1, "backend": "screencapturekit", "available": true,
                "screen_recording": CGPreflightScreenCaptureAccess(), "input": AXIsProcessTrusted(),
                "interactive": desktopAwake(), "setup": true]
            let data = try! JSONSerialization.data(withJSONObject: result)
            print(String(data: data, encoding: .utf8)!); return
        }
        guard args.count == 3, args[1] == "--pid", let pid = Int32(args[2]) else { exit(2) }
        let application = NSApplication.shared
        application.setActivationPolicy(.prohibited)
        Task { @MainActor in
        do {
            // Popen returns before LaunchServices has registered a GUI process.
            // Wait for this exact child, without following another PID.
            let deadline = Date().addingTimeInterval(10)
            while NSRunningApplication(processIdentifier: pid) == nil {
                guard Darwin.kill(pid, 0) == 0, Date() < deadline else {
                    throw failure("The owned application did not register with macOS")
                }
                try await Task.sleep(nanoseconds: 50_000_000)
            }
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
        exit(0)
        }
        // ScreenCaptureKit completion callbacks need a live AppKit runloop,
        // including in this otherwise headless helper process.
        application.run()
    }
}
