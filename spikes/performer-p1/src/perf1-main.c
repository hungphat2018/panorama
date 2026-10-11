// perf1: one borderless window per monitor, one libplacebo-wrapped flip-model swapchain per window,
// one film clock locked to the master monitor's refresh count.
//
// Scheduling follows spikes/per-monitor-presenter/per-monitor-presenter.cpp:
//   - each monitor's refresh grid is fitted to IDXGIOutput::WaitForVBlank times (perf1-clock.c)
//   - the film clock counts the master monitor's refreshes (the slowest monitor): film time at master
//     refresh k is (k - first) * step, where step makes a film frame last a whole number of master
//     refreshes when the playback speed change is within 1 %
//   - "nearest": every other monitor shows, on each of its refreshes, the picture the master shows at the
//     last master refresh whose nearest follower refresh is not later than that refresh
//   - "own": each monitor switches on its first refresh at or after the film frame's time
//   - aimed start: the film starts on the master refresh whose nearest follower refresh is closest
//   - film frames are picked by timestamp: pl_queue (nearest-neighbour, no mixing) is asked for the
//     film time of the refresh being prepared
// Everything GPU-side (device, swapchains, renderers, queues) is used from one render thread that waits on
// every swapchain's frame-latency waitable object; libplacebo's D3D11 pl_gpu is not thread-safe.
//
// Picture: one 16:9 picture in the fold geometry (screens left to right by desktop position, gap_mm
// apart, tops level). Each monitor renders the part of the picture that falls on it with image.crop /
// target.crop, a single resample; the rest of the monitor is black.
//
// Logs per monitor (JSON, same fields as per-monitor-presenter plus render times) are read by
// spikes/per-monitor-presenter/analyze-per-monitor-pacing.py and analyze-perf1.py.
#define COBJMACROS
#include "perf1.h"

#include <avrt.h>
#include <ctype.h>
#include <stdarg.h>
#include <d3d11.h>
#include <dxgi1_3.h>
#include <math.h>
#include <timeapi.h>

#include <libavutil/avutil.h>

#define SETTLE_S 1.5          // refresh grids settle before the film starts
#define FILM_LEAD_S 0.5       // first film frame is at least this far ahead of the settle
#define WARMUP_TIMEOUT_NS 5000000000ULL

struct present_rec { uint32_t id; double call, ret, target; int64_t frame; };
struct stat_rec { uint32_t present_count, present_refresh, sync_refresh; double sync_qpc, polled; };

struct monitor {
    int index;                  // DXGI output index
    WCHAR device[32];
    char name[64];              // "DISPLAY1"
    RECT rect;                  // desktop coordinates, pixels
    double w_mm, h_mm, left_mm; // physical span along the shared path
    IDXGIOutput *output;
    HWND hwnd;
    int sc_w, sc_h;             // swapchain size in pixels
    struct vclock clock;

    IDXGISwapChain1 *sc;
    IDXGISwapChainMedia *media;
    HANDLE waitable;
    pl_swapchain pls;
    pl_renderer rr;
    pl_queue queue;
    struct pl_render_params params;
    uint64_t gpu_acc_ns;        // sum of libplacebo's per-pass GPU timers for the current render

    // this monitor's share of the picture: target pixels and the matching fraction of the source
    bool has_picture;
    int tx0, ty0, tx1, ty1;
    double u0, v0, u1, v1;

    uint64_t first_sig;
    int64_t last_frame;
    uint32_t last_stat;

    struct present_rec *presents; size_t n_presents, cap_presents;
    struct stat_rec *stats; size_t n_stats, cap_stats;
    int comp_modes[4];
    struct samples render_cpu, render_gpu, render_sync, lead;
    uint32_t queue_more, empty_mix, render_fail;
    char error[160];
};

static struct cfg g_cfg;
static struct monitor g_mon[MAX_MONITORS];
static int g_nmon;
static atomic_bool g_stop, g_render_done;
static struct decoder *g_dec;
static struct gpu_ctx g_gpu;

// Film clock (see the header comment).
static int g_master;
static int64_t g_first;
static int64_t g_hold;
static bool g_locked;
static double g_t0, g_step_s, g_frame_dur, g_master_period;

// Picture rectangle on the shared physical path, millimetres.
static double g_pic_x0, g_pic_x1, g_pic_y0, g_pic_y1;

// ---- helpers ------------------------------------------------------------------------------

static void to_utf8(const WCHAR *w, char *out, int size)
{
    if (!WideCharToMultiByte(CP_UTF8, 0, w, -1, out, size, NULL, NULL))
        out[0] = '\0';
}

static void fail(struct monitor *m, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(m->error, sizeof(m->error), fmt, ap);
    va_end(ap);
    fprintf(stderr, "perf1: %s: %s\n", m->name, m->error);
    atomic_store(&g_stop, true);
}

// Physical size of Setup A's panels (EDID / ruler); other panels fall back to 0.25 mm per pixel.
static void panel_size_mm(int w_px, int h_px, double *w_mm, double *h_mm)
{
    if (w_px == 2560 && h_px == 1600) { *w_mm = 344.0; *h_mm = 215.0; }
    else if (w_px == 1920 && h_px == 1080) { *w_mm = 476.0; *h_mm = 268.0; }
    else { *w_mm = w_px * 0.25; *h_mm = h_px * 0.25; }
}

static void info_cb(void *priv, const struct pl_render_info *info)
{
    struct monitor *m = priv;
    m->gpu_acc_ns += info->pass->last;
}

static const struct pl_render_params *preset_params(const char *name)
{
    if (!strcmp(name, "fast"))
        return &pl_render_fast_params;
    if (!strcmp(name, "hq"))
        return &pl_render_high_quality_params;
    return &pl_render_default_params;
}

// Pixel aspect ratio of a frame; the field only exists in libplacebo releases newer than 7.360.
static float frame_sar(const struct pl_frame *f)
{
#if PL_API_VER >= 375
    return f->pixel_aspect_ratio;
#else
    (void) f;
    return 0.0f;
#endif
}

// Index of the frame that is on screen at the mix's target time (last one not in the future).
static int front_index(const struct pl_frame_mix *mix)
{
    int idx = -1;
    for (int i = 0; i < mix->num_frames; i++) {
        if (mix->timestamps[i] > 0.0)
            break;
        idx = i;
    }
    return idx >= 0 ? idx : (mix->num_frames ? 0 : -1);
}

// ---- monitors, windows, swapchains ---------------------------------------------------------

static LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    if ((msg == WM_KEYDOWN && wp == VK_ESCAPE) || msg == WM_CLOSE) {
        atomic_store(&g_stop, true);
        return 0;
    }
    if (msg == WM_SETCURSOR && !g_cfg.windowed) {
        SetCursor(NULL);
        return TRUE;
    }
    if (msg == WM_ERASEBKGND)
        return 1;
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static IDXGIAdapter1 *pick_adapter(void)
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
        bool name_ok = !g_cfg.adapter;
        if (g_cfg.adapter) {
            // case-insensitive substring match
            for (const char *h = name; *h && !name_ok; h++) {
                size_t k = 0;
                while (g_cfg.adapter[k] && h[k] && tolower((unsigned char) h[k]) == tolower((unsigned char) g_cfg.adapter[k]))
                    k++;
                name_ok = !g_cfg.adapter[k];
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

static bool enumerate_monitors(IDXGIAdapter1 *adapter)
{
    for (UINT j = 0; g_nmon < MAX_MONITORS; j++) {
        IDXGIOutput *out = NULL;
        if (IDXGIAdapter1_EnumOutputs(adapter, j, &out) == DXGI_ERROR_NOT_FOUND)
            break;
        DXGI_OUTPUT_DESC od;
        IDXGIOutput_GetDesc(out, &od);
        if (!od.AttachedToDesktop) {
            IDXGIOutput_Release(out);
            continue;
        }
        struct monitor *m = &g_mon[g_nmon++];
        memset(m, 0, sizeof(*m));
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
    if (!g_nmon)
        return false;
    // Left to right by desktop position, gap_mm apart. The array order (and so the log's monitor number)
    // follows the DXGI output index, so only left_mm is derived from the sorted order.
    int order[MAX_MONITORS];
    for (int i = 0; i < g_nmon; i++)
        order[i] = i;
    for (int i = 1; i < g_nmon; i++)
        for (int k = i; k > 0 && g_mon[order[k]].rect.left < g_mon[order[k - 1]].rect.left; k--) {
            int t = order[k]; order[k] = order[k - 1]; order[k - 1] = t;
        }
    double x = 0;
    for (int i = 0; i < g_nmon; i++) {
        g_mon[order[i]].left_mm = x;
        x += g_mon[order[i]].w_mm + g_cfg.gap_mm;
    }
    return true;
}

static bool create_windows(void)
{
    WNDCLASSW wc = {
        .lpfnWndProc = window_proc,
        .hInstance = GetModuleHandleW(NULL),
        .lpszClassName = L"panorama-perf1",
    };
    RegisterClassW(&wc);
    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
        int mw = m->rect.right - m->rect.left, mh = m->rect.bottom - m->rect.top;
        int x = m->rect.left, y = m->rect.top, w = mw, h = mh;
        DWORD ex = WS_EX_TOPMOST;
        if (g_cfg.windowed) {
            // A smaller window centred on its monitor, same aspect, never topmost, never activated.
            w = (int) lround(mw * g_cfg.window_scale);
            h = (int) lround(mh * g_cfg.window_scale);
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
        ShowWindow(m->hwnd, g_cfg.windowed ? SW_SHOWNOACTIVATE : SW_SHOW);
        RECT rc;
        GetClientRect(m->hwnd, &rc);
        m->sc_w = rc.right - rc.left;
        m->sc_h = rc.bottom - rc.top;
    }
    if (!g_cfg.windowed)
        SetForegroundWindow(g_mon[0].hwnd);
    return true;
}

// Our own flip-model swapchain with the frame-latency waitable object and a maximum latency of one frame,
// to be wrapped by libplacebo while Present1 stays ours. Returns an IDXGISwapChain reference for wrapping.
static IDXGISwapChain *create_swapchain(struct monitor *m)
{
    ID3D11Device *dev = g_gpu.d3d11->device;
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
        .Format = g_cfg.rgb10 ? DXGI_FORMAT_R10G10B10A2_UNORM : DXGI_FORMAT_B8G8R8A8_UNORM,
        .SampleDesc.Count = 1,
        .BufferUsage = DXGI_USAGE_SHADER_INPUT | DXGI_USAGE_RENDER_TARGET_OUTPUT | DXGI_USAGE_UNORDERED_ACCESS,
        .BufferCount = 3,
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
    IDXGISwapChain2_SetMaximumFrameLatency(sc2, 1);
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

static bool init_gpu(IDXGIAdapter1 *adapter)
{
    g_gpu.log = pl_log_create(PL_API_VER, pl_log_params(.log_cb = pl_log_simple, .log_level = g_cfg.log_level));
    g_gpu.d3d11 = pl_d3d11_create(g_gpu.log, pl_d3d11_params(
        .adapter = (IDXGIAdapter *) adapter,
        .allow_software = false,
        .flags = g_cfg.decode == DECODE_D3D11VA ? D3D11_CREATE_DEVICE_VIDEO_SUPPORT : 0,
    ));
    if (!g_gpu.d3d11) {
        fprintf(stderr, "perf1: pl_d3d11_create failed\n");
        return false;
    }
    g_gpu.gpu = g_gpu.d3d11->gpu;

    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
        IDXGISwapChain *own = create_swapchain(m);
        if (!own)
            return false;
        m->pls = pl_d3d11_create_swapchain(g_gpu.d3d11, pl_d3d11_swapchain_params(.swapchain = own));
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
        m->params = *preset_params(g_cfg.preset);
        m->params.info_callback = info_cb;
        m->params.info_priv = m;
        m->rr = pl_renderer_create(g_gpu.log, g_gpu.gpu);
        m->queue = pl_queue_create(g_gpu.gpu);
        if (!m->rr || !m->queue) {
            fail(m, "renderer or queue creation failed");
            return false;
        }
    }
    return true;
}

// ---- picture geometry ----------------------------------------------------------------------

// Lays out the picture on the shared physical path and works out each monitor's share: the target
// rectangle in swapchain pixels (snapped to whole pixels) and the matching fraction of the picture.
static void layout_picture(double aspect)
{
    double min_h = g_mon[0].h_mm, left = g_mon[0].left_mm, right = g_mon[0].left_mm + g_mon[0].w_mm;
    for (int i = 1; i < g_nmon; i++) {
        if (g_mon[i].h_mm < min_h) min_h = g_mon[i].h_mm;
        if (g_mon[i].left_mm < left) left = g_mon[i].left_mm;
        if (g_mon[i].left_mm + g_mon[i].w_mm > right) right = g_mon[i].left_mm + g_mon[i].w_mm;
    }
    double h = g_cfg.picture_height_mm > 0 ? g_cfg.picture_height_mm : min_h;
    double cx;
    if (!isnan(g_cfg.picture_center_mm)) {
        cx = g_cfg.picture_center_mm;
    } else if (g_nmon >= 2) {
        // middle of the gap between the two leftmost monitors
        int a = 0, b = -1;
        for (int i = 1; i < g_nmon; i++)
            if (g_mon[i].left_mm < g_mon[a].left_mm) a = i;
        for (int i = 0; i < g_nmon; i++)
            if (i != a && (b < 0 || g_mon[i].left_mm < g_mon[b].left_mm)) b = i;
        cx = 0.5 * (g_mon[a].left_mm + g_mon[a].w_mm + g_mon[b].left_mm);
    } else {
        cx = 0.5 * (left + right);
    }
    g_pic_x0 = cx - 0.5 * h * aspect;
    g_pic_x1 = cx + 0.5 * h * aspect;
    g_pic_y0 = 0.0; // tops level
    g_pic_y1 = h;

    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
        double ix0 = fmax(m->left_mm, g_pic_x0), ix1 = fmin(m->left_mm + m->w_mm, g_pic_x1);
        double iy0 = fmax(0.0, g_pic_y0), iy1 = fmin(m->h_mm, g_pic_y1);
        m->has_picture = false;
        if (ix1 <= ix0 || iy1 <= iy0)
            continue;
        double sx = m->sc_w / m->w_mm, sy = m->sc_h / m->h_mm; // pixels per millimetre
        m->tx0 = (int) lround((ix0 - m->left_mm) * sx);
        m->tx1 = (int) lround((ix1 - m->left_mm) * sx);
        m->ty0 = (int) lround(iy0 * sy);
        m->ty1 = (int) lround(iy1 * sy);
        if (m->tx1 > m->sc_w) m->tx1 = m->sc_w;
        if (m->ty1 > m->sc_h) m->ty1 = m->sc_h;
        if (m->tx1 <= m->tx0 || m->ty1 <= m->ty0)
            continue;
        // the snapped pixel edges back to the picture's own coordinates (one resample, exact mapping)
        double pw = g_pic_x1 - g_pic_x0, ph = g_pic_y1 - g_pic_y0;
        m->u0 = (m->left_mm + m->tx0 / sx - g_pic_x0) / pw;
        m->u1 = (m->left_mm + m->tx1 / sx - g_pic_x0) / pw;
        m->v0 = (m->ty0 / sy - g_pic_y0) / ph;
        m->v1 = (m->ty1 / sy - g_pic_y0) / ph;
        m->has_picture = true;
    }
}

// ---- one refresh of one monitor -------------------------------------------------------------

// Film-time position handed to pl_queue for a refresh of monitor m at time `target`. pl_queue picks the
// frame nearest to the position, so the position is shifted back by half a frame (nearest becomes "last frame
// not after the film time") and forward by half a step (the frame whose timestamp is within half a master
// refresh of the refresh counts as due; container timestamps are rounded to milliseconds).
static double queue_position(struct monitor *m, double target, int64_t *master_refresh_out)
{
    double film_t, slack;
    int64_t j = 0;
    if (g_cfg.sched == SCHED_NEAREST) {
        struct monitor *mm = &g_mon[g_master];
        if (m == mm) {
            j = vclock_index_near(&mm->clock, target);
        } else {
            // largest master refresh whose nearest refresh on this monitor is not later than `target`
            int64_t guess = vclock_index_near(&mm->clock, target);
            j = guess - 2;
            for (int64_t k = guess - 2; k <= guess + 2; k++)
                if (vclock_nearest(&m->clock, vclock_time_of(&mm->clock, k)) <= target + 1e-4)
                    j = k;
        }
        film_t = (double) (j - g_first) * g_step_s;
        slack = 0.5 * g_step_s;
    } else {
        // own: real film time at this refresh, advanced at the same rate as the master-locked clock
        film_t = (target - g_t0) * (g_step_s / g_master_period);
        slack = 0.5e-3;
        j = 0;
    }
    if (master_refresh_out)
        *master_refresh_out = j;
    double pos = film_t + slack - 0.5 * g_frame_dur;
    return pos > 0.0 ? pos : 0.0;
}

// Renders `mix` into the swapchain frame: this monitor's part of the picture, black elsewhere.
static bool render_picture(struct monitor *m, const struct pl_frame_mix *mix, int front,
                           struct pl_frame *target)
{
    if (front < 0 || !m->has_picture) {
        pl_frame_clear(g_gpu.gpu, target, (const float[3]) {0});
        return true;
    }
    struct pl_frame crops[8];
    const struct pl_frame *ptrs[8];
    int n = mix->num_frames < 8 ? mix->num_frames : 8;
    for (int i = 0; i < n; i++) {
        crops[i] = *mix->frames[i];
        pl_rect2df c = mix->frames[i]->crop;
        float dx = c.x1 - c.x0, dy = c.y1 - c.y0;
        crops[i].crop = (pl_rect2df) {
            c.x0 + (float) m->u0 * dx, c.y0 + (float) m->v0 * dy,
            c.x0 + (float) m->u1 * dx, c.y0 + (float) m->v1 * dy,
        };
        ptrs[i] = &crops[i];
    }
    struct pl_frame_mix sub = *mix;
    sub.num_frames = n;
    sub.frames = ptrs;
    target->crop = (pl_rect2df) { (float) m->tx0, (float) m->ty0, (float) m->tx1, (float) m->ty1 };
    return pl_render_image_mix(m->rr, &sub, target, &m->params);
}

static void push_present(struct monitor *m, const struct present_rec *p)
{
    if (m->n_presents == m->cap_presents) {
        size_t cap = m->cap_presents ? m->cap_presents * 2 : 8192;
        struct present_rec *n = realloc(m->presents, cap * sizeof(*n));
        if (!n)
            return;
        m->presents = n;
        m->cap_presents = cap;
    }
    m->presents[m->n_presents++] = *p;
}

static void push_stat(struct monitor *m, const struct stat_rec *s)
{
    if (m->n_stats == m->cap_stats) {
        size_t cap = m->cap_stats ? m->cap_stats * 2 : 8192;
        struct stat_rec *n = realloc(m->stats, cap * sizeof(*n));
        if (!n)
            return;
        m->stats = n;
        m->cap_stats = cap;
    }
    m->n_stats++;
    m->stats[m->n_stats - 1] = *s;
}

static void service_monitor(struct monitor *m)
{
    // The present lands on this monitor's next refresh; the picture is chosen for that moment.
    double target = vclock_next_after(&m->clock, now_s() + 0.0005);
    double pos = queue_position(m, target, NULL);

    struct pl_queue_params qp = *pl_queue_params(
        .interpolation_threshold = 1e9, // never mix frames: a monitor shows one film frame per refresh
        .drift_compensation = 0.0,      // the film clock is exact; no hidden timestamp shifts
        .timeout = 0,                   // never wait for the decoder inside the refresh loop
        .radius = pl_frame_mix_radius(&m->params),
        .pts = pos,
    );
    struct pl_frame_mix mix;
    switch (pl_queue_update(m->queue, &mix, &qp)) {
    case PL_QUEUE_ERR:
        fail(m, "pl_queue_update failed mid-run");
        return;
    case PL_QUEUE_EOF:
        atomic_store(&g_stop, true); // end of file: stop every window together
        return;
    case PL_QUEUE_MORE:
        m->queue_more++; // the decoder is behind: the mix may be stale or empty
        break;
    case PL_QUEUE_OK:
        break;
    }
    int front = front_index(&mix);
    int64_t frame = m->last_frame;
    if (front >= 0)
        frame = (int64_t) (mix.signatures[front] - m->first_sig);
    else
        m->empty_mix++;
    m->last_frame = frame;

    struct pl_swapchain_frame sf;
    if (!pl_swapchain_start_frame(m->pls, &sf)) {
        fail(m, "pl_swapchain_start_frame failed mid-run");
        return;
    }
    struct pl_frame tgt;
    pl_frame_from_swapchain(&tgt, &sf);

    m->gpu_acc_ns = 0;
    double t_r0 = now_ms();
    bool ok = render_picture(m, &mix, front, &tgt);
    double t_r1 = now_ms();
    double sync_ms = 0.0;
    if (ok && g_cfg.sync_render) {
        pl_gpu_finish(g_gpu.gpu);
        sync_ms = now_ms() - t_r1;
    }
    if (!ok) {
        // keep the frame protocol: a started frame is always submitted
        m->render_fail++;
        pl_frame_clear(g_gpu.gpu, &tgt, (const float[3]) {0});
    }
    if (!pl_swapchain_submit_frame(m->pls)) {
        fail(m, "pl_swapchain_submit_frame failed mid-run");
        return;
    }

    struct present_rec p = {0};
    p.call = now_s();
    DXGI_PRESENT_PARAMETERS pp = {0};
    HRESULT hr = IDXGISwapChain1_Present1(m->sc, 1, 0, &pp); // one refresh per present
    p.ret = now_s();
    p.target = target;
    p.frame = frame;
    IDXGISwapChain1_GetLastPresentCount(m->sc, &p.id);
    if (FAILED(hr)) {
        fail(m, "Present1 failed: 0x%08lx", (unsigned long) hr);
        return;
    }
    push_present(m, &p);

    samples_push(&m->render_cpu, t_r1 - t_r0);
    samples_push(&m->render_gpu, (double) m->gpu_acc_ns / 1e6);
    if (g_cfg.sync_render)
        samples_push(&m->render_sync, sync_ms);
    samples_push(&m->lead, (target - p.call) * 1e3);

    DXGI_FRAME_STATISTICS fs;
    if (SUCCEEDED(IDXGISwapChain1_GetFrameStatistics(m->sc, &fs)) && fs.PresentCount != m->last_stat) {
        m->last_stat = fs.PresentCount;
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        struct stat_rec s = { fs.PresentCount, fs.PresentRefreshCount, fs.SyncRefreshCount,
                              (double) fs.SyncQPCTime.QuadPart / (double) f.QuadPart, now_s() };
        push_stat(m, &s);
    }
    if (m->media) {
        DXGI_FRAME_STATISTICS_MEDIA fm;
        if (SUCCEEDED(IDXGISwapChainMedia_GetFrameStatisticsMedia(m->media, &fm)) &&
            (unsigned) fm.CompositionMode < 4)
            m->comp_modes[fm.CompositionMode]++;
    }
}

static DWORD WINAPI render_thread(LPVOID arg)
{
    DWORD task = 0;
    HANDLE mmcss = AvSetMmThreadCharacteristicsW(L"Games", &task);
    double end = g_t0 + g_cfg.seconds;
    int start = 0;

    while (!atomic_load(&g_stop) && now_s() < end) {
        // Rotating order, so a monitor that is always ready cannot starve the other.
        HANDLE h[MAX_MONITORS];
        int map[MAX_MONITORS];
        for (int i = 0; i < g_nmon; i++) {
            map[i] = (start + i) % g_nmon;
            h[i] = g_mon[map[i]].waitable;
        }
        DWORD r = WaitForMultipleObjectsEx((DWORD) g_nmon, h, FALSE, 100, TRUE);
        if (r < WAIT_OBJECT_0 + (DWORD) g_nmon) {
            int mi = map[r - WAIT_OBJECT_0];
            service_monitor(&g_mon[mi]);
            start = (mi + 1) % g_nmon;
        } else if (r != WAIT_TIMEOUT && r != WAIT_IO_COMPLETION) {
            fprintf(stderr, "perf1: waiting on the swapchains failed: %lu\n", (unsigned long) r);
            break;
        }
    }
    atomic_store(&g_stop, true);
    if (mmcss)
        AvRevertMmThreadCharacteristics(mmcss);
    atomic_store(&g_render_done, true);
    return 0;
}

static DWORD WINAPI vblank_thread(LPVOID arg)
{
    struct monitor *m = arg;
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
    while (!atomic_load(&g_stop)) {
        if (FAILED(IDXGIOutput_WaitForVBlank(m->output))) {
            Sleep(1);
            continue;
        }
        vclock_add(&m->clock, now_s());
    }
    return 0;
}

// ---- start-up: first frames, film clock -------------------------------------------------------

// Fully renders frame 0 on every monitor and presents it once, so shader compilation and the first
// texture imports are not counted against the film. Returns the picture's aspect ratio.
static bool warm_up(double *aspect_out)
{
    double aspect = 0.0;
    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
        struct pl_queue_params qp = *pl_queue_params(
            .interpolation_threshold = 1e9,
            .drift_compensation = 0.0,
            .timeout = WARMUP_TIMEOUT_NS,
            .radius = pl_frame_mix_radius(&m->params),
            .pts = 0.0,
        );
        struct pl_frame_mix mix;
        if (pl_queue_update(m->queue, &mix, &qp) != PL_QUEUE_OK || front_index(&mix) < 0) {
            fail(m, "no frame decoded within %.0f s", WARMUP_TIMEOUT_NS / 1e9);
            return false;
        }
        int front = front_index(&mix);
        m->first_sig = mix.signatures[front];
        m->last_frame = 0;
        if (!aspect) {
            const struct pl_frame *f = mix.frames[front];
            double w = fabs(f->crop.x1 - f->crop.x0), h = fabs(f->crop.y1 - f->crop.y0);
            float sar = frame_sar(f);
            aspect = h > 0 ? w / h * (sar > 0 ? sar : 1.0) : 16.0 / 9.0;
            *aspect_out = aspect;
            layout_picture(aspect);
        }

        struct pl_swapchain_frame sf;
        if (!pl_swapchain_start_frame(m->pls, &sf)) {
            fail(m, "first pl_swapchain_start_frame failed");
            return false;
        }
        struct pl_frame tgt;
        pl_frame_from_swapchain(&tgt, &sf);
        if (sf.color_repr.alpha == PL_ALPHA_NONE)
            m->params.background_transparency = 0.0;
        bool ok = render_picture(m, &mix, front, &tgt);
        if (!ok)
            pl_frame_clear(g_gpu.gpu, &tgt, (const float[3]) {0}); // a started frame is always submitted
        if (!pl_swapchain_submit_frame(m->pls)) {
            fail(m, "first submit failed");
            return false;
        }
        if (!ok) {
            fail(m, "first render failed");
            return false;
        }
        pl_gpu_finish(g_gpu.gpu);
        DXGI_PRESENT_PARAMETERS pp = {0};
        IDXGISwapChain1_Present1(m->sc, 1, 0, &pp);
    }
    return true;
}

static void choose_film_start(double stream_fps)
{
    g_master = g_cfg.master;
    if (g_master < 0 || g_master >= g_nmon) {
        g_master = 0; // the slowest monitor
        for (int i = 1; i < g_nmon; i++)
            if (vclock_period(&g_mon[i].clock) > vclock_period(&g_mon[g_master].clock))
                g_master = i;
    }
    struct vclock *mc = &g_mon[g_master].clock;
    g_master_period = vclock_period(mc);
    g_frame_dur = stream_fps > 0 ? 1.0 / stream_fps : 1.0 / 24.0;

    g_hold = (int64_t) llround(g_frame_dur / g_master_period);
    if (g_hold < 1)
        g_hold = 1;
    double lock = g_frame_dur / ((double) g_hold * g_master_period); // playback speed that makes it exact
    g_locked = g_cfg.lock_speed && fabs(lock - 1.0) <= 0.01;
    g_step_s = g_locked ? g_frame_dur / (double) g_hold : g_master_period;
    printf("film %.4f fps; master %s %.4f Hz; %s (speed %.4f if locked, hold %lld refreshes)\n", stream_fps,
           g_mon[g_master].name, 1.0 / g_master_period, g_locked ? "speed LOCKED to the master's refresh count" : "speed NOT locked (plays at 1.0)",
           lock, (long long) g_hold);

    g_first = vclock_index_near(mc, vclock_next_after(mc, now_s() + FILM_LEAD_S));
    if (g_cfg.sched == SCHED_NEAREST && g_nmon > 1) {
        // Master refreshes fall into a few alignment classes against the follower's grid; start the film on
        // the candidate whose nearest follower refresh is closest. Later frame starts (hold master refreshes
        // apart) keep that class while the clocks keep their ratio.
        struct monitor *fol = &g_mon[g_master == 0 ? 1 : 0];
        int64_t base = g_first;
        double best = 1e9;
        for (int64_t k = base; k < base + 24; k++) {
            double t = vclock_time_of(mc, k);
            double off = fabs(vclock_nearest(&fol->clock, t) - t);
            if (off < best - 1e-6) {
                best = off;
                g_first = k;
            }
        }
        printf("film starts %lld master refreshes later: predicted follower offset %.3f ms\n",
               (long long) (g_first - base), best * 1e3);
    }
    g_t0 = vclock_time_of(mc, g_first);
}

// ---- display refresh switch --------------------------------------------------------------------

// Switch one display's refresh for this run, held by this process (CDS_FULLSCREEN): Windows puts the saved
// mode back when the process exits or dies. Returns false if the driver refuses the rate.
static bool switch_refresh(const wchar_t *device, int hz)
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

// ---- logs ----------------------------------------------------------------------------------------

static void j_array(FILE *f, const char *name, const struct samples *s)
{
    fprintf(f, ",\"%s\":[", name);
    for (size_t i = 0; i < s->n; i++)
        fprintf(f, "%s%.4f", i ? "," : "", s->v[i]);
    fputc(']', f);
}

static void write_log(struct monitor *m, const struct decoder_info *di)
{
    const char *mode = g_cfg.sched == SCHED_NEAREST ? "nearest" : "own";
    char path[1024];
    snprintf(path, sizeof(path), "%s/monitor-%d-%s.json", g_cfg.out_dir, m->index, mode);
    FILE *f = fopen(path, "w");
    if (!f) {
        fprintf(stderr, "perf1: cannot write %s\n", path);
        return;
    }
    size_t nv = 0;
    double *vb = vclock_copy_log(&m->clock, &nv);

    fprintf(f, "{\"monitor\":%d,\"name\":\"%s\",\"rect\":[%ld,%ld,%ld,%ld],\"width_mm\":%.1f,\"left_mm\":%.1f,",
            m->index, m->name, m->rect.left, m->rect.top, m->rect.right, m->rect.bottom, m->w_mm, m->left_mm);
    fprintf(f, "\"mode\":\"%s\",\"fps\":%.6f,\"t0\":%.7f,\"master\":%d,\"error\":\"", mode,
            g_locked ? 1.0 / (g_step_s * (double) g_hold) : 1.0 / g_frame_dur, g_t0, g_mon[g_master].index);
    for (const char *c = m->error; *c; c++)
        if ((unsigned char) *c >= 0x20 && *c != '"' && *c != '\\')
            fputc(*c, f);
    fprintf(f, "\",\"composition_modes\":{");
    bool first = true;
    for (int i = 0; i < 4; i++)
        if (m->comp_modes[i]) {
            fprintf(f, "%s\"%d\":%d", first ? "" : ",", i, m->comp_modes[i]);
            first = false;
        }
    fprintf(f, "},\"vblanks\":[");
    for (size_t i = 0; i < nv; i++)
        fprintf(f, "%s%.7f", i ? "," : "", vb[i]);
    fprintf(f, "],\"presents\":[");
    for (size_t i = 0; i < m->n_presents; i++) {
        const struct present_rec *p = &m->presents[i];
        fprintf(f, "%s[%u,%.7f,%.7f,%.7f,%lld]", i ? "," : "", p->id, p->call, p->ret, p->target, (long long) p->frame);
    }
    fprintf(f, "],\"stats\":[");
    for (size_t i = 0; i < m->n_stats; i++) {
        const struct stat_rec *s = &m->stats[i];
        fprintf(f, "%s[%u,%u,%u,%.7f,%.7f]", i ? "," : "", s->present_count, s->present_refresh, s->sync_refresh,
                s->sync_qpc, s->polled);
    }
    fprintf(f, "]");

    // Extras for analyze-perf1.py (one entry per present, in present order): CPU time of the render call,
    // libplacebo's GPU pass timers, time left before the target refresh when Present1 was called.
    j_array(f, "render_cpu_ms", &m->render_cpu);
    j_array(f, "render_gpu_ms", &m->render_gpu);
    j_array(f, "lead_ms", &m->lead);
    if (m->render_sync.n)
        j_array(f, "render_sync_ms", &m->render_sync);
    fprintf(f, ",\"perf1\":{\"queue_more\":%u,\"empty_mix\":%u,\"render_failed\":%u,\"windowed\":%s,"
               "\"swapchain\":[%d,%d],\"target_rect\":[%d,%d,%d,%d],\"has_picture\":%s,\"u\":[%.5f,%.5f],\"v\":[%.5f,%.5f],"
               "\"picture_mm\":[%.2f,%.2f,%.2f,%.2f],\"locked\":%s,\"hold\":%lld,\"step_s\":%.9f,\"frame_dur_s\":%.9f,"
               "\"decoded\":%u,\"mapped\":%u,\"dropped\":%u,\"codec\":\"%s\",\"hw_fmt\":\"%s\",\"preset\":\"%s\",\"format\":\"%s\"}",
            m->queue_more, m->empty_mix, m->render_fail, g_cfg.windowed ? "true" : "false", m->sc_w, m->sc_h,
            m->tx0, m->ty0, m->tx1, m->ty1, m->has_picture ? "true" : "false", m->u0, m->u1, m->v0, m->v1,
            g_pic_x0, g_pic_y0, g_pic_x1, g_pic_y1, g_locked ? "true" : "false", (long long) g_hold, g_step_s, g_frame_dur,
            di->decoded, di->mapped, di->dropped, di->codec, di->hw_fmt, g_cfg.preset, g_cfg.rgb10 ? "rgb10a2" : "bgra8");
    fprintf(f, "}\n");
    fclose(f);
    free(vb);

    struct summary cpu, gpu;
    summarize(m->render_cpu.v, m->render_cpu.n, &cpu);
    summarize(m->render_gpu.v, m->render_gpu.n, &gpu);
    printf("%s: %zu presents, render cpu p50 %.3f / p99 %.3f ms, gpu p50 %.3f / p99 %.3f ms, queue_more %u, empty %u%s%s\n",
           m->name, m->n_presents, cpu.p50, cpu.p99, gpu.p50, gpu.p99, m->queue_more, m->empty_mix,
           m->error[0] ? ", ERROR " : "", m->error);
    printf("wrote %s\n", path);
}

// ---- command line --------------------------------------------------------------------------------

static void usage(const char *argv0)
{
    fprintf(stderr,
        "Usage: %s --file PATH [--windowed [--window-scale 0.5]] [--seconds N] [--mode nearest|own]\n"
        "          [--decode d3d11va|sw] [--preset fast|default|hq] [--master OUTPUT_INDEX] [--no-lock]\n"
        "          [--refresh DISPLAYn=HZ] [--gap-mm 20] [--picture-height-mm N] [--picture-center-mm N]\n"
        "          [--format rgb10a2|bgra8] [--sync-render] [--adapter SUBSTR] [--out DIR] [--log-level 0..6] [--probe]\n"
        "  --windowed       smoke test: half-size borderless windows, not topmost, no focus steal, no display change\n"
        "  --seconds N      playback time after the film clock starts (default 20); Esc stops every window early\n"
        "  --refresh        switch that display for the run with CDS_FULLSCREEN (restored when the process ends)\n"
        "  --no-lock        play at 1.0 speed even when the master's refresh rate would allow a 1 %% lock\n"
        "  --probe          print the outputs of the adapter that drives the screens, then exit\n",
        argv0);
}

static bool parse_args(int argc, char **argv)
{
    g_cfg = (struct cfg) {
        .decode = DECODE_D3D11VA,
        .preset = "default",
        .seconds = 20.0,
        .window_scale = 0.5,
        .sched = SCHED_NEAREST,
        .master = -1,
        .lock_speed = true,
        .rgb10 = true,
        .gap_mm = 20.0,
        .picture_center_mm = NAN,
        .out_dir = ".",
        .log_level = PL_LOG_WARN,
    };
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--file") && v) { g_cfg.file = v; i++; }
        else if (!strcmp(a, "--windowed")) g_cfg.windowed = true;
        else if (!strcmp(a, "--window-scale") && v) { g_cfg.window_scale = atof(v); i++; }
        else if (!strcmp(a, "--seconds") && v) { g_cfg.seconds = atof(v); i++; }
        else if (!strcmp(a, "--mode") && v) {
            if (!strcmp(v, "nearest")) g_cfg.sched = SCHED_NEAREST;
            else if (!strcmp(v, "own")) g_cfg.sched = SCHED_OWN;
            else return false;
            i++;
        } else if (!strcmp(a, "--decode") && v) {
            if (!strcmp(v, "d3d11va")) g_cfg.decode = DECODE_D3D11VA;
            else if (!strcmp(v, "sw")) g_cfg.decode = DECODE_SW;
            else return false;
            i++;
        }
        else if (!strcmp(a, "--preset") && v) { g_cfg.preset = v; i++; }
        else if (!strcmp(a, "--master") && v) { g_cfg.master = atoi(v); i++; }
        else if (!strcmp(a, "--no-lock")) g_cfg.lock_speed = false;
        else if (!strcmp(a, "--sync-render")) g_cfg.sync_render = true;
        else if (!strcmp(a, "--format") && v) {
            if (!strcmp(v, "rgb10a2")) g_cfg.rgb10 = true;
            else if (!strcmp(v, "bgra8")) g_cfg.rgb10 = false;
            else return false;
            i++;
        }
        else if (!strcmp(a, "--gap-mm") && v) { g_cfg.gap_mm = atof(v); i++; }
        else if (!strcmp(a, "--picture-height-mm") && v) { g_cfg.picture_height_mm = atof(v); i++; }
        else if (!strcmp(a, "--picture-center-mm") && v) { g_cfg.picture_center_mm = atof(v); i++; }
        else if (!strcmp(a, "--adapter") && v) { g_cfg.adapter = v; i++; }
        else if (!strcmp(a, "--out") && v) { g_cfg.out_dir = v; i++; }
        else if (!strcmp(a, "--log-level") && v) { g_cfg.log_level = atoi(v); i++; }
        else if (!strcmp(a, "--probe")) g_cfg.probe_only = true;
        else if (!strcmp(a, "--refresh") && v) {
            // DISPLAYn=HZ
            const char *eq = strchr(v, '=');
            if (!eq || eq == v)
                return false;
            char dev[32];
            snprintf(dev, sizeof(dev), "%s%.*s", strncmp(v, "\\\\.\\", 4) ? "\\\\.\\" : "", (int) (eq - v), v);
            MultiByteToWideChar(CP_UTF8, 0, dev, -1, g_cfg.refresh_device, 31);
            g_cfg.refresh_hz = atoi(eq + 1);
            if (g_cfg.refresh_hz <= 0)
                return false;
            i++;
        }
        else return false;
    }
    if (!g_cfg.probe_only && !g_cfg.file)
        return false;
    if (g_cfg.window_scale <= 0.05 || g_cfg.window_scale > 1.0 || g_cfg.seconds <= 0.0)
        return false;
    if (g_cfg.windowed && g_cfg.refresh_hz > 0) {
        fprintf(stderr, "perf1: --refresh is for full-screen trials; refusing to combine it with --windowed\n");
        return false;
    }
    return true;
}

static void probe(IDXGIAdapter1 *adapter)
{
    DXGI_ADAPTER_DESC1 d;
    IDXGIAdapter1_GetDesc1(adapter, &d);
    char name[256];
    to_utf8(d.Description, name, sizeof(name));
    printf("adapter: %s\n", name);
    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
        DEVMODEW dm = { .dmSize = sizeof(dm) };
        EnumDisplaySettingsW(m->device, ENUM_CURRENT_SETTINGS, &dm);
        printf("output %d %s: %ldx%ld at (%ld,%ld), %lu Hz, %.0f x %.0f mm\n", m->index, m->name,
               m->rect.right - m->rect.left, m->rect.bottom - m->rect.top, m->rect.left, m->rect.top,
               (unsigned long) dm.dmDisplayFrequency, m->w_mm, m->h_mm);
    }
}

int main(int argc, char **argv)
{
    int rc = 1;
    if (!parse_args(argc, argv)) {
        usage(argv[0]);
        return 2;
    }
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    timeBeginPeriod(1);
    bool refresh_switched = false;
    struct decoder_info di = {0};
    HANDLE vb_threads[MAX_MONITORS] = {0}, render = NULL;
    IDXGIAdapter1 *adapter = NULL;
    pl_queue queues[MAX_MONITORS] = {0};

    if (g_cfg.refresh_hz > 0) {
        if (!switch_refresh(g_cfg.refresh_device, g_cfg.refresh_hz)) {
            fwprintf(stderr, L"perf1: %ls: switching to %d Hz failed\n", g_cfg.refresh_device, g_cfg.refresh_hz);
            timeEndPeriod(1);
            return 1;
        }
        refresh_switched = true;
        wprintf(L"%ls switched to %d Hz for this run\n", g_cfg.refresh_device, g_cfg.refresh_hz);
    }

    adapter = pick_adapter();
    if (!adapter || !enumerate_monitors(adapter)) {
        fprintf(stderr, "perf1: no adapter with outputs attached to the desktop\n");
        goto cleanup;
    }
    if (g_cfg.probe_only) {
        probe(adapter);
        rc = 0;
        goto cleanup;
    }
    CreateDirectoryA(g_cfg.out_dir, NULL);

    if (!create_windows() || !init_gpu(adapter))
        goto cleanup;
    for (int i = 0; i < g_nmon; i++)
        queues[i] = g_mon[i].queue;
    g_dec = decoder_open(&g_cfg, &g_gpu, queues, g_nmon);
    if (!g_dec) {
        fprintf(stderr, "perf1: decoder_open failed\n");
        goto cleanup;
    }
    decoder_get_info(g_dec, &di);
    if (!decoder_start(g_dec)) {
        fprintf(stderr, "perf1: decoder thread failed to start\n");
        goto cleanup;
    }
    for (int i = 0; i < g_nmon; i++)
        vb_threads[i] = CreateThread(NULL, 0, vblank_thread, &g_mon[i], 0, NULL);
    double t_vb = now_s();

    double aspect = 0.0;
    if (!warm_up(&aspect))
        goto stop_threads;
    // Let the refresh grids settle before the film starts (messages keep flowing).
    while (now_s() < t_vb + SETTLE_S && !atomic_load(&g_stop)) {
        MSG msg;
        while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        Sleep(5);
    }
    if (atomic_load(&g_stop))
        goto stop_threads;

    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
        printf("monitor %d %s: %dx%d at (%ld,%ld), window/swapchain %dx%d, refresh %.4f Hz, %.0fx%.0f mm at %.0f mm\n", m->index,
               m->name, (int) (m->rect.right - m->rect.left), (int) (m->rect.bottom - m->rect.top), m->rect.left,
               m->rect.top, m->sc_w, m->sc_h, 1.0 / vclock_period(&m->clock), m->w_mm, m->h_mm, m->left_mm);
        if (vclock_period(&m->clock) <= 0.0) {
            fail(m, "no vblank grid after %.1f s", SETTLE_S);
            goto stop_threads;
        }
    }
    choose_film_start(di.fps);
    printf("picture %.1f x %.1f mm (aspect %.4f) at x %.1f..%.1f mm; ", g_pic_x1 - g_pic_x0, g_pic_y1 - g_pic_y0, aspect,
           g_pic_x0, g_pic_x1);
    for (int i = 0; i < g_nmon; i++)
        printf("%s: %s ", g_mon[i].name, g_mon[i].has_picture ? "shows part" : "black");
    printf("\n");
    fflush(stdout);

    render = CreateThread(NULL, 0, render_thread, NULL, 0, NULL);
    if (!render) {
        fprintf(stderr, "perf1: render thread failed to start\n");
        goto stop_threads;
    }

    // Main thread: window messages, Esc (also read globally, so it works without focus), a hard deadline.
    double deadline = g_t0 + g_cfg.seconds + 5.0;
    while (!atomic_load(&g_render_done)) {
        MSG msg;
        while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        if (GetAsyncKeyState(VK_ESCAPE) & 0x8000)
            atomic_store(&g_stop, true);
        if (now_s() > deadline) {
            fprintf(stderr, "perf1: deadline passed, stopping\n");
            atomic_store(&g_stop, true);
        }
        Sleep(5);
    }
    rc = 0;

stop_threads:
    atomic_store(&g_stop, true);
    if (render) {
        WaitForSingleObject(render, 5000);
        CloseHandle(render);
    }
    for (int i = 0; i < g_nmon; i++)
        if (vb_threads[i]) {
            WaitForSingleObject(vb_threads[i], 2000);
            CloseHandle(vb_threads[i]);
        }

    if (g_dec) {
        decoder_stop(g_dec);
        decoder_get_info(g_dec, &di);
    }
    if (g_t0 > 0.0) {
        for (int i = 0; i < g_nmon; i++) {
            write_log(&g_mon[i], &di);
            if (g_mon[i].error[0])
                rc = 1;
        }
    } else {
        rc = 1;
    }

cleanup:
    if (g_dec)
        decoder_stop(g_dec); // no thread may push into a queue that is being destroyed
    for (int i = 0; i < g_nmon; i++)
        pl_queue_destroy(&g_mon[i].queue); // unmaps queued frames while the GPU still exists
    decoder_destroy(&g_dec);
    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
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
        vclock_destroy(&m->clock);
        samples_free(&m->render_cpu);
        samples_free(&m->render_gpu);
        samples_free(&m->render_sync);
        samples_free(&m->lead);
        free(m->presents);
        free(m->stats);
    }
    pl_d3d11_destroy(&g_gpu.d3d11);
    pl_log_destroy(&g_gpu.log);
    if (adapter)
        IDXGIAdapter1_Release(adapter);
    if (refresh_switched)
        ChangeDisplaySettingsExW(g_cfg.refresh_device, NULL, NULL, 0, NULL);
    timeEndPeriod(1);
    return rc;
}
