// perf1vk: one borderless window per monitor, one Vulkan swapchain per window (libplacebo's, or our own), one film clock
// locked to the master monitor's refresh count. Derived from perf1 (spikes/performer-p1, D3D11); the film clock, the
// frame picking and the picture layout are the same. What differs is how a monitor is paced and presented:
//
//   - every monitor has its own render thread (libplacebo's Vulkan pl_gpu is thread-safe; perf1 shared one thread
//     because its D3D11 pl_gpu is not);
//   - a thread blocks in the swapchain's image acquire. With two images that releases right after the refresh that
//     showed the previous frame, so one frame is rendered and presented per refresh and a present lands on the
//     monitor's next refresh (the same job the D3D11 frame-latency waitable object did in perf1);
//   - libplacebo's Vulkan swapchain presents inside pl_swapchain_submit_frame and reports nothing back, so with that
//     backend the refresh a present landed on is an inference (the acquire-return times show a missed one). The own
//     backend (--backend own) presents with present ids, waits with present_wait2 and reads VK_EXT_present_timing back.
//
// Scheduling follows spikes/per-monitor-presenter/per-monitor-presenter.cpp:
//   - each monitor's refresh grid is fitted to IDXGIOutput::WaitForVBlank times (perf1vk-clock.c)
//   - the film clock counts the master monitor's refreshes (the slowest monitor): film time at master
//     refresh k is (k - first) * step, where step makes a film frame last a whole number of master
//     refreshes when the playback speed change is within 1 %
//   - "nearest": every other monitor shows, on each of its refreshes, the picture the master shows at the
//     last master refresh whose follower refresh (the one that lights the aim row nearest to the moment the
//     master lights it) is not later than that refresh
//   - "own": each monitor switches on its first refresh at or after the film frame's time
//   - aimed start: the film starts on the master refresh whose follower refresh lights the aim row closest
//   - film frames are picked by timestamp: pl_queue (nearest-neighbour, no mixing) is asked for the
//     film time of the refresh being prepared
// Rows, not blanks: row-timing.h (shared with the presenter spike) gives each monitor's exact signal timing, a scan-line
// sampler fitting its top-row grid, its height and top on the shared vertical axis and its panel lag.
//
// Picture: one 16:9 picture in the fold geometry (screens left to right by desktop position, gap_mm
// apart, tops level). Each monitor renders the part of the picture that falls on it with image.crop /
// target.crop, a single resample; the rest of the monitor is black.
//
// Logs per monitor (JSON, same fields as perf1, so analyze-perf1.py reads them, plus a "vk" block with the phases of
// every present loop) and one events.json (libplacebo / FFmpeg log lines and our notes with their times), read by
// analyze-perf1vk.py.
#define COBJMACROS
#include "perf1vk.h"
#include "row-timing.h"
#include "vk-layers.h"

#include <avrt.h>
#include <ctype.h>
#include <stdarg.h>
#include <dxgi1_3.h>
#include <math.h>
#include <timeapi.h>

#include <libavutil/avutil.h>

#define SETTLE_S 1.5          // refresh grids settle before the film starts
#define FILM_LEAD_S 0.5       // first film frame is at least this far ahead of the settle
#define WARMUP_TIMEOUT_NS 5000000000ULL
#define ACQUIRE_GUARD_S 0.0005 // an acquire that returns within this of a refresh is taken as returning before it
#define START_FAIL_LIMIT 500   // swap_begin failures in a row before the run gives up (an invisible or lost window)

struct present_rec { uint32_t id; double call, ret, target; int64_t frame; };
// Phases of one present loop, QPC seconds: loop start, image acquired, picture chosen, rendered, then the swap times.
struct cycle_rec { double loop, acquired, picked, rendered, call, ret, done; };

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
    row_mon row;                // signal timing, scan-line grid, height / top / lag on the shared vertical axis

    struct vk_swap sw;
    pl_renderer rr;
    pl_queue queue;
    struct pl_render_params params;
    uint64_t gpu_acc_ns;        // sum of libplacebo's per-pass GPU timers for the current render
    HANDLE thread;              // this monitor's render thread

    // this monitor's share of the picture: target pixels and the matching fraction of the source
    bool has_picture;
    int tx0, ty0, tx1, ty1;
    double u0, v0, u1, v1;

    uint64_t first_sig;
    int64_t last_frame;
    double prev_target;         // refresh the previous present was aimed at (--no-window waits for it)
    uint32_t start_fail;

    struct present_rec *presents; size_t n_presents, cap_presents;
    struct cycle_rec *cycles; size_t n_cycles, cap_cycles;
    struct samples render_cpu, render_gpu, render_sync, lead;
    uint32_t queue_more, empty_mix, render_fail, start_fail_total;
    char error[160];
};

static struct cfg g_cfg;
static struct monitor g_mon[MAX_MONITORS];
static int g_nmon;
static atomic_bool g_stop;
static atomic_int g_render_running;
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

// Where the screens agree, millimetres on the shared vertical axis (the master's top is 0): the aim row, and the
// band of height the master and the follower share. g_predicted_ms is what the model predicts for the chosen
// start: second monitor minus first (by output index), in light, at the band's top, the aim row and the band's
// bottom.
static double g_aim_mm, g_band_top_mm, g_band_bottom_mm;
static double g_predicted_ms[3];
static bool g_predicted_valid;

// ---- helpers ------------------------------------------------------------------------------

static void to_utf8(const WCHAR *w, char *out, int size)
{
    if (!WideCharToMultiByte(CP_UTF8, 0, w, -1, out, size, NULL, NULL))
        out[0] = '\0';
}

// Notes a module the Vulkan loader (or anyone) loaded into this process from outside Windows and the MSYS2 runtime: the
// overlay and capture layers that hook the present calls. Called once the instance exists.
static void note_foreign_module(const wchar_t *path, void *user)
{
    char utf8[200];
    to_utf8(path, utf8, sizeof(utf8));
    gpu_note("module from outside Windows after Vulkan start-up: %s", utf8);
}

static void fail(struct monitor *m, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(m->error, sizeof(m->error), fmt, ap);
    va_end(ap);
    fprintf(stderr, "perf1vk: %s: %s\n", m->name, m->error);
    gpu_note("%s: %s", m->name, m->error);
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

// Sleeps until QPC time t (--no-window only): coarse sleeps, then a short spin for the last stretch.
static void wait_until(double t)
{
    for (;;) {
        double d = t - now_s();
        if (d <= 0.0)
            return;
        if (d > 0.003)
            Sleep(1);
        else
            YieldProcessor();
    }
}

// ---- monitors, windows ---------------------------------------------------------------------

#ifndef WM_DWMCOMPOSITIONCHANGED
#define WM_DWMCOMPOSITIONCHANGED 0x031E
#endif

// Window messages that can change a swapchain's surface or how the compositor treats the window. Each one is written to
// the event log with its time, so a hitch that falls next to one of them is blamed on the window, not on the swapchain.
static const char *notable_message(UINT msg)
{
    switch (msg) {
    case WM_SIZE: return "WM_SIZE";
    case WM_MOVE: return "WM_MOVE";
    case WM_WINDOWPOSCHANGED: return "WM_WINDOWPOSCHANGED";
    case WM_SHOWWINDOW: return "WM_SHOWWINDOW";
    case WM_DISPLAYCHANGE: return "WM_DISPLAYCHANGE";
    case WM_DPICHANGED: return "WM_DPICHANGED";
    case WM_ACTIVATE: return "WM_ACTIVATE";
    case WM_ACTIVATEAPP: return "WM_ACTIVATEAPP";
    case WM_SETFOCUS: return "WM_SETFOCUS";
    case WM_KILLFOCUS: return "WM_KILLFOCUS";
    case WM_DWMCOMPOSITIONCHANGED: return "WM_DWMCOMPOSITIONCHANGED";
    case WM_SETTINGCHANGE: return "WM_SETTINGCHANGE";
    case WM_POWERBROADCAST: return "WM_POWERBROADCAST";
    case WM_DEVICECHANGE: return "WM_DEVICECHANGE";
    case WM_SYSCOMMAND: return "WM_SYSCOMMAND";
    default: return NULL;
    }
}

static LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    const char *notable = notable_message(msg);
    if (notable) {
        const struct monitor *m = (const struct monitor *) GetWindowLongPtrW(hwnd, GWLP_USERDATA);
        gpu_note("window %s: %s wp=0x%llx lp=0x%llx", m ? m->name : "?", notable, (unsigned long long) wp,
                 (unsigned long long) lp);
    }
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

// The first adapter that drives outputs (or the one --adapter names). Its LUID goes to the Vulkan device choice.
static IDXGIAdapter1 *pick_adapter(LUID *luid)
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
        if (has_outputs && name_ok && !(d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)) {
            found = ad;
            *luid = d.AdapterLuid;
        } else {
            IDXGIAdapter1_Release(ad);
        }
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
    if (g_cfg.headless) {
        // No windows: each monitor renders to an offscreen target of its own size.
        for (int i = 0; i < g_nmon; i++) {
            g_mon[i].sc_w = g_mon[i].rect.right - g_mon[i].rect.left;
            g_mon[i].sc_h = g_mon[i].rect.bottom - g_mon[i].rect.top;
        }
        return true;
    }
    WNDCLASSW wc = {
        .lpfnWndProc = window_proc,
        .hInstance = GetModuleHandleW(NULL),
        .lpszClassName = L"panorama-perf1vk",
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
        m->hwnd = CreateWindowExW(ex, wc.lpszClassName, L"panorama perf1vk", WS_POPUP, x, y, w, h,
                                  NULL, NULL, wc.hInstance, NULL);
        if (!m->hwnd) {
            fprintf(stderr, "perf1vk: CreateWindowEx failed for %s\n", m->name);
            return false;
        }
        SetWindowLongPtrW(m->hwnd, GWLP_USERDATA, (LONG_PTR) m); // window_proc names the monitor in its event notes
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

// Vulkan device on the adapter that drives the screens, a surface per window, a swapchain (or offscreen target),
// a renderer and a queue per monitor.
static bool init_gpu(const LUID *luid)
{
    if (!gpu_instance_create(&g_gpu, &g_cfg))
        return false;
    for (int i = 0; i < g_nmon && !g_cfg.headless; i++)
        if (!gpu_surface_create(&g_gpu, g_mon[i].hwnd, &g_mon[i].sw.surface))
            return false;
    if (!gpu_device_create(&g_gpu, &g_cfg, luid, g_cfg.headless ? VK_NULL_HANDLE : g_mon[0].sw.surface))
        return false;

    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
        if (!swap_create(&g_gpu, &g_cfg, &m->sw, m->sc_w, m->sc_h)) {
            fail(m, "swapchain creation failed");
            return false;
        }
        m->sc_w = m->sw.w;
        m->sc_h = m->sw.h;
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
// Vertically the monitors sit on the shared axis (row.top_mm is each monitor's top, tops level unless
// --height-offset-mm), and the picture fills the height they all cover unless --picture-height-mm says otherwise.
static void layout_picture(double aspect)
{
    double band_top = g_mon[0].row.top_mm, band_bottom = g_mon[0].row.top_mm + g_mon[0].h_mm;
    double left = g_mon[0].left_mm, right = g_mon[0].left_mm + g_mon[0].w_mm;
    for (int i = 1; i < g_nmon; i++) {
        if (g_mon[i].row.top_mm > band_top) band_top = g_mon[i].row.top_mm;
        if (g_mon[i].row.top_mm + g_mon[i].h_mm < band_bottom) band_bottom = g_mon[i].row.top_mm + g_mon[i].h_mm;
        if (g_mon[i].left_mm < left) left = g_mon[i].left_mm;
        if (g_mon[i].left_mm + g_mon[i].w_mm > right) right = g_mon[i].left_mm + g_mon[i].w_mm;
    }
    double h = g_cfg.picture_height_mm > 0 ? g_cfg.picture_height_mm : band_bottom - band_top;
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
    g_pic_y0 = band_top; // 0 with the tops level
    g_pic_y1 = band_top + h;

    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
        double top = m->row.top_mm;
        double ix0 = fmax(m->left_mm, g_pic_x0), ix1 = fmin(m->left_mm + m->w_mm, g_pic_x1);
        double iy0 = fmax(top, g_pic_y0), iy1 = fmin(top + m->h_mm, g_pic_y1);
        m->has_picture = false;
        if (ix1 <= ix0 || iy1 <= iy0)
            continue;
        double sx = m->sc_w / m->w_mm, sy = m->sc_h / m->h_mm; // pixels per millimetre
        m->tx0 = (int) lround((ix0 - m->left_mm) * sx);
        m->tx1 = (int) lround((ix1 - m->left_mm) * sx);
        m->ty0 = (int) lround((iy0 - top) * sy);
        m->ty1 = (int) lround((iy1 - top) * sy);
        if (m->tx1 > m->sc_w) m->tx1 = m->sc_w;
        if (m->ty1 > m->sc_h) m->ty1 = m->sc_h;
        if (m->tx1 <= m->tx0 || m->ty1 <= m->ty0)
            continue;
        // the snapped pixel edges back to the picture's own coordinates (one resample, exact mapping)
        double pw = g_pic_x1 - g_pic_x0, ph = g_pic_y1 - g_pic_y0;
        m->u0 = (m->left_mm + m->tx0 / sx - g_pic_x0) / pw;
        m->u1 = (m->left_mm + m->tx1 / sx - g_pic_x0) / pw;
        m->v0 = (top + m->ty0 / sy - g_pic_y0) / ph;
        m->v1 = (top + m->ty1 / sy - g_pic_y0) / ph;
        m->has_picture = true;
    }
}

// ---- row timing: when a monitor lights up a row ------------------------------------------------

// The slowest monitor by its exact signal rate (the first of equals), unless --master names one.
static int pick_master(void)
{
    if (g_cfg.master >= 0 && g_cfg.master < g_nmon)
        return g_cfg.master;
    int best = 0;
    for (int i = 1; i < g_nmon; i++)
        if (g_mon[i].row.timing.refresh_hz < g_mon[best].row.timing.refresh_hz)
            best = i;
    return best;
}

// Opens every monitor's signal timing and scan-line source, picks the master, places the monitors on the shared
// vertical axis (the master's top is 0; the follower's top is --height-offset-mm lower), applies --lag-ms, and
// works out the band of height the master and the follower share and the aim row inside it. Runs before the
// picture is laid out, because the layout needs each monitor's top.
static bool assign_geometry(void)
{
    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
        if (!rowmon_open(&m->row, m->device, m->h_mm)) {
            fprintf(stderr, "perf1vk: %s: cannot read its signal timing or scan line (QueryDisplayConfig, D3DKMT)\n", m->name);
            return false;
        }
    }
    g_master = pick_master();
    struct monitor *follower = g_nmon > 1 ? &g_mon[g_master == 0 ? 1 : 0] : NULL;
    for (int i = 0; i < g_nmon; i++)
        g_mon[i].row.top_mm = &g_mon[i] == follower ? g_cfg.height_offset_mm : 0.0;
    for (int i = 0; i < g_cfg.n_lags; i++) {
        struct monitor *target = NULL;
        for (int k = 0; k < g_nmon; k++)
            if (!_wcsicmp(g_mon[k].device, g_cfg.lags[i].device))
                target = &g_mon[k];
        if (!target) {
            fprintf(stderr, "perf1vk: --lag-ms: no monitor named %ls\n", g_cfg.lags[i].device);
            return false;
        }
        target->row.lag_s = g_cfg.lags[i].ms * 1e-3;
    }
    struct monitor *master = &g_mon[g_master];
    g_band_top_mm = master->row.top_mm;
    g_band_bottom_mm = master->row.top_mm + master->row.height_mm;
    if (follower && !rowmon_shared_band(&master->row, &follower->row, &g_band_top_mm, &g_band_bottom_mm)) {
        fprintf(stderr, "perf1vk: the master and the follower share no height with --height-offset-mm %.1f\n",
                g_cfg.height_offset_mm);
        return false;
    }
    g_aim_mm = isnan(g_cfg.aim_mm) ? 0.5 * (g_band_top_mm + g_band_bottom_mm) : g_cfg.aim_mm;
    if (g_aim_mm < g_band_top_mm || g_aim_mm > g_band_bottom_mm) {
        fprintf(stderr, "perf1vk: --aim-mm %.1f is outside the shared height %.1f..%.1f mm\n", g_aim_mm, g_band_top_mm,
                g_band_bottom_mm);
        return false;
    }
    return true;
}

// A monitor's refresh grid as it is now (row-timing.h works on snapshots, so the maths needs no lock).
static row_vgrid grid_of(struct monitor *m)
{
    row_vgrid g;
    vclock_line(&m->clock, &g.base, &g.period);
    return g;
}

// Time of the follower's refresh that carries the picture the master shows at its refresh index j: the one
// whose picture is lit nearest, at the aim row, to the moment the master's screen lights it.
static double follower_switch_time(struct monitor *f, int64_t j)
{
    struct monitor *mas = &g_mon[g_master];
    row_vgrid mg = grid_of(mas), fg = grid_of(f);
    return row_vgrid_time(&fg, row_follower_refresh(&mas->row, &mg, &f->row, &fg, j, g_aim_mm));
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
            // largest master refresh whose carrying refresh on this monitor (the one lighting the aim row
            // nearest to the moment the master lights it) is not later than `target`
            int64_t guess = vclock_index_near(&mm->clock, target);
            j = guess - 2;
            for (int64_t k = guess - 2; k <= guess + 2; k++)
                if (follower_switch_time(m, k) <= target + 1e-4)
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

static void push_present(struct monitor *m, const struct present_rec *p, const struct cycle_rec *c)
{
    if (m->n_presents == m->cap_presents) {
        size_t cap = m->cap_presents ? m->cap_presents * 2 : 8192;
        struct present_rec *n = realloc(m->presents, cap * sizeof(*n));
        struct cycle_rec *k = realloc(m->cycles, cap * sizeof(*k));
        if (n)
            m->presents = n;
        if (k)
            m->cycles = k;
        if (!n || !k)
            return; // out of memory: stop recording, keep the run going
        m->cap_presents = m->cap_cycles = cap;
    }
    m->presents[m->n_presents++] = *p;
    m->cycles[m->n_cycles++] = *c;
}

static void service_monitor(struct monitor *m)
{
    struct cycle_rec cy = {0};
    cy.loop = now_s();
    // --no-window: there is no swapchain to block in, so wait for the refresh the previous present was aimed at,
    // the moment a real swapchain would hand back an image.
    if (g_cfg.headless && m->prev_target > 0.0)
        wait_until(m->prev_target + 0.0002);

    // 1. an image: blocks until the swapchain has one free, i.e. until the previous frame has been shown
    struct pl_frame tgt;
    if (!swap_begin(&g_gpu, &m->sw, &tgt)) {
        m->start_fail_total++;
        if (++m->start_fail >= START_FAIL_LIMIT)
            fail(m, "no swapchain image for %u tries in a row", m->start_fail);
        else
            Sleep(2);
        return;
    }
    m->start_fail = 0;
    cy.acquired = now_s();

    // 2. the present lands on this monitor's next refresh; the picture is chosen for that moment
    double target = vclock_next_after(&m->clock, cy.acquired + ACQUIRE_GUARD_S);
    double pos = queue_position(m, target, NULL);

    struct pl_queue_params qp = *pl_queue_params(
        .interpolation_threshold = 1e9, // never mix frames: a monitor shows one film frame per refresh
        .drift_compensation = 0.0,      // the film clock is exact; no hidden timestamp shifts
        .timeout = 0,                   // never wait for the decoder inside the refresh loop
        .radius = pl_frame_mix_radius(&m->params),
        .pts = pos,
    );
    struct pl_frame_mix mix;
    bool stop_after = false;
    switch (pl_queue_update(m->queue, &mix, &qp)) {
    case PL_QUEUE_ERR:
        fail(m, "pl_queue_update failed mid-run");
        stop_after = true;
        break;
    case PL_QUEUE_EOF:
        atomic_store(&g_stop, true); // end of file: stop every window together
        stop_after = true;
        break;
    case PL_QUEUE_MORE:
        m->queue_more++; // the decoder is behind: the mix may be stale or empty
        break;
    case PL_QUEUE_OK:
        break;
    }
    int front = stop_after ? -1 : front_index(&mix);
    int64_t frame = m->last_frame;
    if (front >= 0)
        frame = (int64_t) (mix.signatures[front] - m->first_sig);
    else if (!stop_after)
        m->empty_mix++;
    m->last_frame = frame;
    cy.picked = now_s();

    // 3. render. A started frame is always submitted (an unsubmitted one blocks the swapchain's destruction), so a
    // frame we cannot or need not draw is cleared to black.
    m->gpu_acc_ns = 0;
    double t_r0 = now_ms();
    bool ok = !stop_after && render_picture(m, &mix, front, &tgt);
    double t_r1 = now_ms();
    double sync_ms = 0.0;
    if (ok && g_cfg.sync_render) {
        pl_gpu_finish(g_gpu.gpu);
        sync_ms = now_ms() - t_r1;
    }
    if (!ok) {
        if (!stop_after)
            m->render_fail++;
        pl_frame_clear(g_gpu.gpu, &tgt, (const float[3]) {0});
    }
    cy.rendered = now_s();

    // 4. present
    struct swap_end_times te = {0};
    bool sent = swap_end(&g_gpu, &m->sw, &te);
    cy.call = te.call;
    cy.ret = te.ret;
    cy.done = te.done;
    if (!sent) {
        fail(m, "present failed mid-run");
        return;
    }
    if (stop_after)
        return;

    struct present_rec p = {0};
    p.id = te.id ? (uint32_t) te.id : (uint32_t) (m->n_presents + 1);
    p.call = te.call;
    p.ret = te.ret;
    p.target = target;
    p.frame = frame;
    push_present(m, &p, &cy);
    m->prev_target = target;

    samples_push(&m->render_cpu, t_r1 - t_r0);
    samples_push(&m->render_gpu, (double) m->gpu_acc_ns / 1e6);
    if (g_cfg.sync_render)
        samples_push(&m->render_sync, sync_ms);
    samples_push(&m->lead, (target - te.call) * 1e3);
}

static DWORD WINAPI render_thread(LPVOID arg)
{
    struct monitor *m = arg;
    DWORD task = 0;
    HANDLE mmcss = g_cfg.low_priority ? NULL : AvSetMmThreadCharacteristicsW(L"Games", &task);
    double end = g_t0 + g_cfg.seconds;

    while (!atomic_load(&g_stop) && now_s() < end)
        service_monitor(m);

    if (mmcss)
        AvRevertMmThreadCharacteristics(mmcss);
    atomic_fetch_sub(&g_render_running, 1);
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

// Fully renders frame 0 on every monitor, so shader compilation and the first texture imports are not counted against
// the film: one frame drawn twice (the first draw of a film frame and the repeat of it are different passes inside
// libplacebo), then two more frames, so every swapchain image has been drawn into and presented. Returns the
// picture's aspect ratio.
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

        for (int cycle = 0; cycle < 3; cycle++) {
            struct pl_frame tgt;
            if (!swap_begin(&g_gpu, &m->sw, &tgt)) {
                fail(m, "first swap_begin failed");
                return false;
            }
            if (tgt.repr.alpha == PL_ALPHA_NONE)
                m->params.background_transparency = 0.0;
            bool ok = render_picture(m, &mix, front, &tgt);
            if (ok && cycle == 0)
                ok = render_picture(m, &mix, front, &tgt); // the repeat of a film frame
            if (!ok)
                pl_frame_clear(g_gpu.gpu, &tgt, (const float[3]) {0}); // a started frame is always submitted
            struct swap_end_times te;
            if (!swap_end(&g_gpu, &m->sw, &te)) {
                fail(m, "first present failed");
                return false;
            }
            if (!ok) {
                fail(m, "first render failed");
                return false;
            }
        }
        pl_gpu_finish(g_gpu.gpu);
    }
    return true;
}

// g_master was picked by assign_geometry (the slowest monitor, or --master).
static void choose_film_start(double stream_fps)
{
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
        // the candidate whose follower refresh lights the aim row closest to the moment the master lights it.
        // Later frame starts (hold master refreshes apart) keep that class while the clocks keep their ratio.
        struct monitor *mas = &g_mon[g_master];
        struct monitor *fol = &g_mon[g_master == 0 ? 1 : 0];
        row_vgrid mg = grid_of(mas), fg = grid_of(fol);
        int64_t base = g_first;
        double err_s = 0.0;
        g_first = row_aim_start(&mas->row, &mg, &fol->row, &fg, base, 24, g_aim_mm, &err_s);
        // What the model predicts for this start: second monitor minus first (by output index), in light.
        int64_t kf = row_follower_refresh(&mas->row, &mg, &fol->row, &fg, g_first, g_aim_mm);
        const double heights[3] = {g_band_top_mm, g_aim_mm, g_band_bottom_mm};
        const double sign = fol->index > mas->index ? 1.0 : -1.0;
        for (int i = 0; i < 3; i++)
            g_predicted_ms[i] = sign * (rowmon_light_at(&fol->row, &fg, kf, heights[i]) -
                                        rowmon_light_at(&mas->row, &mg, g_first, heights[i])) * 1e3;
        g_predicted_valid = true;
        const struct monitor *second = fol->index > mas->index ? fol : mas;
        const struct monitor *first = fol->index > mas->index ? mas : fol;
        printf("film starts %lld master refreshes later; aim row %.1f mm\n", (long long) (g_first - base), g_aim_mm);
        printf("predicted seam offset %s - %s, in light: top row (%.1f mm) %+.2f ms, aim row %+.2f ms, bottom row (%.1f mm) %+.2f ms\n",
               second->name, first->name, g_band_top_mm, g_predicted_ms[0], g_predicted_ms[1], g_band_bottom_mm,
               g_predicted_ms[2]);
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

static void j_string(FILE *f, const char *s)
{
    fputc('"', f);
    for (; *s; s++) {
        unsigned char c = (unsigned char) *s;
        if (c == '"' || c == '\\')
            fprintf(f, "\\%c", c);
        else if (c >= 0x20 && c < 0x7f)
            fputc(c, f);
        else if (c >= 0x80)
            fputc('?', f); // keep the file plain ASCII
    }
    fputc('"', f);
}

static void write_log(struct monitor *m, const struct decoder_info *di)
{
    const char *mode = g_cfg.sched == SCHED_NEAREST ? "nearest" : "own";
    char path[1024];
    snprintf(path, sizeof(path), "%s/monitor-%d-%s.json", g_cfg.out_dir, m->index, mode);
    FILE *f = fopen(path, "w");
    if (!f) {
        fprintf(stderr, "perf1vk: cannot write %s\n", path);
        return;
    }
    size_t nv = 0;
    double *vb = vclock_copy_log(&m->clock, &nv);

    fprintf(f, "{\"monitor\":%d,\"name\":\"%s\",\"rect\":[%ld,%ld,%ld,%ld],\"width_mm\":%.1f,\"left_mm\":%.1f,",
            m->index, m->name, m->rect.left, m->rect.top, m->rect.right, m->rect.bottom, m->w_mm, m->left_mm);
    fprintf(f, "\"mode\":\"%s\",\"fps\":%.6f,\"t0\":%.7f,\"master\":%d,\"error\":", mode,
            g_locked ? 1.0 / (g_step_s * (double) g_hold) : 1.0 / g_frame_dur, g_t0, g_mon[g_master].index);
    j_string(f, m->error);
    // composition_modes stays: the Vulkan path has no DXGI media statistics, so it is always empty here
    fprintf(f, ",\"composition_modes\":{},\"vblanks\":[");
    for (size_t i = 0; i < nv; i++)
        fprintf(f, "%s%.7f", i ? "," : "", vb[i]);
    fprintf(f, "],\"presents\":[");
    for (size_t i = 0; i < m->n_presents; i++) {
        const struct present_rec *p = &m->presents[i];
        fprintf(f, "%s[%u,%.7f,%.7f,%.7f,%lld]", i ? "," : "", p->id, p->call, p->ret, p->target, (long long) p->frame);
    }
    fprintf(f, "],\"stats\":[]");

    // Extras for analyze-perf1.py (one entry per present, in present order): CPU time of the render call,
    // libplacebo's GPU pass timers, time left before the target refresh when the present was called.
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
            di->decoded, di->mapped, di->dropped, di->codec, di->hw_fmt, g_cfg.preset, m->sw.format);

    // The Vulkan block: how the swapchain was made and the phases of every present loop.
    fprintf(f, ",\"vk\":{\"backend\":\"%s\",\"headless\":%s,\"present_mode\":\"%s\",\"depth\":%d,\"images\":%d,"
               "\"alpha\":\"%s\",\"format\":\"%s\",\"device\":", g_cfg.backend == BACKEND_OWN ? "own" : "libplacebo",
            g_cfg.headless ? "true" : "false", g_cfg.present_mode, g_cfg.vk_depth, m->sw.images, m->sw.alpha, m->sw.format);
    j_string(f, g_gpu.device_name);
    fprintf(f, ",\"layers_off\":\"%s\",\"start_fail\":%u,\"distinct_images\":%d,\"hitch_ms\":%.1f,"
               "\"cycle_fields\":[\"loop\",\"acquired\",\"picked\",\"rendered\",\"call\",\"ret\",\"done\"],\"cycles\":[",
            g_cfg.layers_off, m->start_fail_total, m->sw.n_seen, g_cfg.hitch_ms);
    for (size_t i = 0; i < m->n_cycles; i++) {
        const struct cycle_rec *c = &m->cycles[i];
        fprintf(f, "%s[%.7f,%.7f,%.7f,%.7f,%.7f,%.7f,%.7f]", i ? "," : "", c->loop, c->acquired, c->picked, c->rendered,
                c->call, c->ret, c->done);
    }
    fprintf(f, "]");
    if (m->sw.own)
        own_swap_write_json(f, &m->sw);
    fprintf(f, "}");

    // Row timing (same fields as the presenter spike): signal timing, scan-line grid, geometry, aim, prediction.
    fprintf(f, ",\"windowed\":%s", g_cfg.windowed ? "true" : "false");
    rowmon_write_json(&m->row, f);
    fprintf(f, ",\"aim_mm\":%.3f,\"shared_mm\":[%.3f,%.3f]", g_aim_mm, g_band_top_mm, g_band_bottom_mm);
    if (g_predicted_valid)
        fprintf(f, ",\"predicted_ms\":{\"top\":%.4f,\"aim\":%.4f,\"bottom\":%.4f}", g_predicted_ms[0], g_predicted_ms[1],
                g_predicted_ms[2]);
    fprintf(f, "}\n");
    fclose(f);
    free(vb);

    struct summary cpu, gpu;
    summarize(m->render_cpu.v, m->render_cpu.n, &cpu);
    summarize(m->render_gpu.v, m->render_gpu.n, &gpu);
    printf("%s: %zu presents, render cpu p50 %.3f / p99 %.3f ms, gpu p50 %.3f / p99 %.3f ms, queue_more %u, empty %u, "
           "swapchain %s %s (%d distinct images), swap_begin failures %u%s%s\n",
           m->name, m->n_presents, cpu.p50, cpu.p99, gpu.p50, gpu.p99, m->queue_more, m->empty_mix, m->sw.format,
           m->sw.alpha, m->sw.n_seen, m->start_fail_total, m->error[0] ? ", ERROR " : "", m->error);
    printf("wrote %s\n", path);
}

// events.json: the libplacebo / FFmpeg log lines and our notes with their QPC times (the same clock as the monitor logs).
static void write_events(void)
{
    char path[1024];
    snprintf(path, sizeof(path), "%s/events.json", g_cfg.out_dir);
    FILE *f = fopen(path, "w");
    if (!f) {
        fprintf(stderr, "perf1vk: cannot write %s\n", path);
        return;
    }
    size_t n = 0;
    struct log_event *ev = gpu_log_events(&n);
    fprintf(f, "{\"t0\":%.7f,\"events\":[", g_t0);
    for (size_t i = 0; i < n; i++) {
        fprintf(f, "%s[%.7f,%d,", i ? ",\n" : "\n", ev[i].t, ev[i].level);
        j_string(f, ev[i].text);
        fputc(']', f);
    }
    fprintf(f, "\n]}\n");
    fclose(f);
    printf("wrote %s (%zu events)\n", path, n);
}

// ---- command line --------------------------------------------------------------------------------

static void usage(const char *argv0)
{
    fprintf(stderr,
        "Usage: %s --file PATH [--windowed [--window-scale 0.5] | --no-window] [--seconds N] [--mode nearest|own]\n"
        "          [--decode sw|vulkan] [--preset fast|default|hq] [--master OUTPUT_INDEX] [--no-lock]\n"
        "          [--refresh DISPLAYn=HZ] [--gap-mm 20] [--picture-height-mm N] [--picture-center-mm N]\n"
        "          [--lag-ms DISPLAYn=X] [--height-offset-mm X] [--aim-mm X]\n"
        "          [--format rgb10a2|bgra8] [--sync-render] [--adapter SUBSTR] [--out DIR] [--log-level 0..6] [--probe] [--device-test]\n"
        "          [--backend libplacebo|own] [--vk-depth N] [--present-mode fifo|fifo-relaxed|mailbox|immediate|latest-ready]\n"
        "          [--layers-off none|third-party|all-implicit] [--cache FILE] [--hitch-ms 50] [--event-level info|debug] [--log-all]\n"
        "          [--low-priority] [--wait-lag N] [--alpha opaque|premultiplied] [--sharing concurrent|exclusive]\n"
        "  --windowed       smoke test: half-size borderless windows, not topmost, no focus steal, no display change\n"
        "  --no-window      self-test with no window and no swapchain: render to offscreen targets, wait for each refresh\n"
        "  --seconds N      playback time after the film clock starts (default 20); Esc stops every window early\n"
        "  --refresh        switch that display for the run with CDS_FULLSCREEN (restored when the process ends)\n"
        "  --lag-ms         that panel's lag: its light comes X ms after the signal (repeatable, default 0)\n"
        "  --height-offset-mm  how far the follower's top is below the master's top (default 0, tops level)\n"
        "  --aim-mm         height, from the master's top, where the screens agree (default: middle of the shared height)\n"
        "  --no-lock        play at 1.0 speed even when the master's refresh rate would allow a 1 %% lock\n"
        "  --backend        libplacebo: pl_vulkan_create_swapchain (default); own: our VkSwapchainKHR with present_wait2 / timing\n"
        "  --vk-depth       swapchain depth: images = depth + 1 (default 1: two images, one frame in flight)\n"
        "  --wait-lag N     own backend: before drawing, wait until the present N frames back is on screen (default 1; 0 = acquire only)\n"
        "  --alpha          own backend: composite alpha opaque (default) or premultiplied (what libplacebo's own swapchain picks)\n"
        "  --sharing        own backend: images shared by all of libplacebo's queue families (default, its wrap contract) or exclusive\n"
        "  --low-priority   below-normal process priority, no MMCSS: for self-tests next to a running game\n"
        "  --layers-off     switch Vulkan implicit layers off for this run (third-party overlay/capture hooks, or all)\n"
        "  --probe          print the outputs of the adapter that drives the screens and the Vulkan device, then exit\n"
        "  --device-test    no window, no --file needed: make the Vulkan instance and the device that --backend / --decode would use\n"
        "                   (present-timing set for own, Vulkan Video device adopted by FFmpeg for vulkan), print what it has, exit\n",
        argv0);
}

static bool parse_display_arg(const char *v, wchar_t *device, double *value)
{
    // DISPLAYn=X
    const char *eq = strchr(v, '=');
    char *end = NULL;
    if (!eq || eq == v)
        return false;
    *value = strtod(eq + 1, &end);
    if (end == eq + 1 || *end != '\0')
        return false;
    char dev[32];
    snprintf(dev, sizeof(dev), "%s%.*s", strncmp(v, "\\\\.\\", 4) ? "\\\\.\\" : "", (int) (eq - v), v);
    MultiByteToWideChar(CP_UTF8, 0, dev, -1, device, 31);
    return true;
}

static bool parse_args(int argc, char **argv)
{
    g_cfg = (struct cfg) {
        .decode = DECODE_SW,
        .preset = "default",
        .seconds = 20.0,
        .window_scale = 0.5,
        .sched = SCHED_NEAREST,
        .master = -1,
        .lock_speed = true,
        .rgb10 = true,
        .gap_mm = 20.0,
        .picture_center_mm = NAN,
        .aim_mm = NAN,
        .out_dir = ".",
        .log_level = PL_LOG_WARN,
        .event_level = PL_LOG_INFO,
        .backend = BACKEND_LIBPLACEBO,
        .vk_depth = 1,
        .present_mode = "fifo",
        .layers_off = "none",
        .hitch_ms = 50.0,
        .wait_lag = 1,
    };
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--file") && v) { g_cfg.file = v; i++; }
        else if (!strcmp(a, "--windowed")) g_cfg.windowed = true;
        else if (!strcmp(a, "--no-window")) g_cfg.headless = true;
        else if (!strcmp(a, "--window-scale") && v) { g_cfg.window_scale = atof(v); i++; }
        else if (!strcmp(a, "--seconds") && v) { g_cfg.seconds = atof(v); i++; }
        else if (!strcmp(a, "--mode") && v) {
            if (!strcmp(v, "nearest")) g_cfg.sched = SCHED_NEAREST;
            else if (!strcmp(v, "own")) g_cfg.sched = SCHED_OWN;
            else return false;
            i++;
        } else if (!strcmp(a, "--decode") && v) {
            if (!strcmp(v, "sw")) g_cfg.decode = DECODE_SW;
            else if (!strcmp(v, "vulkan")) g_cfg.decode = DECODE_VULKAN;
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
        else if (!strcmp(a, "--height-offset-mm") && v) { g_cfg.height_offset_mm = atof(v); i++; }
        else if (!strcmp(a, "--aim-mm") && v) { g_cfg.aim_mm = atof(v); i++; }
        else if (!strcmp(a, "--lag-ms") && v) {
            if (g_cfg.n_lags >= MAX_MONITORS ||
                !parse_display_arg(v, g_cfg.lags[g_cfg.n_lags].device, &g_cfg.lags[g_cfg.n_lags].ms))
                return false;
            g_cfg.n_lags++;
            i++;
        }
        else if (!strcmp(a, "--adapter") && v) { g_cfg.adapter = v; i++; }
        else if (!strcmp(a, "--out") && v) { g_cfg.out_dir = v; i++; }
        else if (!strcmp(a, "--log-level") && v) { g_cfg.log_level = atoi(v); i++; }
        else if (!strcmp(a, "--probe")) g_cfg.probe_only = true;
        else if (!strcmp(a, "--device-test")) g_cfg.device_test = true;
        else if (!strcmp(a, "--backend") && v) {
            if (!strcmp(v, "libplacebo")) g_cfg.backend = BACKEND_LIBPLACEBO;
            else if (!strcmp(v, "own")) g_cfg.backend = BACKEND_OWN;
            else return false;
            i++;
        }
        else if (!strcmp(a, "--vk-depth") && v) { g_cfg.vk_depth = atoi(v); i++; }
        else if (!strcmp(a, "--present-mode") && v) { g_cfg.present_mode = v; i++; }
        else if (!strcmp(a, "--layers-off") && v) { g_cfg.layers_off = v; i++; }
        else if (!strcmp(a, "--cache") && v) { g_cfg.cache_file = v; i++; }
        else if (!strcmp(a, "--hitch-ms") && v) { g_cfg.hitch_ms = atof(v); i++; }
        else if (!strcmp(a, "--event-level") && v) {
            if (!strcmp(v, "info")) g_cfg.event_level = PL_LOG_INFO;
            else if (!strcmp(v, "debug")) g_cfg.event_level = PL_LOG_DEBUG;
            else return false;
            i++;
        }
        else if (!strcmp(a, "--log-all")) g_cfg.log_all = true;
        else if (!strcmp(a, "--low-priority")) g_cfg.low_priority = true;
        else if (!strcmp(a, "--wait-lag") && v) { g_cfg.wait_lag = atoi(v); i++; }
        else if (!strcmp(a, "--sharing") && v) {
            if (!strcmp(v, "exclusive")) g_cfg.sharing_exclusive = true;
            else if (!strcmp(v, "concurrent")) g_cfg.sharing_exclusive = false;
            else return false;
            i++;
        }
        else if (!strcmp(a, "--alpha") && v) {
            if (!strcmp(v, "premultiplied")) g_cfg.premultiplied = true;
            else if (!strcmp(v, "opaque")) g_cfg.premultiplied = false;
            else return false;
            i++;
        }
        else if (!strcmp(a, "--refresh") && v) {
            double hz = 0;
            if (!parse_display_arg(v, g_cfg.refresh_device, &hz) || hz <= 0)
                return false;
            g_cfg.refresh_hz = (int) hz;
            i++;
        }
        else return false;
    }
    if (!g_cfg.probe_only && !g_cfg.device_test && !g_cfg.file)
        return false;
    if (g_cfg.window_scale <= 0.05 || g_cfg.window_scale > 1.0 || g_cfg.seconds <= 0.0 || g_cfg.vk_depth < 1 ||
        g_cfg.vk_depth > 6)
        return false;
    if (g_cfg.windowed && g_cfg.refresh_hz > 0) {
        fprintf(stderr, "perf1vk: --refresh is for full-screen trials; refusing to combine it with --windowed\n");
        return false;
    }
    if (g_cfg.headless && (g_cfg.windowed || g_cfg.refresh_hz > 0 || g_cfg.backend == BACKEND_OWN)) {
        fprintf(stderr, "perf1vk: --no-window runs with no window and no swapchain; it cannot combine with "
                        "--windowed, --refresh or --backend own\n");
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
    printf("adapter: %s (LUID %08lx:%08lx)\n", name, (unsigned long) d.AdapterLuid.HighPart,
           (unsigned long) d.AdapterLuid.LowPart);
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
    bool stuck = false;
    if (!parse_args(argc, argv)) {
        usage(argv[0]);
        return 2;
    }
    if (!vk_layers_apply(g_cfg.layers_off)) {
        fprintf(stderr, "perf1vk: unknown --layers-off value '%s'\n", g_cfg.layers_off);
        return 2;
    }
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    if (g_cfg.low_priority)
        SetPriorityClass(GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS);
    timeBeginPeriod(1);
    bool refresh_switched = false;
    struct decoder_info di = {0};
    HANDLE vb_threads[MAX_MONITORS] = {0};
    IDXGIAdapter1 *adapter = NULL;
    LUID luid = {0};
    pl_queue queues[MAX_MONITORS] = {0};

    if (g_cfg.refresh_hz > 0) {
        if (!switch_refresh(g_cfg.refresh_device, g_cfg.refresh_hz)) {
            fwprintf(stderr, L"perf1vk: %ls: switching to %d Hz failed\n", g_cfg.refresh_device, g_cfg.refresh_hz);
            timeEndPeriod(1);
            return 1;
        }
        refresh_switched = true;
        wprintf(L"%ls switched to %d Hz for this run\n", g_cfg.refresh_device, g_cfg.refresh_hz);
    }

    adapter = pick_adapter(&luid);
    if (!adapter || !enumerate_monitors(adapter)) {
        fprintf(stderr, "perf1vk: no adapter with outputs attached to the desktop\n");
        goto cleanup;
    }
    if (g_cfg.probe_only) {
        probe(adapter);
        rc = 0;
        goto cleanup;
    }
    if (g_cfg.device_test) {
        // No window, no surface: the instance and the device with the extensions this --backend and --decode would ask for.
        if (!gpu_instance_create(&g_gpu, &g_cfg) || !gpu_device_create(&g_gpu, &g_cfg, &luid, VK_NULL_HANDLE))
            goto cleanup;
        printf("device test ok: %s; present timing set %s; Vulkan Video device for FFmpeg %s\n", g_gpu.device_name,
               g_gpu.have_timing ? "ON" : "off", g_gpu.ff_vk_device ? "adopted by FFmpeg" : "not requested");
        rc = 0;
        goto cleanup;
    }
    CreateDirectoryA(g_cfg.out_dir, NULL);

    if (!assign_geometry() || !create_windows())
        goto cleanup;
    if (!init_gpu(&luid))
        goto cleanup;
    vk_layers_foreach_foreign_module(note_foreign_module, NULL);
    for (int i = 0; i < g_nmon; i++)
        queues[i] = g_mon[i].queue;
    g_dec = decoder_open(&g_cfg, g_gpu.gpu, g_gpu.ff_vk_device, queues, g_nmon);
    if (!g_dec) {
        fprintf(stderr, "perf1vk: decoder_open failed\n");
        goto cleanup;
    }
    decoder_get_info(g_dec, &di);
    if (!decoder_start(g_dec)) {
        fprintf(stderr, "perf1vk: decoder thread failed to start\n");
        goto cleanup;
    }
    for (int i = 0; i < g_nmon; i++)
        vb_threads[i] = CreateThread(NULL, 0, vblank_thread, &g_mon[i], 0, NULL);
    for (int i = 0; i < g_nmon; i++)
        if (!rowmon_start_sampler(&g_mon[i].row))
            fail(&g_mon[i], "scan-line sampler thread failed to start");
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
    // Fit the scan-line grids; from here the film runs on them.
    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
        if (!rowmon_fit(&m->row, &m->row.fit_start)) {
            fail(m, "no scan-line fit at film start (%zu samples)", m->row.fit_start.taken);
            goto stop_threads;
        }
        // the constant gap between this monitor's wake-up grid and its top-row grid
        row_vgrid vg = grid_of(m);
        rowmon_set_wake_to_top(&m->row, &vg, now_s());
        const scan_timing *t = &m->row.timing;
        printf("  %s: signal %ux%u total, line %.4f us, scan %.4f ms, blank %.4f ms; scan grid %zu of %zu samples, residual p50 %.2f us; "
               "top row %.3f ms after the vblank wake-up; height %.1f mm, top %.1f mm, lag %.2f ms\n", m->name,
               t->total_w, t->total_h, t->line_s * 1e6, t->scan_s * 1e3, t->blank_s * 1e3, m->row.fit_start.samples,
               m->row.fit_start.taken, m->row.fit_start.resid_p50_us, m->row.wake_to_top_s * 1e3, m->row.height_mm,
               m->row.top_mm, m->row.lag_s * 1e3);
    }
    printf("aim row %.1f mm; shared height %.1f..%.1f mm\n", g_aim_mm, g_band_top_mm, g_band_bottom_mm);
    choose_film_start(di.fps);
    printf("picture %.1f x %.1f mm (aspect %.4f) at x %.1f..%.1f mm; ", g_pic_x1 - g_pic_x0, g_pic_y1 - g_pic_y0, aspect,
           g_pic_x0, g_pic_x1);
    for (int i = 0; i < g_nmon; i++)
        printf("%s: %s ", g_mon[i].name, g_mon[i].has_picture ? "shows part" : "black");
    printf("\n");
    gpu_note("film clock started: t0 %.6f, hold %lld refreshes, locked %d", g_t0, (long long) g_hold, (int) g_locked);
    fflush(stdout);

    atomic_store(&g_render_running, g_nmon);
    for (int i = 0; i < g_nmon; i++) {
        g_mon[i].thread = CreateThread(NULL, 0, render_thread, &g_mon[i], 0, NULL);
        if (!g_mon[i].thread) {
            fprintf(stderr, "perf1vk: render thread failed to start\n");
            atomic_fetch_sub(&g_render_running, 1);
            atomic_store(&g_stop, true);
        }
    }

    // Main thread: window messages, Esc (also read globally, so it works without focus), a hard deadline.
    double deadline = g_t0 + g_cfg.seconds + 5.0;
    while (atomic_load(&g_render_running) > 0) {
        MSG msg;
        while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        if (!g_cfg.headless && (GetAsyncKeyState(VK_ESCAPE) & 0x8000))
            atomic_store(&g_stop, true);
        if (now_s() > deadline) {
            fprintf(stderr, "perf1vk: deadline passed, stopping\n");
            atomic_store(&g_stop, true);
        }
        Sleep(5);
    }
    rc = 0;

stop_threads:
    atomic_store(&g_stop, true);
    for (int i = 0; i < g_nmon; i++)
        if (g_mon[i].thread) {
            if (WaitForSingleObject(g_mon[i].thread, 5000) == WAIT_TIMEOUT) {
                // Blocked inside the swapchain (an acquire that never returns). Destroying it would wait for the
                // lock that thread holds, so the logs are written and the process ends without tearing down.
                fprintf(stderr, "perf1vk: %s: render thread did not stop within 5 s, it is stuck in the swapchain\n",
                        g_mon[i].name);
                gpu_note("%s: render thread did not stop within 5 s", g_mon[i].name);
                snprintf(g_mon[i].error, sizeof(g_mon[i].error), "render thread stuck in the swapchain at the end");
                stuck = true;
            }
            CloseHandle(g_mon[i].thread);
            g_mon[i].thread = NULL;
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
    // The samplers ran through the whole film, so the end-of-run fit scores it.
    for (int i = 0; i < g_nmon; i++)
        rowmon_stop_sampler(&g_mon[i].row);
    for (int i = 0; i < g_nmon; i++)
        if (g_mon[i].sw.own)
            own_swap_finish(&g_gpu, &g_mon[i].sw);
    if (g_t0 > 0.0) {
        for (int i = 0; i < g_nmon; i++) {
            struct monitor *m = &g_mon[i];
            if (!rowmon_fit(&m->row, &m->row.fit_end) && !m->error[0])
                snprintf(m->error, sizeof(m->error), "no scan-line fit at the end");
            row_vgrid vg = grid_of(m);
            rowmon_set_wake_to_top_end(&m->row, &vg, now_s());
            double drift = rowmon_grid_drift_s(&m->row, now_s());
            printf("%s: top-row grid at film start vs end, at the film's end: %+.1f us (end fit: %zu of %zu samples, residual p50 %.2f us, "
                   "p95 %.2f us; the film followed a grid refitted %u times); vblank wake-up to top row %.3f ms at film start, "
                   "%.3f ms at the end\n", m->name, drift * 1e6, m->row.fit_end.samples, m->row.fit_end.taken,
                   m->row.fit_end.resid_p50_us, m->row.fit_end.resid_p95_us, m->row.refits, m->row.wake_to_top_s * 1e3,
                   m->row.wake_to_top_end_s * 1e3);
            write_log(m, &di);
            if (m->error[0])
                rc = 1;
        }
        write_events();
    } else {
        rc = 1;
    }

cleanup:
    if (stuck) {
        fflush(stdout);
        fflush(stderr);
        ExitProcess(4); // display mode changes made with CDS_FULLSCREEN are undone by Windows when the process ends
    }
    if (g_dec)
        decoder_stop(g_dec); // no thread may push into a queue that is being destroyed
    for (int i = 0; i < g_nmon; i++)
        pl_queue_destroy(&g_mon[i].queue); // unmaps queued frames while the GPU still exists
    decoder_destroy(&g_dec);
    for (int i = 0; i < g_nmon; i++) {
        struct monitor *m = &g_mon[i];
        pl_renderer_destroy(&m->rr);
        if (g_gpu.inst)
            swap_destroy(&g_gpu, &m->sw);
        if (m->hwnd)
            DestroyWindow(m->hwnd);
        if (m->output)
            IDXGIOutput_Release(m->output);
        rowmon_close(&m->row);
        vclock_destroy(&m->clock);
        samples_free(&m->render_cpu);
        samples_free(&m->render_gpu);
        samples_free(&m->render_sync);
        samples_free(&m->lead);
        free(m->presents);
        free(m->cycles);
    }
    if (g_gpu.inst)
        gpu_destroy(&g_gpu, &g_cfg);
    gpu_log_free();
    if (adapter)
        IDXGIAdapter1_Release(adapter);
    if (refresh_switched)
        ChangeDisplaySettingsExW(g_cfg.refresh_device, NULL, NULL, 0, NULL);
    timeEndPeriod(1);
    return rc;
}
