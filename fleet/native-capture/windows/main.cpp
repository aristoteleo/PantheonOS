// Windows 10 1903+ owned-window capture. WGC, not desktop duplication/cropping.
// Same bounded packet/JSON-lines protocol as the ScreenCaptureKit helper.
#include <windows.h>
#include <dwmapi.h>
#include <d3d11.h>
#include <dxgi.h>
#include <wincodec.h>
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>
#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Foundation.Collections.h>
#include <winrt/Windows.Foundation.Metadata.h>
#include <winrt/Windows.Data.Json.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <winrt/Windows.Graphics.DirectX.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fcntl.h>
#include <io.h>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

using namespace winrt;
using namespace winrt::Windows::Data::Json;
using namespace winrt::Windows::Graphics::Capture;
using namespace winrt::Windows::Graphics::DirectX;
using namespace winrt::Windows::Graphics::DirectX::Direct3D11;
using namespace winrt::Windows::Foundation;
using Clock = std::chrono::steady_clock;
std::mutex outputMutex;

void packet(uint8_t tag, const std::vector<uint8_t>& bytes) {
    if (bytes.size() > 16 * 1024 * 1024) throw std::runtime_error("Capture frame exceeds limit");
    std::lock_guard lock(outputMutex);
    uint32_t n = static_cast<uint32_t>(bytes.size() + 1);
    uint8_t header[]{uint8_t(n >> 24), uint8_t(n >> 16), uint8_t(n >> 8), uint8_t(n), tag};
    std::cout.write(reinterpret_cast<char*>(header), 5);
    std::cout.write(reinterpret_cast<const char*>(bytes.data()), bytes.size()); std::cout.flush();
    if (!std::cout) std::exit(0);
}
void emit(JsonObject const& object) {
    auto text = to_string(object.Stringify()); packet(1, {text.begin(), text.end()});
}
JsonObject errorObject(std::string const& error) {
    JsonObject result; result.SetNamedValue(L"ok", JsonValue::CreateBooleanValue(false));
    result.SetNamedValue(L"error", JsonValue::CreateStringValue(to_hstring(error))); return result;
}
void number(JsonObject const& j, wchar_t const* key, double value) { j.SetNamedValue(key, JsonValue::CreateNumberValue(value)); }
void text(JsonObject const& j, wchar_t const* key, std::wstring const& value) { j.SetNamedValue(key, JsonValue::CreateStringValue(value)); }
double num(JsonObject const& j, wchar_t const* key, double fallback = 0) { return j.GetNamedNumber(key, fallback); }
std::string str(JsonObject const& j, wchar_t const* key) { return to_string(j.GetNamedString(key, L"")); }

RECT bounds(HWND hwnd) {
    RECT r{};
    if (FAILED(DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, &r, sizeof(r)))) GetWindowRect(hwnd, &r);
    return r;
}

struct CaptureSession: std::enable_shared_from_this<CaptureSession> {
    HWND hwnd;
    Direct3D11CaptureFramePool pool{nullptr};
    GraphicsCaptureSession session{nullptr};
    GraphicsCaptureItem item{nullptr};
    IDirect3DDevice device{nullptr};
    com_ptr<ID3D11Device> d3d;
    com_ptr<ID3D11DeviceContext> context;
    com_ptr<IWICImagingFactory> wic;
    event_token frameToken{};
    std::mutex frameMutex;
    Clock::time_point last{};
    explicit CaptureSession(HWND window): hwnd(window) {}
    void start() {
        auto created = D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION, d3d.put(), nullptr, context.put());
        if (FAILED(created)) check_hresult(D3D11CreateDevice(nullptr, D3D_DRIVER_TYPE_WARP, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION, d3d.put(), nullptr, context.put()));
        auto dxgi = d3d.as<IDXGIDevice>();
        com_ptr<::IInspectable> inspectable;
        check_hresult(CreateDirect3D11DeviceFromDXGIDevice(dxgi.get(), inspectable.put()));
        device = inspectable.as<IDirect3DDevice>();
        wic = create_instance<IWICImagingFactory>(CLSID_WICImagingFactory);
        auto interop = get_activation_factory<GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
        check_hresult(interop->CreateForWindow(hwnd, guid_of<GraphicsCaptureItem>(), put_abi(item)));
        pool = Direct3D11CaptureFramePool::CreateFreeThreaded(device, DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, item.Size());
        std::weak_ptr<CaptureSession> weak = shared_from_this();
        frameToken = pool.FrameArrived([weak](auto const& sender, auto const&) {
            if (auto self = weak.lock()) self->frame(sender);
        });
        session = pool.CreateCaptureSession(item);
        if (winrt::Windows::Foundation::Metadata::ApiInformation::IsPropertyPresent(L"Windows.Graphics.Capture.GraphicsCaptureSession", L"IsCursorCaptureEnabled")) session.IsCursorCaptureEnabled(false);
        // Keep Windows' capture border. Never weaken the OS recording indicator.
        session.StartCapture();
    }
    void frame(Direct3D11CaptureFramePool const& sender) noexcept {
        try {
            std::unique_lock lock(frameMutex, std::try_to_lock);
            if (!lock) return;
            auto frame = sender.TryGetNextFrame(); if (!frame) return;
            auto now = Clock::now(); if (now - last < std::chrono::milliseconds(50)) return;
            last = now;
            auto size = frame.ContentSize();
            if (size.Width < 1 || size.Height < 1 || size.Width > 7680 || size.Height > 4320) return;
            auto access = frame.Surface().as<::Windows::Graphics::DirectX::Direct3D11::IDirect3DDxgiInterfaceAccess>();
            com_ptr<ID3D11Texture2D> texture;
            check_hresult(access->GetInterface(__uuidof(ID3D11Texture2D), texture.put_void()));
            D3D11_TEXTURE2D_DESC desc{}; texture->GetDesc(&desc);
            // Recreate after releasing the old frame when the native window resizes.
            if (size.Width != int(desc.Width) || size.Height != int(desc.Height)) {
                frame.Close(); sender.Recreate(device, DirectXPixelFormat::B8G8R8A8UIntNormalized, 2, size); return;
            }
            desc.Usage = D3D11_USAGE_STAGING; desc.BindFlags = 0; desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ; desc.MiscFlags = 0;
            com_ptr<ID3D11Texture2D> staging; check_hresult(d3d->CreateTexture2D(&desc, nullptr, staging.put()));
            context->CopyResource(staging.get(), texture.get());
            D3D11_MAPPED_SUBRESOURCE mapped{};
            check_hresult(context->Map(staging.get(), 0, D3D11_MAP_READ, 0, &mapped));
            std::vector<uint8_t> bgr(size.Width * size.Height * 3);
            for (int y = 0; y < size.Height; ++y) {
                auto row = static_cast<uint8_t*>(mapped.pData) + y * mapped.RowPitch;
                for (int x = 0; x < size.Width; ++x) {
                    auto offset = (y * size.Width + x) * 3;
                    std::copy_n(row + x * 4, 3, bgr.data() + offset);
                }
            }
            context->Unmap(staging.get(), 0); frame.Close();
            com_ptr<IStream> stream; check_hresult(CreateStreamOnHGlobal(nullptr, TRUE, stream.put()));
            com_ptr<IWICBitmapEncoder> encoder; check_hresult(wic->CreateEncoder(GUID_ContainerFormatJpeg, nullptr, encoder.put()));
            check_hresult(encoder->Initialize(stream.get(), WICBitmapEncoderNoCache));
            com_ptr<IWICBitmapFrameEncode> encoded; com_ptr<IPropertyBag2> options;
            check_hresult(encoder->CreateNewFrame(encoded.put(), options.put()));
            PROPBAG2 property{}; property.pstrName = const_cast<wchar_t*>(L"ImageQuality");
            VARIANT quality{}; quality.vt = VT_R4; quality.fltVal = .78f;
            check_hresult(options->Write(1, &property, &quality));
            check_hresult(encoded->Initialize(options.get()));
            check_hresult(encoded->SetSize(size.Width, size.Height));
            WICPixelFormatGUID format = GUID_WICPixelFormat24bppBGR;
            check_hresult(encoded->SetPixelFormat(&format));
            check_hresult(encoded->WritePixels(size.Height, size.Width * 3, static_cast<UINT>(bgr.size()), bgr.data()));
            check_hresult(encoded->Commit()); check_hresult(encoder->Commit());
            STATSTG stat{}; check_hresult(stream->Stat(&stat, STATFLAG_NONAME));
            HGLOBAL memory{}; check_hresult(GetHGlobalFromStream(stream.get(), &memory));
            auto length = static_cast<size_t>(stat.cbSize.QuadPart);
            if (length > 16 * 1024 * 1024 - 8) throw std::runtime_error("JPEG exceeds capture limit");
            auto data = static_cast<uint8_t*>(GlobalLock(memory));
            if (!data) throw std::runtime_error("Could not access encoded frame");
            auto id = reinterpret_cast<uint64_t>(hwnd);
            std::vector<uint8_t> payload(8 + length);
            for (int i = 0; i < 8; ++i) payload[i] = uint8_t(id >> (56 - i * 8));
            std::copy_n(data, length, payload.data() + 8); GlobalUnlock(memory);
            packet(2, payload);
        } catch (hresult_error const& e) { failure(to_string(e.message())); }
          catch (std::exception const& e) { failure(e.what()); }
    }
    void failure(std::string const& message) noexcept {
        try { auto j = errorObject(message); text(j, L"event", L"capture_error"); number(j, L"window", reinterpret_cast<uint64_t>(hwnd)); emit(j); } catch (...) {}
    }
    ~CaptureSession() {
        try { if (pool) pool.FrameArrived(frameToken); if (session) session.Close(); if (pool) pool.Close(); } catch (...) {}
    }
};

struct Owner {
    DWORD pid;
    HANDLE process;
    std::map<HWND, std::shared_ptr<CaptureSession>> streams;
    explicit Owner(DWORD owner): pid(owner), process(OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, owner)) {
        if (!process) throw std::runtime_error("Owned application is not running or cannot be inspected");
    }
    ~Owner() { streams.clear(); CloseHandle(process); }
    bool owns(HWND hwnd) const {
        if (WaitForSingleObject(process, 0) != WAIT_TIMEOUT) return false;
        DWORD actual{}; GetWindowThreadProcessId(hwnd, &actual); return actual == pid && IsWindow(hwnd);
    }
    void check(HWND hwnd) const { if (!owns(hwnd)) throw std::runtime_error("Window is closed or belongs to another application"); }
    void focus(HWND hwnd) const {
        check(hwnd); if (IsIconic(hwnd)) ShowWindow(hwnd, SW_RESTORE);
        SetForegroundWindow(hwnd);
        if (GetAncestor(GetForegroundWindow(), GA_ROOTOWNER) != GetAncestor(hwnd, GA_ROOTOWNER))
            throw std::runtime_error("Windows denied foreground input; activate the app on this node and retry");
    }
    JsonArray windows() const {
        struct State { Owner const* owner; JsonArray result; } state{this, JsonArray{}};
        EnumWindows([](HWND hwnd, LPARAM parameter) -> BOOL {
            auto& s = *reinterpret_cast<State*>(parameter);
            if (!s.owner->owns(hwnd) || !IsWindowVisible(hwnd)) return TRUE;
            auto r = bounds(hwnd); if (r.right - r.left < 2 || r.bottom - r.top < 2) return TRUE;
            wchar_t title[4096]{}; GetWindowTextW(hwnd, title, 4096);
            JsonObject item; number(item, L"id", reinterpret_cast<uint64_t>(hwnd)); text(item, L"title", title);
            number(item, L"pid", s.owner->pid); number(item, L"x", r.left); number(item, L"y", r.top);
            number(item, L"w", r.right - r.left); number(item, L"h", r.bottom - r.top);
            number(item, L"parent", reinterpret_cast<uint64_t>(GetWindow(hwnd, GW_OWNER)));
            item.SetNamedValue(L"visible", JsonValue::CreateBooleanValue(!IsIconic(hwnd)));
            s.result.Append(item); return TRUE;
        }, reinterpret_cast<LPARAM>(&state)); return state.result;
    }
    JsonObject command(JsonObject const& a) {
        auto op = str(a, L"op"); JsonObject result;
        if (op == "list") { result.SetNamedValue(L"windows", windows()); return result; }
        HWND hwnd = reinterpret_cast<HWND>(static_cast<uint64_t>(num(a, L"window")));
        if (op == "uncapture") { streams.erase(hwnd); return result; }
        check(hwnd);
        if (op == "capture") {
            if (!streams.count(hwnd)) { auto capture = std::make_shared<CaptureSession>(hwnd); capture->start(); streams[hwnd] = capture; }
        } else if (op == "focus") focus(hwnd);
        else if (op == "resize") {
            if (!SetWindowPos(hwnd, nullptr, 0, 0, std::clamp(int(num(a, L"w", 1280)), 320, 3840),
                std::clamp(int(num(a, L"h", 800)), 200, 2160), SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE))
                throw std::runtime_error("Could not resize this window");
        } else if (op == "close") PostMessageW(hwnd, WM_CLOSE, 0, 0);
        else if (op == "input") input(hwnd, a);
        else throw std::runtime_error("Unknown capture command");
        return result;
    }
    static WORD virtualKey(std::string const& code) {
        if (code.size() == 4 && code.substr(0, 3) == "Key") return WORD(code[3]);
        if (code.size() == 6 && code.substr(0, 5) == "Digit") return WORD(code[5]);
        const std::map<std::string, WORD> keys{{"Enter",VK_RETURN},{"Escape",VK_ESCAPE},{"Tab",VK_TAB},{"Space",VK_SPACE},
            {"Backspace",VK_BACK},{"Delete",VK_DELETE},{"ArrowLeft",VK_LEFT},{"ArrowRight",VK_RIGHT},{"ArrowUp",VK_UP},{"ArrowDown",VK_DOWN},
            {"Home",VK_HOME},{"End",VK_END},{"PageUp",VK_PRIOR},{"PageDown",VK_NEXT},{"ShiftLeft",VK_LSHIFT},{"ShiftRight",VK_RSHIFT},
            {"ControlLeft",VK_LCONTROL},{"ControlRight",VK_RCONTROL},{"AltLeft",VK_LMENU},{"AltRight",VK_RMENU},{"MetaLeft",VK_LWIN},{"MetaRight",VK_RWIN},
            {"Minus",VK_OEM_MINUS},{"Equal",VK_OEM_PLUS},{"BracketLeft",VK_OEM_4},{"BracketRight",VK_OEM_6},{"Backslash",VK_OEM_5},
            {"Semicolon",VK_OEM_1},{"Quote",VK_OEM_7},{"Comma",VK_OEM_COMMA},{"Period",VK_OEM_PERIOD},{"Slash",VK_OEM_2},{"Backquote",VK_OEM_3}};
        auto it = keys.find(code); if (it != keys.end()) return it->second;
        if (code.size() >= 2 && code.size() <= 3 && code[0] == 'F') { int n = std::stoi(code.substr(1)); if (n >= 1 && n <= 12) return WORD(VK_F1 + n - 1); }
        throw std::runtime_error("Unsupported physical key");
    }
    void input(HWND hwnd, JsonObject const& a) const {
        focus(hwnd); auto kind = str(a, L"kind"); std::vector<INPUT> events;
        if (kind == "key") {
            INPUT e{}; e.type = INPUT_KEYBOARD; e.ki.wVk = virtualKey(str(a, L"code"));
            if (e.ki.wVk == VK_LEFT || e.ki.wVk == VK_RIGHT || e.ki.wVk == VK_UP || e.ki.wVk == VK_DOWN ||
                e.ki.wVk == VK_HOME || e.ki.wVk == VK_END || e.ki.wVk == VK_PRIOR || e.ki.wVk == VK_NEXT ||
                e.ki.wVk == VK_DELETE || e.ki.wVk == VK_RCONTROL || e.ki.wVk == VK_RMENU) e.ki.dwFlags |= KEYEVENTF_EXTENDEDKEY;
            if (!a.GetNamedBoolean(L"down", false)) e.ki.dwFlags |= KEYEVENTF_KEYUP;
            events.push_back(e);
        } else if (kind == "text") {
            auto value = a.GetNamedString(L"text", L""); if (value.size() > 4096) throw std::runtime_error("Text too long");
            for (auto character: value) { INPUT e{}; e.type = INPUT_KEYBOARD; e.ki.wScan = character; e.ki.dwFlags = KEYEVENTF_UNICODE;
                events.push_back(e); e.ki.dwFlags |= KEYEVENTF_KEYUP; events.push_back(e); }
        } else if (kind == "pointer") {
            auto r = bounds(hwnd); int x = r.left + int(std::clamp(num(a, L"x"), 0., 1.) * (r.right-r.left-1));
            int y = r.top + int(std::clamp(num(a, L"y"), 0., 1.) * (r.bottom-r.top-1));
            POINT point{x, y};
            auto hit = WindowFromPoint(point);
            if (!hit || !owns(GetAncestor(hit, GA_ROOT)) || GetAncestor(hit, GA_ROOT) != GetAncestor(hwnd, GA_ROOT))
                throw std::runtime_error("The owned window is obscured; refusing to click another window");
            INPUT e{}; e.type = INPUT_MOUSE; e.mi.dwFlags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK;
            e.mi.dx = LONG((x-GetSystemMetrics(SM_XVIRTUALSCREEN))*65535.0/std::max(1,GetSystemMetrics(SM_CXVIRTUALSCREEN)-1));
            e.mi.dy = LONG((y-GetSystemMetrics(SM_YVIRTUALSCREEN))*65535.0/std::max(1,GetSystemMetrics(SM_CYVIRTUALSCREEN)-1));
            auto phase = str(a, L"phase"); bool right = num(a, L"button") == 2, middle = num(a, L"button") == 1;
            if (phase == "down") e.mi.dwFlags |= right ? MOUSEEVENTF_RIGHTDOWN : middle ? MOUSEEVENTF_MIDDLEDOWN : MOUSEEVENTF_LEFTDOWN;
            if (phase == "up") e.mi.dwFlags |= right ? MOUSEEVENTF_RIGHTUP : middle ? MOUSEEVENTF_MIDDLEUP : MOUSEEVENTF_LEFTUP;
            events.push_back(e);
        } else if (kind == "wheel") {
            for (auto axis: {L"dy", L"dx"}) { auto delta = std::clamp(int(num(a, axis)), -2000, 2000); if (!delta) continue;
                INPUT e{}; e.type = INPUT_MOUSE; e.mi.dwFlags = axis[1] == L'y' ? MOUSEEVENTF_WHEEL : MOUSEEVENTF_HWHEEL;
                e.mi.mouseData = DWORD(axis[1] == L'y' ? -delta : delta); events.push_back(e); }
        } else throw std::runtime_error("Unknown input event");
        if (!events.empty() && SendInput(static_cast<UINT>(events.size()), events.data(), sizeof(INPUT)) != events.size())
            throw std::runtime_error("Windows blocked input (app elevation or desktop mismatch)");
    }
};

int main(int argc, char** argv) {
    _setmode(_fileno(stdout), _O_BINARY); SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    try {
        init_apartment(apartment_type::multi_threaded);
        if (argc == 2 && (std::string(argv[1]) == "--probe" || std::string(argv[1]) == "--permissions")) {
            JsonObject result; number(result,L"protocol",1); text(result,L"backend",L"windows-graphics-capture");
            auto desktop = OpenInputDesktop(0,FALSE,DESKTOP_READOBJECTS);
            DWORD sessionId = 0;
            wchar_t inputName[256]{}, ownName[256]{};
            DWORD needed = 0;
            bool interactive = desktop && ProcessIdToSessionId(GetCurrentProcessId(), &sessionId) && sessionId != 0
                && GetUserObjectInformationW(desktop, UOI_NAME, inputName, sizeof(inputName), &needed)
                && GetUserObjectInformationW(GetThreadDesktop(GetCurrentThreadId()), UOI_NAME, ownName, sizeof(ownName), &needed)
                && _wcsicmp(inputName, ownName) == 0;
            if (desktop) CloseDesktop(desktop);
            result.SetNamedValue(L"available",JsonValue::CreateBooleanValue(GraphicsCaptureSession::IsSupported()));
            result.SetNamedValue(L"interactive",JsonValue::CreateBooleanValue(interactive));
            result.SetNamedValue(L"screen_recording",JsonValue::CreateBooleanValue(interactive));
            result.SetNamedValue(L"input",JsonValue::CreateBooleanValue(interactive));
            std::cout << to_string(result.Stringify()) << std::endl; return 0;
        }
        if (argc != 3 || std::string(argv[1]) != "--pid") return 2;
        Owner owner(static_cast<DWORD>(std::stoul(argv[2])));
        std::string line;
        while (std::getline(std::cin,line)) {
            if (line.size() > 65536) break;
            hstring id;
            try {
                auto command = JsonObject::Parse(to_hstring(line)); id = command.GetNamedString(L"id",L"");
                auto result = owner.command(command); result.SetNamedValue(L"ok",JsonValue::CreateBooleanValue(true));
                result.SetNamedValue(L"id",JsonValue::CreateStringValue(id)); emit(result);
            } catch (hresult_error const& e) { auto result=errorObject(to_string(e.message())); result.SetNamedValue(L"id",JsonValue::CreateStringValue(id)); emit(result); }
              catch (std::exception const& e) { auto result=errorObject(e.what()); result.SetNamedValue(L"id",JsonValue::CreateStringValue(id)); emit(result); }
        }
    } catch (hresult_error const& e) { auto result=errorObject(to_string(e.message())); text(result,L"event",L"fatal"); emit(result); return 1; }
      catch (std::exception const& e) { auto result=errorObject(e.what()); text(result,L"event",L"fatal"); emit(result); return 1; }
}
