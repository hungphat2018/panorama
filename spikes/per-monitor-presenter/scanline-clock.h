// scanline-clock.h: when each monitor draws its rows (signal timing, read from the display controller).
//
// IDXGIOutput::WaitForVBlank and IDXGISwapChain::GetFrameStatistics mark the start of the vertical blank,
// just after a refresh's bottom row. The next picture's top row comes one blank later, and the blank depends
// on the mode (Setup A: laptop 0.46 ms; external 0.31 ms at 100 Hz, 0.72 ms at 96 Hz, 4.18 ms at 72 Hz).
// Seam timing compares rows, so each monitor gets:
//   - its exact timing (line time, scan and blank lengths) from QueryDisplayConfig;
//   - a grid of its top-row times, fitted to D3DKMTGetScanLine samples taken over picture rows. Samples
//     inside the blank are numbered differently (front porch on top of the range, then from 0 again) and
//     are dropped.
// Row y of a refresh (0 = top) is drawn at top_row_time + y * line_s; the whole picture takes scan_s.
//
// Header-only, C11 and C++17. No WDK header: the three D3DKMT entry points are loaded from gdi32.dll.
// Link user32 (QueryDisplayConfig). A scan_grid is not thread-safe: lock around it when shared.
#pragma once
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

typedef struct scan_timing {
    double refresh_hz;      // exact signal rate (vSyncFreq)
    double line_s;          // one line, active plus horizontal blank (1 / hSyncFreq)
    double scan_s;          // top row to the end of the bottom row: active_h * line_s
    double blank_s;         // end of the bottom row to the next top row: (total_h - active_h) * line_s
    double pixel_rate_hz;
    unsigned active_w, active_h, total_w, total_h;
    bool interlaced;
} scan_timing;

typedef struct scan_source {
    UINT adapter;           // D3DKMT adapter handle
    UINT source;            // VidPN source id
} scan_source;

// Top-row grid of one monitor: refresh k's top row at base + k * period (QPC seconds).
typedef struct scan_grid {
    double *t;              // top-row estimates, one per sample, in sample order
    size_t n, cap;
    double base, period;    // last fit; base is the top row of the refresh holding the fit's first sample
    double resid_p50_us, resid_p95_us;
    size_t used;            // samples in the last fit
} scan_grid;

static inline double scan_now_s(void)
{
    static double freq = 0.0;
    LARGE_INTEGER t;
    if (freq == 0.0) {
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        freq = (double) f.QuadPart;
    }
    QueryPerformanceCounter(&t);
    return (double) t.QuadPart / freq;
}

// ---- exact timing of the current mode ----------------------------------------------------------

// gdi_name: L"\\\\.\\DISPLAY1" (DXGI_OUTPUT_DESC::DeviceName).
static inline bool scan_timing_query(const wchar_t *gdi_name, scan_timing *out)
{
    UINT32 np = 0, nm = 0;
    bool ok = false;
    if (GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, &np, &nm) != ERROR_SUCCESS)
        return false;
    DISPLAYCONFIG_PATH_INFO *paths = (DISPLAYCONFIG_PATH_INFO *) calloc(np ? np : 1, sizeof(*paths));
    DISPLAYCONFIG_MODE_INFO *modes = (DISPLAYCONFIG_MODE_INFO *) calloc(nm ? nm : 1, sizeof(*modes));
    if (paths && modes &&
        QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, &np, paths, &nm, modes, NULL) == ERROR_SUCCESS) {
        for (UINT32 i = 0; i < np && !ok; i++) {
            DISPLAYCONFIG_SOURCE_DEVICE_NAME src;
            memset(&src, 0, sizeof(src));
            src.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME;
            src.header.size = sizeof(src);
            src.header.adapterId = paths[i].sourceInfo.adapterId;
            src.header.id = paths[i].sourceInfo.id;
            if (DisplayConfigGetDeviceInfo(&src.header) != ERROR_SUCCESS ||
                wcscmp(src.viewGdiDeviceName, gdi_name) != 0)
                continue;
            UINT32 mi = paths[i].targetInfo.modeInfoIdx;
            if (mi >= nm || modes[mi].infoType != DISPLAYCONFIG_MODE_INFO_TYPE_TARGET)
                continue;
            const DISPLAYCONFIG_VIDEO_SIGNAL_INFO *s = &modes[mi].targetMode.targetVideoSignalInfo;
            if (!s->hSyncFreq.Numerator || !s->hSyncFreq.Denominator || !s->vSyncFreq.Numerator ||
                !s->vSyncFreq.Denominator || s->totalSize.cy <= s->activeSize.cy)
                continue;
            memset(out, 0, sizeof(*out));
            out->refresh_hz = (double) s->vSyncFreq.Numerator / (double) s->vSyncFreq.Denominator;
            out->line_s = (double) s->hSyncFreq.Denominator / (double) s->hSyncFreq.Numerator;
            out->pixel_rate_hz = (double) s->pixelRate;
            out->active_w = s->activeSize.cx;
            out->active_h = s->activeSize.cy;
            out->total_w = s->totalSize.cx;
            out->total_h = s->totalSize.cy;
            out->scan_s = (double) out->active_h * out->line_s;
            out->blank_s = (double) (out->total_h - out->active_h) * out->line_s;
            out->interlaced = s->scanLineOrdering != DISPLAYCONFIG_SCANLINE_ORDERING_PROGRESSIVE;
            ok = true;
        }
    }
    free(paths);
    free(modes);
    return ok;
}

// ---- scan-line samples ---------------------------------------------------------------------------

typedef struct { WCHAR DeviceName[32]; UINT hAdapter; LUID AdapterLuid; UINT VidPnSourceId; } scan_kmt_open_t;
typedef struct { UINT hAdapter; UINT VidPnSourceId; BOOLEAN InVerticalBlank; UINT ScanLine; } scan_kmt_line_t;
typedef struct { UINT hAdapter; } scan_kmt_close_t;
typedef LONG (APIENTRY *scan_kmt_fn)(void *);

static inline scan_kmt_fn scan_kmt(const char *name)
{
    HMODULE m = GetModuleHandleW(L"gdi32.dll");
    if (!m)
        m = LoadLibraryW(L"gdi32.dll");
    return m ? (scan_kmt_fn) (void (*)(void)) GetProcAddress(m, name) : NULL;
}

static inline bool scan_source_open(const wchar_t *gdi_name, scan_source *out)
{
    scan_kmt_fn open = scan_kmt("D3DKMTOpenAdapterFromGdiDisplayName");
    scan_kmt_open_t o;
    memset(&o, 0, sizeof(o));
    for (int i = 0; i < 31 && gdi_name[i]; i++)
        o.DeviceName[i] = gdi_name[i];
    if (!open || open(&o) != 0)
        return false;
    out->adapter = o.hAdapter;
    out->source = o.VidPnSourceId;
    return true;
}

static inline void scan_source_close(scan_source *s)
{
    scan_kmt_fn close = scan_kmt("D3DKMTCloseAdapter");
    scan_kmt_close_t c = { s->adapter };
    if (close && s->adapter)
        close(&c);
    s->adapter = 0;
}

// One scan-line sample. Returns false inside the blank, or when the call took over 50 us (preempted).
// *t_top = when the top row of the refresh being drawn was drawn (QPC seconds).
static inline bool scan_sample_top(const scan_source *s, const scan_timing *t, double *t_top)
{
    static scan_kmt_fn get = NULL;
    if (!get)
        get = scan_kmt("D3DKMTGetScanLine");
    if (!get)
        return false;
    scan_kmt_line_t q;
    memset(&q, 0, sizeof(q));
    q.hAdapter = s->adapter;
    q.VidPnSourceId = s->source;
    double a = scan_now_s();
    LONG st = get(&q);
    double b = scan_now_s();
    if (st != 0 || q.InVerticalBlank || q.ScanLine >= t->active_h || b - a > 50e-6)
        return false;
    *t_top = 0.5 * (a + b) - (double) q.ScanLine * t->line_s;
    return true;
}

// ---- top-row grid ----------------------------------------------------------------------------------

static inline void scan_grid_add(scan_grid *g, double t_top)
{
    if (g->n == g->cap) {
        size_t cap = g->cap ? g->cap * 2 : 4096;
        double *p = (double *) realloc(g->t, cap * sizeof(double));
        if (!p)
            return; // out of memory: keep the samples we have
        g->t = p;
        g->cap = cap;
    }
    g->t[g->n++] = t_top;
}

static inline void scan_grid_free(scan_grid *g)
{
    free(g->t);
    memset(g, 0, sizeof(*g));
}

static inline int scan_cmp_double(const void *a, const void *b)
{
    double x = *(const double *) a, y = *(const double *) b;
    return (x > y) - (x < y);
}

// Least-squares line through the samples taken within the last window_s seconds (0 = all), each indexed by
// its refresh relative to the first one. Two passes: index with nominal_period (1 / refresh_hz), fit, re-index
// with the fitted period and drop outliers (over 4x the median residual and over 10 us), fit again.
static inline bool scan_grid_fit(scan_grid *g, double nominal_period, double window_s)
{
    if (g->n < 16 || nominal_period <= 0.0)
        return false;
    size_t first = 0;
    if (window_s > 0.0)
        while (first < g->n && g->t[first] < g->t[g->n - 1] - window_s)
            first++;
    size_t m = g->n - first;
    if (m < 16)
        return false;
    double *res = (double *) malloc(m * sizeof(double));
    if (!res)
        return false;
    const double r = g->t[first];
    double period = nominal_period, icpt = 0.0, cut = INFINITY;
    bool ok = false;
    for (int pass = 0; pass < 3; pass++) {
        double sk = 0, st = 0, skk = 0, skt = 0, n = 0;
        for (size_t i = first; i < g->n; i++) {
            double ti = g->t[i] - r;
            double k = (double) llround((ti - icpt) / period);
            if (fabs(ti - (icpt + k * period)) > cut)
                continue;
            sk += k; st += ti; skk += k * k; skt += k * ti; n += 1;
        }
        double den = n * skk - sk * sk;
        if (n < 16 || den <= 0.0)
            break;
        double p = (n * skt - sk * st) / den;
        if (!(p > 0.5 * nominal_period && p < 1.5 * nominal_period))
            break;
        period = p;
        icpt = (st - p * sk) / n;
        size_t c = 0;
        for (size_t i = first; i < g->n; i++) {
            double ti = g->t[i] - r;
            double k = (double) llround((ti - icpt) / period);
            res[c++] = fabs(ti - (icpt + k * period));
        }
        qsort(res, c, sizeof(double), scan_cmp_double);
        double p50 = res[c / 2];
        cut = fmax(4.0 * p50, 10e-6);
        g->period = period;
        g->base = r + icpt;
        g->resid_p50_us = p50 * 1e6;
        g->resid_p95_us = res[(size_t) (0.95 * (double) (c - 1))] * 1e6;
        g->used = (size_t) n;
        ok = true;
    }
    free(res);
    return ok;
}

// First top row at or after t, on the last fit.
static inline double scan_grid_top_after(const scan_grid *g, double t)
{
    return g->base + ceil((t - g->base) / g->period - 1e-9) * g->period;
}

// Top row nearest t, on the last fit.
static inline double scan_grid_top_nearest(const scan_grid *g, double t)
{
    return g->base + floor((t - g->base) / g->period + 0.5) * g->period;
}
