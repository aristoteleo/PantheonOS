#include <windows.h>
LRESULT CALLBACK procedure(HWND hwnd, UINT message, WPARAM w, LPARAM l) {
    if (message == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(hwnd, message, w, l);
}
int main() {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    WNDCLASSW cls{}; cls.lpfnWndProc = procedure; cls.hInstance = GetModuleHandleW(nullptr);
    cls.lpszClassName = L"FleetCaptureFixture";
    auto brush = CreateSolidBrush(RGB(51,178,77)); cls.hbrBackground = brush;
    RegisterClassW(&cls);
    auto window = CreateWindowW(cls.lpszClassName, L"Fleet capture fixture", WS_OVERLAPPEDWINDOW,
        100,100,480,320,nullptr,nullptr,cls.hInstance,nullptr);
    ShowWindow(window,SW_SHOW); UpdateWindow(window);
    MSG message{}; while (GetMessageW(&message,nullptr,0,0) > 0) { TranslateMessage(&message); DispatchMessageW(&message); }
    DeleteObject(brush);
}
