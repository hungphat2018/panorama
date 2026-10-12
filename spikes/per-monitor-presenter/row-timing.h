// row-timing.h: when a monitor lights up each row of the picture (shared by the per-monitor spikes).
//
// Built on scanline-clock.h (read its header comment first). Header-only, valid as C11 and as C++17, so the
// C++ presenter (per-monitor-presenter.cpp) and the C player (spikes/performer-p1) run the same model. Include
// it after per-monitor-common.h in C++ (that header defines NOMINMAX before windows.h). Link user32; the
// process must call timeBeginPeriod(1), or the sampler's Sleep(1) turns into 15 ms (with it, about 2 ms).
//
// One row_mon per monitor holds
//   - the monitor's exact signal timing (scan_timing_query);
//   - a sampler thread that feeds a scan_grid with top-row times and, once a second, refits the last 10 s of
//     them into the "live" grid that light times follow;
//   - two whole-grid fits for the record: one when the film starts, one at the end of the run (scores the run);
//   - the monitor's place on the shared vertical axis: height, top offset and panel lag.
//
// Light time. IDXGIOutput::WaitForVBlank and DXGI frame statistics mark the start of the vertical blank. A
// present that lands on the refresh whose WaitForVBlank time is v shows its top row at top_row(v), a line of the
// scan grid one blank after the blank start (0.4 ms on the laptop, 0.2 ms to 4 ms on the external by mode). Row y
// of the picture, y in millimetres on the shared vertical axis (y grows down, the master's top is 0), lights up at
//     light(v, y) = top_row(v) + (y - top_mm) / height_mm * scan_s + lag_s
// Two monitors compare at the same physical y, so a seam offset is light(follower) - light(master) at that y.
//
// top_row(v) is the grid line nearest the middle of the blank after v (v + blank_s / 2). That is the first line
// at or after v, the "top_after" of the analysis, whenever the grid is exact; unlike top_after it can't slip by a
// whole refresh when v and the grid disagree by more than the gap between a wake-up and its top row (0.2 ms on
// the external at 100 Hz), and it holds for disagreements of up to about half a refresh.
//
// Why the grid is refitted while the film runs: a 1.5 s fit has a period good to a few ppm (measured against the
// whole-run fit: 0.1 to 3 ppm), which extrapolated over 300 s is up to 1 ms, enough to make a
// schedule flip to another refresh on its own. The wake-up times can't stand in for the grid either: their delay
// after the blank start moves with load (the laptop's moved by up to 0.37 ms within one run: 0.06 ms at the film
// start, 0.43 ms at the end). The grid is the hardware; refitting it keeps extrapolation to a second.
//
// Threads. The sampler thread (lock) adds samples and refits; render threads read the live grid (live_lock) through
// rowmon_top_row / rowmon_light. rowmon_fit and the wake_to_top setters belong to the main thread, at the start and
// at the end, with no render thread running.
#pragma once
#include "scanline-clock.h"

#include <stdio.h>

#ifndef ROWMON_WINDOW_S
#define ROWMON_WINDOW_S 10.0 // the live grid is fitted to this many seconds of samples
#endif
#ifndef ROWMON_REFIT_S
#define ROWMON_REFIT_S 1.0   // and fitted again this often
#endif

typedef struct row_fit {
    bool ok;
    double base, period;            // top row of refresh k at base + k * period (QPC seconds)
    double resid_p50_us, resid_p95_us;
    size_t samples;                 // samples the fit used
    size_t taken;                   // picture-row samples gathered up to the fit
} row_fit;

typedef struct row_mon {
    wchar_t gdi_name[32];           // L"\\\\.\\DISPLAY1"
    scan_timing timing;             // exact signal timing of the current mode
    scan_source src;                // D3DKMT handle for the scan-line samples
    bool have_src, have_locks;
    double height_mm;               // physical height of the picture area
    double top_mm;                  // its top on the shared vertical axis (the master's top is 0)
    double lag_s;                   // panel lag: the light comes this long after the signal
    CRITICAL_SECTION lock;          // guards grid: the sampler adds and refits, the main thread fits
    scan_grid grid;
    HANDLE thread;
    volatile LONG stop;
    CRITICAL_SECTION live_lock;     // guards live: the sampler publishes, render threads read
    row_fit live;                   // the grid light times follow: refitted from the last ROWMON_WINDOW_S seconds
    unsigned refits;                // live refits published (read after the sampler stopped)
    row_fit fit_start, fit_end;     // whole-grid fits at film start and at the end of the run
    row_fit active;                 // the whole-grid fit the post-hoc functions use: fit_start, then fit_end
    double wake_to_top_s;           // top row minus WaitForVBlank wake-up, between the fitted lines (film start)
    double wake_to_top_end_s;       // the same, measured again at the end of the run
    bool have_wake_to_top, have_wake_to_top_end;
} row_mon;

// A snapshot of a monitor's refresh grid (the fit of its WaitForVBlank wake-ups): refresh k is woken at
// base + k * period. Taken from VblankClock::line() / vclock_line(); nothing here locks.
typedef struct row_vgrid {
    double base, period;
} row_vgrid;

static inline double row_vgrid_time(const row_vgrid *g, int64_t k)
{
    return g->base + (double) k * g->period;
}

static inline int64_t row_vgrid_index_near(const row_vgrid *g, double t)
{
    return (int64_t) llround((t - g->base) / g->period);
}

// ---- set-up and tear-down ----------------------------------------------------------------------------

// A row_mon with nothing in it but its two locks (rowmon_open fills the rest; the self-test fills it by hand).
static inline void rowmon_init_empty(row_mon *r)
{
    memset(r, 0, sizeof(*r));
    InitializeCriticalSection(&r->lock);
    InitializeCriticalSection(&r->live_lock);
    r->have_locks = true;
}

// Reads the monitor's exact timing and opens its scan-line source. gdi_name: DXGI_OUTPUT_DESC::DeviceName.
// Returns false (and leaves r safe to rowmon_close) if either fails.
static inline bool rowmon_open(row_mon *r, const wchar_t *gdi_name, double height_mm)
{
    rowmon_init_empty(r);
    for (int i = 0; i < 31 && gdi_name[i]; i++)
        r->gdi_name[i] = gdi_name[i];
    r->height_mm = height_mm;
    if (!scan_timing_query(r->gdi_name, &r->timing))
        return false;
    if (!scan_source_open(r->gdi_name, &r->src))
        return false;
    r->have_src = true;
    return true;
}

// Fits the picture-row samples of the last window_s seconds (0 = all) into *out. Does not touch r->active.
static inline bool rowmon_fit_window(row_mon *r, double window_s, row_fit *out)
{
    memset(out, 0, sizeof(*out));
    EnterCriticalSection(&r->lock);
    out->taken = r->grid.n;
    out->ok = scan_grid_fit(&r->grid, 1.0 / r->timing.refresh_hz, window_s);
    if (out->ok) {
        out->base = r->grid.base;
        out->period = r->grid.period;
        out->resid_p50_us = r->grid.resid_p50_us;
        out->resid_p95_us = r->grid.resid_p95_us;
        out->samples = r->grid.used;
    }
    LeaveCriticalSection(&r->lock);
    return out->ok;
}

static inline void rowmon_publish(row_mon *r, const row_fit *f)
{
    EnterCriticalSection(&r->live_lock);
    r->live = *f;
    r->refits++;
    LeaveCriticalSection(&r->live_lock);
}

// One live refit: the last ROWMON_WINDOW_S seconds of samples. A failed fit leaves the live grid as it was.
static inline bool rowmon_refit_live(row_mon *r)
{
    row_fit f;
    if (!rowmon_fit_window(r, ROWMON_WINDOW_S, &f))
        return false;
    rowmon_publish(r, &f);
    return true;
}

static inline DWORD WINAPI rowmon_sampler_main(LPVOID arg)
{
    row_mon *r = (row_mon *) arg;
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL);
    double next_refit = scan_now_s() + 0.5;
    // Diagnostic switch: PANORAMA_SCAN_SAMPLER_SECONDS=S stops reading the scan line S seconds after the sampler
    // starts (the grid then extrapolates from the samples it has). Used to test whether the scan-line reads
    // themselves make presents late (seen at 240 + 72 Hz).
    const char *limit = getenv("PANORAMA_SCAN_SAMPLER_SECONDS");
    const double stop_reading_at = limit ? scan_now_s() + atof(limit) : INFINITY;
    while (!r->stop) {
        double t;
        if (scan_now_s() < stop_reading_at && scan_sample_top(&r->src, &r->timing, &t)) {
            EnterCriticalSection(&r->lock);
            scan_grid_add(&r->grid, t);
            LeaveCriticalSection(&r->lock);
        }
        double now = scan_now_s();
        if (now >= next_refit) {
            rowmon_refit_live(r);
            next_refit = now + ROWMON_REFIT_S;
        }
        Sleep(1); // with timeBeginPeriod(1) this returns after about 2 ms
    }
    return 0;
}

static inline bool rowmon_start_sampler(row_mon *r)
{
    r->stop = 0;
    r->thread = CreateThread(NULL, 0, rowmon_sampler_main, r, 0, NULL);
    return r->thread != NULL;
}

static inline void rowmon_stop_sampler(row_mon *r)
{
    if (!r->thread)
        return;
    InterlockedExchange(&r->stop, 1);
    WaitForSingleObject(r->thread, 2000);
    CloseHandle(r->thread);
    r->thread = NULL;
}

static inline void rowmon_close(row_mon *r)
{
    rowmon_stop_sampler(r);
    if (r->have_src)
        scan_source_close(&r->src);
    r->have_src = false;
    scan_grid_free(&r->grid);
    if (r->have_locks) {
        DeleteCriticalSection(&r->lock);
        DeleteCriticalSection(&r->live_lock);
    }
    r->have_locks = false;
}

// Fits every picture-row sample gathered so far into *out, for the record (fit_start / fit_end): it becomes the
// whole-grid fit the post-hoc functions use, and the live grid too (so the film starts on it). Main thread only.
// Returns false if there are too few samples (the sampler is not running, or the scan line can't be read).
static inline bool rowmon_fit(row_mon *r, row_fit *out)
{
    if (!rowmon_fit_window(r, 0.0, out))
        return false;
    r->active = *out;
    rowmon_publish(r, out);
    return true;
}

// ---- light time --------------------------------------------------------------------------------------

// A scan_grid holding just the fitted line of f, for the scan_grid_top_* queries.
static inline scan_grid rowmon_line_of(const row_fit *f)
{
    scan_grid g;
    memset(&g, 0, sizeof(g));
    g.base = f->base;
    g.period = f->period;
    return g;
}

// The live grid, copied under its lock.
static inline row_fit rowmon_live_copy(const row_mon *r)
{
    row_mon *w = (row_mon *) r; // the lock is state, not value
    EnterCriticalSection(&w->live_lock);
    row_fit f = w->live;
    LeaveCriticalSection(&w->live_lock);
    return f;
}

// Top row of the refresh woken at v: the live grid's line nearest the middle of the blank after v.
// NaN before any grid has been fitted.
static inline double rowmon_top_row(const row_mon *r, double v)
{
    row_fit f = rowmon_live_copy(r);
    if (!f.ok)
        return NAN;
    scan_grid g = rowmon_line_of(&f);
    return scan_grid_top_nearest(&g, v + 0.5 * r->timing.blank_s);
}

// Top row of the first refresh at or after t, on the whole-grid fit (post-hoc: used to measure wake_to_top).
static inline double rowmon_top_after(const row_mon *r, double t)
{
    scan_grid g = rowmon_line_of(&r->active);
    return scan_grid_top_after(&g, t);
}

// How far the grid fitted at film start has drifted from the one fitted at the end, near time t: the nearest
// top row on the start fit minus the nearest on the end fit, in seconds. NaN if either fit failed.
static inline double rowmon_grid_drift_s(const row_mon *r, double t)
{
    if (!r->fit_start.ok || !r->fit_end.ok)
        return NAN;
    scan_grid a = rowmon_line_of(&r->fit_start), b = rowmon_line_of(&r->fit_end);
    return scan_grid_top_nearest(&a, t) - scan_grid_top_nearest(&b, t);
}

// Wake-up to top row, between the two fitted lines: for the refresh of vg woken nearest t, the first top row of
// the whole-grid fit at or after its wake-up, minus the wake-up. The same for every refresh while the lines are
// parallel. It grows with load (the wake-up gets later); the light times don't use it, only the search for a
// refresh near a top row and the log.
static inline double rowmon_wake_to_top_at(const row_mon *r, const row_vgrid *vg, double t)
{
    double v = row_vgrid_time(vg, row_vgrid_index_near(vg, t));
    return rowmon_top_after(r, v) - v;
}

// Measures wake_to_top (call it right after the film-start fit), or the end-of-run value (right after the end
// fit). vg is the monitor's refresh grid now, t a time near it. No fit, no measurement.
static inline bool rowmon_set_wake_to_top(row_mon *r, const row_vgrid *vg, double t)
{
    r->have_wake_to_top = r->active.ok && vg->period > 0.0;
    r->wake_to_top_s = r->have_wake_to_top ? rowmon_wake_to_top_at(r, vg, t) : 0.0;
    return r->have_wake_to_top;
}

static inline bool rowmon_set_wake_to_top_end(row_mon *r, const row_vgrid *vg, double t)
{
    r->have_wake_to_top_end = r->active.ok && vg->period > 0.0;
    r->wake_to_top_end_s = r->have_wake_to_top_end ? rowmon_wake_to_top_at(r, vg, t) : 0.0;
    return r->have_wake_to_top_end;
}

// Seconds from a refresh's top row to its row at height y_mm on the shared axis.
static inline double rowmon_row_s(const row_mon *r, double y_mm)
{
    return (y_mm - r->top_mm) / r->height_mm * r->timing.scan_s;
}

// When row y_mm lights up for the present that lands on the refresh woken at v.
static inline double rowmon_light(const row_mon *r, double v, double y_mm)
{
    return rowmon_top_row(r, v) + rowmon_row_s(r, y_mm) + r->lag_s;
}

// The band of the shared vertical axis that both monitors cover. False if they share none.
static inline bool rowmon_shared_band(const row_mon *a, const row_mon *b, double *top_mm, double *bottom_mm)
{
    double a_bottom = a->top_mm + a->height_mm, b_bottom = b->top_mm + b->height_mm;
    double top = a->top_mm > b->top_mm ? a->top_mm : b->top_mm;
    double bottom = a_bottom < b_bottom ? a_bottom : b_bottom;
    if (!(bottom > top))
        return false;
    *top_mm = top;
    *bottom_mm = bottom;
    return true;
}

// ---- choosing refreshes by the light of a row ----------------------------------------------------------

// When monitor r lights up height y_mm of the picture presented on refresh index k of its grid g.
static inline double rowmon_light_at(const row_mon *r, const row_vgrid *g, int64_t k, double y_mm)
{
    return rowmon_light(r, row_vgrid_time(g, k), y_mm);
}

// Index of the refresh of r whose picture is lit nearest to light time t at height y_mm. The refresh nearest in
// time is searched around the one whose top row would put that row at t; its neighbours are tried too.
static inline int64_t rowmon_refresh_lit_nearest(const row_mon *r, const row_vgrid *g, double t, double y_mm)
{
    double top_wanted = t - rowmon_row_s(r, y_mm) - r->lag_s;
    int64_t k0 = row_vgrid_index_near(g, top_wanted - r->wake_to_top_s);
    int64_t best = k0;
    double best_err = INFINITY;
    for (int64_t k = k0 - 1; k <= k0 + 1; k++) {
        double err = fabs(rowmon_light_at(r, g, k, y_mm) - t);
        if (err < best_err - 1e-9) {
            best_err = err;
            best = k;
        }
    }
    return best;
}

// The follower's refresh that carries the picture the master shows on its refresh index j: the one whose
// picture is lit nearest, at the aim row, to the moment the master's screen lights it.
static inline int64_t row_follower_refresh(const row_mon *master, const row_vgrid *mg, const row_mon *fol,
                                           const row_vgrid *fg, int64_t j, double aim_mm)
{
    return rowmon_refresh_lit_nearest(fol, fg, rowmon_light_at(master, mg, j, aim_mm), aim_mm);
}

// Aimed start: of `count` master refreshes from j0, the one whose follower refresh lights the aim row closest
// to the moment the master lights it. Returns the master refresh index; *err_s is that distance in seconds.
static inline int64_t row_aim_start(const row_mon *master, const row_vgrid *mg, const row_mon *fol,
                                    const row_vgrid *fg, int64_t j0, int count, double aim_mm, double *err_s)
{
    int64_t best = j0;
    double best_err = INFINITY;
    for (int64_t j = j0; j < j0 + count; j++) {
        double lit = rowmon_light_at(master, mg, j, aim_mm);
        int64_t kf = rowmon_refresh_lit_nearest(fol, fg, lit, aim_mm);
        double err = fabs(rowmon_light_at(fol, fg, kf, aim_mm) - lit);
        if (err < best_err - 1e-6) {
            best_err = err;
            best = j;
        }
    }
    *err_s = best_err;
    return best;
}

// ---- log fields --------------------------------------------------------------------------------------

static inline void rowmon_json_fit(FILE *f, const char *key, const row_fit *x)
{
    fprintf(f, ",\"%s\":{\"ok\":%s,\"base\":%.9f,\"period\":%.12f,\"resid_p50_us\":%.3f,\"resid_p95_us\":%.3f,"
               "\"samples\":%zu,\"taken\":%zu}",
            key, x->ok ? "true" : "false", x->base, x->period, x->resid_p50_us, x->resid_p95_us, x->samples,
            x->taken);
}

// Writes ,"timing":{...},"scan_grid":{...},"scan_grid_at_start":{...},"live_refits":n,"wake_to_top_ms":x,
// "wake_to_top_end_ms":x,"geometry":{...} (a leading comma, no braces around them) into a JSON object that
// already has fields. scan_grid is the end-of-run fit, scan_grid_at_start the film-start fit, live_refits the
// number of times the grid the film followed was refitted.
static inline void rowmon_write_json(const row_mon *r, FILE *f)
{
    const scan_timing *t = &r->timing;
    fprintf(f, ",\"timing\":{\"refresh_hz\":%.9f,\"line_s\":%.10g,\"scan_s\":%.10g,\"blank_s\":%.10g,"
               "\"pixel_rate_hz\":%.0f,\"active\":[%u,%u],\"total\":[%u,%u],\"interlaced\":%s}",
            t->refresh_hz, t->line_s, t->scan_s, t->blank_s, t->pixel_rate_hz, t->active_w, t->active_h,
            t->total_w, t->total_h, t->interlaced ? "true" : "false");
    rowmon_json_fit(f, "scan_grid", &r->fit_end);
    rowmon_json_fit(f, "scan_grid_at_start", &r->fit_start);
    fprintf(f, ",\"live_refits\":%u", r->refits);
    if (r->have_wake_to_top)
        fprintf(f, ",\"wake_to_top_ms\":%.4f", r->wake_to_top_s * 1e3);
    else
        fprintf(f, ",\"wake_to_top_ms\":null");
    if (r->have_wake_to_top_end)
        fprintf(f, ",\"wake_to_top_end_ms\":%.4f", r->wake_to_top_end_s * 1e3);
    else
        fprintf(f, ",\"wake_to_top_end_ms\":null");
    fprintf(f, ",\"geometry\":{\"height_mm\":%.3f,\"top_mm\":%.3f,\"lag_ms\":%.4f}", r->height_mm, r->top_mm,
            r->lag_s * 1e3);
}
