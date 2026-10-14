// The real displays: adapter, monitors, one borderless window and one flip-model swapchain per monitor, the
// libplacebo D3D11 device. (Virtual displays for --headless are in perf1-headless.c.)
#include "perf1-app.h"
#include "perf1-gpu-share.h"
#include "perf1-picture.h"

#include <ctype.h>
#include <stdarg.h>
#include <math.h>

void to_utf8(const WCHAR *w, char *out, int size)
{
    if (!WideCharToMultiByte(CP_UTF8, 0, w, -1, out, size, NULL, NULL))
        out[0] = '\0';
}

void fail(struct monitor *m, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(m->error, sizeof(m->error), fmt, ap);
    va_end(ap);
    fprintf(stderr, "perf1: %s: %s\n", m->name, m->error);
    atomic_store(&A.stop, true);
}

// Physical size of Setup A's panels (EDID / ruler); other panels fall back to 0.25 mm per pixel.
static void panel_size_mm(int w_px, int h_px, double *w_mm, double *h_mm)
{
    if (w_px == 2560 && h_px == 1600) { *w_mm = 344.0; *h_mm = 215.0; }
    else if (w_px == 1920 && h_px == 1080) { *w_mm = 476.0; *h_mm = 268.0; }
    else { *w_mm = w_px * 0.25; *h_mm = h_px * 0.25; }
}

static LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    if ((msg == WM_KEYDOWN && wp == VK_ESCAPE) || msg == WM_CLOSE) {
        atomic_store(&A.stop, true);
        return 0;
    }
    if (msg == WM_SETCURSOR && !A.cfg.windowed) {
        SetCursor(NULL);
        return TRUE;
    }
    if (msg == WM_ERASEBKGND)
        return 1;
    return DefWindowProcW(hwnd, msg, wp, lp);
}

IDXGIAdapter1 *display_pick_adapter(void)
{
    IDXGIFactory1 *fac = NULL;
    if (FAILED(CreateDXGIFactory1(&IID_IDXGIFactory1, (void **) &fac)))
        return NULL;
    IDXGIAdapter1 *found = NULL;
    for (UINT i = 0; !found; i++) {
        IDXGIAdapter1 *ad = NULL;
        if (IDXGIFactory1_EnumAdapters1(fac, i, &ad) == DXGI_ERROR_NOT_FOUND)
            break;
        DXGI_ADAPTER_DESC1 d;
        IDXGIAdapter1_GetDesc1(ad, &d);
        char name[256];
        to_utf8(d.Description, name, sizeof(name));
        IDXGIOutput *o = NULL;
        bool has_outputs = IDXGIAdapter1_EnumOutputs(ad, 0, &o) != DXGI_ERROR_NOT_FOUND;
        if (o)
            IDXGIOutput_Release(o);
        bool name_ok = !A.cfg.adapter;
        if (A.cfg.adapter) {
            // case-insensitive substring match
            for (const char *h = name; *h && !name_ok; h++) {
                size_t k = 0;
                while (A.cfg.adapter[k] && h[k] && tolower((unsigned char) h[k]) == tolower((unsigned char) A.cfg.adapter[k]))
                    k++;
                name_ok = !A.cfg.adapter[k];
            }
        }
        if (has_outputs && name_ok && !(d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE))
            found = ad;
        else
            IDXGIAdapter1_Release(ad);
    }
    IDXGIFactory1_Release(fac);
    return found;
}

bool display_enumerate(IDXGIAdapter1 *adapter)
{
    for (UINT j = 0; A.nmon < MAX_MONITORS; j++) {
        IDXGIOutput *out = NULL;
        if (IDXGIAdapter1_EnumOutputs(adapter, j, &out) == DXGI_ERROR_NOT_FOUND)
            break;
        DXGI_OUTPUT_DESC od;
        IDXGIOutput_GetDesc(out, &od);
        if (!od.AttachedToDesktop) {
            IDXGIOutput_Release(out);
            continue;
        }
        struct monitor *m = &A.mon[A.nmon];
        memset(m, 0, sizeof(*m));
        m->slot = A.nmon++;
        m->index = (int) j;
        wcsncpy(m->device, od.DeviceName, 31);
        char full[64];
        to_utf8(od.DeviceName, full, sizeof(full));
        snprintf(m->name, sizeof(m->name), "%s", strncmp(full, "\\\\.\\", 4) ? full : full + 4);
        m->rect = od.DesktopCoordinates;
        m->output = out;
        panel_size_mm(m->rect.right - m->rect.left, m->rect.bottom - m->rect.top, &m->w_mm, &m->h_mm);
        vclock_init(&m->clock);
    }
    if (!A.nmon)
        return false;
    // Left to right by desktop position, gap_mm apart. The array order (and so the log's monitor number)
    // follows the DXGI output index, so only left_mm is derived from the sorted order.
    int order[MAX_MONITORS];
    for (int i = 0; i < A.nmon; i++)
        order[i] = i;
    for (int i = 1; i < A.nmon; i++)
        for (int k = i; k > 0 && A.mon[order[k]].rect.left < A.mon[order[k - 1]].rect.left; k--) {
            int t = order[k]; order[k] = order[k - 1]; order[k - 1] = t;
        }
    double x = 0;
    for (int i = 0; i < A.nmon; i++) {
        A.mon[order[i]].left_mm = x;
        x += A.mon[order[i]].w_mm + A.cfg.gap_mm;
    }
    return true;
}

bool display_create_windows(void)
{
    WNDCLASSW wc = {
        .lpfnWndProc = window_proc,
        .hInstance = GetModuleHandleW(NULL),
        .lpszClassName = L"panorama-perf1",
    };
    RegisterClassW(&wc);
    for (int i = 0; i < A.nmon; i++) {
        struct monitor *m = &A.mon[i];
        int mw = m->rect.right - m->rect.left, mh = m->rect.bottom - m->rect.top;
        int x = m->rect.left, y = m->rect.top, w = mw, h = mh;
        DWORD ex = WS_EX_TOPMOST;
        if (A.cfg.windowed) {
            // A smaller window centred on its monitor, same aspect, never topmost, never activated.
            w = (int) lround(mw * A.cfg.window_scale);
            h = (int) lround(mh * A.cfg.window_scale);
            x = m->rect.left + (mw - w) / 2;
            y = m->rect.top + (mh - h) / 2;
            ex = WS_EX_NOACTIVATE;
        }
        m->hwnd = CreateWindowExW(ex, wc.lpszClassName, L"panorama perf1", WS_POPUP, x, y, w, h,
                                  NULL, NULL, wc.hInstance, NULL);
        if (!m->hwnd) {
            fprintf(stderr, "perf1: CreateWindowEx failed for %s\n", m->name);
            return false;
        }
        ShowWindow(m->hwnd, A.cfg.windowed ? SW_SHOWNOACTIVATE : SW_SHOW);
        RECT rc;
        GetClientRect(m->hwnd, &rc);
        m->sc_w = rc.right - rc.left;
        m->sc_h = rc.bottom - rc.top;
    }
    if (!A.cfg.windowed)
        SetForegroundWindow(A.mon[0].hwnd);
    return true;
}

// Our own flip-model swapchain with the frame-latency waitable object and a maximum latency of one frame (or
// --frame-latency N, perf1-gpu-share.h), to be wrapped by libplacebo while Present1 stays ours. Returns an
// IDXGISwapChain reference for wrapping.
static IDXGISwapChain *create_swapchain(struct monitor *m)
{
    ID3D11Device *dev = A.gpu.d3d11->device;
    IDXGIDevice1 *dxdev = NULL;
    IDXGIAdapter *adapter = NULL;
    IDXGIFactory2 *fac = NULL;
    IDXGISwapChain2 *sc2 = NULL;
    IDXGISwapChain *out = NULL;

    if (FAILED(ID3D11Device_QueryInterface(dev, &IID_IDXGIDevice1, (void **) &dxdev)) ||
        FAILED(IDXGIDevice1_GetAdapter(dxdev, &adapter)) ||
        FAILED(IDXGIAdapter_GetParent(adapter, &IID_IDXGIFactory2, (void **) &fac)))
    {
        fail(m, "cannot reach the DXGI factory of the device");
        goto done;
    }

    DXGI_SWAP_CHAIN_DESC1 desc = {
        .Width = (UINT) m->sc_w,
        .Height = (UINT) m->sc_h,
        .Format = A.cfg.rgb10 ? DXGI_FORMAT_R10G10B10A2_UNORM : DXGI_FORMAT_B8G8R8A8_UNORM,
        .SampleDesc.Count = 1,
        .BufferUsage = DXGI_USAGE_SHADER_INPUT | DXGI_USAGE_RENDER_TARGET_OUTPUT | DXGI_USAGE_UNORDERED_ACCESS,
        .BufferCount = gpu_share_buffer_count(3), // 3, or N + 1 for a deeper --frame-latency
        .SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD,
        .Flags = DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT,
    };
    HRESULT hr = IDXGIFactory2_CreateSwapChainForHwnd(fac, (IUnknown *) dev, m->hwnd, &desc, NULL, NULL, &m->sc);
    if (FAILED(hr)) {
        fail(m, "CreateSwapChainForHwnd failed: 0x%08lx", (unsigned long) hr);
        goto done;
    }
    IDXGIFactory2_MakeWindowAssociation(fac, m->hwnd, DXGI_MWA_NO_WINDOW_CHANGES | DXGI_MWA_NO_ALT_ENTER);

    if (FAILED(IDXGISwapChain1_QueryInterface(m->sc, &IID_IDXGISwapChain2, (void **) &sc2))) {
        fail(m, "IDXGISwapChain2 not available");
        goto done;
    }
    gpu_share_frame_latency(sc2, m->name); // SetMaximumFrameLatency: 1, or --frame-latency N; recorded for the report
    m->waitable = IDXGISwapChain2_GetFrameLatencyWaitableObject(sc2);
    IDXGISwapChain1_QueryInterface(m->sc, &IID_IDXGISwapChainMedia, (void **) &m->media); // optional
    IDXGISwapChain1_QueryInterface(m->sc, &IID_IDXGISwapChain, (void **) &out);

done:
    if (sc2)
        IDXGISwapChain2_Release(sc2);
    if (fac)
        IDXGIFactory2_Release(fac);
    if (adapter)
        IDXGIAdapter_Release(adapter);
    if (dxdev)
        IDXGIDevice1_Release(dxdev);
    return out;
}

bool display_init_gpu(IDXGIAdapter1 *adapter)
{
    gpu_share_before_device(); // --gpu-priority high: the process's GPU scheduling class, before its first device
    A.gpu.log = pl_log_create(PL_API_VER, pl_log_params(.log_cb = pl_log_simple, .log_level = A.cfg.log_level));
    A.gpu.d3d11 = pl_d3d11_create(A.gpu.log, pl_d3d11_params(
        .adapter = (IDXGIAdapter *) adapter,
        .allow_software = false,
        .flags = A.cfg.decode == DECODE_D3D11VA ? D3D11_CREATE_DEVICE_VIDEO_SUPPORT : 0,
    ));
    if (!A.gpu.d3d11) {
        fprintf(stderr, "perf1: pl_d3d11_create failed\n");
        return false;
    }
    A.gpu.gpu = A.gpu.d3d11->gpu;

    for (int i = 0; i < A.nmon; i++) {
        struct monitor *m = &A.mon[i];
        IDXGISwapChain *own = create_swapchain(m);
        if (!own)
            return false;
        m->pls = pl_d3d11_create_swapchain(A.gpu.d3d11, pl_d3d11_swapchain_params(.swapchain = own));
        IDXGISwapChain_Release(own); // libplacebo holds its own reference
        if (!m->pls) {
            fail(m, "pl_d3d11_create_swapchain failed");
            return false;
        }
        int w = m->sc_w, h = m->sc_h;
        pl_swapchain_colorspace_hint(m->pls, &(struct pl_swapchain_colors) {0});
        if (!pl_swapchain_resize(m->pls, &w, &h)) {
            fail(m, "pl_swapchain_resize failed");
            return false;
        }
        render_init_monitor(m);
        if (!m->rr || !m->queue || m->error[0]) {
            fail(m, "renderer or queue creation failed");
            return false;
        }
    }
    gpu_share_after_device(A.gpu.d3d11->device, A.cfg.out_dir); // the device's GPU thread priority; prints + gpu-share.json
    return true;
}

// Switch one display's refresh for this run, held by this process (CDS_FULLSCREEN): Windows puts the saved
// mode back when the process exits or dies. Returns false if the driver refuses the rate.
bool display_switch_refresh(const wchar_t *device, int hz)
{
    DEVMODEW dm = { .dmSize = sizeof(dm) };
    if (!EnumDisplaySettingsW(device, ENUM_CURRENT_SETTINGS, &dm))
        return false;
    dm.dmDisplayFrequency = (DWORD) hz;
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY;
    if (ChangeDisplaySettingsExW(device, &dm, NULL, CDS_TEST, NULL) != DISP_CHANGE_SUCCESSFUL)
        return false;
    if (ChangeDisplaySettingsExW(device, &dm, NULL, CDS_FULLSCREEN, NULL) != DISP_CHANGE_SUCCESSFUL)
        return false;
    Sleep(3000); // let the monitor sync to the new timing
    return true;
}

void display_probe(IDXGIAdapter1 *adapter)
{
    DXGI_ADAPTER_DESC1 d;
    IDXGIAdapter1_GetDesc1(adapter, &d);
    char name[256];
    to_utf8(d.Description, name, sizeof(name));
    printf("adapter: %s\n", name);
    for (int i = 0; i < A.nmon; i++) {
        struct monitor *m = &A.mon[i];
        DEVMODEW dm = { .dmSize = sizeof(dm) };
        EnumDisplaySettingsW(m->device, ENUM_CURRENT_SETTINGS, &dm);
        printf("output %d %s: %ldx%ld at (%ld,%ld), %lu Hz, %.0f x %.0f mm\n", m->index, m->name,
               m->rect.right - m->rect.left, m->rect.bottom - m->rect.top, m->rect.left, m->rect.top,
               (unsigned long) dm.dmDisplayFrequency, m->w_mm, m->h_mm);
    }
}

// Releases the graphics side of every monitor and the device (queues and the decoder are gone by now).
void display_destroy(IDXGIAdapter1 *adapter)
{
    picture_write_logs();
    picture_destroy(); // its textures, timers and passes belong to the device that is about to go
    for (int i = 0; i < A.nmon; i++) {
        struct monitor *m = &A.mon[i];
        pl_renderer_destroy(&m->rr);
        pl_swapchain_destroy(&m->pls);
        if (m->media)
            IDXGISwapChainMedia_Release(m->media);
        if (m->waitable)
            CloseHandle(m->waitable);
        if (m->sc)
            IDXGISwapChain1_Release(m->sc);
        if (m->hwnd)
            DestroyWindow(m->hwnd);
        if (m->output)
            IDXGIOutput_Release(m->output);
    }
    pl_d3d11_destroy(&A.gpu.d3d11);
    pl_log_destroy(&A.gpu.log);
    if (adapter)
        IDXGIAdapter1_Release(adapter);
}
