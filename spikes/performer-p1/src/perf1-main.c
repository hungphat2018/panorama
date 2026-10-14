// perf1 (the P3 build): one borderless window per monitor, one libplacebo-wrapped flip-model swapchain per window, one
// film clock locked to the master monitor's refresh count, a cue sheet applied shot by shot, seeking.
//
// Where things are (perf1-app.h lists the files). This file starts everything in order, picks the master and the
// film's pace, plans the first epoch through the engine, runs the main loop (window messages, Esc, a deadline) and
// tears down. The decisions are the engine's (perf1-engine.c):
//   - the film clock counts the master monitor's refreshes (the slowest monitor), at a pace that makes a film frame last
//     a whole number of master refreshes when the speed change is within 1 %
//   - frames are picked as the last one due with half a master refresh of tolerance, from the frames' own timestamps
//   - every other monitor ("nearest"): the refresh whose picture lights the aim row nearest to the moment the master
//     lights it (row-timing.h, ADR 0025), kept per alignment class (hysteresis), moved only at cuts, with the master's
//     frame changes re-aimed at cuts
//   - the cue (crop, placement) comes from the version the frame's shot started with, looked up by the frame's timestamp
//   - a seek, pause or resume stops presenting, resets the queues and restarts the clock on one master refresh for every
//     monitor
// Everything GPU-side (device, swapchains, renderers, queues) is used from one render thread that waits on every
// swapchain's frame-latency waitable object; libplacebo's D3D11 pl_gpu is not thread-safe.
//
// Rows, not blanks: WaitForVBlank marks the start of the vertical blank, and the picture's top row comes one blank later, each
// row a scan-time fraction after that. The "light" of a row is worked out by row-timing.h (shared with the presenter spike):
// the monitor's exact signal timing, a scan-line sampler fitting its top-row grid, its height and top on the shared vertical
// axis and its panel lag.
#include "perf1-app.h"
#include "perf1-audio.h"
#include "perf1-control.h"

#include <math.h>
#include <timeapi.h>

#include <libavutil/avutil.h>

#define SETTLE_S 1.5          // refresh grids settle before the film starts
#define FILM_LEAD_S 0.5       // first film frame is at least this far ahead of the settle

struct app A;

// ---- what the engine needs from this program -------------------------------------------------------------

static row_vgrid host_grid(void *user, int m)
{
    row_vgrid g;
    vclock_line(&A.mon[m].clock, &g.base, &g.period);
    return g;
}

// Called on the render thread while the decoder is parked: every monitor's queue is emptied (this unmaps frames, so it
// is GPU work and belongs to the thread that owns the device).
static void host_reset_queues(void *user)
{
    for (int i = 0; i < A.nmon; i++)
        pl_queue_reset(A.mon[i].queue);
}

static void on_control_stop(void)
{
    atomic_store(&A.stop, true);
}

// ---- master and geometry -----------------------------------------------------------------------------------

// The slowest monitor by its exact signal rate (the first of equals), unless --master names one.
static int pick_master(void)
{
    if (A.cfg.master >= 0 && A.cfg.master < A.nmon)
        return A.cfg.master;
    int best = 0;
    for (int i = 1; i < A.nmon; i++)
        if (A.mon[i].row.timing.refresh_hz < A.mon[best].row.timing.refresh_hz)
            best = i;
    return best;
}

// Opens every monitor's signal timing and scan-line source (virtual displays have theirs already), picks the master, places
// the monitors on the shared vertical axis (the master's top is 0; the follower's top is --height-offset-mm lower), applies
// --lag-ms, and works out the band of height the master and the follower share and the aim row inside it. Runs before the
// picture is laid out, because the layout needs each monitor's top.
static bool assign_geometry(void)
{
    if (!A.cfg.headless_hz)
        for (int i = 0; i < A.nmon; i++) {
            struct monitor *m = &A.mon[i];
            if (!rowmon_open(&m->row, m->device, m->h_mm)) {
                fprintf(stderr, "perf1: %s: cannot read its signal timing or scan line (QueryDisplayConfig, D3DKMT)\n", m->name);
                return false;
            }
        }
    A.master = pick_master();
    struct monitor *follower = A.nmon > 1 ? &A.mon[A.master == 0 ? 1 : 0] : NULL;
    for (int i = 0; i < A.nmon; i++)
        A.mon[i].row.top_mm = picture_top_mm(i, &A.mon[i] == follower); // --room: the room's; else --height-offset-mm
    for (int i = 0; i < A.cfg.n_lags; i++) {
        struct monitor *target = NULL;
        for (int k = 0; k < A.nmon; k++)
            if (!_wcsicmp(A.mon[k].device, A.cfg.lags[i].device))
                target = &A.mon[k];
        if (!target) {
            fprintf(stderr, "perf1: --lag-ms: no monitor named %ls\n", A.cfg.lags[i].device);
            return false;
        }
        target->row.lag_s = A.cfg.lags[i].ms * 1e-3;
    }
    struct monitor *master = &A.mon[A.master];
    A.band_top_mm = master->row.top_mm;
    A.band_bottom_mm = master->row.top_mm + master->row.height_mm;
    if (follower && !rowmon_shared_band(&master->row, &follower->row, &A.band_top_mm, &A.band_bottom_mm)) {
        fprintf(stderr, "perf1: the master and the follower share no height with --height-offset-mm %.1f\n",
                A.cfg.height_offset_mm);
        return false;
    }
    A.aim_mm = isnan(A.cfg.aim_mm) ? 0.5 * (A.band_top_mm + A.band_bottom_mm) : A.cfg.aim_mm;
    if (A.aim_mm < A.band_top_mm || A.aim_mm > A.band_bottom_mm) {
        fprintf(stderr, "perf1: --aim-mm %.1f is outside the shared height %.1f..%.1f mm\n", A.aim_mm, A.band_top_mm,
                A.band_bottom_mm);
        return false;
    }
    return true;
}

static bool setup_engine(void)
{
    struct engine_cfg cfg;
    engine_cfg_defaults(&cfg);
    cfg.n_mon = A.nmon;
    cfg.master = A.master;
    for (int i = 0; i < A.nmon; i++)
        cfg.row[i] = &A.mon[i].row;
    cfg.aim_mm = A.aim_mm;
    cfg.frame_dur_s = cfg.step_s = 1.0 / 24.0; // provisional: engine_set_film once the film's pace is known
    cfg.own_mode = A.cfg.sched == SCHED_OWN;
    cfg.hysteresis = !A.cfg.no_hysteresis;
    cfg.reaim = !A.cfg.no_reaim;
    cfg.eta_s = A.cfg.eta_ms * 1e-3;
    cfg.restart_margin_s = A.cfg.restart_margin_ms * 1e-3;
    struct engine_host host = { .user = NULL, .grid = host_grid, .reset_queues = host_reset_queues, .note = audio_engine_note };
    if (!engine_init(&A.engine, &cfg, &host, &A.store)) {
        fprintf(stderr, "perf1: engine_init failed\n");
        return false;
    }
    A.engine_ready = true;
    return true;
}

// ---- the film's start --------------------------------------------------------------------------------------

// What the model predicts for the first epoch: second monitor minus first (by output index), in light, at the shared band's
// top, the aim row and its bottom.
static void report_predicted_seam(void)
{
    if (A.nmon < 2)
        return;
    struct monitor *mas = &A.mon[A.master];
    struct monitor *fol = &A.mon[A.master == 0 ? 1 : 0];
    const struct epoch_rec *ep = &A.engine.epochs[0];
    row_vgrid mg = host_grid(NULL, mas->slot), fg = host_grid(NULL, fol->slot);
    int64_t kf = ep->start_k[fol->slot];
    const double heights[3] = { A.band_top_mm, A.aim_mm, A.band_bottom_mm };
    const double sign = fol->index > mas->index ? 1.0 : -1.0;
    for (int i = 0; i < 3; i++)
        A.predicted_ms[i] = sign * (rowmon_light_at(&fol->row, &fg, kf, heights[i]) -
                                    rowmon_light_at(&mas->row, &mg, ep->J0, heights[i])) * 1e3;
    A.predicted_valid = true;
    const struct monitor *second = fol->index > mas->index ? fol : mas;
    const struct monitor *first = fol->index > mas->index ? mas : fol;
    printf("film starts on master refresh %lld (class chosen for the followers, seam error %.3f ms at the aim row); aim row %.1f mm\n",
           (long long) ep->J0, ep->cost_s * 1e3, A.aim_mm);
    printf("predicted seam offset %s - %s, in light: top row (%.1f mm) %+.2f ms, aim row %+.2f ms, bottom row (%.1f mm) %+.2f ms\n",
           second->name, first->name, A.band_top_mm, A.predicted_ms[0], A.predicted_ms[1], A.band_bottom_mm, A.predicted_ms[2]);
}

// Works out the film's pace from the master's refresh rate, hands it to the engine and starts the first epoch.
static bool choose_film_start(double stream_fps)
{
    struct vclock *mc = &A.mon[A.master].clock;
    A.master_period = vclock_period(mc);
    A.frame_dur = stream_fps > 0 ? 1.0 / stream_fps : 1.0 / 24.0;

    A.hold = (int64_t) llround(A.frame_dur / A.master_period);
    if (A.hold < 1)
        A.hold = 1;
    double lock = A.frame_dur / ((double) A.hold * A.master_period); // playback speed that makes it exact
    A.locked = A.cfg.lock_speed && fabs(lock - 1.0) <= 0.01;
    A.step_s = A.locked ? A.frame_dur / (double) A.hold : A.master_period;
    printf("film %.4f fps; master %s %.4f Hz; %s (speed %.4f if locked, hold %lld refreshes)\n", stream_fps,
           A.mon[A.master].name, 1.0 / A.master_period, A.locked ? "speed LOCKED to the master's refresh count" : "speed NOT locked (plays at 1.0)",
           lock, (long long) A.hold);

    engine_set_film(&A.engine, A.step_s, A.frame_dur);
    double now = now_s();
    if (!engine_start(&A.engine, now, 0.0, now + FILM_LEAD_S)) {
        fprintf(stderr, "perf1: the decoder has queued no frame yet\n");
        return false;
    }
    A.t0 = vclock_time_of(mc, A.engine.epochs[0].J0);
    report_predicted_seam();
    return true;
}

// ---- main ----------------------------------------------------------------------------------------------------

static void pump_messages(void)
{
    MSG msg;
    while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
}

int main(int argc, char **argv)
{
    int rc = 1;
    if (!args_parse(argc, argv, &A.cfg)) {
        args_usage(argv[0]);
        return 2;
    }
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    timeBeginPeriod(1);
    bool refresh_switched = false, store_ready = false;
    HANDLE vb_threads[MAX_MONITORS] = {0}, render = NULL;
    IDXGIAdapter1 *adapter = NULL;
    pl_queue queues[MAX_MONITORS] = {0};
    bool started = false;
    struct audio *sound = NULL;

    if (A.cfg.refresh_hz > 0) {
        if (!display_switch_refresh(A.cfg.refresh_device, A.cfg.refresh_hz)) {
            fwprintf(stderr, L"perf1: %ls: switching to %d Hz failed\n", A.cfg.refresh_device, A.cfg.refresh_hz);
            timeEndPeriod(1);
            return 1;
        }
        refresh_switched = true;
        wprintf(L"%ls switched to %d Hz for this run\n", A.cfg.refresh_device, A.cfg.refresh_hz);
    }

    if (A.cfg.headless_hz) {
        if (!headless_init())
            goto cleanup;
    } else {
        adapter = display_pick_adapter();
        if (!adapter || !display_enumerate(adapter)) {
            fprintf(stderr, "perf1: no adapter with outputs attached to the desktop\n");
            goto cleanup;
        }
        if (A.cfg.probe_only) {
            display_probe(adapter);
            rc = 0;
            goto cleanup;
        }
    }
    CreateDirectoryA(A.cfg.out_dir, NULL);
    store_ready = cue_store_init(&A.store);
    if (!store_ready)
        goto cleanup;

    if (!picture_load_room() || !assign_geometry())
        goto cleanup;
    if (!A.cfg.headless_hz && (!display_create_windows() || !display_init_gpu(adapter)))
        goto cleanup;
    for (int i = 0; i < A.nmon; i++)
        queues[i] = A.mon[i].queue;
    if (!setup_engine())
        goto cleanup;

    A.dec = decoder_open(&A.cfg, &A.gpu, queues, A.nmon, &A.engine);
    if (!A.dec) {
        fprintf(stderr, "perf1: decoder_open failed\n");
        goto cleanup;
    }
    decoder_get_info(A.dec, &A.di);
    A.src_w = A.di.width;
    A.src_h = A.di.height;
    sound = audio_open(&A.cfg); // NULL when there is no sound (the film plays on without it)
    if (sound) {
        // Every restart is planned far enough ahead for the sound to start with the picture, not only for the renderers.
        double lead = audio_start_lead_s(sound, 0.5);
        if (lead > A.engine.cfg.restart_margin_s) {
            printf("audio: restart margin %.1f ms (the sound's measured lead), was %.1f ms\n", lead * 1e3,
                   A.engine.cfg.restart_margin_s * 1e3);
            A.engine.cfg.restart_margin_s = lead;
        }
    }

    // The first cue sheet and the control channel, before anything plays: a bad sheet or script fails the run here.
    if (A.cfg.cues_file) {
        char err[300] = "";
        struct cue_sheet *s = cue_sheet_load_file(A.cfg.cues_file, A.src_w, A.src_h, err, sizeof(err));
        if (!s || !cue_store_publish(&A.store, s, err, sizeof(err))) {
            fprintf(stderr, "perf1: %s\n", err);
            cue_sheet_free(s);
            goto cleanup;
        }
        engine_note_load(&A.engine, now_s(), s->version, true, A.cfg.cues_file);
        printf("perf1: cue sheet version %d from %s, %zu shots\n", s->version, A.cfg.cues_file, s->n_shots);
        if (s->ignored_keys)
            fprintf(stderr, "perf1: %s: %u unknown key(s) ignored, the first is \"%s\"\n", A.cfg.cues_file, s->ignored_keys,
                    s->first_ignored_key);
    }
    A.control = control_create(&A.engine, &A.store, A.src_w, A.src_h, on_control_stop);
    if (!A.control)
        goto cleanup;
    if (A.cfg.control_script) {
        char err[400] = "";
        if (!control_load_script(A.control, A.cfg.control_script, err, sizeof(err))) {
            fprintf(stderr, "perf1: %s\n", err);
            goto cleanup;
        }
    }
    if (A.cfg.pipe_name && !control_start_pipe(A.control, A.cfg.pipe_name)) {
        fprintf(stderr, "perf1: cannot start the control pipe\n");
        goto cleanup;
    }

    if (!decoder_start(A.dec)) {
        fprintf(stderr, "perf1: decoder thread failed to start\n");
        goto cleanup;
    }
    if (A.cfg.headless_hz) {
        if (!headless_start())
            goto cleanup;
    } else {
        for (int i = 0; i < A.nmon; i++)
            render_vblank_thread_start(&A.mon[i], &vb_threads[i]);
        for (int i = 0; i < A.nmon; i++)
            if (!rowmon_start_sampler(&A.mon[i].row))
                fail(&A.mon[i], "scan-line sampler thread failed to start");
    }
    double t_vb = now_s();

    if (!render_warm_up())
        goto stop_threads;
    // Let the refresh grids settle before the film starts (messages keep flowing).
    while (now_s() < t_vb + SETTLE_S && !atomic_load(&A.stop)) {
        pump_messages();
        Sleep(5);
    }
    if (atomic_load(&A.stop))
        goto stop_threads;

    for (int i = 0; i < A.nmon; i++) {
        struct monitor *m = &A.mon[i];
        printf("monitor %d %s: %dx%d at (%ld,%ld), window/swapchain %dx%d, refresh %.4f Hz, %.0fx%.0f mm at %.0f mm\n", m->index,
               m->name, (int) (m->rect.right - m->rect.left), (int) (m->rect.bottom - m->rect.top), m->rect.left,
               m->rect.top, m->sc_w, m->sc_h, 1.0 / vclock_period(&m->clock), m->w_mm, m->h_mm, m->left_mm);
        if (vclock_period(&m->clock) <= 0.0) {
            fail(m, "no vblank grid after %.1f s", SETTLE_S);
            goto stop_threads;
        }
    }
    if (!A.cfg.headless_hz) {
        // Fit the scan-line grids; from here the film runs on them.
        for (int i = 0; i < A.nmon; i++) {
            struct monitor *m = &A.mon[i];
            if (!rowmon_fit(&m->row, &m->row.fit_start)) {
                fail(m, "no scan-line fit at film start (%zu samples)", m->row.fit_start.taken);
                goto stop_threads;
            }
            // the constant gap between this monitor's wake-up grid and its top-row grid
            row_vgrid vg = host_grid(NULL, i);
            rowmon_set_wake_to_top(&m->row, &vg, now_s());
            const scan_timing *t = &m->row.timing;
            printf("  %s: signal %ux%u total, line %.4f us, scan %.4f ms, blank %.4f ms; scan grid %zu of %zu samples, residual p50 %.2f us; "
                   "top row %.3f ms after the vblank wake-up; height %.1f mm, top %.1f mm, lag %.2f ms\n", m->name,
                   t->total_w, t->total_h, t->line_s * 1e6, t->scan_s * 1e3, t->blank_s * 1e3, m->row.fit_start.samples,
                   m->row.fit_start.taken, m->row.fit_start.resid_p50_us, m->row.wake_to_top_s * 1e3, m->row.height_mm,
                   m->row.top_mm, m->row.lag_s * 1e3);
        }
    }
    printf("aim row %.1f mm; shared height %.1f..%.1f mm\n", A.aim_mm, A.band_top_mm, A.band_bottom_mm);
    if (!choose_film_start(A.di.fps))
        goto stop_threads;
    started = true;
    printf("picture %.1f x %.1f mm at x %.1f..%.1f mm; ", A.pic_x1 - A.pic_x0, A.pic_y1 - A.pic_y0, A.pic_x0, A.pic_x1);
    for (int i = 0; i < A.nmon; i++)
        printf("%s: %s ", A.mon[i].name, A.first_pic.share[i].has_picture ? "shows part" : "black");
    printf("\n");
    fflush(stdout);
    control_film_started(A.control, A.t0);

    render_thread_start(&render);
    if (!render) {
        fprintf(stderr, "perf1: render thread failed to start\n");
        goto stop_threads;
    }

    // Main thread: window messages, Esc (also read globally, so it works without focus), a hard deadline.
    double deadline = A.t0 + A.cfg.seconds + 5.0;
    while (!atomic_load(&A.render_done)) {
        pump_messages();
        if (!A.cfg.headless_hz && (GetAsyncKeyState(VK_ESCAPE) & 0x8000))
            atomic_store(&A.stop, true);
        if (now_s() > deadline) {
            fprintf(stderr, "perf1: deadline passed, stopping\n");
            atomic_store(&A.stop, true);
        }
        Sleep(5);
    }
    rc = 0;

stop_threads:
    atomic_store(&A.stop, true);
    if (render) {
        WaitForSingleObject(render, 5000);
        CloseHandle(render);
    }
    if (A.cfg.headless_hz) {
        headless_stop();
    } else {
        for (int i = 0; i < A.nmon; i++)
            if (vb_threads[i]) {
                WaitForSingleObject(vb_threads[i], 2000);
                CloseHandle(vb_threads[i]);
            }
    }
    control_destroy(A.control); // the pipe thread talks to the engine: it ends before the engine is torn down
    A.control = NULL;
    if (A.dec)
        decoder_stop(A.dec);
    engine_note_stop(&A.engine, now_s());
    audio_close(&sound, started ? A.cfg.out_dir : NULL); // the sound's thread listens to the engine's notes: it ends first
    if (!A.cfg.headless_hz)
        for (int i = 0; i < A.nmon; i++)
            rowmon_stop_sampler(&A.mon[i].row); // the samplers ran through the whole film, so the end-of-run fit scores it
    if (started) {
        for (int i = 0; i < A.nmon && !A.cfg.headless_hz; i++) {
            struct monitor *m = &A.mon[i];
            if (!rowmon_fit(&m->row, &m->row.fit_end) && !m->error[0])
                snprintf(m->error, sizeof(m->error), "no scan-line fit at the end");
            row_vgrid vg = host_grid(NULL, i);
            rowmon_set_wake_to_top_end(&m->row, &vg, now_s());
            double drift = rowmon_grid_drift_s(&m->row, now_s());
            printf("%s: top-row grid at film start vs end, at the film's end: %+.1f us (end fit: %zu of %zu samples, residual p50 %.2f us, "
                   "p95 %.2f us; the film followed a grid refitted %u times); vblank wake-up to top row %.3f ms at film start, "
                   "%.3f ms at the end\n", m->name, drift * 1e6, m->row.fit_end.samples, m->row.fit_end.taken,
                   m->row.fit_end.resid_p50_us, m->row.fit_end.resid_p95_us, m->row.refits, m->row.wake_to_top_s * 1e3,
                   m->row.wake_to_top_end_s * 1e3);
        }
        if (A.dec)
            log_write_all();
        for (int i = 0; i < A.nmon; i++)
            if (A.mon[i].error[0])
                rc = 1;
    } else {
        rc = 1;
    }

cleanup:
    audio_close(&sound, NULL);
    if (A.dec)
        decoder_stop(A.dec); // no thread may push into a queue that is being destroyed
    for (int i = 0; i < A.nmon; i++)
        pl_queue_destroy(&A.mon[i].queue); // unmaps queued frames while the GPU still exists
    decoder_destroy(&A.dec);
    control_destroy(A.control);
    if (A.cfg.headless_hz) {
        headless_destroy();
    } else if (A.gpu.log || A.nmon) {
        display_destroy(adapter);
    }
    for (int i = 0; i < A.nmon; i++) {
        struct monitor *m = &A.mon[i];
        if (!A.cfg.headless_hz)
            rowmon_close(&m->row);
        vclock_destroy(&m->clock);
        samples_free(&m->render_cpu);
        samples_free(&m->render_gpu);
        samples_free(&m->render_sync);
        samples_free(&m->lead);
        free(m->presents);
        free(m->stats);
    }
    if (A.engine_ready)
        engine_destroy(&A.engine);
    if (store_ready)
        cue_store_destroy(&A.store);
    if (refresh_switched)
        ChangeDisplaySettingsExW(A.cfg.refresh_device, NULL, NULL, 0, NULL);
    timeEndPeriod(1);
    return rc;
}
