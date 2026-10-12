// Custom refresh modes on NVIDIA GPUs (M1 phase 01 spike C): try a refresh rate the monitor doesn't list,
// keep it only if the viewer sees a normal picture, and remove it again later.
//
// A 24 fps film has an even pace only on a refresh rate that is a whole multiple of 24 (48, 72, 96 Hz...).
// Setup A's external screen lists 50/59/60/75/100 Hz. This tool builds the new mode from the monitor's own
// current timing. By default it keeps the pixel clock (as close as the 10 kHz step allows, never above) and
// lengthens the vertical blank instead, so the screen still draws its picture top to bottom as fast as at its
// native rate: a slower scan would widen the seam delay down the screen (ADR 0020). 96 Hz on the external:
// 2000 x 1160 total at 222.72 MHz, scan 9.70 ms. Some monitors refuse a long vertical blank; then
// --same-totals keeps every line and lowers the clock instead (2000 x 1115 at 214.08 MHz, scan 10.09 ms).
//
// A third recipe sets the totals and the pixel clock exactly, for a mode whose scan speed matches another
// screen's (zero-delay research, X3): the laptop at 120 Hz, 2720 x 1725 total at 563.04 MHz, draws its 215 mm in
// 7.73 ms, at the external's speed. Sync widths and back porches are kept; the front porches take the difference.
//
//   list   NAME              custom modes the driver has saved for the display
//   plan   NAME HZ [--totals HxV --clock MHZ]
//                            print the recipes for HZ, change nothing
//   try    NAME HZ [--same-totals | --totals HxV --clock MHZ] [--ask-on NAME2]
//                            apply HZ as an unsaved NVIDIA trial; a dialog asks whether the picture is normal, on
//                            the main screen or, with --ask-on, on NAME2 (use it when the screen under test is the
//                            main one: it may go dark). Yes saves the mode (Windows then lists it like any other)
//                            and switches back to the display's saved Windows mode. No, or no answer in 15 s,
//                            reverts.
//   delete NAME HZ           remove a saved custom mode
// NAME is the Windows display name, e.g. DISPLAY2. Uses the public NVAPI (headers in nvapi/, MIT licence),
// loaded at run time from the driver's nvapi64.dll; nothing else is changed.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>
#include <vector>

#include "nvapi/nvapi.h"

#pragma comment(lib, "user32.lib")

namespace {

using QueryInterface = void*(__cdecl*)(unsigned int);
QueryInterface g_qi = nullptr;

template <typename F>
F api(unsigned int id) {
    void* p = g_qi ? g_qi(id) : nullptr;
    if (!p) { fprintf(stderr, "NVAPI function 0x%08x not available\n", id); exit(3); }
    return reinterpret_cast<F>(p);
}

// Function ids from nvapi/nvapi_interface.h.
const unsigned kInitialize = 0x0150e828, kGetErrorMessage = 0x6c2d048c, kGetDisplayIdByDisplayName = 0xae457190,
               kGetTiming = 0x175167e9, kEnumCustomDisplay = 0xa2072d59, kTryCustomDisplay = 0x1f7db630,
               kDeleteCustomDisplay = 0x552e5b9b, kSaveCustomDisplay = 0x49882876, kRevertCustomDisplayTrial = 0xcbbd40f0;

std::string message(NvAPI_Status st) {
    NvAPI_ShortString text = {};
    api<NvAPI_Status(__cdecl*)(NvAPI_Status, NvAPI_ShortString)>(kGetErrorMessage)(st, text);
    return text;
}

void check(NvAPI_Status st, const char* what) {
    if (st != NVAPI_OK) { fprintf(stderr, "%s failed: %d %s\n", what, int(st), message(st).c_str()); exit(4); }
}

// Exact current refresh of a Windows display, from the display path's signal timing (CCD).
double current_hz(const std::wstring& gdi_name) {
    UINT32 np = 0, nm = 0;
    GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, &np, &nm);
    std::vector<DISPLAYCONFIG_PATH_INFO> paths(np);
    std::vector<DISPLAYCONFIG_MODE_INFO> modes(nm);
    if (QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, &np, paths.data(), &nm, modes.data(), nullptr) != ERROR_SUCCESS) return 0;
    for (UINT32 i = 0; i < np; ++i) {
        DISPLAYCONFIG_SOURCE_DEVICE_NAME src{};
        src.header = {DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME, sizeof(src), paths[i].sourceInfo.adapterId, paths[i].sourceInfo.id};
        if (DisplayConfigGetDeviceInfo(&src.header) != ERROR_SUCCESS || gdi_name != src.viewGdiDeviceName) continue;
        UINT32 mi = paths[i].targetInfo.modeInfoIdx;
        if (mi >= nm || modes[mi].infoType != DISPLAYCONFIG_MODE_INFO_TYPE_TARGET) continue;
        const auto& sig = modes[mi].targetMode.targetVideoSignalInfo;
        double total = double(sig.totalSize.cx) * double(sig.totalSize.cy);
        return total > 0 ? double(sig.pixelRate) / total : 0;
    }
    return 0;
}

bool windows_lists(const std::wstring& gdi_name, int hz) {
    DEVMODEW cur{};
    cur.dmSize = sizeof(cur);
    EnumDisplaySettingsW(gdi_name.c_str(), ENUM_CURRENT_SETTINGS, &cur);
    for (DWORD i = 0;; ++i) {
        DEVMODEW dm{};
        dm.dmSize = sizeof(dm);
        if (!EnumDisplaySettingsExW(gdi_name.c_str(), i, &dm, EDS_RAWMODE)) return false;
        if (dm.dmPelsWidth == cur.dmPelsWidth && dm.dmPelsHeight == cur.dmPelsHeight && int(dm.dmDisplayFrequency) == hz) return true;
    }
}

void print_timing(const char* label, const NV_TIMING& t) {
    double line_s = t.pclk ? double(t.HTotal) / (t.pclk * 1e4) : 0;
    int v_back = int(t.VTotal) - int(t.VVisible) - 2 * int(t.VBorder) - int(t.VFrontPorch) - int(t.VSyncWidth);
    printf("%s: %ux%u visible, %ux%u total, pixel clock %.2f MHz, %.3f Hz (rr %u), line rate %.2f kHz, "
           "scan %.3f ms, blank %.3f ms (vertical front porch %u, sync %u, back porch %d lines)\n",
           label, t.HVisible, t.VVisible, t.HTotal, t.VTotal, t.pclk / 100.0, t.etc.rrx1k / 1000.0, t.etc.rr,
           line_s > 0 ? 1e-3 / line_s : 0.0, 1e3 * double(t.VVisible) * line_s,
           1e3 * double(t.VTotal - t.VVisible) * line_s, t.VFrontPorch, t.VSyncWidth, v_back);
}

// Timing for `hz` from the current one. Fast scan (default): keep the pixel clock and add lines to the vertical
// front porch; the largest vertical total whose clock is a whole 10 kHz step and not above the current clock.
// Same totals: keep every line, change only the clock.
NV_TIMING make_timing(const NV_TIMING& cur, int hz, bool same_totals) {
    NV_TIMING t = cur;
    double clock_hz = cur.pclk * 1e4;
    if (!same_totals) {
        for (NvU32 v = NvU32(std::floor(clock_hz / (double(cur.HTotal) * hz))); v >= cur.VTotal; --v) {
            double p = double(cur.HTotal) * v * hz;
            if (std::fmod(p, 1e4) == 0) {
                t.VFrontPorch = NvU16(cur.VFrontPorch + (v - cur.VTotal));
                t.VTotal = NvU16(v);
                break;
            }
        }
    }
    double total = double(t.HTotal) * double(t.VTotal);
    t.pclk = NvU32(std::llround(total * hz / 1e4));
    t.etc.rr = NvU16(hz);
    t.etc.rrx1k = NvU32(std::llround(t.pclk * 1e4 * 1000.0 / total));
    return t;
}

// Exact recipe: the given totals and pixel clock. Sync widths and back porches stay; the front porches take the
// difference (a back porch shrinks only if a front porch would drop below one line). Exits if the totals can't
// hold the visible area.
NV_TIMING exact_timing(const NV_TIMING& cur, int hz, unsigned htotal, unsigned vtotal, double clock_mhz) {
    auto porch = [](int visible, int border, int front, int sync, int total, int new_total, const char* axis) {
        int f = front + (new_total - total);
        int back = new_total - visible - 2 * border - std::max(f, 1) - sync;
        if (back < 1) { fprintf(stderr, "%s total %d is too small for %d visible + sync %d\n", axis, new_total, visible, sync); exit(2); }
        return std::max(f, 1);
    };
    NV_TIMING t = cur;
    t.HFrontPorch = NvU16(porch(cur.HVisible, cur.HBorder, cur.HFrontPorch, cur.HSyncWidth, cur.HTotal, int(htotal), "horizontal"));
    t.VFrontPorch = NvU16(porch(cur.VVisible, cur.VBorder, cur.VFrontPorch, cur.VSyncWidth, cur.VTotal, int(vtotal), "vertical"));
    t.HTotal = NvU16(htotal);
    t.VTotal = NvU16(vtotal);
    t.pclk = NvU32(std::llround(clock_mhz * 100.0));  // 10 kHz units
    t.etc.rr = NvU16(hz);
    t.etc.rrx1k = NvU32(std::llround(t.pclk * 1e4 * 1000.0 / (double(htotal) * double(vtotal))));
    return t;
}

// Desktop rectangle of a Windows display (physical pixels: the process is per-monitor DPI aware).
bool monitor_rect(const std::wstring& gdi_name, RECT* out) {
    struct Find { std::wstring name; RECT rect; bool found; } f{gdi_name, {}, false};
    EnumDisplayMonitors(nullptr, nullptr, [](HMONITOR m, HDC, LPRECT, LPARAM p) -> BOOL {
        auto* f = reinterpret_cast<Find*>(p);
        MONITORINFOEXW mi{};
        mi.cbSize = sizeof(mi);
        if (GetMonitorInfoW(m, &mi) && f->name == mi.szDevice) { f->rect = mi.rcMonitor; f->found = true; return FALSE; }
        return TRUE;
    }, reinterpret_cast<LPARAM>(&f));
    if (f.found) *out = f.rect;
    return f.found;
}

// "left", "right" or "middle": where a display sits among the others, for the question's wording.
const wchar_t* screen_side(const std::wstring& gdi_name) {
    RECT me{};
    if (!monitor_rect(gdi_name, &me)) return L"tested";
    struct Count { LONG center; int left, right; } c{(me.left + me.right) / 2, 0, 0};
    EnumDisplayMonitors(nullptr, nullptr, [](HMONITOR, HDC, LPRECT r, LPARAM p) -> BOOL {
        auto* c = reinterpret_cast<Count*>(p);
        LONG x = (r->left + r->right) / 2;
        if (x < c->center) c->left++;
        if (x > c->center) c->right++;
        return TRUE;
    }, reinterpret_cast<LPARAM>(&c));
    return c.left == 0 && c.right == 0 ? L"tested" : c.left == 0 ? L"left" : c.right == 0 ? L"right" : L"middle";
}

// Puts the question box in the middle of a chosen display when it opens.
RECT g_ask_rect{};
HHOOK g_cbt = nullptr;
LRESULT CALLBACK center_question(int code, WPARAM wp, LPARAM lp) {
    if (code == HCBT_ACTIVATE) {
        HWND box = reinterpret_cast<HWND>(wp);
        RECT r{};
        GetWindowRect(box, &r);
        int w = r.right - r.left, h = r.bottom - r.top;
        SetWindowPos(box, HWND_TOPMOST, (g_ask_rect.left + g_ask_rect.right - w) / 2,
                     (g_ask_rect.top + g_ask_rect.bottom - h) / 2, 0, 0, SWP_NOSIZE);
        UnhookWindowsHookEx(g_cbt);
        g_cbt = nullptr;
    }
    return CallNextHookEx(nullptr, code, wp, lp);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: custom-refresh-mode list|plan|try|delete DISPLAYn [HZ] "
                        "[--same-totals | --totals HxV --clock MHZ] [--ask-on DISPLAYm]\n");
        return 2;
    }
    std::string action = argv[1], name = std::string("\\\\.\\") + argv[2];
    std::wstring wname(name.begin(), name.end());
    int hz = argc > 3 ? atoi(argv[3]) : 0;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

    HMODULE dll = LoadLibraryW(L"nvapi64.dll");
    if (!dll) { fprintf(stderr, "nvapi64.dll not found: not an NVIDIA driver\n"); return 3; }
    g_qi = reinterpret_cast<QueryInterface>(GetProcAddress(dll, "nvapi_QueryInterface"));
    check(api<NvAPI_Status(__cdecl*)()>(kInitialize)(), "NvAPI_Initialize");
    NvU32 display = 0;
    check(api<NvAPI_Status(__cdecl*)(const char*, NvU32*)>(kGetDisplayIdByDisplayName)(name.c_str(), &display),
          "NvAPI_DISP_GetDisplayIdByDisplayName");
    auto enum_custom = api<NvAPI_Status(__cdecl*)(NvU32, NvU32, NV_CUSTOM_DISPLAY*)>(kEnumCustomDisplay);

    if (action == "list") {
        for (NvU32 i = 0;; ++i) {
            NV_CUSTOM_DISPLAY cd{};
            cd.version = NV_CUSTOM_DISPLAY_VER;
            if (enum_custom(display, i, &cd) != NVAPI_OK) break;
            printf("custom %u: %ux%u, ", i, cd.width, cd.height);
            print_timing("timing", cd.timing);
        }
        return 0;
    }
    if (hz < 24 || hz > 500) { fprintf(stderr, "give a refresh rate in Hz\n"); return 2; }

    if (action == "delete") {
        for (NvU32 i = 0;; ++i) {
            NV_CUSTOM_DISPLAY cd{};
            cd.version = NV_CUSTOM_DISPLAY_VER;
            if (enum_custom(display, i, &cd) != NVAPI_OK) break;
            if (std::lround(cd.timing.etc.rrx1k / 1000.0) != hz) continue;
            check(api<NvAPI_Status(__cdecl*)(NvU32*, NvU32, NV_CUSTOM_DISPLAY*)>(kDeleteCustomDisplay)(&display, 1, &cd),
                  "NvAPI_DISP_DeleteCustomDisplay");
            printf("deleted the %d Hz custom mode on %s; Windows lists it: %s\n", hz, argv[2], windows_lists(wname, hz) ? "yes" : "no");
            return 0;
        }
        printf("no %d Hz custom mode on %s\n", hz, argv[2]);
        return 0;
    }
    if (action != "try" && action != "plan") { fprintf(stderr, "unknown action\n"); return 2; }

    // Start from the display's current timing.
    NV_TIMING_INPUT in{};
    in.version = NV_TIMING_INPUT_VER;
    in.type = NV_TIMING_OVERRIDE_CURRENT;
    NV_TIMING timing{};
    check(api<NvAPI_Status(__cdecl*)(NvU32, NV_TIMING_INPUT*, NV_TIMING*)>(kGetTiming)(display, &in, &timing), "NvAPI_DISP_GetTiming");
    print_timing("current", timing);

    bool same_totals = false;
    unsigned htotal = 0, vtotal = 0;
    double clock_mhz = 0;
    std::wstring ask_on;
    for (int i = 4; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--same-totals") same_totals = true;
        else if (a == "--totals" && i + 1 < argc && sscanf_s(argv[i + 1], "%ux%u", &htotal, &vtotal) == 2) ++i;
        else if (a == "--clock" && i + 1 < argc) clock_mhz = atof(argv[++i]);
        else if (a == "--ask-on" && i + 1 < argc) { std::string n = std::string("\\\\.\\") + argv[++i]; ask_on.assign(n.begin(), n.end()); }
        else { fprintf(stderr, "unknown or incomplete option %s\n", argv[i]); return 2; }
    }
    bool exact = htotal && vtotal && clock_mhz > 0;
    if ((htotal || clock_mhz > 0) && !exact) { fprintf(stderr, "--totals HxV and --clock MHZ go together\n"); return 2; }

    if (action == "plan") {  // print the recipes, change nothing
        print_timing("fast scan", make_timing(timing, hz, false));
        print_timing("same totals", make_timing(timing, hz, true));
        if (exact) print_timing("exact", exact_timing(timing, hz, htotal, vtotal, clock_mhz));
        return 0;
    }
    NV_TIMING custom = exact ? exact_timing(timing, hz, htotal, vtotal, clock_mhz) : make_timing(timing, hz, same_totals);
    print_timing(exact ? "custom (exact)" : same_totals ? "custom (same totals)" : "custom (fast scan)", custom);
    if (custom.etc.rrx1k != NvU32(hz) * 1000) printf("note: not exact, the timing gives %.4f Hz\n", custom.etc.rrx1k / 1000.0);
    if (!ask_on.empty() && !monitor_rect(ask_on, &g_ask_rect)) { fprintf(stderr, "--ask-on: no such display\n"); return 2; }

    NV_CUSTOM_DISPLAY cd{};
    cd.version = NV_CUSTOM_DISPLAY_VER;
    cd.width = timing.HVisible;
    cd.height = timing.VVisible;
    cd.depth = 32;
    cd.colorFormat = NV_FORMAT_UNKNOWN;
    cd.srcPartition = {0.0f, 0.0f, 1.0f, 1.0f};
    cd.xRatio = cd.yRatio = 1.0f;
    cd.timing = custom;
    printf("trying %d Hz on %s ...\n", hz, argv[2]);
    fflush(stdout);
    check(api<NvAPI_Status(__cdecl*)(NvU32*, NvU32, NV_CUSTOM_DISPLAY*)>(kTryCustomDisplay)(&display, 1, &cd), "NvAPI_DISP_TryCustomDisplay");
    Sleep(2500);  // the monitor needs a moment to sync to the new timing
    printf("Windows now reports %.4f Hz on %s\n", current_hz(wname), argv[2]);
    fflush(stdout);

    // Ask on the main screen, or on --ask-on; no answer in 15 s counts as "no". A Yes/No box ignores WM_CLOSE, so
    // the timeout presses its No button.
    std::atomic<int> answer{0};
    std::thread timeout([&] {
        for (int i = 0; i < 150 && answer == 0; ++i) Sleep(100);
        if (answer == 0) {
            answer = IDNO;
            HWND box = FindWindowW(nullptr, L"panorama: custom refresh test");
            if (box) PostMessageW(box, WM_COMMAND, MAKEWPARAM(IDNO, BN_CLICKED), reinterpret_cast<LPARAM>(GetDlgItem(box, IDNO)));
        }
    });
    std::wstring text = std::wstring(L"The ") + screen_side(wname) + L" screen now runs at " + std::to_wstring(hz) +
                        L" Hz as a test.\n\nDoes it show a normal, steady picture (no black screen, no 'out of range')?\n\n"
                        L"Yes: keep the mode for the next tests.\nNo, or no answer in 15 seconds: put it back.";
    if (!ask_on.empty()) g_cbt = SetWindowsHookExW(WH_CBT, center_question, nullptr, GetCurrentThreadId());
    int choice = MessageBoxW(nullptr, text.c_str(), L"panorama: custom refresh test", MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND);
    if (g_cbt) UnhookWindowsHookEx(g_cbt);
    if (answer == 0) answer = choice;
    timeout.join();

    if (answer == IDYES) {
        check(api<NvAPI_Status(__cdecl*)(NvU32*, NvU32, NvU32, NvU32)>(kSaveCustomDisplay)(&display, 1, 1, 1), "NvAPI_DISP_SaveCustomDisplay");
        printf("saved: the %d Hz mode is kept for %s\n", hz, argv[2]);
    } else {
        check(api<NvAPI_Status(__cdecl*)(NvU32*, NvU32)>(kRevertCustomDisplayTrial)(&display, 1), "NvAPI_DISP_RevertCustomDisplayTrial");
        printf("reverted: the %d Hz trial is undone on %s\n", hz, argv[2]);
    }
    Sleep(1500);
    ChangeDisplaySettingsExW(wname.c_str(), nullptr, nullptr, 0, nullptr);  // back to the display's saved Windows mode
    Sleep(2500);
    printf("now %.4f Hz on %s; Windows lists %d Hz: %s\n", current_hz(wname), argv[2], hz, windows_lists(wname, hz) ? "yes" : "no");
    return answer == IDYES ? 0 : 1;
}
