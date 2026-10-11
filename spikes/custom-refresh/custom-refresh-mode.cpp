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
//   list   NAME              custom modes the driver has saved for the display
//   plan   NAME HZ           print both recipes for HZ, change nothing
//   try    NAME HZ [--same-totals]
//                            apply HZ as an unsaved NVIDIA trial; a dialog on the main screen asks whether the
//                            picture is normal. Yes saves the mode (Windows then lists it like any other) and
//                            switches back to the display's saved Windows mode. No, or no answer in 15 s, reverts.
//   delete NAME HZ           remove a saved custom mode
// NAME is the Windows display name, e.g. DISPLAY2. Uses the public NVAPI (headers in nvapi/, MIT licence),
// loaded at run time from the driver's nvapi64.dll; nothing else is changed.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

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
    double scan_ms = t.pclk ? 1000.0 * double(t.VVisible) * double(t.HTotal) / (t.pclk * 1e4) : 0;
    printf("%s: %ux%u visible, %ux%u total, pixel clock %.2f MHz, %.3f Hz (rr %u), scan %.2f ms\n", label, t.HVisible,
           t.VVisible, t.HTotal, t.VTotal, t.pclk / 100.0, t.etc.rrx1k / 1000.0, t.etc.rr, scan_ms);
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

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) { fprintf(stderr, "usage: custom-refresh-mode list|try|delete DISPLAYn [HZ]\n"); return 2; }
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
    if (action == "plan") {  // print both recipes, change nothing
        print_timing("fast scan", make_timing(timing, hz, false));
        print_timing("same totals", make_timing(timing, hz, true));
        return 0;
    }
    bool same_totals = argc > 4 && std::string(argv[4]) == "--same-totals";
    NV_TIMING custom = make_timing(timing, hz, same_totals);
    print_timing(same_totals ? "custom (same totals)" : "custom (fast scan)", custom);
    if (custom.etc.rrx1k != NvU32(hz) * 1000) printf("note: not exact, the 10 kHz clock step gives %.4f Hz\n", custom.etc.rrx1k / 1000.0);

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

    // Ask on the main screen; no answer in 15 s counts as "no".
    std::atomic<int> answer{0};
    std::thread timeout([&] {
        for (int i = 0; i < 150 && answer == 0; ++i) Sleep(100);
        if (answer == 0) {
            answer = IDNO;
            HWND box = FindWindowW(nullptr, L"panorama: custom refresh test");
            if (box) PostMessageW(box, WM_CLOSE, 0, 0);
        }
    });
    std::wstring text = L"The right screen now runs at " + std::to_wstring(hz) +
                        L" Hz as a test.\n\nDoes it show a normal, steady picture (no black screen, no 'out of range')?\n\n"
                        L"Yes: keep the mode for the next tests.\nNo, or no answer in 15 seconds: put it back.";
    int choice = MessageBoxW(nullptr, text.c_str(), L"panorama: custom refresh test", MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND);
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
