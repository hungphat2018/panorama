// row-timing-selftest: checks row-timing.h against numbers worked out by hand. No hardware, no window: the
// monitors are built from Setup A's exact signal timings and the grids are made up.
//
// What it covers: the light-time formula against the research table (blank starts aligned: external minus
// laptop +0.26 / +2.29 / +4.33 ms at the top / middle / bottom row at 240 + 96 Hz); the top row of a wake-up
// when the grid and the wake-up grid disagree; the live grid tracking a clock whose rate changes, where a
// whole-run fit lags; reads of the live grid against concurrent publishing (never half of one fit and half of
// another); the nearest-light refresh against a brute-force search; the aimed start (never worse than half the
// alignment-class spacing) with a panel lag; the shared height; wake_to_top between the fitted lines; the
// whole-grid fit; the shape of the JSON fields.
//
// Plain C so it builds both ways the spikes use the header:
//   MSVC as C++17 (the presenter):  compile-row-timing-selftest.bat   (compiles and runs it)
//   gcc as C11 (perf1):  gcc -std=gnu11 -Wall -Wextra -o row-timing-selftest.exe row-timing-selftest.c -luser32
#define _CRT_SECURE_NO_WARNINGS
#include "row-timing.h"

#include <inttypes.h>

static int g_checks, g_failed;

#define CHECK(cond, ...)                                                                     \
    do {                                                                                     \
        g_checks++;                                                                          \
        if (!(cond)) {                                                                       \
            g_failed++;                                                                      \
            printf("FAIL %s:%d: %s\n  ", __FILE__, __LINE__, #cond);                         \
            printf(__VA_ARGS__);                                                             \
            printf("\n");                                                                    \
        }                                                                                    \
    } while (0)

// value and expected in seconds, tolerance in microseconds
#define CHECK_NEAR_US(value, expected, tol_us)                                               \
    CHECK(fabs((value) - (expected)) <= (tol_us) * 1e-6, "%s = %.4f ms, expected %.4f ms (+-%g us)", #value, \
          (value) * 1e3, (expected) * 1e3, (double) (tol_us))

// A monitor with an exact signal timing and a made-up top-row grid, live from the start. `top0` is the top row
// of refresh 0; the wake-up (WaitForVBlank) comes `wake_delay_s` after the blank starts, so the top row comes
// blank_s - wake_delay_s after the wake-up. *vg gets the matching refresh grid (blank start + wake delay).
static void make_mon(row_mon *r, row_vgrid *vg, double refresh_hz, unsigned active_h, unsigned total_h,
                     double height_mm, double top0, double wake_delay_s)
{
    rowmon_init_empty(r);
    scan_timing *t = &r->timing;
    t->refresh_hz = refresh_hz;
    t->line_s = 1.0 / (refresh_hz * (double) total_h);
    t->scan_s = (double) active_h * t->line_s;
    t->blank_s = (double) (total_h - active_h) * t->line_s;
    t->active_h = active_h;
    t->total_h = total_h;
    r->height_mm = height_mm;
    r->active.ok = true;
    r->active.base = top0;
    r->active.period = 1.0 / refresh_hz;
    r->live = r->active;
    r->fit_start = r->active;
    r->fit_end = r->active;
    r->wake_to_top_s = t->blank_s - wake_delay_s;
    r->wake_to_top_end_s = r->wake_to_top_s;
    r->have_wake_to_top = true;
    r->have_wake_to_top_end = true;
    vg->base = top0 - r->wake_to_top_s;
    vg->period = 1.0 / refresh_hz;
}

// Setup A's laptop (2560x1600 in 2720x1800 at 240 Hz) and external (1920x1080 in 2000x1160 at 96 Hz, or in
// 2000x1545 at 72 Hz). `top0` is the top row of refresh 0.
static void make_laptop(row_mon *r, row_vgrid *vg, double top0)
{
    make_mon(r, vg, 240.0, 1600, 1800, 215.0, top0, 0.09e-3);
}
static void make_external96(row_mon *r, row_vgrid *vg, double top0)
{
    make_mon(r, vg, 96.0, 1080, 1160, 268.0, top0, 0.09e-3);
}
static void make_external72(row_mon *r, row_vgrid *vg, double top0)
{
    make_mon(r, vg, 72.0, 1080, 1545, 268.0, top0, 0.09e-3);
}

static void test_timing_numbers(void)
{
    row_mon l, e96, e72;
    row_vgrid vl, v96, v72;
    make_laptop(&l, &vl, 100.0);
    make_external96(&e96, &v96, 100.0);
    make_external72(&e72, &v72, 100.0);
    CHECK_NEAR_US(l.timing.scan_s, 3.7037e-3, 1.0);
    CHECK_NEAR_US(l.timing.blank_s, 0.4630e-3, 1.0);
    CHECK_NEAR_US(e96.timing.scan_s, 9.698e-3, 1.0);
    CHECK_NEAR_US(e96.timing.blank_s, 0.718e-3, 1.0);
    CHECK_NEAR_US(e72.timing.scan_s, 9.709e-3, 1.0);
    CHECK_NEAR_US(e72.timing.blank_s, 4.180e-3, 1.0);
    // row 0 is the top row; the middle of the laptop (107.5 mm) is half its scan
    CHECK_NEAR_US(rowmon_row_s(&l, 0.0), 0.0, 0.001);
    CHECK_NEAR_US(rowmon_row_s(&l, 107.5), 0.5 * l.timing.scan_s, 0.001);
    CHECK_NEAR_US(rowmon_row_s(&e96, 107.5), 107.5 / 268.0 * e96.timing.scan_s, 0.001);
    // a monitor whose top is 10 mm lower has its first row at y = 10 and its last at y = 278
    e96.top_mm = 10.0;
    CHECK_NEAR_US(rowmon_row_s(&e96, 10.0), 0.0, 0.001);
    CHECK_NEAR_US(rowmon_row_s(&e96, 278.0), e96.timing.scan_s, 0.001);
}

// The research table (3.2): blank starts aligned, external minus laptop at 240 + 96 Hz and 240 + 72 Hz.
static void test_aligned_blank_starts(void)
{
    const double T = 1000.0; // blank start of refresh 0 on both
    const double wake = T + 0.09e-3;
    const double ys[3] = {0.0, 107.5, 215.0};
    row_mon l, e96, e72;
    row_vgrid vl, v96, v72;
    make_laptop(&l, &vl, T + 0.4630e-3);
    make_external96(&e96, &v96, T + 0.7184e-3);
    make_external72(&e72, &v72, T + 4.1802e-3);
    const double expect96[3] = {0.26e-3, 2.29e-3, 4.33e-3};
    const double expect72[3] = {3.72e-3, 5.76e-3, 7.80e-3};
    for (int i = 0; i < 3; i++) {
        CHECK_NEAR_US(rowmon_light(&e96, wake, ys[i]) - rowmon_light(&l, wake, ys[i]), expect96[i], 10.0);
        CHECK_NEAR_US(rowmon_light(&e72, wake, ys[i]) - rowmon_light(&l, wake, ys[i]), expect72[i], 10.0);
    }
    // a panel lag moves the light by exactly that much
    double before = rowmon_light(&e96, wake, 50.0);
    e96.lag_s = 1.5e-3;
    CHECK_NEAR_US(rowmon_light(&e96, wake, 50.0) - before, 1.5e-3, 0.001);
}

// A wake-up maps to its own refresh's top row, also when the grid and the wake-up grid disagree by more than the
// gap between a wake-up and its top row (0.37 ms on the laptop, 4.09 ms on the external at 72 Hz): top_after
// would slip a whole refresh, top_row does not. No grid yet: NaN.
static void test_top_row_snapping(void)
{
    const double T = 2000.0;
    row_mon l, e72;
    row_vgrid vl, v72;
    make_laptop(&l, &vl, T);
    make_external72(&e72, &v72, T);
    row_mon *mons[2] = {&l, &e72};
    row_vgrid *grids[2] = {&vl, &v72};
    for (int m = 0; m < 2; m++) {
        row_mon *r = mons[m];
        for (int64_t k = -3; k <= 3; k++) {
            double v = row_vgrid_time(grids[m], k);
            double truth = T + (double) k * r->live.period;
            CHECK_NEAR_US(rowmon_top_row(r, v), truth, 1.0);
            CHECK_NEAR_US(rowmon_top_after(r, v), truth, 1.0); // exact grid: the same line
        }
        const double drift = r->wake_to_top_s + 0.3e-3;
        r->live.base -= drift;
        r->active.base -= drift;
        double v = row_vgrid_time(grids[m], 0);
        CHECK_NEAR_US(rowmon_top_row(r, v), T - drift, 1.0);
        CHECK(rowmon_top_after(r, v) > T - drift + 0.5 * r->live.period, "top_after should have slipped a refresh");
        r->live.base += drift;
        r->active.base += drift;
    }
    row_mon none;
    rowmon_init_empty(&none);
    none.timing.blank_s = 1e-3;
    double nan_row = rowmon_top_row(&none, 5.0);
    CHECK(nan_row != nan_row, "no live grid: NaN");
}

// Synthetic samples of a clock that runs at `period`, starting from a top row at `phase0`: the top row of the
// refresh in progress at each sampling instant t0, t0 + step, ... (jitter +-1.5 us).
static double add_samples(row_mon *r, double phase0, double period, double t_from, double t_to, double step, unsigned *seed)
{
    double phase = phase0;
    double last_top = phase0;
    for (double t = t_from; t < t_to; t += step) {
        // advance the line to the refresh in progress at t (the clock is continuous: lines at phase0 + j * period
        // until the rate changes, which the caller does by passing the line reached so far as phase0)
        double j = floor((t - phase) / period);
        double top = phase + j * period;
        *seed = *seed * 1664525u + 1013904223u;
        double jitter = ((double) (*seed >> 8) / (double) (1u << 24) - 0.5) * 3e-6;
        EnterCriticalSection(&r->lock);
        scan_grid_add(&r->grid, top + jitter);
        LeaveCriticalSection(&r->lock);
        last_top = top;
    }
    return last_top;
}

// The live grid follows a clock whose rate changes (a few ppm, as the whole-run versus 1.5 s fits showed), where a
// fit over the whole run lags.
static void test_live_refit_tracks_a_rate_change(void)
{
    row_mon r;
    rowmon_init_empty(&r);
    r.timing.refresh_hz = 240.0;
    r.timing.blank_s = 0.463e-3;
    const double nominal = 1.0 / 240.0;
    const double p1 = nominal * (1.0 - 1.5e-6), p2 = nominal * (1.0 + 1.5e-6); // 3 ppm apart
    unsigned seed = 99;
    CHECK(!rowmon_refit_live(&r) && r.refits == 0, "no samples: no live grid");
    double edge = add_samples(&r, 100.0, p1, 100.0, 130.0, 0.0021, &seed); // 30 s at p1
    // the clock changes rate at the last line reached: lines continue from there at p2
    add_samples(&r, edge, p2, 130.0, 160.0, 0.0021, &seed);                // 30 s at p2
    CHECK(rowmon_refit_live(&r) && r.refits == 1, "refit after samples");
    CHECK(r.live.ok && r.live.samples > 3000, "live fit used %zu samples", r.live.samples);
    CHECK(fabs(r.live.period / p2 - 1.0) < 0.3e-6, "live period off p2 by %.3f ppm", (r.live.period / p2 - 1.0) * 1e6);
    row_fit whole;
    CHECK(rowmon_fit_window(&r, 0.0, &whole), "whole-run fit");
    CHECK(fabs(whole.period / p2 - 1.0) > 0.8e-6, "the whole-run period should lag p2 (off %.3f ppm)", (whole.period / p2 - 1.0) * 1e6);
    // the live line predicts the next top row a second ahead to a microsecond
    double t_ahead = 161.0;
    double truth = edge + floor((t_ahead - edge) / p2 + 0.5) * p2; // the true line nearest t_ahead
    scan_grid lg = rowmon_line_of(&r.live);
    CHECK_NEAR_US(scan_grid_top_nearest(&lg, t_ahead), truth, 2.0);
    // a failed refit (window with no samples) leaves the live grid and the counter alone
    double before = r.live.period;
    unsigned n = r.refits;
    EnterCriticalSection(&r.lock);
    size_t keep = r.grid.n;
    r.grid.n = 5; // pretend the grid has only five samples
    LeaveCriticalSection(&r.lock);
    CHECK(!rowmon_refit_live(&r) && r.refits == n && r.live.period == before, "failed refit changes nothing");
    EnterCriticalSection(&r.lock);
    r.grid.n = keep;
    LeaveCriticalSection(&r.lock);
    rowmon_close(&r);
}

typedef struct { row_mon *r; volatile LONG stop; long reads, torn, saw_a, saw_b; double v; double line_a, line_b; } reader_ctx;

static DWORD WINAPI reader_main(LPVOID arg)
{
    reader_ctx *c = (reader_ctx *) arg;
    while (!c->stop) {
        double top = rowmon_top_row(c->r, c->v);
        c->reads++;
        if (fabs(top - c->line_a) <= 1e-9)
            c->saw_a++;
        else if (fabs(top - c->line_b) <= 1e-9)
            c->saw_b++;
        else
            c->torn++;
    }
    return 0;
}

// Render threads read the live grid while the sampler publishes new fits: every read is one whole fit.
static void test_live_grid_reads_are_whole(void)
{
    row_mon r;
    rowmon_init_empty(&r);
    r.timing.refresh_hz = 240.0;
    r.timing.blank_s = 0.463e-3;
    row_fit a = {true, 500.0, 1.0 / 240.0, 1.0, 2.0, 100, 100};
    row_fit b = {true, 500.0017, 1.0 / 240.0 * (1.0 + 1e-3), 1.0, 2.0, 100, 100}; // 4 us longer per refresh
    rowmon_publish(&r, &a);
    const double v = 500.0 + 1000.0 / 240.0; // about 1000 refreshes in: the two lines are milliseconds apart
    scan_grid ga = rowmon_line_of(&a), gb = rowmon_line_of(&b);
    reader_ctx c;
    memset(&c, 0, sizeof(c));
    c.r = &r;
    c.v = v;
    c.line_a = scan_grid_top_nearest(&ga, v + 0.5 * r.timing.blank_s);
    c.line_b = scan_grid_top_nearest(&gb, v + 0.5 * r.timing.blank_s);
    CHECK(fabs(c.line_a - c.line_b) > 1e-4, "the two fits should disagree about this line (%g)", c.line_a - c.line_b);
    HANDLE th = CreateThread(NULL, 0, reader_main, &c, 0, NULL);
    CHECK(th != NULL, "reader thread");
    if (th) {
        double until = scan_now_s() + 0.15;
        for (int i = 0; scan_now_s() < until; i++)
            rowmon_publish(&r, (i & 1) ? &a : &b);
        InterlockedExchange(&c.stop, 1);
        WaitForSingleObject(th, 2000);
        CloseHandle(th);
    }
    CHECK(c.reads > 1000, "reader made %ld reads", c.reads);
    CHECK(c.torn == 0, "%ld of %ld reads mixed two fits", c.torn, c.reads);
    CHECK(c.saw_a > 0 && c.saw_b > 0, "the reader should see both published fits (%ld of the first, %ld of the second)", c.saw_a, c.saw_b);
    rowmon_close(&r);
}

// The refresh whose light is nearest to t, against trying every refresh in a wide range.
static void test_nearest_light_matches_brute_force(void)
{
    row_mon l, e;
    row_vgrid vl, ve;
    make_laptop(&l, &vl, 3000.0);
    make_external96(&e, &ve, 3000.0013);
    l.lag_s = 0.4e-3;
    e.lag_s = 2.1e-3;
    e.top_mm = 7.0; // the external's top 7 mm below the laptop's
    row_mon *rs[2] = {&l, &e};
    row_vgrid *gs[2] = {&vl, &ve};
    unsigned seed = 12345;
    for (int trial = 0; trial < 400; trial++) {
        seed = seed * 1664525u + 1013904223u;
        double t = 3000.0 + 0.4 * (double) (seed >> 8) / (double) (1u << 24);
        seed = seed * 1664525u + 1013904223u;
        double y = 7.0 + 200.0 * (double) (seed >> 8) / (double) (1u << 24); // inside both screens
        for (int m = 0; m < 2; m++) {
            int64_t got = rowmon_refresh_lit_nearest(rs[m], gs[m], t, y);
            int64_t best = -1000;
            double best_err = INFINITY;
            for (int64_t k = -1000; k < 1000; k++) {
                double err = fabs(rowmon_light_at(rs[m], gs[m], k, y) - t);
                if (err < best_err) {
                    best_err = err;
                    best = k;
                }
            }
            CHECK(got == best, "monitor %d trial %d: got refresh %" PRId64 ", brute force %" PRId64, m, trial, got, best);
        }
    }
}

// Aimed start, 240 Hz laptop (follower) with a 96 Hz external (master): the master's refreshes fall into two
// alignment classes 2.083 ms apart on the laptop's grid, so whatever the phase some candidate lights the aim row
// within 1.042 ms of the master's. Top and bottom of the shared height then fan out by the scan-speed difference.
static void test_aimed_start(void)
{
    const double class_half = 0.5 * (1.0 / 240.0) / 2.0;
    const double aim = 107.5;
    for (int phase_i = 0; phase_i < 12; phase_i++) {
        double phase = (double) phase_i * 0.37e-3 + 0.05e-3;
        row_mon lap, ext;
        row_vgrid vlap, vext;
        make_external96(&ext, &vext, 5000.0 + 0.7184e-3);     // master: refresh 0 has its blank start at 5000.0
        make_laptop(&lap, &vlap, 5000.0 + 0.4630e-3 + phase); // laptop refresh 0 starts `phase` later
        double err = 0;
        int64_t j = row_aim_start(&ext, &vext, &lap, &vlap, 40, 24, aim, &err);
        CHECK(j >= 40 && j < 64, "aimed start %" PRId64 " outside the candidates", j);
        CHECK(err <= class_half + 1e-9, "phase %.2f ms: aim row off by %.4f ms, more than %.4f", phase * 1e3, err * 1e3,
              class_half * 1e3);
        int64_t kf = row_follower_refresh(&ext, &vext, &lap, &vlap, j, aim);
        // laptop minus external at three heights
        double off[3];
        const double ys[3] = {0.0, aim, 215.0};
        for (int i = 0; i < 3; i++)
            off[i] = rowmon_light_at(&lap, &vlap, kf, ys[i]) - rowmon_light_at(&ext, &vext, j, ys[i]);
        CHECK_NEAR_US(fabs(off[1]), err, 0.001);
        // equal tops: moving down, the slow external falls behind the fast laptop by (external row time - laptop
        // row time) at that height, relative to the aim row; about 2.04 ms each way for the middle of 215 mm
        for (int i = 0; i < 3; i += 2) {
            double expected = off[1] + (rowmon_row_s(&lap, ys[i]) - rowmon_row_s(&lap, aim)) -
                              (rowmon_row_s(&ext, ys[i]) - rowmon_row_s(&ext, aim));
            CHECK_NEAR_US(off[i], expected, 0.001);
        }
        CHECK_NEAR_US(off[0] - off[1], +2.04e-3, 5.0);
        CHECK_NEAR_US(off[2] - off[1], -2.04e-3, 5.0);
    }
}

// A lag on the follower moves the refresh that carries a master refresh; a height offset moves the shared band.
static void test_lag_and_height_offset(void)
{
    const double class_half = 0.5 * (1.0 / 240.0) / 2.0;
    row_mon lap, ext;
    row_vgrid vlap, vext;
    make_external96(&ext, &vext, 7000.0 + 0.7184e-3);
    make_laptop(&lap, &vlap, 7000.0 + 0.4630e-3 + 0.9e-3);
    double err = 0;
    int64_t j = row_aim_start(&ext, &vext, &lap, &vlap, 10, 24, 107.5, &err);
    int64_t k0 = row_follower_refresh(&ext, &vext, &lap, &vlap, j, 107.5);
    // follower lag of exactly one 240 Hz refresh: the same master refresh is now carried one refresh earlier
    lap.lag_s = 1.0 / 240.0;
    int64_t k1 = row_follower_refresh(&ext, &vext, &lap, &vlap, j, 107.5);
    CHECK(k1 == k0 - 1, "a lag of one refresh should move the carrying refresh by one: %" PRId64 " -> %" PRId64, k0, k1);
    // an uneven lag still leaves some candidate within half a class
    lap.lag_s = 1.3e-3;
    row_aim_start(&ext, &vext, &lap, &vlap, 10, 24, 107.5, &err);
    CHECK(err <= class_half + 1e-9, "with a 1.3 ms lag: aim row off by %.4f ms", err * 1e3);
    // follower 12 mm lower: its first row is at y = 12, and the shared band starts there
    lap.lag_s = 0.0;
    lap.top_mm = 12.0;
    double top = 0, bottom = 0;
    CHECK(rowmon_shared_band(&ext, &lap, &top, &bottom) && top == 12.0 && bottom == 227.0, "band %.1f..%.1f", top, bottom);
    CHECK_NEAR_US(rowmon_light_at(&lap, &vlap, 0, 12.0) - rowmon_top_row(&lap, row_vgrid_time(&vlap, 0)), 0.0, 0.001);
}

static void test_shared_band(void)
{
    row_mon a, b;
    row_vgrid va, vb;
    make_laptop(&a, &va, 0.0);
    make_external96(&b, &vb, 0.0);
    double top = -1, bottom = -1;
    CHECK(rowmon_shared_band(&a, &b, &top, &bottom) && top == 0.0 && bottom == 215.0, "level tops: band %.1f..%.1f", top, bottom);
    b.top_mm = 30.0;
    CHECK(rowmon_shared_band(&a, &b, &top, &bottom) && top == 30.0 && bottom == 215.0, "30 mm lower: band %.1f..%.1f", top, bottom);
    b.top_mm = -40.0; // higher than the laptop
    CHECK(rowmon_shared_band(&a, &b, &top, &bottom) && top == 0.0 && bottom == 215.0, "40 mm higher: band %.1f..%.1f", top, bottom);
    b.top_mm = 215.0; // touching: no shared height
    CHECK(!rowmon_shared_band(&a, &b, &top, &bottom), "touching screens share no height");
    b.top_mm = 400.0;
    CHECK(!rowmon_shared_band(&a, &b, &top, &bottom), "screens far apart share no height");
}

// wake_to_top, between the two fitted lines, is the gap from a wake-up to its own refresh's top row: 0.37 ms on
// the laptop, 0.63 ms on the external at 96 Hz, 4.09 ms at 72 Hz; the same whichever refresh it is measured on.
static void test_wake_to_top_between_fitted_lines(void)
{
    const double T = 2000.0;
    row_mon l, e96, e72;
    row_vgrid vl, v96, v72;
    make_laptop(&l, &vl, T);
    make_external96(&e96, &v96, T);
    make_external72(&e72, &v72, T);
    row_mon *mons[3] = {&l, &e96, &e72};
    row_vgrid *grids[3] = {&vl, &v96, &v72};
    const double expect[3] = {0.4630e-3 - 0.09e-3, 0.7184e-3 - 0.09e-3, 4.1802e-3 - 0.09e-3};
    for (int m = 0; m < 3; m++) {
        row_mon *r = mons[m];
        r->have_wake_to_top = false;
        r->wake_to_top_s = 0.0;
        CHECK(rowmon_set_wake_to_top(r, grids[m], T + 0.2), "monitor %d: wake_to_top should be measurable", m);
        CHECK_NEAR_US(r->wake_to_top_s, expect[m], 10.0);
        for (int64_t k = -3; k <= 3; k++)
            CHECK_NEAR_US(rowmon_wake_to_top_at(r, grids[m], T + (double) k * r->active.period), r->wake_to_top_s, 0.01);
        // the end-of-run value is stored separately
        CHECK(rowmon_set_wake_to_top_end(r, grids[m], T + 1.0) && r->have_wake_to_top_end, "end-of-run measurement");
        CHECK_NEAR_US(r->wake_to_top_end_s, r->wake_to_top_s, 0.01);
        // no fit, no measurement
        r->active.ok = false;
        CHECK(!rowmon_set_wake_to_top(r, grids[m], T) && !r->have_wake_to_top, "no fit: no wake_to_top");
        r->active.ok = true;
    }
}

// The whole-grid fit through the sampler's lock recovers a made-up grid, refuses too few samples, and becomes the
// live grid.
static void test_fit_wrapper(void)
{
    row_mon r;
    rowmon_init_empty(&r);
    r.timing.refresh_hz = 240.0;
    row_fit f;
    for (int i = 0; i < 5; i++)
        scan_grid_add(&r.grid, 100.0 + i * (1.0 / 240.0));
    CHECK(!rowmon_fit(&r, &f), "5 samples should not fit");
    CHECK(!f.ok && !r.active.ok && !r.live.ok && f.taken == 5, "a failed fit leaves no grid");

    const double true_period = 1.0 / 240.0 * (1.0 + 0.4e-6), true_base = 100.00123;
    unsigned seed = 7;
    for (int i = 0; i < 1500; i++) {
        seed = seed * 1664525u + 1013904223u;
        double jitter = ((double) (seed >> 8) / (double) (1u << 24) - 0.5) * 6e-6; // +-3 us
        double t = 100.0 + 0.0021 * i + 0.0007;                                    // a sample about every 2.1 ms
        double k = floor((t - true_base) / true_period);
        scan_grid_add(&r.grid, true_base + k * true_period + jitter);
    }
    CHECK(rowmon_fit(&r, &f), "1500 samples should fit");
    CHECK(f.ok && r.active.ok && r.live.ok && r.refits == 1, "the fit becomes the active and the live one");
    CHECK(f.samples > 1000 && f.taken == 1505, "samples %zu taken %zu", f.samples, f.taken);
    double model = true_base + floor((f.base - true_base) / true_period + 0.5) * true_period;
    CHECK(fabs(f.base - model) < 2e-6, "base off by %.2f us", (f.base - model) * 1e6);
    CHECK(fabs(f.period / true_period - 1.0) < 1e-6, "period off by %.3f ppm", (f.period / true_period - 1.0) * 1e6);
    CHECK(f.resid_p50_us < 3.0 && f.resid_p95_us < 5.0, "residuals p50 %.2f p95 %.2f us", f.resid_p50_us, f.resid_p95_us);
    // the drift between a start fit and an end fit: none for the same fit, -5 us when the end grid sits 5 us later
    r.fit_start = f;
    r.fit_end = f;
    CHECK_NEAR_US(rowmon_grid_drift_s(&r, 103.0), 0.0, 0.001);
    r.fit_end.base += 5e-6;
    CHECK_NEAR_US(rowmon_grid_drift_s(&r, 103.0), -5e-6, 0.01);
    r.fit_end.ok = false;
    double nan_drift = rowmon_grid_drift_s(&r, 103.0);
    CHECK(nan_drift != nan_drift, "the drift is NaN without an end fit");
    rowmon_close(&r);
}

// The JSON fields are a fragment that continues an object: a leading comma, balanced braces, every key present.
static void test_json_fields(void)
{
    row_mon l;
    row_vgrid vl;
    make_laptop(&l, &vl, 1234.5);
    l.timing.active_w = 2560;
    l.timing.total_w = 2720;
    l.timing.pixel_rate_hz = 1175045000.0;
    l.lag_s = 0.0025;
    l.top_mm = 3.0;
    l.refits = 41;
    FILE *f = tmpfile();
    CHECK(f != NULL, "tmpfile");
    if (!f)
        return;
    fputs("{\"x\":1", f);
    rowmon_write_json(&l, f);
    fputs("}", f);
    long n = ftell(f);
    rewind(f);
    char buf[4096];
    size_t got = fread(buf, 1, sizeof(buf) - 1, f);
    buf[got] = '\0';
    fclose(f);
    CHECK((long) got == n, "read back %zu of %ld bytes", got, n);
    const char *keys[] = {"{\"x\":1,\"timing\":{", "\"refresh_hz\":", "\"line_s\":", "\"scan_s\":", "\"blank_s\":",
                          "\"active\":[2560,1600]", "\"total\":[2720,1800]", "\"scan_grid\":{\"ok\":true",
                          "\"scan_grid_at_start\":{\"ok\":true", "\"base\":", "\"period\":", "\"resid_p50_us\":",
                          "\"resid_p95_us\":", "\"samples\":", "\"live_refits\":41", "\"wake_to_top_ms\":0.3",
                          "\"wake_to_top_end_ms\":0.3",
                          "\"geometry\":{\"height_mm\":215.000,\"top_mm\":3.000,\"lag_ms\":2.5000}"};
    for (size_t i = 0; i < sizeof(keys) / sizeof(keys[0]); i++)
        CHECK(strstr(buf, keys[i]) != NULL, "missing %s in %s", keys[i], buf);
    int depth = 0, in_string = 0;
    for (const char *c = buf; *c; c++) {
        if (*c == '"')
            in_string = !in_string;
        if (!in_string && (*c == '{' || *c == '['))
            depth++;
        if (!in_string && (*c == '}' || *c == ']'))
            depth--;
        CHECK(depth >= 0, "unbalanced at offset %ld", (long) (c - buf));
    }
    CHECK(depth == 0 && !in_string, "braces and quotes balanced: depth %d", depth);
    CHECK(strstr(buf, "nan") == NULL && strstr(buf, "inf") == NULL, "no nan/inf in the JSON: %s", buf);
    // wake-up to top row not measured: null, not a number
    l.have_wake_to_top = false;
    l.have_wake_to_top_end = false;
    f = tmpfile();
    if (f) {
        rowmon_write_json(&l, f);
        rewind(f);
        got = fread(buf, 1, sizeof(buf) - 1, f);
        buf[got] = '\0';
        fclose(f);
        CHECK(strstr(buf, "\"wake_to_top_ms\":null") != NULL && strstr(buf, "\"wake_to_top_end_ms\":null") != NULL,
              "an unmeasured wake_to_top should be null: %s", buf);
    }
}

int main(void)
{
    test_timing_numbers();
    test_aligned_blank_starts();
    test_top_row_snapping();
    test_live_refit_tracks_a_rate_change();
    test_live_grid_reads_are_whole();
    test_nearest_light_matches_brute_force();
    test_aimed_start();
    test_lag_and_height_offset();
    test_shared_band();
    test_wake_to_top_between_fitted_lines();
    test_fit_wrapper();
    test_json_fields();
    printf("row-timing selftest: %d checks, %d failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
