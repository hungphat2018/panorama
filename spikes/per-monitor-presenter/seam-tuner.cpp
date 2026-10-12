// Seam tuner: make motion cross the bezel with no delay (ADR 0020, M1 phase 01 step 6b), v2.
//
// One window and swapchain per monitor (as in per-monitor-presenter.cpp). Both screens show one picture laid out
// in millimetres along the row of monitors, continuing behind the bezel. Every row is timed from the scan-line
// grid (scanline-clock.h): its light starts at the monitor's top-row time + row * line time + the monitor's
// panel lag, and in smooth motion each row is drawn for the middle of the time its light stays up.
//
// Modes (--mode):
//   free        the key-driven tuner: you adjust gap, height and lag by eye (seam-free-session.h)
//   x2          film steps (research X2): a white bar crosses the bezel in 24 fps steps; each trial is a forced choice
//               between today's aimed-row switch and the rolling blend (seam-x2-session.h); score the answer file
//               with seam-score-x2-trials.py
//   calibrate   forced-choice calibration of height, gap and the right screen's lag (seam-calibration-session.h)
// --self-check runs the numeric check of the blend weights on the real row clocks and exits (no windows).
//
// Usage: seam-tuner.exe [--mode free|x2|calibrate] [--refresh DISPLAYn=HZ] [--windowed] [--seconds N]
//                       [--out DIR] [--settings FILE] [--gap-mm 20] [--height-mm 0] [--lag-ms L1,L2]
//        x2:         [--conditions a:300,b:300,a:600,b:600] [--trials 10] [--seed N]
//        calibrate:  [--rounds 7]
//        smoke tests: [--quick] [--auto-answer right|wrong|random] [--sim-truth HEIGHT,GAP,LAG]
//        timing:      [--lead-ms 0.42]  how long before a blank starts a present must be in to make that refresh;
//                     raise it if the HUD (free mode) or the answer log (x2) counts presents that missed their refresh
// --refresh switches that display for the run with CDS_FULLSCREEN, so Windows puts the mode back when this process
// ends (ADR 0023); X2 wants DISPLAY2=96 for an even 24 fps pace. --windowed uses half-size windows that are not
// topmost and don't take focus (Windows composes them, so timing is not representative). Esc quits and saves.
#define _CRT_SECURE_NO_WARNINGS
#include "seam-app.h"
#include "seam-calibration-session.h"
#include "seam-free-session.h"
#include "seam-settings.h"
#include "seam-x2-session.h"

#include <timeapi.h>

#include <cstdio>
#include <cstdlib>
#include <cwchar>
#include <string>
#include <thread>

#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "winmm.lib")
#pragma comment(lib, "user32.lib")

namespace seam {
std::atomic<bool> g_stop{false};
std::atomic<bool> g_failed{false};
std::vector<Monitor*> g_monitors;
int g_master = 0;
bool g_windowed = false;
Settings g_set;
double g_lead_s = 0.00042;
}  // namespace seam

namespace {

using namespace seam;

Session* g_session = nullptr;
bool g_session_repeats = false;  // free mode wants key repeat (held arrows); the forced-choice sessions don't

struct Args {
    std::wstring mode = L"free";
    double seconds = 0;  // 0: run until Esc
    std::wstring out = L".";
    std::wstring settings;
    std::wstring refresh_device;
    int refresh_hz = 0;
    bool windowed = false, quick = false, self_check = false;
    bool has_gap = false, has_height = false, has_lag = false;
    double gap_mm = 20, height_mm = 0;
    std::vector<double> lag_ms;
    std::vector<std::pair<char, int>> conditions;
    int trials = 10, rounds = 7;
    uint32_t seed = 0;
    std::string auto_answer;
    double sim[3] = {-1.3, 23.7, 2.6};  // hidden truth for auto answers in calibration: height, gap, lag
};

std::vector<std::wstring> split(const std::wstring& s, wchar_t sep) {
    std::vector<std::wstring> out;
    size_t at = 0;
    for (;;) {
        size_t next = s.find(sep, at);
        out.push_back(s.substr(at, next == std::wstring::npos ? std::wstring::npos : next - at));
        if (next == std::wstring::npos) return out;
        at = next + 1;
    }
}

bool parse_args(int argc, wchar_t** argv, Args* a) {
    for (int i = 1; i < argc; ++i) {
        const std::wstring k = argv[i];
        auto value = [&](std::wstring* v) {
            if (i + 1 >= argc) {
                fwprintf(stderr, L"%s needs a value\n", k.c_str());
                return false;
            }
            *v = argv[++i];
            return true;
        };
        std::wstring v;
        if (k == L"--windowed") a->windowed = true;
        else if (k == L"--quick") a->quick = true;
        else if (k == L"--self-check") a->self_check = true;
        else if (k == L"--mode") { if (!value(&a->mode)) return false; }
        else if (k == L"--out") { if (!value(&a->out)) return false; }
        else if (k == L"--settings") { if (!value(&a->settings)) return false; }
        else if (k == L"--seconds") { if (!value(&v)) return false; a->seconds = _wtof(v.c_str()); }
        else if (k == L"--lead-ms") { if (!value(&v)) return false; g_lead_s = _wtof(v.c_str()) * 1e-3; }
        else if (k == L"--gap-mm") { if (!value(&v)) return false; a->gap_mm = _wtof(v.c_str()); a->has_gap = true; }
        else if (k == L"--height-mm") { if (!value(&v)) return false; a->height_mm = _wtof(v.c_str()); a->has_height = true; }
        else if (k == L"--lag-ms") {
            if (!value(&v)) return false;
            for (const auto& part : split(v, L',')) a->lag_ms.push_back(_wtof(part.c_str()));
            a->has_lag = true;
        } else if (k == L"--trials") { if (!value(&v)) return false; a->trials = _wtoi(v.c_str()); }
        else if (k == L"--rounds") { if (!value(&v)) return false; a->rounds = _wtoi(v.c_str()); }
        else if (k == L"--seed") { if (!value(&v)) return false; a->seed = uint32_t(_wtoi64(v.c_str())); }
        else if (k == L"--auto-answer") {
            if (!value(&v)) return false;
            a->auto_answer = utf8(v);
            if (a->auto_answer != "right" && a->auto_answer != "wrong" && a->auto_answer != "random") {
                fwprintf(stderr, L"--auto-answer takes right, wrong or random\n");
                return false;
            }
        } else if (k == L"--sim-truth") {
            if (!value(&v)) return false;
            auto parts = split(v, L',');
            if (parts.size() != 3) { fwprintf(stderr, L"--sim-truth takes HEIGHT,GAP,LAG\n"); return false; }
            for (int p = 0; p < 3; ++p) a->sim[p] = _wtof(parts[size_t(p)].c_str());
        } else if (k == L"--conditions") {
            if (!value(&v)) return false;
            for (const auto& part : split(v, L',')) {
                if (part.size() < 3 || (part[0] != L'a' && part[0] != L'b') || part[1] != L':' || _wtoi(part.c_str() + 2) <= 0) {
                    fwprintf(stderr, L"--conditions takes a:SPEED or b:SPEED, e.g. a:300,b:600\n");
                    return false;
                }
                a->conditions.push_back({char(part[0]), _wtoi(part.c_str() + 2)});
            }
        } else if (k == L"--refresh") {
            if (!value(&v)) return false;
            size_t eq = v.find(L'=');
            if (eq == std::wstring::npos || _wtoi(v.c_str() + eq + 1) <= 0) {
                fwprintf(stderr, L"--refresh takes DISPLAYn=HZ\n");
                return false;
            }
            a->refresh_device = v.substr(0, eq);
            if (a->refresh_device.rfind(L"\\\\.\\", 0) != 0) a->refresh_device = L"\\\\.\\" + a->refresh_device;
            a->refresh_hz = _wtoi(v.c_str() + eq + 1);
        } else {
            fwprintf(stderr, L"unknown option %s (see the comment at the top of seam-tuner.cpp)\n", k.c_str());
            return false;
        }
    }
    if (a->mode != L"free" && a->mode != L"x2" && a->mode != L"calibrate") {
        fwprintf(stderr, L"--mode takes free, x2 or calibrate\n");
        return false;
    }
    if (a->conditions.empty())
        a->conditions = {{'a', 300}, {'b', 300}, {'a', 600}, {'b', 600}};
    return true;
}

// Switches one display's refresh for the run, held by this process (CDS_FULLSCREEN): Windows puts the saved mode
// back when the process exits or dies (ADR 0023). CDS_TEST first, so a refused rate changes nothing.
class RefreshHold {
public:
    bool switch_to(const std::wstring& device, int hz) {
        DEVMODEW dm{};
        dm.dmSize = sizeof(dm);
        if (!EnumDisplaySettingsW(device.c_str(), ENUM_CURRENT_SETTINGS, &dm)) return false;
        dm.dmDisplayFrequency = DWORD(hz);
        dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY;
        if (ChangeDisplaySettingsExW(device.c_str(), &dm, nullptr, CDS_TEST, nullptr) != DISP_CHANGE_SUCCESSFUL) return false;
        if (ChangeDisplaySettingsExW(device.c_str(), &dm, nullptr, CDS_FULLSCREEN, nullptr) != DISP_CHANGE_SUCCESSFUL) return false;
        device_ = device;
        active_ = true;
        Sleep(3000);  // let the monitor sync to the new timing
        return true;
    }
    ~RefreshHold() {
        if (active_) ChangeDisplaySettingsExW(device_.c_str(), nullptr, nullptr, 0, nullptr);
    }

private:
    std::wstring device_;
    bool active_ = false;
};

// Setup A's panels (EDID / ruler); others fall back to 0.25 mm per pixel.
void physical_size(Monitor& m) {
    LONG w = m.rect.right - m.rect.left, h = m.rect.bottom - m.rect.top;
    m.width_mm = (w == 2560) ? 344.0 : (w == 1920) ? 476.0 : w * 0.25;
    m.pitch_mm = m.width_mm / double(w);
    m.height_mm = h * m.pitch_mm;
}

LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_SETCURSOR:
            if (!g_windowed) { SetCursor(nullptr); return TRUE; }
            break;
        case WM_ERASEBKGND: return 1;
        case WM_CLOSE: g_stop = true; return 0;
        case WM_KEYDOWN: {
            if (wp == VK_ESCAPE) { g_stop = true; return 0; }
            const bool repeat = (lp & (1 << 30)) != 0;
            if (g_session && (!repeat || g_session_repeats)) g_session->on_key(wp, (GetKeyState(VK_SHIFT) & 0x8000) != 0);
            return 0;
        }
        default: break;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}

void pump_messages() {
    MSG msg;
    while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
}

// Full screen: a topmost borderless window exactly covering the monitor. Windowed: a half-size window with a title
// bar, not topmost, not activated, placed on the half of the monitor nearest the seam so the layout stays right.
bool make_window(Monitor* m, HINSTANCE inst, const wchar_t* cls) {
    const int mw = int(m->rect.right - m->rect.left), mh = int(m->rect.bottom - m->rect.top);
    if (!g_windowed) {
        m->hwnd = CreateWindowExW(WS_EX_TOPMOST, cls, L"panorama seam tuner", WS_POPUP | WS_VISIBLE, m->rect.left, m->rect.top, mw, mh, nullptr, nullptr, inst, nullptr);
        m->view_x = m->view_y = 0;
        m->view_w = mw;
        m->view_h = mh;
        return m->hwnd != nullptr;
    }
    const int w = mw / 2, h = mh / 2;
    const int cx = m->rect.left + (m->index == 0 ? mw - w : 0), cy = m->rect.top + (mh - h) / 2;
    m->hwnd = CreateWindowExW(0, cls, L"panorama seam tuner (windowed)", WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX, cx, cy, w, h, nullptr, nullptr, inst, nullptr);
    if (!m->hwnd) return false;
    ShowWindow(m->hwnd, SW_SHOWNOACTIVATE);
    // Make the client area exactly w x h at (cx, cy), whatever the frame measures.
    RECT wr, cr;
    GetWindowRect(m->hwnd, &wr);
    GetClientRect(m->hwnd, &cr);
    POINT pt{0, 0};
    ClientToScreen(m->hwnd, &pt);
    SetWindowPos(m->hwnd, nullptr, cx - (pt.x - wr.left), cy - (pt.y - wr.top), w + (wr.right - wr.left) - cr.right, h + (wr.bottom - wr.top) - cr.bottom,
                 SWP_NOACTIVATE | SWP_NOZORDER);
    pt = {0, 0};
    ClientToScreen(m->hwnd, &pt);
    GetClientRect(m->hwnd, &cr);
    m->view_x = pt.x - int(m->rect.left);
    m->view_y = pt.y - int(m->rect.top);
    m->view_w = int(cr.right);
    m->view_h = int(cr.bottom);
    return true;
}

// Waits (pumping messages) until every row clock has a fit, then a second more for it to settle.
bool wait_for_clocks(double timeout_s) {
    const double end = now_s() + timeout_s;
    for (;;) {
        bool all = true;
        for (auto* m : g_monitors) all = all && m->clock.ready();
        if (all) break;
        if (now_s() > end) return false;
        pump_messages();
        Sleep(10);
    }
    const double settle = now_s() + 1.0;
    while (now_s() < settle) {
        pump_messages();
        Sleep(10);
    }
    return true;
}

int run(const Args& args) {
    RefreshHold refresh;
    if (args.refresh_hz > 0) {
        if (!refresh.switch_to(args.refresh_device, args.refresh_hz)) {
            fwprintf(stderr, L"%s: switching to %d Hz failed\n", args.refresh_device.c_str(), args.refresh_hz);
            return 1;
        }
        wprintf(L"%s switched to %d Hz for this run\n", args.refresh_device.c_str(), args.refresh_hz);
    }

    // The adapter that drives the monitors (on Setup A: the NVIDIA GPU drives both).
    IDXGIFactory6* factory = nullptr;
    if (FAILED(CreateDXGIFactory2(0, IID_PPV_ARGS(&factory)))) { fwprintf(stderr, L"CreateDXGIFactory2 failed\n"); return 1; }
    IDXGIAdapter1* adapter = nullptr;
    for (UINT i = 0; factory->EnumAdapters1(i, &adapter) != DXGI_ERROR_NOT_FOUND; ++i) {
        IDXGIOutput* o = nullptr;
        if (adapter->EnumOutputs(0, &o) != DXGI_ERROR_NOT_FOUND) { o->Release(); break; }
        adapter->Release();
        adapter = nullptr;
    }
    if (!adapter) { fwprintf(stderr, L"no adapter with outputs\n"); return 1; }
    IDXGIOutput* output = nullptr;
    for (UINT j = 0; adapter->EnumOutputs(j, &output) != DXGI_ERROR_NOT_FOUND; ++j) {
        DXGI_OUTPUT_DESC od;
        output->GetDesc(&od);
        auto* m = new Monitor;
        m->name = od.DeviceName;
        m->label = m->name.substr(m->name.find_last_of(L'\\') + 1);
        m->rect = od.DesktopCoordinates;
        m->output = output;
        physical_size(*m);
        g_monitors.push_back(m);
    }
    std::sort(g_monitors.begin(), g_monitors.end(), [](Monitor* a, Monitor* b) { return a->rect.left < b->rect.left; });
    for (size_t i = 0; i < g_monitors.size(); ++i) g_monitors[i]->index = int(i);
    for (auto* m : g_monitors) {
        std::string why;
        if (!m->clock.open(m->name.c_str(), &why)) {
            fwprintf(stderr, L"%s: no row timing: %hs\n", m->label.c_str(), why.c_str());
            return 1;
        }
    }

    g_set.lag_ms.assign(g_monitors.size(), 0.0);  // before anything reads it
    g_set.selected = int(g_monitors.size()) - 1;
    if (!args.settings.empty() && !load_settings(args.settings, &g_set)) return 1;
    if (args.has_gap) g_set.gap_mm = args.gap_mm;
    if (args.has_height) g_set.height_mm = args.height_mm;
    if (args.has_lag)
        for (size_t i = 0; i < args.lag_ms.size() && i < g_set.lag_ms.size(); ++i) g_set.lag_ms[i] = args.lag_ms[i];

    for (auto* m : g_monitors) m->clock.start();
    const wchar_t* cls = L"panorama-seam-tuner";
    HINSTANCE inst = GetModuleHandleW(nullptr);
    if (!args.self_check) {
        WNDCLASSW wc{};
        wc.lpfnWndProc = window_proc;
        wc.hInstance = inst;
        wc.lpszClassName = cls;
        RegisterClassW(&wc);
        for (auto* m : g_monitors)
            if (!make_window(m, inst, cls)) { fwprintf(stderr, L"%s: CreateWindowEx failed\n", m->label.c_str()); g_stop = true; }
        if (!g_windowed && !g_stop) SetForegroundWindow(g_monitors.front()->hwnd);
    }
    int rc = 0;
    if (g_stop || !wait_for_clocks(10.0)) {
        if (!g_stop) fwprintf(stderr, L"the scan-line clocks did not settle within 10 s\n");
        rc = 1;
    }
    if (rc == 0) {
        for (auto* m : g_monitors)  // the reference (film clock) is the slowest monitor
            if (m->clock.wave().period > g_monitors[size_t(g_master)]->clock.wave().period) g_master = m->index;
        for (auto* m : g_monitors) {
            double p50, p95;
            size_t used;
            m->clock.quality(&p50, &p95, &used);
            const scan_timing& t = m->clock.timing();
            wprintf(L"%s: %.0f x %.0f mm, %.3f Hz, line %.3f us, scan %.3f ms, blank %.3f ms; scan-line fit residual p50 %.1f us p95 %.1f us (%zu samples)%s\n",
                    m->label.c_str(), m->width_mm, m->height_mm, t.refresh_hz, t.line_s * 1e6, t.scan_s * 1e3, t.blank_s * 1e3, p50, p95, used,
                    m->index == g_master ? L"  [reference: slowest]" : L"");
        }
    }

    std::unique_ptr<Session> session;
    if (rc == 0 && args.self_check) {
        rc = run_self_check();
    } else if (rc == 0) {
        std::string why;
        if (args.mode == L"x2") {
            if (!x2_can_run(&why)) { fprintf(stderr, "x2: %s\n", why.c_str()); rc = 1; }
            else {
                X2Options o;
                o.conditions = args.conditions;
                o.trials_per_condition = args.trials;
                o.quick = args.quick;
                o.auto_answer = args.auto_answer;
                o.seed = args.seed;
                o.out_dir = args.out;
                session = make_x2_session(o);
            }
        } else if (args.mode == L"calibrate") {
            if (!calibration_can_run(&why)) { fprintf(stderr, "calibrate: %s\n", why.c_str()); rc = 1; }
            else {
                CalibrationOptions o;
                o.rounds = args.rounds;
                o.quick = args.quick;
                o.auto_answer = args.auto_answer;
                o.seed = args.seed;
                o.out_dir = args.out;
                o.sim_height_mm = args.sim[0];
                o.sim_gap_mm = args.sim[1];
                o.sim_lag_ms = args.sim[2];
                session = make_calibration_session(o);
            }
        } else {
            session = make_free_session(args.out);
            g_session_repeats = true;
        }
    }

    std::vector<std::thread> threads;
    if (session) {
        g_session = session.get();
        session->start();
        for (auto* m : g_monitors) threads.emplace_back(render_thread, m, adapter);
        const double end = args.seconds > 0 ? now_s() + args.seconds : 1e300;
        while (!g_stop && now_s() < end && !session->finished()) {
            pump_messages();
            session->tick(now_s());
            Sleep(5);
        }
    }
    g_stop = true;
    for (auto& t : threads) t.join();
    if (session) session->finish();
    g_session = nullptr;
    for (auto* m : g_monitors) {
        m->clock.stop();
        // The same counts the HUD shows, so a full-screen run can be judged from the console.
        if (!g_windowed)
            wprintf(L"%s: presents reaching the predicted refresh: %d late, %d early of %d\n", m->label.c_str(), m->late.load(),
                    m->early.load(), m->checked.load());
        if (m->clock.rejects()) wprintf(L"%s: %d row-clock refits refused (they disagreed with the running clock)\n", m->label.c_str(), m->clock.rejects());
        if (m->hwnd) DestroyWindow(m->hwnd);
    }
    if (g_failed) rc = 1;
    return rc;  // `refresh` goes out of scope here and puts the display mode back
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    Args args;
    if (!parse_args(argc, argv, &args)) return 2;
    g_windowed = args.windowed;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    timeBeginPeriod(1);
    int rc = run(args);
    timeEndPeriod(1);
    return rc;
}
