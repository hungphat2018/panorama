// What happens on one refresh of one monitor, and the threads that drive it.
//
// The render thread waits on every monitor's frame-latency object (headless: an event the virtual display sets), and for
// each monitor that can take a frame asks the engine what to show. The engine (perf1-engine.c) answers with the frame
// (its exact timestamp), the latched cue and a HOLD when nothing may be presented (a break is under way, or the monitor's
// first refresh of an epoch is not here yet). The frame itself comes from the monitor's pl_queue, asked for that exact
// timestamp so its nearest-frame pick is exact; the picture path (perf1-picture.h) draws it: prepared frame, the one
// resample with the cue's crop and place, surround, colour, dither, subtitles.
#include "perf1-app.h"
#include "perf1-control.h"
#include "perf1-picture.h"

#include <avrt.h>
#include <math.h>
#include <timeapi.h>

#define WARMUP_TIMEOUT_NS 5000000000ULL
#define HELD_POLL_MS 2             // a held monitor, or an idle engine, is looked at least this often
#define END_OF_FILM_GRACE_S 0.3

// ---- small helpers ------------------------------------------------------------------------------

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

void render_init_monitor(struct monitor *m)
{
    m->params = *preset_params(A.cfg.preset);
    m->params.info_callback = info_cb;
    m->params.info_priv = m;
    if (!A.cfg.headless_hz) // the dummy GPU compiles no shaders: the headless run never renders
        m->rr = pl_renderer_create(A.gpu.log, A.gpu.gpu);
    m->queue = pl_queue_create(A.gpu.gpu);
    if (!picture_init_monitor(m))
        fail(m, "the picture path could not start");
}

double render_end_time(void)
{
    return A.t0 + A.cfg.seconds;
}

// ---- the monitors on the layout surface ----------------------------------------------------------------------

static void build_layout_monitors(void)
{
    for (int i = 0; i < A.nmon; i++) {
        const struct monitor *m = &A.mon[i];
        A.lay_mon[i] = (struct lay_monitor) { .left_mm = m->left_mm, .top_mm = m->row.top_mm, .w_mm = m->w_mm,
                                              .h_mm = m->h_mm, .sc_w = m->sc_w, .sc_h = m->sc_h };
    }
    layout_defaults(A.lay_mon, A.nmon, A.cfg.picture_height_mm, A.cfg.picture_center_mm, &A.lay_def);
}

// ---- logging helpers ------------------------------------------------------------------------------

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
    m->stats[m->n_stats++] = *s;
}

// ---- one refresh of one monitor ---------------------------------------------------------------------

static void service_monitor(struct monitor *m)
{
    double now = now_s();
    struct svc_out out;
    engine_service(&A.engine, m->slot, now, &out);
    if (out.action != SVC_PRESENT) {
        m->not_before = out.retry_at; // nothing to show yet: come back at retry_at
        m->slot_taken = !A.cfg.headless_hz; // headless: the virtual display sets its event every refresh anyway
        m->held_services++;
        return;
    }
    m->not_before = 0.0;
    m->slot_taken = false;

    // The frame the engine chose, by its exact timestamp: the queue's nearest-frame pick then cannot pick another.
    struct pl_queue_params qp = *pl_queue_params(
        .interpolation_threshold = 1e9, // never mix frames: a monitor shows one film frame per refresh
        .drift_compensation = 0.0,      // the film clock is exact; no hidden timestamp shifts
        .timeout = 0,                   // never wait for the decoder inside the refresh loop
        .radius = pl_frame_mix_radius(&m->params),
        .pts = out.pts,
    );
    struct pl_frame_mix mix;
    switch (pl_queue_update(m->queue, &mix, &qp)) {
    case PL_QUEUE_ERR:
        fail(m, "pl_queue_update failed mid-run (frame %.6f)", out.pts);
        return;
    case PL_QUEUE_EOF:
        break; // the engine ends the run when the last frame has been up for its time
    case PL_QUEUE_MORE:
        m->queue_more++; // the decoder is behind: the mix may be stale or empty
        break;
    case PL_QUEUE_OK:
        break;
    }
    int front = front_index(&mix);
    int drawn = -1;
    if (front >= 0) {
        if (out.first_of_epoch || m->sig_epoch != out.epoch) {
            // frame 0 of the epoch: its signature is the base for ordinals. The queue must hold exactly this frame first.
            struct pl_source_frame first;
            m->first_sig = mix.signatures[front];
            m->sig_epoch = out.epoch;
            if (!pl_queue_peek(m->queue, 0, &first) || first.pts != out.pts)
                m->drawn_mismatch++;
        }
        drawn = (int) (mix.signatures[front] - m->first_sig);
    } else {
        m->empty_mix++;
    }
    engine_note_drawn(&A.engine, m->slot, out.rec, drawn);
    if (drawn != (int) out.ord)
        m->drawn_mismatch++;

    struct pic_refresh pr = {
        .m = m, .mix = &mix, .front = front, .cue = out.cue, .shot_id = out.shot->id, .pts = out.pts, .ord = out.ord,
        .epoch = out.epoch, .first_of_epoch = out.first_of_epoch, .k = out.k, .wake = out.target, .timed = true,
    };
    if (A.cfg.headless_hz) {
        // no swapchain: the frame was fetched exactly as a real run does, the picture path's CPU side runs, nothing is
        // drawn or presented
        picture_plan_only(&pr);
        struct present_rec p = { .call = now, .ret = now_s(), .target = out.target, .frame = out.ord };
        push_present(m, &p);
        return;
    }

    struct pl_swapchain_frame sf;
    if (!pl_swapchain_start_frame(m->pls, &sf)) {
        fail(m, "pl_swapchain_start_frame failed mid-run");
        return;
    }
    struct pl_frame tgt;
    pl_frame_from_swapchain(&tgt, &sf);

    pr.sf = &sf;
    double t_r0 = now_ms();
    bool ok = front >= 0 && picture_draw(&pr);
    double t_r1 = now_ms();
    double sync_ms = 0.0;
    if (ok && A.cfg.sync_render) {
        pl_gpu_finish(A.gpu.gpu);
        sync_ms = now_ms() - t_r1;
    }
    if (!ok) {
        // keep the frame protocol: a started frame is always submitted
        m->render_fail++;
        pl_frame_clear(A.gpu.gpu, &tgt, (const float[3]) {0});
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
    p.target = out.target;
    p.frame = out.ord;
    IDXGISwapChain1_GetLastPresentCount(m->sc, &p.id);
    if (FAILED(hr)) {
        fail(m, "Present1 failed: 0x%08lx", (unsigned long) hr);
        return;
    }
    push_present(m, &p);

    samples_push(&m->render_cpu, t_r1 - t_r0);
    samples_push(&m->render_gpu, picture_last_gpu_ms(m->slot));
    if (A.cfg.sync_render)
        samples_push(&m->render_sync, sync_ms);
    samples_push(&m->lead, (out.target - p.call) * 1e3);

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

// ---- the film ends ----------------------------------------------------------------------------------

// The decoder is at the end of the file and the master has had the last frame up for its time.
static bool film_has_ended(double now, double *since)
{
    static double last_check;
    if (now < last_check + 0.05)
        return false;
    last_check = now;
    struct decoder_info di;
    decoder_get_info(A.dec, &di);
    if (di.eof && engine_on_last_frame(&A.engine)) {
        if (*since == 0.0)
            *since = now;
        return now - *since > A.frame_dur + END_OF_FILM_GRACE_S;
    }
    *since = 0.0;
    return false;
}

// ---- threads -------------------------------------------------------------------------------------------

static DWORD WINAPI render_thread(LPVOID arg)
{
    DWORD task = 0;
    HANDLE mmcss = A.cfg.headless_hz ? NULL : AvSetMmThreadCharacteristicsW(L"Games", &task); // headless: no boost
    int start = 0;
    double eof_since = 0.0;

    while (!atomic_load(&A.stop)) {
        double now = now_s();
        if (now >= render_end_time())
            break;
        engine_tick(&A.engine, now);
        if (A.control)
            control_poll(A.control, now);
        if (film_has_ended(now, &eof_since)) {
            printf("perf1: the film has ended\n");
            break;
        }
        // Rotating order, so a monitor that is always ready cannot starve the other. A monitor the engine is holding
        // is left out until its time. One whose wait already took the frame-latency slot is serviced at that time
        // without waiting: DXGI's semaphore will not signal again until this monitor presents.
        HANDLE h[MAX_MONITORS];
        int map[MAX_MONITORS], n = 0;
        bool any_held = false, serviced = false;
        for (int i = 0; i < A.nmon; i++) {
            int mi = (start + i) % A.nmon;
            if (A.mon[mi].not_before > now) {
                any_held = true;
                continue;
            }
            if (A.mon[mi].slot_taken) {
                service_monitor(&A.mon[mi]);
                start = (mi + 1) % A.nmon;
                serviced = true;
                break;
            }
            map[n] = mi;
            h[n++] = A.mon[mi].waitable;
        }
        if (serviced)
            continue;
        if (!n) {
            Sleep(1);
            continue;
        }
        DWORD r = WaitForMultipleObjectsEx((DWORD) n, h, FALSE, any_held ? HELD_POLL_MS : 20, TRUE);
        if (r < WAIT_OBJECT_0 + (DWORD) n) {
            int mi = map[r - WAIT_OBJECT_0];
            service_monitor(&A.mon[mi]);
            start = (mi + 1) % A.nmon;
        } else if (r != WAIT_TIMEOUT && r != WAIT_IO_COMPLETION) {
            fprintf(stderr, "perf1: waiting on the swapchains failed: %lu\n", (unsigned long) r);
            break;
        }
    }
    atomic_store(&A.stop, true);
    if (mmcss)
        AvRevertMmThreadCharacteristics(mmcss);
    if (A.cfg.headless_hz)
        picture_write_logs(); // real runs write theirs when the device goes (display_destroy)
    atomic_store(&A.render_done, true);
    return 0;
}

void render_thread_start(HANDLE *out)
{
    *out = CreateThread(NULL, 0, render_thread, NULL, 0, NULL);
}

static DWORD WINAPI vblank_thread(LPVOID arg)
{
    struct monitor *m = arg;
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
    while (!atomic_load(&A.stop)) {
        if (FAILED(IDXGIOutput_WaitForVBlank(m->output))) {
            Sleep(1);
            continue;
        }
        vclock_add(&m->clock, now_s());
    }
    return 0;
}

void render_vblank_thread_start(struct monitor *m, HANDLE *out)
{
    *out = CreateThread(NULL, 0, vblank_thread, m, 0, NULL);
}

// ---- start-up: first frames ----------------------------------------------------------------------------

// Fully renders frame 0 on every monitor and presents it once, so shader compilation and the first texture imports are
// not counted against the film. Also learns the pixel aspect ratio and works out the picture's defaults.
bool render_warm_up(void)
{
    build_layout_monitors();
    picture_geometry();
    if (!picture_init_subs())
        return false;
    for (int i = 0; i < A.nmon; i++) {
        struct monitor *m = &A.mon[i];
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
        if (i == 0) {
            float sar = frame_sar(mix.frames[front]);
            A.sar = sar > 0 ? sar : 0.0;
        }
        // the first shot of the sheet that is current now, for the warm-up frame
        const struct cue_sheet *sheet = cue_store_current(&A.store);
        const struct cue_shot *cue = &sheet->shots[cue_sheet_find_shot(sheet, 0.0)];
        struct lay_picture pic;
        layout_shot(cue, A.src_w, A.src_h, A.sar, A.lay_mon, A.nmon, &A.lay_def, &pic);
        if (i == 0) {
            A.first_pic = pic;
            A.pic_x0 = pic.rect_mm[0];
            A.pic_y0 = pic.rect_mm[1];
            A.pic_x1 = pic.rect_mm[2];
            A.pic_y1 = pic.rect_mm[3];
        }
        if (A.cfg.headless_hz)
            continue;

        struct pl_swapchain_frame sf;
        if (!pl_swapchain_start_frame(m->pls, &sf)) {
            fail(m, "first pl_swapchain_start_frame failed");
            return false;
        }
        struct pl_frame tgt;
        pl_frame_from_swapchain(&tgt, &sf);
        struct pic_refresh pr = {
            .m = m, .mix = &mix, .front = front, .cue = cue, .shot_id = UINT32_MAX, .pts = 0.0, .ord = -1, .epoch = -1,
            .wake = now_s(), .sf = &sf,
        };
        bool ok = picture_draw(&pr);
        if (!ok)
            pl_frame_clear(A.gpu.gpu, &tgt, (const float[3]) {0}); // a started frame is always submitted
        if (!pl_swapchain_submit_frame(m->pls)) {
            fail(m, "first submit failed");
            return false;
        }
        if (!ok) {
            fail(m, "first render failed");
            return false;
        }
        pl_gpu_finish(A.gpu.gpu);
        DXGI_PRESENT_PARAMETERS pp = {0};
        IDXGISwapChain1_Present1(m->sc, 1, 0, &pp);
    }
    return true;
}
