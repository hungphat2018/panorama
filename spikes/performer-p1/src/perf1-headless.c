// Virtual displays for --headless: no window, no graphics hardware. Two monitors with Setup A's numbers (the laptop at
// 240.0013 Hz and the external at 96, 72 or 100 Hz, locked to each other by one GPU clock up to an optional drift), each
// with a thread that "wakes" at the start of every blank like IDXGIOutput::WaitForVBlank does, feeds the monitor's
// refresh-grid fit with the measured wake-up time and sets the event the render thread waits on. The scan-line grid
// (what row-timing.h's sampler would read from the display controller) is filled in exactly from the same truth.
// libplacebo's dummy GPU stands in for the device, so the decoder and the pl_queue run for real (software decoding)
// while nothing is drawn.
#include "perf1-app.h"

#include <math.h>

struct fake_display {
    double period;              // true refresh period
    double phase;               // true top-row time of refresh 0 (host clock)
    double blank, wake_delay;
    HANDLE thread, timer;
};

static void make_fake_monitor(int slot, const char *name, RECT rect, double w_mm, double h_mm, double left_mm, double rate_hz,
                              unsigned active_w, unsigned active_h, unsigned total_w, unsigned total_h, double true_period,
                              double phase)
{
    struct monitor *m = &A.mon[slot];
    memset(m, 0, sizeof(*m));
    m->slot = slot;
    m->index = slot;
    snprintf(m->name, sizeof(m->name), "%s", name);
    m->rect = rect;
    m->w_mm = w_mm;
    m->h_mm = h_mm;
    m->left_mm = left_mm;
    m->sc_w = rect.right - rect.left;
    m->sc_h = rect.bottom - rect.top;
    vclock_init(&m->clock);
    m->waitable = CreateEventW(NULL, FALSE, FALSE, NULL); // auto-reset: one wake-up, one service

    struct fake_display *f = calloc(1, sizeof(*f));
    m->fake = f;
    f->period = true_period;
    f->phase = phase;
    f->wake_delay = 0.09e-3;

    row_mon *r = &m->row;
    rowmon_init_empty(r);
    scan_timing *t = &r->timing;
    t->refresh_hz = rate_hz;
    t->line_s = 1.0 / (rate_hz * (double) total_h);
    t->scan_s = (double) active_h * t->line_s;
    t->blank_s = (double) (total_h - active_h) * t->line_s;
    t->active_w = active_w;
    t->active_h = active_h;
    t->total_w = total_w;
    t->total_h = total_h;
    r->height_mm = h_mm;
    f->blank = t->blank_s;
    // the scan grid, exactly: the top row of refresh k is at phase + k * period
    row_fit fit = { .ok = true, .base = phase, .period = true_period, .resid_p50_us = 0.0, .resid_p95_us = 0.0,
                    .samples = 1000, .taken = 1000 };
    r->live = fit;
    r->active = fit;
    r->fit_start = fit;
    r->fit_end = fit;
    r->wake_to_top_s = t->blank_s - f->wake_delay;
    r->wake_to_top_end_s = r->wake_to_top_s;
    r->have_wake_to_top = r->have_wake_to_top_end = true;
}

bool headless_init(void)
{
    // A headless run is a test: it must never compete with a live session (a full-screen run, a game). At idle class even
    // the virtual displays' high-priority threads stay below every normal thread of other programs; the price is some
    // scheduling noise in the virtual refresh times, which the engine's fits absorb.
    SetPriorityClass(GetCurrentProcess(), IDLE_PRIORITY_CLASS);
    int ext_hz = A.cfg.headless_hz;
    unsigned total_h;
    double pixel_clock;
    int p, q;
    switch (ext_hz) {
    case 96: total_h = 1160; pixel_clock = 222.72e6; p = 5; q = 2; break;
    case 72: total_h = 1545; pixel_clock = 222.48e6; p = 10; q = 3; break;
    case 100: total_h = 1115; pixel_clock = 223.0e6; p = 12; q = 5; break;
    default: return false;
    }
    double ext_rate = pixel_clock / (2000.0 * (double) total_h);
    double lap_rate = 1175.045e6 / (2720.0 * 1800.0);
    double p_ext = 1.0 / ext_rate;
    double p_lap = p_ext * (double) q / (double) p * (1.0 + A.cfg.headless_drift_ppm * 1e-6);
    double t0 = now_s() + 0.3;

    A.gpu.log = pl_log_create(PL_API_VER, pl_log_params(.log_cb = pl_log_simple, .log_level = A.cfg.log_level));
    A.gpu.gpu = pl_gpu_dummy_create(A.gpu.log, NULL);
    if (!A.gpu.gpu) {
        fprintf(stderr, "perf1: pl_gpu_dummy_create failed\n");
        return false;
    }

    A.nmon = 2;
    make_fake_monitor(0, "DISPLAY1", (RECT) { 0, 0, 2560, 1600 }, 344.0, 215.0, 0.0, lap_rate, 2560, 1600, 2720, 1800, p_lap,
                      t0 + 0.37 * p_lap);
    make_fake_monitor(1, "DISPLAY2", (RECT) { 2560, 0, 4480, 1080 }, 476.0, 268.0, 344.0 + A.cfg.gap_mm, ext_rate, 1920, 1080, 2000,
                      total_h, p_ext, t0 + 0.61 * p_ext);
    for (int i = 0; i < A.nmon; i++) {
        render_init_monitor(&A.mon[i]);
        if (!A.mon[i].queue) {
            fail(&A.mon[i], "queue creation failed");
            return false;
        }
    }
    printf("headless: DISPLAY1 %.4f Hz (laptop) + DISPLAY2 %.4f Hz (external), %.2f ppm drift, dummy GPU\n", lap_rate, ext_rate,
           A.cfg.headless_drift_ppm);
    return true;
}

// Waits until the host clock reaches t: a high-resolution timer for the bulk, a short spin for the rest.
static void wait_until(HANDLE timer, double t)
{
    double rest = t - now_s();
    if (rest > 0.0007 && timer) {
        LARGE_INTEGER due;
        due.QuadPart = -(LONGLONG) ((rest - 0.0004) * 1e7);
        if (SetWaitableTimer(timer, &due, 0, NULL, NULL, FALSE))
            WaitForSingleObject(timer, INFINITE);
    }
    while (now_s() < t && !atomic_load(&A.stop))
        YieldProcessor();
}

static DWORD WINAPI fake_vblank_thread(LPVOID arg)
{
    struct monitor *m = arg;
    struct fake_display *f = m->fake;
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
    // the first refresh whose wake-up is still ahead
    int64_t k = (int64_t) ceil((now_s() + 0.005 - (f->phase - f->blank + f->wake_delay)) / f->period);
    while (!atomic_load(&A.stop)) {
        double t_wake = f->phase + (double) k * f->period - f->blank + f->wake_delay;
        wait_until(f->timer, t_wake);
        if (atomic_load(&A.stop))
            break;
        vclock_add(&m->clock, now_s()); // what WaitForVBlank's return would be stamped with
        SetEvent(m->waitable);
        k++;
    }
    return 0;
}

bool headless_start(void)
{
    for (int i = 0; i < A.nmon; i++) {
        struct fake_display *f = A.mon[i].fake;
        f->timer = CreateWaitableTimerExW(NULL, NULL, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
        if (!f->timer)
            f->timer = CreateWaitableTimerW(NULL, TRUE, NULL); // older Windows: coarser, the spin makes up for it
        f->thread = CreateThread(NULL, 0, fake_vblank_thread, &A.mon[i], 0, NULL);
        if (!f->thread)
            return false;
    }
    return true;
}

void headless_stop(void)
{
    for (int i = 0; i < A.nmon; i++) {
        struct fake_display *f = A.mon[i].fake;
        if (f && f->thread) {
            WaitForSingleObject(f->thread, 2000);
            CloseHandle(f->thread);
            f->thread = NULL;
        }
    }
}

void headless_destroy(void)
{
    for (int i = 0; i < A.nmon; i++) {
        struct monitor *m = &A.mon[i];
        if (m->fake && m->fake->timer)
            CloseHandle(m->fake->timer);
        free(m->fake);
        m->fake = NULL;
        if (m->waitable)
            CloseHandle(m->waitable);
        m->waitable = NULL;
    }
    pl_gpu_dummy_destroy(&A.gpu.gpu);
    pl_log_destroy(&A.gpu.log);
}
