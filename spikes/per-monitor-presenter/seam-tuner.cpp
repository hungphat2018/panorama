// Seam tuner: make motion cross the bezel with no delay (ADR 0020, M1 phase 01 step 6b).
//
// One window and swapchain per monitor (as in per-monitor-presenter.cpp). Both screens show one pattern laid
// out in millimetres along the row of monitors, continuing behind the bezel: diagonal stripes, thin horizontal
// lines and white vertical bars. It slides left and right (reversing every 2.5 s). In smooth mode each screen
// draws the pattern where it is at the moment that screen's light appears: its refresh time, plus its panel
// lag, plus (rows on) the time its scan takes to reach each row. In step mode it moves in film-frame steps,
// switched on each screen's refresh nearest the master's switch.
//
// The user only judges; the keys adjust:
//   1 2 3 4       speed 0 / 150 / 300 / 600 mm/s
//   PgUp PgDn     height of the right screen against the left one (0.5 mm, Shift 5 mm): horizontal lines straight
//   Left Right    bezel gap (0.5 mm, Shift 5 mm): with the pattern still, diagonal stripes straight across the bezel
//   Up Down       lag of the selected screen (0.5 ms, Shift 5 ms): with it moving, stripes straight in BOTH directions
//   Tab           select which screen's lag Up/Down changes
//   S             smooth / film steps        R  rows timing on / off        P  stripes on / off (bars only)
//   Enter         save settings to JSON      Esc quit (also saves to seam-settings-last.json)
// A lag error breaks the stripes one way moving right and the other way moving left; a gap error breaks them
// the same way in both directions (and when still).
//
// Usage: seam-tuner.exe [--gap-mm 20] [--out DIR] [--seconds N (auto-quit, for smoke tests)]
#define _CRT_SECURE_NO_WARNINGS
#include "per-monitor-common.h"
#include <avrt.h>
#include <d2d1_1.h>
#include <d3d11_1.h>
#include <d3dcompiler.h>
#include <dwrite.h>
#include <dxgi1_6.h>
#include <timeapi.h>

#include <atomic>
#include <cstdio>
#include <cwchar>
#include <string>
#include <thread>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "d3dcompiler.lib")
#pragma comment(lib, "d2d1.lib")
#pragma comment(lib, "dwrite.lib")
#pragma comment(lib, "avrt.lib")
#pragma comment(lib, "winmm.lib")
#pragma comment(lib, "user32.lib")

namespace {

const char* kShader = R"(
cbuffer Params : register(b0) {
    float2 mm_per_px;     // pixel pitch, x and y
    float2 origin_mm;     // world position (mm) of this screen's top-left pixel corner; y grows downward
    float  shift_top;     // pattern offset (mm) along x for the first row
    float  shift_per_row; // change of that offset per pixel row (scan timing), mm
    float  show_stripes;
    float  pad;
};
float4 vs(uint id : SV_VertexID) : SV_Position {
    float2 p = float2((id << 1) & 2, id & 2);
    return float4(p * float2(2, -2) + float2(-1, 1), 0, 1);
}
float band(float u, float width, float aa) {          // 1 inside [0, width) of a unit period, anti-aliased
    return smoothstep(0, aa, u) * (1 - smoothstep(width, width + aa, u));
}
float4 ps(float4 pos : SV_Position) : SV_Target {
    float2 w = origin_mm + pos.xy * mm_per_px;
    float x = w.x - (shift_top + pos.y * shift_per_row);
    float aa = mm_per_px.x;
    float stripes = band(frac((x - w.y) / 40.0), 4.0 / 40.0, aa / 40.0);      // 45-degree stripes, 40 mm period
    float lines = band(frac(w.y / 60.0), 1.2 / 60.0, aa / 60.0);              // horizontal lines every 60 mm
    float bars = band(frac(x / 250.0), 10.0 / 250.0, aa / 250.0);             // white bars every 250 mm
    float v = 0.10 + 0.40 * stripes * show_stripes + 0.30 * lines * show_stripes;
    v = lerp(v, 0.95, bars);
    return float4(v, v, v, 1);
}
)";

struct alignas(16) Params {
    float mm_per_px[2], origin_mm[2];
    float shift_top, shift_per_row, show_stripes, pad;
};

struct Monitor {
    int index = 0;
    std::wstring name;
    RECT rect{};
    IDXGIOutput* output = nullptr;
    HWND hwnd = nullptr;
    VblankClock clock;
    double width_mm = 0, height_mm = 0, pitch_mm = 0.25;
    double scan_fraction = 0.96;  // active lines / total lines: share of a refresh spent scanning the picture
};

struct Settings {
    std::mutex mutex;
    double gap_mm = 20, height_mm = 0;  // height: right screen's top edge below the left screen's (mm)
    std::vector<double> lag_ms;
    int selected = 1, speed_level = 2;
    bool smooth = true, rows = true, stripes = true, help = true;
};

const wchar_t* kHelp =
    L"How to tune (H hides this):\n"
    L"1. Press 1 (still). Thin horizontal lines: PgUp / PgDn until each line runs straight across the bezel, no step.\n"
    L"2. Still. Diagonal stripes: Left / Right (gap) until each stripe runs on as one straight line, as if behind the bezel.\n"
    L"    Pass: straight now, and one press either way makes a visible bend. Shift = big steps.\n"
    L"3. Press 3 or 4 (moving). Watch the stripes at the bezel, moving right, then left. Up / Down (lag of the right screen):\n"
    L"    breaks one way moving right and the other way moving left = lag, keep adjusting;\n"
    L"    breaks the same way in both directions = gap, go back to step 2. Pass: straight both ways.\n"
    L"4. Watch a white bar cross. Still a delay? Try S (film steps) and R (row timing) to compare.\n"
    L"5. Enter saves. Esc quits.";

const double kSpeeds[] = {0, 150, 300, 600};
const double kHalfCycle = 2.5;  // seconds between direction reversals
std::atomic<bool> g_stop{false};
Settings g_set;
std::vector<Monitor*> g_monitors;  // sorted left to right
std::wstring g_out = L".";
double g_motion_t0 = 0;
// Film clock for step mode, locked to the master (slowest monitor), as in the presenter's nearest mode.
int g_master = 0;
int64_t g_first = 0, g_hold = 4;
double g_film_t0 = 0, g_frame_s = 1.0 / 25;

// Pattern offset (mm) at time t and its rate (mm/s): slides at `speed`, reversing every kHalfCycle seconds.
void motion(double t, double speed, double* pos, double* rate) {
    double phase = std::fmod(t - g_motion_t0, 2 * kHalfCycle);
    if (phase < 0) phase += 2 * kHalfCycle;
    *pos = speed * (phase < kHalfCycle ? phase : 2 * kHalfCycle - phase);
    *rate = phase < kHalfCycle ? speed : -speed;
}

double master_switch(int64_t n) { return g_monitors[g_master]->clock.time_of(g_first + g_hold * n); }

// Film frame shown on monitor m at its refresh v: switches on its refresh nearest the master's switch, shifted
// by the difference in panel lag so the light changes together.
int64_t step_frame(Monitor& m, double v, double lag_m, double lag_master) {
    if (m.index == g_monitors[g_master]->index) {
        int64_t k = m.clock.index_near(v) - g_first;
        return k >= 0 ? k / g_hold : -1;
    }
    int64_t guess = int64_t(std::floor((v - g_film_t0) / g_frame_s));
    int64_t best = guess - 3;
    for (int64_t n = guess - 3; n <= guess + 3; ++n)
        if (m.clock.nearest(master_switch(n) + lag_master - lag_m) <= v + 1e-4) best = n;
    return best;
}

// Share of each refresh spent scanning active lines, from the display path's signal timing (CCD).
double scan_fraction(const std::wstring& gdi_name) {
    UINT32 np = 0, nm = 0;
    if (GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, &np, &nm) != ERROR_SUCCESS) return 0.96;
    std::vector<DISPLAYCONFIG_PATH_INFO> paths(np);
    std::vector<DISPLAYCONFIG_MODE_INFO> modes(nm);
    if (QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, &np, paths.data(), &nm, modes.data(), nullptr) != ERROR_SUCCESS) return 0.96;
    for (UINT32 i = 0; i < np; ++i) {
        DISPLAYCONFIG_SOURCE_DEVICE_NAME src{};
        src.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME;
        src.header.size = sizeof(src);
        src.header.adapterId = paths[i].sourceInfo.adapterId;
        src.header.id = paths[i].sourceInfo.id;
        if (DisplayConfigGetDeviceInfo(&src.header) != ERROR_SUCCESS || gdi_name != src.viewGdiDeviceName) continue;
        UINT32 mi = paths[i].targetInfo.modeInfoIdx;
        if (mi >= nm || modes[mi].infoType != DISPLAYCONFIG_MODE_INFO_TYPE_TARGET) continue;
        const auto& sig = modes[mi].targetMode.targetVideoSignalInfo;
        if (sig.totalSize.cy > 0) return double(sig.activeSize.cy) / double(sig.totalSize.cy);
    }
    return 0.96;
}

// Setup A's panels (EDID / ruler); others fall back to 0.25 mm per pixel.
void physical_size(Monitor& m) {
    LONG w = m.rect.right - m.rect.left, h = m.rect.bottom - m.rect.top;
    m.width_mm = (w == 2560) ? 344.0 : (w == 1920) ? 476.0 : w * 0.25;
    m.pitch_mm = m.width_mm / double(w);
    m.height_mm = h * m.pitch_mm;
}

ID3DBlob* compile(const char* entry, const char* target) {
    ID3DBlob* blob = nullptr;
    ID3DBlob* errors = nullptr;
    if (FAILED(D3DCompile(kShader, strlen(kShader), "seam-tuner", nullptr, nullptr, entry, target, 0, 0, &blob, &errors))) {
        if (errors) fprintf(stderr, "%s\n", (const char*)errors->GetBufferPointer());
        return nullptr;
    }
    return blob;
}

std::wstring hud_text() {
    std::lock_guard<std::mutex> lock(g_set.mutex);
    wchar_t buf[600];
    std::wstring lags;
    for (size_t i = 0; i < g_monitors.size(); ++i) {
        wchar_t one[80];
        swprintf(one, 80, L"%s%s %+.1f ms%s", i ? L",  " : L"", i == 0 ? L"left" : (i == 1 ? L"right" : L"third"),
                 g_set.lag_ms[i], int(i) == g_set.selected ? L" [selected]" : L"");
        lags += one;
    }
    swprintf(buf, 600,
             L"gap %.1f mm (← →)    height %+.1f mm (PgUp PgDn)    lag: %s (↑ ↓, Tab)\n"
             L"speed %.0f mm/s (1-4)    %s (S)    rows %s (R)    stripes %s (P)    Enter save    Esc quit",
             g_set.gap_mm, g_set.height_mm, lags.c_str(), kSpeeds[g_set.speed_level],
             g_set.smooth ? L"smooth" : L"film steps", g_set.rows ? L"on" : L"off", g_set.stripes ? L"on" : L"off");
    return buf;
}

void render_thread(Monitor* m, IDXGIAdapter1* adapter) {
    DWORD task = 0;
    HANDLE mmcss = AvSetMmThreadCharacteristicsW(L"Games", &task);
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* ctx = nullptr;
    D3D_FEATURE_LEVEL want = D3D_FEATURE_LEVEL_11_1, got;
    if (FAILED(D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT, &want, 1,
                                 D3D11_SDK_VERSION, &device, &got, &ctx))) { g_stop = true; return; }
    IDXGIFactory2* factory = nullptr;
    adapter->GetParent(IID_PPV_ARGS(&factory));
    DXGI_SWAP_CHAIN_DESC1 desc{};
    desc.Width = UINT(m->rect.right - m->rect.left);
    desc.Height = UINT(m->rect.bottom - m->rect.top);
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.BufferCount = 2;
    desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    desc.Flags = DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT;
    IDXGISwapChain1* sc1 = nullptr;
    if (FAILED(factory->CreateSwapChainForHwnd(device, m->hwnd, &desc, nullptr, nullptr, &sc1))) { g_stop = true; return; }
    factory->MakeWindowAssociation(m->hwnd, DXGI_MWA_NO_ALT_ENTER);
    IDXGISwapChain2* sc = nullptr;
    sc1->QueryInterface(IID_PPV_ARGS(&sc));
    sc->SetMaximumFrameLatency(1);
    HANDLE waitable = sc->GetFrameLatencyWaitableObject();
    ID3D11Texture2D* back = nullptr;
    sc->GetBuffer(0, IID_PPV_ARGS(&back));
    ID3D11RenderTargetView* rtv = nullptr;
    device->CreateRenderTargetView(back, nullptr, &rtv);

    ID3DBlob* vsb = compile("vs", "vs_5_0");
    ID3DBlob* psb = compile("ps", "ps_5_0");
    if (!vsb || !psb) { g_stop = true; return; }
    ID3D11VertexShader* vs = nullptr;
    ID3D11PixelShader* ps = nullptr;
    device->CreateVertexShader(vsb->GetBufferPointer(), vsb->GetBufferSize(), nullptr, &vs);
    device->CreatePixelShader(psb->GetBufferPointer(), psb->GetBufferSize(), nullptr, &ps);
    D3D11_BUFFER_DESC cbd{sizeof(Params), D3D11_USAGE_DEFAULT, D3D11_BIND_CONSTANT_BUFFER, 0, 0, 0};
    ID3D11Buffer* cb = nullptr;
    device->CreateBuffer(&cbd, nullptr, &cb);

    // Text overlay (Direct2D on the same back buffer), on the leftmost screen only.
    ID2D1Factory1* d2d = nullptr;
    ID2D1DeviceContext* d2dctx = nullptr;
    ID2D1SolidColorBrush *white = nullptr, *shade = nullptr;
    IDWriteTextFormat* format = nullptr;
    if (m->index == 0) {
        D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, __uuidof(ID2D1Factory1), nullptr, (void**)&d2d);
        IDXGIDevice* dxgi_device = nullptr;
        device->QueryInterface(IID_PPV_ARGS(&dxgi_device));
        ID2D1Device* d2ddev = nullptr;
        d2d->CreateDevice(dxgi_device, &d2ddev);
        d2ddev->CreateDeviceContext(D2D1_DEVICE_CONTEXT_OPTIONS_NONE, &d2dctx);
        IDXGISurface* surface = nullptr;
        sc->GetBuffer(0, IID_PPV_ARGS(&surface));
        D2D1_BITMAP_PROPERTIES1 bp = D2D1::BitmapProperties1(D2D1_BITMAP_OPTIONS_TARGET | D2D1_BITMAP_OPTIONS_CANNOT_DRAW,
                                                             D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_IGNORE));
        ID2D1Bitmap1* target = nullptr;
        d2dctx->CreateBitmapFromDxgiSurface(surface, &bp, &target);
        d2dctx->SetTarget(target);
        d2dctx->CreateSolidColorBrush(D2D1::ColorF(1, 1, 1, 1), &white);
        d2dctx->CreateSolidColorBrush(D2D1::ColorF(0, 0, 0, 0.75f), &shade);
        IDWriteFactory* dw = nullptr;
        DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED, __uuidof(IDWriteFactory), (IUnknown**)&dw);
        dw->CreateTextFormat(L"Segoe UI", nullptr, DWRITE_FONT_WEIGHT_SEMI_BOLD, DWRITE_FONT_STYLE_NORMAL,
                             DWRITE_FONT_STRETCH_NORMAL, 26.0f, L"en-us", &format);
        surface->Release(); d2ddev->Release(); dxgi_device->Release();
    }

    const float w = float(desc.Width), h = float(desc.Height);
    D3D11_VIEWPORT vp{0, 0, w, h, 0, 1};
    while (!g_stop) {
        WaitForSingleObjectEx(waitable, 100, TRUE);
        double v = m->clock.next_after(now_s() + 0.0005);  // refresh this present will reach the screen on
        double gap, height, lag_m, lag_master, speed;
        bool smooth, rows, stripes;
        {
            std::lock_guard<std::mutex> lock(g_set.mutex);
            gap = g_set.gap_mm; height = g_set.height_mm; speed = kSpeeds[g_set.speed_level];
            lag_m = g_set.lag_ms[m->index] / 1000; lag_master = g_set.lag_ms[g_master] / 1000;
            smooth = g_set.smooth; rows = g_set.rows; stripes = g_set.stripes;
        }
        // Physical layout: screens left to right, `gap` mm apart; the second screen `height` mm lower.
        double left = 0, top = (m->index == 1) ? height : 0;
        for (int i = 0; i < m->index; ++i) left += g_monitors[i]->width_mm + gap;
        double pos, rate;
        Params p{};
        if (smooth) {
            double light = v + lag_m;                                       // when the top row lights up
            motion(light, speed, &pos, &rate);
            double scan_s = m->clock.period() * m->scan_fraction;           // time to scan the picture rows
            p.shift_per_row = rows ? float(rate * scan_s / h) : 0.0f;
        } else {
            int64_t n = step_frame(*m, v, lag_m, lag_master);
            motion(g_film_t0 + double(std::max<int64_t>(n, 0)) * g_frame_s, speed, &pos, &rate);
        }
        p.mm_per_px[0] = p.mm_per_px[1] = float(m->pitch_mm);
        p.origin_mm[0] = float(left);
        p.origin_mm[1] = float(top);
        p.shift_top = float(pos);
        p.show_stripes = stripes ? 1.0f : 0.0f;
        ctx->UpdateSubresource(cb, 0, nullptr, &p, 0, 0);
        ctx->OMSetRenderTargets(1, &rtv, nullptr);
        ctx->RSSetViewports(1, &vp);
        ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        ctx->VSSetShader(vs, nullptr, 0);
        ctx->PSSetShader(ps, nullptr, 0);
        ctx->PSSetConstantBuffers(0, 1, &cb);
        ctx->Draw(3, 0);
        if (d2dctx) {
            std::wstring text = hud_text();
            bool help;
            { std::lock_guard<std::mutex> lock(g_set.mutex); help = g_set.help; }
            d2dctx->BeginDraw();
            d2dctx->FillRectangle(D2D1::RectF(20, 20, 1700, 112), shade);
            d2dctx->DrawText(text.c_str(), UINT32(text.size()), format, D2D1::RectF(36, 28, 1690, 110), white);
            if (help) {  // left half of the laptop screen, away from the bezel
                d2dctx->FillRectangle(D2D1::RectF(20, 130, 1180, 560), shade);
                d2dctx->DrawText(kHelp, UINT32(wcslen(kHelp)), format, D2D1::RectF(36, 140, 1170, 555), white);
            }
            d2dctx->EndDraw();
        }
        sc->Present(1, 0);
    }
    if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
}

void save(const std::wstring& path) {
    FILE* f = _wfopen(path.c_str(), L"w");
    if (!f) return;
    std::lock_guard<std::mutex> lock(g_set.mutex);
    fprintf(f, "{\n \"gap_mm\": %.2f,\n \"right_screen_lower_mm\": %.2f,\n \"lag_ms\": {", g_set.gap_mm, g_set.height_mm);
    for (size_t i = 0; i < g_monitors.size(); ++i) {
        std::wstring n = g_monitors[i]->name;
        fprintf(f, "%s\"%ls\": %.2f", i ? ", " : "", n.substr(n.find_last_of(L'\\') + 1).c_str(), g_set.lag_ms[i]);
    }
    fprintf(f, "},\n \"scan_fraction\": {");
    for (size_t i = 0; i < g_monitors.size(); ++i) {
        std::wstring n = g_monitors[i]->name;
        fprintf(f, "%s\"%ls\": %.4f", i ? ", " : "", n.substr(n.find_last_of(L'\\') + 1).c_str(), g_monitors[i]->scan_fraction);
    }
    fprintf(f, "},\n \"refresh_hz\": {");
    for (size_t i = 0; i < g_monitors.size(); ++i) {
        std::wstring n = g_monitors[i]->name;
        fprintf(f, "%s\"%ls\": %.4f", i ? ", " : "", n.substr(n.find_last_of(L'\\') + 1).c_str(), 1.0 / g_monitors[i]->clock.period());
    }
    fprintf(f, "},\n \"rows_timing\": %s,\n \"note\": \"lag is relative; widths assumed 344 mm (2560 px) and 476 mm (1920 px)\"\n}\n",
            g_set.rows ? "true" : "false");
    fclose(f);
    wprintf(L"saved %s\n", path.c_str());
}

LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_SETCURSOR) { SetCursor(nullptr); return TRUE; }
    if (msg == WM_ERASEBKGND) return 1;
    if (msg != WM_KEYDOWN) return DefWindowProcW(hwnd, msg, wp, lp);
    bool big = (GetKeyState(VK_SHIFT) & 0x8000) != 0;
    if (wp == VK_ESCAPE) { g_stop = true; return 0; }
    if (wp == VK_RETURN) {
        SYSTEMTIME st;
        GetLocalTime(&st);
        wchar_t name[64];
        swprintf(name, 64, L"\\seam-settings-%04d%02d%02d-%02d%02d%02d.json", st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);
        save(g_out + name);
        return 0;
    }
    std::lock_guard<std::mutex> lock(g_set.mutex);
    double mm = big ? 5.0 : 0.5, ms = big ? 5.0 : 0.5;
    switch (wp) {
        case VK_LEFT: g_set.gap_mm = std::max(0.0, g_set.gap_mm - mm); break;
        case VK_RIGHT: g_set.gap_mm += mm; break;
        case VK_PRIOR: g_set.height_mm -= mm; break;
        case VK_NEXT: g_set.height_mm += mm; break;
        case VK_UP: g_set.lag_ms[g_set.selected] += ms; break;
        case VK_DOWN: g_set.lag_ms[g_set.selected] -= ms; break;
        case VK_TAB: g_set.selected = (g_set.selected + 1) % int(g_set.lag_ms.size()); break;
        case '1': case '2': case '3': case '4': g_set.speed_level = int(wp - '1'); break;
        case 'S': g_set.smooth = !g_set.smooth; break;
        case 'R': g_set.rows = !g_set.rows; break;
        case 'P': g_set.stripes = !g_set.stripes; break;
        case 'H': g_set.help = !g_set.help; break;
        default: break;
    }
    return 0;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    double seconds = 0;  // 0: run until Esc
    for (int i = 1; i + 1 < argc; i += 2) {
        std::wstring k = argv[i];
        if (k == L"--gap-mm") g_set.gap_mm = _wtof(argv[i + 1]);
        else if (k == L"--out") g_out = argv[i + 1];
        else if (k == L"--seconds") seconds = _wtof(argv[i + 1]);
    }
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    timeBeginPeriod(1);
    IDXGIFactory6* factory = nullptr;
    CreateDXGIFactory2(0, IID_PPV_ARGS(&factory));
    IDXGIAdapter1* adapter = nullptr;
    for (UINT i = 0; factory->EnumAdapters1(i, &adapter) != DXGI_ERROR_NOT_FOUND; ++i) {
        IDXGIOutput* o = nullptr;
        if (adapter->EnumOutputs(0, &o) != DXGI_ERROR_NOT_FOUND) { o->Release(); break; }
        adapter->Release();
        adapter = nullptr;
    }
    if (!adapter) return 1;
    IDXGIOutput* output = nullptr;
    for (UINT j = 0; adapter->EnumOutputs(j, &output) != DXGI_ERROR_NOT_FOUND; ++j) {
        DXGI_OUTPUT_DESC od;
        output->GetDesc(&od);
        auto* m = new Monitor;
        m->name = od.DeviceName;
        m->rect = od.DesktopCoordinates;
        m->output = output;
        physical_size(*m);
        m->scan_fraction = scan_fraction(m->name);
        g_monitors.push_back(m);
    }
    std::sort(g_monitors.begin(), g_monitors.end(), [](Monitor* a, Monitor* b) { return a->rect.left < b->rect.left; });
    for (size_t i = 0; i < g_monitors.size(); ++i) g_monitors[i]->index = int(i);
    g_set.lag_ms.assign(g_monitors.size(), 0.0);
    g_set.selected = int(g_monitors.size()) - 1;

    WNDCLASSW wc{};
    wc.lpfnWndProc = window_proc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.lpszClassName = L"panorama-seam-tuner";
    RegisterClassW(&wc);
    for (auto* m : g_monitors)
        m->hwnd = CreateWindowExW(WS_EX_TOPMOST, wc.lpszClassName, L"panorama seam tuner", WS_POPUP | WS_VISIBLE, m->rect.left,
                                  m->rect.top, m->rect.right - m->rect.left, m->rect.bottom - m->rect.top, nullptr, nullptr,
                                  wc.hInstance, nullptr);
    SetForegroundWindow(g_monitors.front()->hwnd);

    std::vector<std::thread> threads;
    for (auto* m : g_monitors)
        threads.emplace_back([m] {
            SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
            while (!g_stop) {
                if (FAILED(m->output->WaitForVBlank())) { Sleep(1); continue; }
                m->clock.add(now_s());
            }
        });
    MSG msg;
    double settle = now_s() + 1.5;
    while (now_s() < settle) {
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
        Sleep(5);
    }
    for (auto* m : g_monitors)
        if (m->clock.period() > g_monitors[g_master]->clock.period()) g_master = m->index;
    {   // film steps: 24 fps or the nearest whole number of master refreshes to it
        VblankClock& mc = g_monitors[g_master]->clock;
        g_hold = std::max<int64_t>(1, std::llround(1.0 / (24.0 * mc.period())));
        g_first = mc.index_near(mc.next_after(now_s() + 0.2));
        g_film_t0 = mc.time_of(g_first);
        g_frame_s = double(g_hold) * mc.period();
    }
    g_motion_t0 = now_s();
    for (auto* m : g_monitors) {
        wprintf(L"%s: %.0f x %.0f mm, %.3f Hz, scan %.1f%% of each refresh%s\n", m->name.c_str(), m->width_mm, m->height_mm,
                1.0 / m->clock.period(), 100 * m->scan_fraction, m->index == g_master ? L" (master for film steps)" : L"");
        threads.emplace_back(render_thread, m, adapter);
    }
    double end = seconds > 0 ? now_s() + seconds : 1e300;
    while (!g_stop && now_s() < end) {
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
        Sleep(5);
    }
    g_stop = true;
    for (auto& t : threads) t.join();
    save(g_out + L"\\seam-settings-last.json");
    timeEndPeriod(1);
    return 0;
}
