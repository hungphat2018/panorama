// seam-render.cpp: the shader, one render thread per monitor, and the per-refresh plan.
//
// Before every refresh each render thread takes the latest Scene, predicts which refresh its present will reach
// (the first top row, from the monitor's scan-line grid, that comes at least one blank plus g_lead_s, 0.42 ms
// by default, after now: the present has to be in before the blank starts),
// and plans that refresh row by row:
//   tuner pattern, smooth   row y is drawn for the middle of the time its light stays up: the row's light start
//                           (top row + y lines + panel lag) plus half a refresh, so the position changes by
//                           rate * line_s per row and the hold's centroid is the same on every screen.
//   film steps              the reference monitor shows film frame n for `hold` of its refreshes; the follower
//                           either switches whole pictures on its refresh nearest (aimed row) or fades the old
//                           frame into the new one row by row (rolling blend, mixed in linear light).
// After the present, DXGI frame statistics say which refresh the present really reached; a mismatch with the
// prediction is counted (not in windowed mode, where Windows composes the window).
#define _CRT_SECURE_NO_WARNINGS
#include "seam-app.h"

#include <avrt.h>
#include <d2d1_1.h>
#include <d3d11_1.h>
#include <d3dcompiler.h>
#include <dwrite.h>

#include <cstdio>
#include <cstring>
#include <deque>
#include <mutex>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "d3dcompiler.lib")
#pragma comment(lib, "d2d1.lib")
#pragma comment(lib, "dwrite.lib")
#pragma comment(lib, "avrt.lib")

namespace seam {
namespace {

const char* kShader = R"(
cbuffer Params : register(b0) {
    float2 mm_per_px;     // pixel pitch, x and y
    float2 origin_mm;     // world position (mm) of the view's top-left pixel corner; y grows downward
    float  shift_a;       // picture A: pattern offset (mm) along x at the first row
    float  shift_b;       // picture B (film steps); the same as A when nothing fades
    float  shift_per_row; // smooth motion: change of that offset per pixel row, mm
    float  w_top;         // share of picture B at the first row, in light
    float  w_per_row;     // change of that share per pixel row
    float  row0;          // monitor row of the view's first row
    float  pattern;       // 0 black, 1 tuner pattern, 2 film bar
    float  flags;         // tuner: bit 0 stripes, bit 1 horizontal lines, bit 2 white bars
    float  bar_w_mm;      // film bar width
    float  strip_x0;      // black strip (fake bezel), world x range; none when x1 <= x0
    float  strip_x1;
    float  pad;
};
float4 vs(uint id : SV_VertexID) : SV_Position {
    float2 p = float2((id << 1) & 2, id & 2);
    return float4(p * float2(2, -2) + float2(-1, 1), 0, 1);
}
float to_lin(float v) { return v <= 0.04045 ? v / 12.92 : pow((v + 0.055) / 1.055, 2.4); }
float to_enc(float l) { return l <= 0.0031308 ? l * 12.92 : 1.055 * pow(l, 1.0 / 2.4) - 0.055; }
float band(float u, float width, float aa) {          // 1 inside [0, width) of a unit period, anti-aliased
    return smoothstep(0, aa, u) * (1 - smoothstep(width, width + aa, u));
}
float tuner(float2 w, float row, float shift) {
    float x = w.x - (shift + row * shift_per_row);
    float aa = mm_per_px.x;
    uint f = (uint)flags;
    float stripes = band(frac((x - w.y) / 40.0), 4.0 / 40.0, aa / 40.0);      // 45-degree stripes, 40 mm period
    float lines = band(frac(w.y / 60.0), 1.2 / 60.0, aa / 60.0);              // horizontal lines every 60 mm
    float bars = band(frac(x / 250.0), 10.0 / 250.0, aa / 250.0);             // white bars every 250 mm
    float v = 0.10 + 0.40 * stripes * float(f & 1u) + 0.30 * lines * float((f >> 1) & 1u);
    return lerp(v, 0.95, bars * float((f >> 2) & 1u));
}
float film_bar(float2 w, float shift) {               // white bar from `shift` to `shift + bar_w_mm`, edges one pixel wide
    float x = w.x - shift;
    float aa = mm_per_px.x;
    return 0.95 * (saturate(x / aa + 0.5) - saturate((x - bar_w_mm) / aa + 0.5));
}
float4 ps(float4 pos : SV_Position) : SV_Target {
    float2 w = origin_mm + pos.xy * mm_per_px;
    float row = pos.y - 0.5 + row0;
    float v = 0;
    if (pattern > 1.5) {
        float wgt = saturate(w_top + w_per_row * row);
        float va = film_bar(w, shift_a);
        float vb = film_bar(w, shift_b);
        v = wgt <= 0 ? va : (wgt >= 1 ? vb : to_enc(lerp(to_lin(va), to_lin(vb), wgt)));   // mix in light, not in code values
    } else if (pattern > 0.5) {
        v = tuner(w, row, shift_a);
    }
    if (w.x >= strip_x0 && w.x < strip_x1) v = 0;
    return float4(v, v, v, 1);
}
)";

struct alignas(16) Params {
    float mm_per_px[2], origin_mm[2];
    float shift_a, shift_b, shift_per_row, w_top;
    float w_per_row, row0, pattern, flags;
    float bar_w_mm, strip_x0, strip_x1, pad;
};

std::mutex g_scene_mutex;
std::shared_ptr<const Scene> g_scene = std::make_shared<Scene>();

std::shared_ptr<const Scene> scene_get() {
    std::lock_guard<std::mutex> lock(g_scene_mutex);
    return g_scene;
}

struct Plan {
    Params p{};
    int64_t j = 0;         // the monitor's refresh the present should reach
    bool show_text = false;
};

double bar_left(const FilmClip& c, int64_t n) { return n >= c.frames ? kAbsentMm : c.path.left_mm(n); }

// Plan of one refresh of monitor m: what to draw, given the scene and the monitor's row clock at `now`.
Plan plan_frame(const Scene& sc, Monitor& m, double now) {
    Plan out;
    Params& p = out.p;
    const Wave self = wave_of(m, sc.height_mm, size_t(m.index) < sc.lag_ms.size() ? sc.lag_ms[size_t(m.index)] : 0.0);
    if (!(self.period > 0)) return out;
    out.j = self.index_after(now + g_lead_s + self.blank_s);  // the blank starts one blank before the top row
    const double T = self.top_time(out.j);                  // top row of that refresh
    p.mm_per_px[0] = p.mm_per_px[1] = float(m.pitch_mm);
    p.origin_mm[0] = float(world_left_mm(m.index, sc.gap_mm) + double(m.view_x) * m.pitch_mm);
    p.origin_mm[1] = float(self.top_mm + double(m.view_y) * m.pitch_mm);
    p.row0 = float(m.view_y);
    p.bar_w_mm = float(kBarWidthMm);
    if (m.index == 0 && sc.strip_x1 > sc.strip_x0) {
        p.strip_x0 = float(sc.strip_x0);
        p.strip_x1 = float(sc.strip_x1);
    }
    switch (sc.kind) {
        case Kind::Black:
            out.show_text = true;
            break;
        case Kind::Tuner: {
            p.pattern = 1;
            p.flags = float(sc.flags);
            double pos = 0, rate = 0;
            if (sc.steps) {
                // Film steps: the pattern jumps to where the motion is at each film frame's start.
                SeamClock s = make_seam(m.index, sc.height_mm, sc.lag_ms);
                s.k0 = sc.clip.k0;
                s.hold = sc.clip.hold;
                int64_t n = (m.index == g_master) ? ref_frame(s, out.j) : aimed_frame(s, out.j);
                double film_t0 = s.ref.top_time(s.k0);
                motion(film_t0 + double(std::max<int64_t>(n, 0)) * s.frame_s(), sc.motion_t0, sc.half_cycle, sc.speed, &pos, &rate);
            } else {
                motion(T + self.lag_s + 0.5 * self.period, sc.motion_t0, sc.half_cycle, sc.speed, &pos, &rate);
                p.shift_per_row = sc.rows ? float(rate * self.line_s) : 0.0f;
            }
            p.shift_a = p.shift_b = float(pos);
            out.show_text = true;
            break;
        }
        case Kind::Film: {
            const FilmClip& c = sc.clip;
            SeamClock s = make_seam(m.index, sc.height_mm, sc.lag_ms);
            s.k0 = c.k0;
            s.hold = c.hold;
            double a = kAbsentMm, b = kAbsentMm;
            if (m.index == g_master && !c.fake_bezel) {
                a = b = bar_left(c, ref_frame(s, out.j));
            } else if (m.index == 0) {
                if (c.cond == Cond::Aimed) {
                    a = b = bar_left(c, aimed_frame(s, out.j));
                } else {
                    Blend bl = rolling_blend(s, out.j);
                    a = bar_left(c, bl.newer - 1);
                    b = bar_left(c, bl.newer);
                    p.w_top = float(bl.w_top);
                    p.w_per_row = float(bl.w_per_row);
                }
            } else {
                break;  // the other monitors stay black
            }
            p.pattern = 2;
            p.shift_a = float(a);
            p.shift_b = float(b);
            out.show_text = !sc.text_until_clip || T < s.ref.top_time(c.k0) - 0.05;
            break;
        }
    }
    return out;
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

struct Pending {
    UINT id;
    int64_t j;
};

}  // namespace

void scene_post(std::shared_ptr<const Scene> scene) {
    std::lock_guard<std::mutex> lock(g_scene_mutex);
    g_scene = std::move(scene);
}

void render_thread(Monitor* m, IDXGIAdapter1* adapter) {
    DWORD task = 0;
    HANDLE mmcss = AvSetMmThreadCharacteristicsW(L"Games", &task);
    if (mmcss) AvSetMmThreadPriority(mmcss, AVRT_PRIORITY_HIGH);  // a late present shifts a refresh: keep the thread ahead
    auto fail = [&](const char* what) {
        fprintf(stderr, "%ls: %s\n", m->label.c_str(), what);
        g_failed = true;
        g_stop = true;
    };
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* ctx = nullptr;
    D3D_FEATURE_LEVEL want = D3D_FEATURE_LEVEL_11_1, got;
    if (FAILED(D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, D3D11_CREATE_DEVICE_BGRA_SUPPORT, &want, 1,
                                 D3D11_SDK_VERSION, &device, &got, &ctx))) return fail("D3D11CreateDevice failed");
    IDXGIFactory2* factory = nullptr;
    adapter->GetParent(IID_PPV_ARGS(&factory));
    DXGI_SWAP_CHAIN_DESC1 desc{};
    desc.Width = UINT(m->view_w);
    desc.Height = UINT(m->view_h);
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    desc.BufferCount = 2;
    desc.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    desc.Flags = DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT;
    IDXGISwapChain1* sc1 = nullptr;
    if (FAILED(factory->CreateSwapChainForHwnd(device, m->hwnd, &desc, nullptr, nullptr, &sc1))) return fail("CreateSwapChainForHwnd failed");
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
    if (!vsb || !psb) return fail("shader compile failed");
    ID3D11VertexShader* vs = nullptr;
    ID3D11PixelShader* ps = nullptr;
    device->CreateVertexShader(vsb->GetBufferPointer(), vsb->GetBufferSize(), nullptr, &vs);
    device->CreatePixelShader(psb->GetBufferPointer(), psb->GetBufferSize(), nullptr, &ps);
    D3D11_BUFFER_DESC cbd{sizeof(Params), D3D11_USAGE_DEFAULT, D3D11_BIND_CONSTANT_BUFFER, 0, 0, 0};
    ID3D11Buffer* cb = nullptr;
    device->CreateBuffer(&cbd, nullptr, &cb);

    // Text overlay (Direct2D on the same back buffer), on the first monitor only.
    ID2D1DeviceContext* d2dctx = nullptr;
    ID2D1SolidColorBrush *white = nullptr, *shade = nullptr;
    IDWriteTextFormat* formats[3] = {};
    if (m->index == 0) {
        ID2D1Factory1* d2d = nullptr;
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
        d2dctx->CreateSolidColorBrush(D2D1::ColorF(0, 0, 0, 0.8f), &shade);
        IDWriteFactory* dw = nullptr;
        DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED, __uuidof(IDWriteFactory), (IUnknown**)&dw);
        const float sizes[3] = {26.0f, 34.0f, 150.0f};
        for (int i = 0; i < 3; ++i)
            dw->CreateTextFormat(L"Segoe UI", nullptr, i == 2 ? DWRITE_FONT_WEIGHT_BOLD : DWRITE_FONT_WEIGHT_SEMI_BOLD,
                                 DWRITE_FONT_STYLE_NORMAL, DWRITE_FONT_STRETCH_NORMAL, sizes[i], L"en-us", &formats[i]);
        surface->Release(); d2ddev->Release(); dxgi_device->Release();
    }

    const float w = float(desc.Width), h = float(desc.Height);
    D3D11_VIEWPORT vp{0, 0, w, h, 0, 1};
    std::deque<Pending> pending;
    UINT last_stat = 0;
    while (!g_stop) {
        WaitForSingleObjectEx(waitable, 100, TRUE);
        std::shared_ptr<const Scene> scene = scene_get();
        Plan plan = plan_frame(*scene, *m, now_s());
        ctx->UpdateSubresource(cb, 0, nullptr, &plan.p, 0, 0);
        ctx->OMSetRenderTargets(1, &rtv, nullptr);
        ctx->RSSetViewports(1, &vp);
        ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
        ctx->VSSetShader(vs, nullptr, 0);
        ctx->PSSetShader(ps, nullptr, 0);
        ctx->PSSetConstantBuffers(0, 1, &cb);
        ctx->Draw(3, 0);
        if (d2dctx && plan.show_text && !scene->text.empty()) {
            d2dctx->BeginDraw();
            for (const TextBlock& t : scene->text) {
                float tw = std::min(t.w, w - t.x - 8), th = std::min(t.h, h - t.y - 8);
                d2dctx->FillRectangle(D2D1::RectF(t.x, t.y, t.x + tw, t.y + th), shade);
                d2dctx->DrawText(t.text.c_str(), UINT32(t.text.size()), formats[t.font],
                                 D2D1::RectF(t.x + 16, t.y + 8, t.x + tw - 12, t.y + th - 6), white);
            }
            d2dctx->EndDraw();
        }
        HRESULT hr = sc->Present(1, 0);
        if (FAILED(hr)) { fail("Present failed"); break; }
        if (hr == DXGI_STATUS_OCCLUDED) Sleep(1);  // minimised or hidden window: don't spin

        // Which refresh did that present reach? DXGI names it by present count and the vblank time.
        if (!g_windowed) {
            UINT id = 0;
            sc->GetLastPresentCount(&id);
            pending.push_back({id, plan.j});
            if (pending.size() > 16) pending.pop_front();
            DXGI_FRAME_STATISTICS fs{};
            if (SUCCEEDED(sc->GetFrameStatistics(&fs)) && fs.PresentCount != last_stat && fs.SyncQPCTime.QuadPart != 0) {
                last_stat = fs.PresentCount;
                // The sync time is the start of the blank (the vblank wake-up): its top row comes one blank later. The
                // nearest top row to that moment tolerates a timestamp up to half a refresh early or late.
                const Wave w0 = wave_of(*m, 0, 0);
                int64_t shown = w0.index_near(double(fs.SyncQPCTime.QuadPart) / qpc_freq() + w0.blank_s);
                while (!pending.empty() && pending.front().id < fs.PresentCount) {  // replaced before it was shown
                    m->late++;
                    pending.pop_front();
                }
                if (!pending.empty() && pending.front().id == fs.PresentCount) {
                    m->checked++;
                    if (shown > pending.front().j) m->late++;
                    else if (shown < pending.front().j) m->early++;
                    pending.pop_front();
                }
            }
        }
    }
    if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
}

}  // namespace seam
