import AppKit
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let window = NSWindow(contentRect: NSRect(x: 100, y: 100, width: 480, height: 320),
    styleMask: [.titled, .closable, .resizable], backing: .buffered, defer: false)
window.title = "Fleet capture fixture"
window.backgroundColor = NSColor(calibratedRed: 0.2, green: 0.7, blue: 0.3, alpha: 1)
window.makeKeyAndOrderFront(nil)
app.activate(ignoringOtherApps: true)
app.run()
