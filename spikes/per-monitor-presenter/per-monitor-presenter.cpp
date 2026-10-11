// Spike for M1 phase 01 (pace): one process, one borderless window and flip-model swapchain per monitor.
//
// Question: if every monitor presents on its own, can both sides of a bezel switch film frames together,
// with an even pace on each? A window spanning both screens can't: Windows composes it once per refresh of
// the main display (spike 261011-1034). Here each monitor has its own swapchain and render thread, and a
// scheduling mode decides on which refresh each monitor shows each film frame:
//   own      each monitor switches on its first refresh at or after the film frame's start time
//   nearest  the master monitor's refresh carries each frame change; every other monitor switches on its
//            own refresh nearest to that moment (offset bounded by half the other monitor's period)
//   follow   the follower presents at the master's refresh times with tearing allowed, so a VRR panel
//            refreshes in step with the master (falls back to tearing if VRR is not active)
// The film clock runs at --fps (default 20: 240 Hz = 12 refreshes per frame and 100 Hz = 5, even on both
// native rates of Setup A). Each monitor draws white bars moving along one physical path across the gap,
// and a barcode of the film frame number along its top edge.
//
// Logs per monitor (JSON): refresh times (IDXGIOutput::WaitForVBlank), every present (film frame, present
// id, call time), DXGI frame statistics (when each present reached the screen) and the composition mode
// reported by IDXGISwapChainMedia (composed vs overlay / independent flip). analyze-per-monitor-pacing.py
// turns them into refreshes per frame and the frame-change offset between monitors.
//
// Usage: per-monitor-presenter.exe [--mode own|nearest|follow] [--fps 20] [--seconds 20]
//                                  [--master N] [--out DIR] [--gap-mm 20] [--refresh DISPLAYn=HZ]
// --refresh switches that display for the run with CDS_FULLSCREEN (Windows restores it when the process ends).
// Esc stops early. Takes over every monitor of the adapter that drives them for the run.
#define _CRT_SECURE_NO_WARNINGS
#include "per-monitor-common.h"
#include <avrt.h>
#include <timeapi.h>
#include <d3d11_1.h>
#include <dxgi1_6.h>

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cwchar>
#include <map>
#include <string>
#include <thread>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "avrt.lib")
#pragma comment(lib, "winmm.lib")
#pragma comment(lib, "user32.lib")

namespace {

std::atomic<bool> g_stop{false};

struct PresentRecord { uint32_t id; double call, ret, target; int64_t frame; };
struct StatRecord { uint32_t present_count, present_refresh, sync_refresh; double sync_qpc; double polled; };

struct Monitor {
    int index = 0;
    std::wstring name;
    RECT rect{};
    double width_mm = 0, left_mm = 0;   // physical span along the shared path
    IDXGIOutput* output = nullptr;
    HWND hwnd = nullptr;
    VblankClock clock;
    std::vector<PresentRecord> presents;
    std::vector<StatRecord> stats;
    std::map<int, int> composition_modes;  // DXGI_FRAME_PRESENTATION_MODE -> count of polls
    std::string error;
};

struct Settings {
    std::string mode = "own";
    double fps = 20, seconds = 20, gap_mm = 20;
    int master = -1;  // default: the monitor with the longest refresh period
    std::wstring out = L".";
    std::wstring refresh_device;  // --refresh DISPLAYn=HZ: switch that display for the run
    int refresh_hz = 0;
};

Settings g_cfg;
std::vector<Monitor*> g_monitors;
// The film clock is locked to the master's refresh grid (as a player locks playback speed to one display):
// film frame n starts on master refresh index g_first + g_hold * n, i.e. at g_t0 + n * g_frame_s.
double g_t0 = 0, g_frame_s = 0.05;
int64_t g_first = 0, g_hold = 5;

// Physical widths of Setup A's panels (EDID / ruler); others fall back to 0.25 mm per pixel.
double panel_width_mm(int width_px) {
    if (width_px == 2560) return 344.0;
    if (width_px == 1920) return 476.0;
    return width_px * 0.25;
}

// Film frame due at time t (own mode: a monitor switches on its first refresh at or after the frame start).
// Half a millisecond of slack keeps a refresh that lands exactly on a boundary from flip-flopping.
int64_t frame_at(double t) { return int64_t(std::floor((t - g_t0) / g_frame_s + 0.5e-3 / g_frame_s)); }

// Moment the master's screen switches to frame n: master refresh index g_first + g_hold * n.
double master_switch(int64_t n) { return g_monitors[g_cfg.master]->clock.time_of(g_first + g_hold * n); }

// Film frame a monitor shows on its refresh at time v, for the "nearest" mode: the master counts its
// own refreshes; every other monitor switches on its refresh nearest to the master's switch.
int64_t frame_nearest(Monitor& m, double v) {
    if (m.index == g_cfg.master) {
        int64_t k = m.clock.index_near(v) - g_first;
        return k >= 0 ? k / g_hold : -1;
    }
    int64_t guess = frame_at(v);
    int64_t best = guess - 2;
    for (int64_t n = guess - 2; n <= guess + 2; ++n)
        if (m.clock.nearest(master_switch(n)) <= v + 1e-4) best = n;
    return best;
}

void vblank_thread(Monitor* m) {
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
    while (!g_stop) {
        if (FAILED(m->output->WaitForVBlank())) { Sleep(1); continue; }
        m->clock.add(now_s());
    }
}

void fill(ID3D11DeviceContext1* ctx, ID3D11RenderTargetView* rtv, const std::vector<D3D11_RECT>& rects, float v) {
    if (rects.empty()) return;
    const float color[4] = {v, v, v, 1.0f};
    ctx->ClearView(rtv, color, rects.data(), UINT(rects.size()));
}

void draw(ID3D11DeviceContext1* ctx, ID3D11RenderTargetView* rtv, const Monitor& m, int64_t frame) {
    const LONG w = m.rect.right - m.rect.left, h = m.rect.bottom - m.rect.top;
    const float bg[4] = {0.12f, 0.12f, 0.12f, 1.0f};
    ctx->ClearView(rtv, bg, nullptr, 0);
    // Bars every 150 mm along one physical path through both screens, moving 150 mm/s in film-frame steps.
    const double mm_per_px = m.width_mm / double(w), spacing = 150.0, bar_mm = 12.0;
    double shift = std::fmod(150.0 * double(frame) / g_cfg.fps, spacing);
    std::vector<D3D11_RECT> bars;
    for (double x = std::floor(m.left_mm / spacing) * spacing - spacing + shift; x < m.left_mm + m.width_mm; x += spacing) {
        LONG a = LONG(std::lround((x - m.left_mm) / mm_per_px)), b = LONG(std::lround((x + bar_mm - m.left_mm) / mm_per_px));
        a = std::max<LONG>(a, 0); b = std::min<LONG>(b, w);
        if (b > a) bars.push_back({a, 48, b, h});
    }
    fill(ctx, rtv, bars, 0.95f);
    // Barcode: 16 bits of the frame number, most significant first, 40 px cells along the top edge.
    std::vector<D3D11_RECT> ones, zeros;
    for (int bit = 0; bit < 16; ++bit) {
        D3D11_RECT r{8 + bit * 40, 4, 8 + bit * 40 + 32, 40};
        (((frame >> (15 - bit)) & 1) ? ones : zeros).push_back(r);
    }
    fill(ctx, rtv, ones, 1.0f);
    fill(ctx, rtv, zeros, 0.0f);
}

void render_thread(Monitor* m, IDXGIAdapter1* adapter) {
    DWORD task = 0;
    HANDLE mmcss = AvSetMmThreadCharacteristicsW(L"Games", &task);
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    D3D_FEATURE_LEVEL want = D3D_FEATURE_LEVEL_11_1, got;
    if (FAILED(D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT, &want, 1,
                                 D3D11_SDK_VERSION, &device, &got, &context))) {
        m->error = "D3D11CreateDevice failed";
        return;
    }
    ID3D11DeviceContext1* ctx = nullptr;
    context->QueryInterface(IID_PPV_ARGS(&ctx));
    IDXGIFactory5* factory = nullptr;
    adapter->GetParent(IID_PPV_ARGS(&factory));
    BOOL tearing = FALSE;
    factory->CheckFeatureSupport(DXGI_FEATURE_PRESENT_ALLOW_TEARING, &tearing, sizeof(tearing));
    DXGI_SWAP_CHAIN_DESC1 desc{};
    desc.Width = UINT(m->rect.right - m->rect.left);
    desc.Height = UINT(m->rect.bottom - m->rect.top);
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.BufferCount = 2;
    desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    desc.Flags = DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT | (tearing ? DXGI_SWAP_CHAIN_FLAG_ALLOW_TEARING : 0);
    IDXGISwapChain1* sc1 = nullptr;
    if (FAILED(factory->CreateSwapChainForHwnd(device, m->hwnd, &desc, nullptr, nullptr, &sc1))) {
        m->error = "CreateSwapChainForHwnd failed";
        return;
    }
    factory->MakeWindowAssociation(m->hwnd, DXGI_MWA_NO_ALT_ENTER);
    IDXGISwapChain2* sc = nullptr;
    sc1->QueryInterface(IID_PPV_ARGS(&sc));
    IDXGISwapChainMedia* media = nullptr;
    sc1->QueryInterface(IID_PPV_ARGS(&media));
    sc->SetMaximumFrameLatency(1);
    HANDLE waitable = sc->GetFrameLatencyWaitableObject();
    ID3D11Texture2D* back = nullptr;
    sc->GetBuffer(0, IID_PPV_ARGS(&back));
    ID3D11RenderTargetView* rtv = nullptr;
    device->CreateRenderTargetView(back, nullptr, &rtv);

    const bool follower = g_cfg.mode == "follow" && m->index != g_cfg.master;
    const double end = g_t0 + g_cfg.seconds;
    uint32_t last_stat = 0;
    while (!g_stop && now_s() < end) {
        WaitForSingleObjectEx(waitable, 100, TRUE);
        // Follower: present right at the master's next refresh; with VRR active the panel refreshes on
        // arrival. Everyone else: the present lands on this monitor's next refresh.
        double target = follower ? g_monitors[g_cfg.master]->clock.next_after(now_s() + 0.0012)
                                 : m->clock.next_after(now_s() + 0.0005);
        int64_t frame = (g_cfg.mode == "nearest") ? frame_nearest(*m, target) : frame_at(target);
        draw(ctx, rtv, *m, std::max<int64_t>(frame, 0));
        if (follower) sleep_until(target - 0.0003);
        PresentRecord p{};
        p.call = now_s();
        HRESULT hr = follower ? sc->Present(0, tearing ? DXGI_PRESENT_ALLOW_TEARING : 0) : sc->Present(1, 0);
        p.ret = now_s();
        p.target = target;
        p.frame = frame;
        sc->GetLastPresentCount(&p.id);
        if (SUCCEEDED(hr)) m->presents.push_back(p);
        DXGI_FRAME_STATISTICS fs{};
        if (SUCCEEDED(sc->GetFrameStatistics(&fs)) && fs.PresentCount != last_stat) {
            last_stat = fs.PresentCount;
            m->stats.push_back({fs.PresentCount, fs.PresentRefreshCount, fs.SyncRefreshCount,
                                double(fs.SyncQPCTime.QuadPart) / qpc_freq(), now_s()});
        }
        if (media) {
            DXGI_FRAME_STATISTICS_MEDIA fm{};
            if (SUCCEEDED(media->GetFrameStatisticsMedia(&fm))) m->composition_modes[int(fm.CompositionMode)]++;
        }
    }
    g_stop = true;
    rtv->Release(); back->Release();
    if (media) media->Release();
    sc->Release(); sc1->Release(); factory->Release(); ctx->Release(); context->Release(); device->Release();
    if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
}

LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_KEYDOWN && wp == VK_ESCAPE) { g_stop = true; return 0; }
    if (msg == WM_SETCURSOR) { SetCursor(nullptr); return TRUE; }
    if (msg == WM_ERASEBKGND) return 1;
    return DefWindowProcW(hwnd, msg, wp, lp);
}

std::string utf8(const std::wstring& w) {
    int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, nullptr, 0, nullptr, nullptr);
    std::string s(n > 0 ? n - 1 : 0, '\0');
    if (n > 1) WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, s.data(), n, nullptr, nullptr);
    return s;
}

void write_log(const Monitor& m) {
    std::wstring path = g_cfg.out + L"\\monitor-" + std::to_wstring(m.index) + L"-" + std::wstring(g_cfg.mode.begin(), g_cfg.mode.end()) + L".json";
    FILE* f = _wfopen(path.c_str(), L"w");
    if (!f) return;
    auto vb = const_cast<Monitor&>(m).clock.log();
    fprintf(f, "{\"monitor\":%d,\"name\":\"%s\",\"rect\":[%ld,%ld,%ld,%ld],\"width_mm\":%.1f,\"left_mm\":%.1f,",
            m.index, utf8(m.name).c_str() + 4, m.rect.left, m.rect.top, m.rect.right, m.rect.bottom, m.width_mm, m.left_mm);
    fprintf(f, "\"mode\":\"%s\",\"fps\":%.6f,\"t0\":%.7f,\"master\":%d,\"error\":\"%s\",\"composition_modes\":{",
            g_cfg.mode.c_str(), g_cfg.fps, g_t0, g_cfg.master, m.error.c_str());
    bool first = true;
    for (auto& kv : m.composition_modes) { fprintf(f, "%s\"%d\":%d", first ? "" : ",", kv.first, kv.second); first = false; }
    fprintf(f, "},\"vblanks\":[");
    for (size_t i = 0; i < vb.size(); ++i) fprintf(f, "%s%.7f", i ? "," : "", vb[i]);
    fprintf(f, "],\"presents\":[");
    for (size_t i = 0; i < m.presents.size(); ++i) {
        const auto& p = m.presents[i];
        fprintf(f, "%s[%u,%.7f,%.7f,%.7f,%lld]", i ? "," : "", p.id, p.call, p.ret, p.target, (long long)p.frame);
    }
    fprintf(f, "],\"stats\":[");
    for (size_t i = 0; i < m.stats.size(); ++i) {
        const auto& s = m.stats[i];
        fprintf(f, "%s[%u,%u,%u,%.7f,%.7f]", i ? "," : "", s.present_count, s.present_refresh, s.sync_refresh, s.sync_qpc, s.polled);
    }
    fprintf(f, "]}\n");
    fclose(f);
    wprintf(L"wrote %s\n", path.c_str());
}

void parse_args(int argc, wchar_t** argv) {
    for (int i = 1; i + 1 < argc; i += 2) {
        std::wstring k = argv[i], v = argv[i + 1];
        if (k == L"--mode") g_cfg.mode = std::string(v.begin(), v.end());
        else if (k == L"--fps") g_cfg.fps = _wtof(v.c_str());
        else if (k == L"--seconds") g_cfg.seconds = _wtof(v.c_str());
        else if (k == L"--master") g_cfg.master = _wtoi(v.c_str());
        else if (k == L"--out") g_cfg.out = v;
        else if (k == L"--gap-mm") g_cfg.gap_mm = _wtof(v.c_str());
        else if (k == L"--refresh") {
            size_t eq = v.find(L'=');
            if (eq != std::wstring::npos) {
                g_cfg.refresh_device = v.substr(0, eq);
                if (g_cfg.refresh_device.rfind(L"\\\\.\\", 0) != 0) g_cfg.refresh_device = L"\\\\.\\" + g_cfg.refresh_device;
                g_cfg.refresh_hz = _wtoi(v.c_str() + eq + 1);
            }
        }
    }
}

// Switch one display's refresh for this run, held by this process (CDS_FULLSCREEN): Windows puts the saved
// mode back when the process exits or dies (ADR 0023). Returns false if the driver refuses the rate.
bool switch_refresh(const std::wstring& device, int hz) {
    DEVMODEW dm{};
    dm.dmSize = sizeof(dm);
    if (!EnumDisplaySettingsW(device.c_str(), ENUM_CURRENT_SETTINGS, &dm)) return false;
    dm.dmDisplayFrequency = DWORD(hz);
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY;
    if (ChangeDisplaySettingsExW(device.c_str(), &dm, nullptr, CDS_TEST, nullptr) != DISP_CHANGE_SUCCESSFUL) return false;
    if (ChangeDisplaySettingsExW(device.c_str(), &dm, nullptr, CDS_FULLSCREEN, nullptr) != DISP_CHANGE_SUCCESSFUL) return false;
    Sleep(3000);  // let the monitor sync to the new timing
    return true;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    parse_args(argc, argv);
    if (g_cfg.mode != "own" && g_cfg.mode != "nearest" && g_cfg.mode != "follow") {
        fwprintf(stderr, L"unknown --mode\n");
        return 2;
    }
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    timeBeginPeriod(1);
    if (g_cfg.refresh_hz > 0) {
        if (!switch_refresh(g_cfg.refresh_device, g_cfg.refresh_hz)) {
            fwprintf(stderr, L"%s: switching to %d Hz failed\n", g_cfg.refresh_device.c_str(), g_cfg.refresh_hz);
            return 1;
        }
        wprintf(L"%s switched to %d Hz for this run\n", g_cfg.refresh_device.c_str(), g_cfg.refresh_hz);
    }

    // The adapter that drives the monitors (on Setup A: the NVIDIA GPU drives both).
    IDXGIFactory6* factory = nullptr;
    CreateDXGIFactory2(0, IID_PPV_ARGS(&factory));
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
        m->index = int(j);
        m->name = od.DeviceName;
        m->rect = od.DesktopCoordinates;
        m->output = output;
        m->width_mm = panel_width_mm(m->rect.right - m->rect.left);
        g_monitors.push_back(m);
    }
    // Physical path: monitors left to right by desktop position, gap_mm apart.
    std::vector<Monitor*> order = g_monitors;
    std::sort(order.begin(), order.end(), [](Monitor* a, Monitor* b) { return a->rect.left < b->rect.left; });
    double x = 0;
    for (auto* m : order) { m->left_mm = x; x += m->width_mm + g_cfg.gap_mm; }

    WNDCLASSW wc{};
    wc.lpfnWndProc = window_proc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = L"panorama-per-monitor-presenter";
    RegisterClassW(&wc);
    for (auto* m : g_monitors) {
        m->hwnd = CreateWindowExW(WS_EX_TOPMOST, wc.lpszClassName, L"panorama per-monitor presenter", WS_POPUP | WS_VISIBLE,
                                  m->rect.left, m->rect.top, m->rect.right - m->rect.left, m->rect.bottom - m->rect.top,
                                  nullptr, nullptr, wc.hInstance, nullptr);
    }
    SetForegroundWindow(g_monitors.front()->hwnd);

    std::vector<std::thread> vblank_threads, render_threads;
    for (auto* m : g_monitors) vblank_threads.emplace_back(vblank_thread, m);
    // Let the refresh grids settle before the film starts.
    double settle_end = now_s() + 1.5;
    MSG msg;
    while (now_s() < settle_end) {
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
        Sleep(5);
    }
    if (g_cfg.master < 0) {  // master: the slowest monitor
        g_cfg.master = 0;
        for (auto* m : g_monitors)
            if (m->clock.period() > g_monitors[g_cfg.master]->clock.period()) g_cfg.master = m->index;
    }
    for (auto* m : g_monitors)
        wprintf(L"monitor %d %s: %ldx%ld at (%ld,%ld), refresh %.4f Hz%s\n", m->index, m->name.c_str(),
                m->rect.right - m->rect.left, m->rect.bottom - m->rect.top, m->rect.left, m->rect.top,
                1.0 / m->clock.period(), m->index == g_cfg.master ? L" (master)" : L"");
    {   // Lock the film clock to the master: frames hold a whole number of master refreshes.
        VblankClock& mc = g_monitors[g_cfg.master]->clock;
        g_hold = std::max<int64_t>(1, std::llround(1.0 / (g_cfg.fps * mc.period())));
        g_first = mc.index_near(mc.next_after(now_s() + 0.5));
        if (g_cfg.mode == "nearest" && g_monitors.size() > 1) {
            // The heads' clocks keep a fixed ratio, so master refreshes fall into a few alignment classes against
            // the follower's grid. Start the film on the candidate whose nearest follower refresh is closest; every
            // later frame start (g_hold master refreshes apart) keeps that class while the ratio holds.
            Monitor* follower = g_monitors[g_cfg.master == 0 ? 1 : 0];
            int64_t base = g_first;
            double best = 1e9;
            for (int64_t k = base; k < base + 24; ++k) {
                double t = mc.time_of(k);
                double off = std::fabs(follower->clock.nearest(t) - t);
                if (off < best - 1e-6) { best = off; g_first = k; }
            }
            wprintf(L"film starts %lld master refreshes later: predicted follower offset %.3f ms\n",
                    (long long)(g_first - base), best * 1000);
        }
        g_t0 = mc.time_of(g_first);
        g_frame_s = double(g_hold) * mc.period();
        g_cfg.fps = 1.0 / g_frame_s;
        wprintf(L"film clock locked to the master: %lld refreshes per frame, %.5f fps\n", (long long)g_hold, g_cfg.fps);
    }
    for (auto* m : g_monitors) render_threads.emplace_back(render_thread, m, adapter);
    while (!g_stop) {
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
        Sleep(5);
    }
    for (auto& t : render_threads) t.join();
    for (auto& t : vblank_threads) t.join();
    CreateDirectoryW(g_cfg.out.c_str(), nullptr);
    for (auto* m : g_monitors) {
        if (!m->error.empty()) fwprintf(stderr, L"monitor %d: %hs\n", m->index, m->error.c_str());
        write_log(*m);
        DestroyWindow(m->hwnd);
        m->output->Release();
    }
    adapter->Release();
    factory->Release();
    if (g_cfg.refresh_hz > 0) ChangeDisplaySettingsExW(g_cfg.refresh_device.c_str(), nullptr, nullptr, 0, nullptr);
    timeEndPeriod(1);
    return 0;
}
