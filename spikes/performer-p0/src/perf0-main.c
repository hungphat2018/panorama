// perf0: render loop, statistics and result files. Adapted from libplacebo
// demos/plplay.c (CC0 / public domain).
#define COBJMACROS
#include "perf0.h"

#include <math.h>
#include <psapi.h>
#include <timeapi.h>

#include <libavutil/avutil.h>
#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>

// One record per rendered refresh.
struct refresh_rec {
    double t_ms;          // start of the iteration, since the first presented frame
    float poll_ms;        // window message pump (glfwPollEvents), measured just before this iteration
    float acquire_ms;     // pl_swapchain_start_frame
    float update_ms;      // pl_queue_update
    float render_ms;      // pl_render_image_mix as seen by the CPU
    float sync_ms;        // pl_gpu_finish after the render (--sync-render only)
    float gpu_ms;         // sum of libplacebo's per-pass GPU timer samples for this render
    float submit_ms;      // pl_swapchain_submit_frame
    float swap_ms;        // pl_swapchain_swap_buffers (includes the vsync wait)
    float interval_ms;    // time between consecutive swap_buffers returns
    int16_t dxgi_refresh_delta; // refreshes between consecutive presents (D3D11 only, 0 = unknown)
    uint8_t new_frame;    // front film frame changed on this refresh
    uint8_t n_mix;        // frames in the mix
};

struct run {
    struct cfg cfg;
    struct gpu_ctx g;
    pl_log log;
    pl_renderer rr;
    pl_queue queue;
    pl_cache cache;
    uint64_t cache_sig;
    struct decoder *dec;
    struct pl_render_params params;
    IDXGISwapChain *dxgi;

    // per-render accumulators filled by info_cb
    uint64_t gpu_acc_ns;
    int gpu_passes;

    struct refresh_rec *recs;
    size_t nrecs, caprecs;

    uint32_t run_hist[40];   // refreshes per film frame: index = count, [39] = 39 or more
    uint32_t dxgi_hist[8];   // refresh delta between presents: index = delta, [7] = 7 or more
    uint32_t stalls;
    double stall_ms;

    double t_main_ms;        // process age when main() started
    double t_first_render_done_ms, t_first_present_ms;
    double wall_start_ms;    // QPC at first present
    double measured_s;
    double cpu_proc_s, cpu_render_thread_s;
    double peak_ws_mb, peak_pagefile_mb;
    bool eof;
    char error[256];
};

static void info_cb(void *priv, const struct pl_render_info *info)
{
    struct run *r = priv;
    r->gpu_acc_ns += info->pass->last;
    r->gpu_passes++;
}

static double filetime_s(FILETIME ft)
{
    ULARGE_INTEGER u = { .LowPart = ft.dwLowDateTime, .HighPart = ft.dwHighDateTime };
    return (double) u.QuadPart / 1e7;
}

static double process_cpu_s(void)
{
    FILETIME c, e, k, u;
    GetProcessTimes(GetCurrentProcess(), &c, &e, &k, &u);
    return filetime_s(k) + filetime_s(u);
}

static double thread_cpu_s(void)
{
    FILETIME c, e, k, u;
    GetThreadTimes(GetCurrentThread(), &c, &e, &k, &u);
    return filetime_s(k) + filetime_s(u);
}

static bool push_rec(struct run *r, const struct refresh_rec *rec)
{
    if (r->nrecs == r->caprecs) {
        size_t cap = r->caprecs ? r->caprecs * 2 : 32768;
        struct refresh_rec *n = realloc(r->recs, cap * sizeof(*n));
        if (!n)
            return false;
        r->recs = n;
        r->caprecs = cap;
    }
    r->recs[r->nrecs++] = *rec;
    return true;
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

// Fit the picture inside the window keeping its aspect ratio (plplay's default "pad" zoom).
static bool render_frame(struct run *r, const struct pl_swapchain_frame *frame,
                         const struct pl_frame_mix *mix)
{
    struct pl_frame target;
    pl_frame_from_swapchain(&target, frame);

    pl_rect2df crop = mix->frames[0]->crop;
    float sar = frame_sar(mix->frames[0]);
    if (sar)
        pl_rect2df_stretch(&crop, fmaxf(1.0f, sar), fmaxf(1.0f, 1.0f / sar));
    pl_rect2df_aspect_copy(&target.crop, &crop, 0.0f);

    // --worst-case: libplacebo reuses a frame's rendered texture while its source crop is unchanged.
    // Moving the crop by 1/1000 pixel on every refresh (what a pan does) defeats that cache, so the
    // whole pipeline, scaler included, runs on every refresh.
    struct pl_frame jittered[16];
    const struct pl_frame *jittered_ptrs[16];
    struct pl_frame_mix jmix;
    if (r->cfg.worst_case && mix->num_frames <= 16) {
        static unsigned tick;
        float dx = (++tick & 1) ? 0.001f : -0.001f;
        for (int i = 0; i < mix->num_frames; i++) {
            jittered[i] = *mix->frames[i];
            jittered[i].crop.x0 += dx;
            jittered[i].crop.x1 += dx;
            jittered_ptrs[i] = &jittered[i];
        }
        jmix = *mix;
        jmix.frames = jittered_ptrs;
        mix = &jmix;
    }

    return pl_render_image_mix(r->rr, mix, &target, &r->params);
}

static const struct pl_render_params *preset_params(const char *name)
{
    if (!strcmp(name, "fast"))
        return &pl_render_fast_params;
    if (!strcmp(name, "hq"))
        return &pl_render_high_quality_params;
    return &pl_render_default_params;
}

// Debug aid: render one film frame alone (no mixing) into an offscreen RGBA8 texture and write it
// as a binary PPM, so different decode paths can be compared pixel by pixel.
static bool dump_frame(struct run *r, const struct pl_frame_mix *mix, int idx, const char *path)
{
    pl_gpu gpu = r->g.gpu;
    int w = r->cfg.win_w, h = r->cfg.win_h;
    pl_fmt fmt = pl_find_fmt(gpu, PL_FMT_UNORM, 4, 8, 8, PL_FMT_CAP_RENDERABLE | PL_FMT_CAP_HOST_READABLE);
    pl_tex tex = NULL;
    if (!fmt || !pl_tex_recreate(gpu, &tex, pl_tex_params(
            .w = w, .h = h, .format = fmt, .renderable = true, .blit_dst = true, .host_readable = true)))
        return false;

    const struct pl_frame *frames[1] = { mix->frames[idx] };
    uint64_t sigs[1] = { mix->signatures[idx] };
    __typeof__(*mix->timestamps) ts[1] = { 0 };
    struct pl_frame_mix single = {
        .num_frames = 1, .frames = frames, .signatures = sigs, .timestamps = ts, .vsync_duration = 1.0,
    };

    struct pl_frame target = {
        .num_planes = 1,
        .planes = {{ .texture = tex, .components = 4, .component_mapping = {0, 1, 2, 3} }},
        .crop = { 0, 0, w, h },
        .repr = pl_color_repr_rgb,
        .color = pl_color_space_srgb,
    };
    pl_rect2df crop = frames[0]->crop;
    float sar = frame_sar(frames[0]);
    if (sar)
        pl_rect2df_stretch(&crop, fmaxf(1.0f, sar), fmaxf(1.0f, 1.0f / sar));
    pl_rect2df_aspect_copy(&target.crop, &crop, 0.0f);

    uint8_t *rgba = malloc((size_t) w * h * 4);
    bool ok = rgba && pl_render_image_mix(r->rr, &single, &target, &r->params) &&
              pl_tex_download(gpu, pl_tex_transfer_params(.tex = tex, .ptr = rgba));
    if (ok) {
        FILE *f = fopen(path, "wb");
        ok = f != NULL;
        if (f) {
            fprintf(f, "P6\n%d %d\n255\n", w, h);
            for (size_t i = 0; i < (size_t) w * h; i++)
                fwrite(&rgba[i * 4], 1, 3, f);
            fclose(f);
        }
    }
    free(rgba);
    pl_tex_destroy(gpu, &tex);
    return ok;
}

// A frame taken with pl_swapchain_start_frame must always be submitted: libplacebo's Vulkan swapchain
// keeps an internal lock until pl_swapchain_submit_frame, so destroying the swapchain after an early
// exit (end of file, error) would hang. Clear the target to black and submit it.
static void abort_frame(struct run *r, const struct pl_swapchain_frame *frame)
{
    struct pl_frame target;
    pl_frame_from_swapchain(&target, frame);
    pl_frame_clear(r->g.gpu, &target, (const float[3]) {0});
    pl_swapchain_submit_frame(r->g.sw);
}

// Headless render benchmark: draw one mapped film frame into an offscreen target of the requested
// size over and over with every cache bypassed (pl_render_image), waiting for the GPU after each
// draw. Answers "how long does one full per-monitor picture take", independent of any window.
static bool bench_render(struct run *r)
{
    const struct cfg *cfg = &r->cfg;
    pl_gpu gpu = r->g.gpu;
    const int tw = cfg->bench_w, th = cfg->bench_h;
    const int warmup = 30, iters = cfg->bench_iters > 0 ? cfg->bench_iters : 600;

    struct pl_queue_params qparams = *pl_queue_params(
        .interpolation_threshold = 0.01,
        .timeout = UINT64_MAX,
        .radius = pl_frame_mix_radius(&r->params),
    );
    struct pl_frame_mix mix;
    if (pl_queue_update(r->queue, &mix, &qparams) != PL_QUEUE_OK || front_index(&mix) < 0) {
        snprintf(r->error, sizeof(r->error), "no frame available for the render benchmark");
        return false;
    }
    const struct pl_frame *image = mix.frames[front_index(&mix)];

    // Same kind of target the swapchain gave on this machine: rgb10a2, else 8-bit.
    pl_fmt fmt = pl_find_named_fmt(gpu, "rgb10a2");
    if (!fmt || !(fmt->caps & PL_FMT_CAP_RENDERABLE) || !(fmt->caps & PL_FMT_CAP_BLITTABLE))
        fmt = pl_find_fmt(gpu, PL_FMT_UNORM, 4, 8, 8, PL_FMT_CAP_RENDERABLE | PL_FMT_CAP_BLITTABLE);
    pl_tex tex = NULL;
    if (!fmt || !pl_tex_recreate(gpu, &tex, pl_tex_params(
            .w = tw, .h = th, .format = fmt, .renderable = true, .blit_dst = true, .blit_src = true))) {
        snprintf(r->error, sizeof(r->error), "cannot create the %dx%d offscreen target", tw, th);
        return false;
    }

    struct pl_frame target = {
        .num_planes = 1,
        .planes = {{ .texture = tex, .components = 4, .component_mapping = {0, 1, 2, 3} }},
        .crop = { 0, 0, tw, th },
        .repr = pl_color_repr_rgb,
        .color = pl_color_space_srgb,
    };
    pl_rect2df crop = image->crop;
    float sar = frame_sar(image);
    if (sar)
        pl_rect2df_stretch(&crop, fmaxf(1.0f, sar), fmaxf(1.0f, 1.0f / sar));
    pl_rect2df_aspect_copy(&target.crop, &crop, 0.0f);

    struct samples wall = {0}, gpu_ms = {0};
    bool ok = true;
    for (int i = 0; i < warmup + iters && ok; i++) {
        r->gpu_acc_ns = 0;
        double t0 = now_ms();
        ok = pl_render_image(r->rr, image, &target, &r->params);
        pl_gpu_finish(gpu);
        double t1 = now_ms();
        if (i >= warmup) {
            samples_push(&wall, t1 - t0);
            samples_push(&gpu_ms, (double) r->gpu_acc_ns / 1e6);
        }
    }
    if (!ok) {
        snprintf(r->error, sizeof(r->error), "pl_render_image failed in the render benchmark");
    } else {
        struct summary sw, sg;
        summarize(wall.v, wall.n, &sw);
        summarize(gpu_ms.v, gpu_ms.n, &sg);
        printf("{\"run_id\": ");
        json_string(stdout, cfg->run_id);
        printf(", \"bench_render\": {\"gpu\": ");
        json_string(stdout, r->g.gpu_name);
        printf(", \"backend\": \"%s\", \"preset\": \"%s\", \"source\": [%d, %d], \"target\": [%d, %d], "
               "\"target_format\": \"%s\", \"iterations\": %d,\n",
               cfg->backend == BACKEND_D3D11 ? "d3d11" : "vulkan", cfg->preset,
               (int) fabsf(image->crop.x1 - image->crop.x0), (int) fabsf(image->crop.y1 - image->crop.y0),
               tw, th, fmt->name, iters);
        printf("  \"wall_ms_draw_plus_gpu_finish\": {\"mean\": %.4f, \"p50\": %.4f, \"p90\": %.4f, \"p99\": %.4f, "
               "\"p999\": %.4f, \"max\": %.4f},\n",
               sw.mean, sw.p50, sw.p90, sw.p99, sw.p999, sw.max);
        printf("  \"gpu_pass_sum_ms\": {\"mean\": %.4f, \"p50\": %.4f, \"p90\": %.4f, \"p99\": %.4f, "
               "\"p999\": %.4f, \"max\": %.4f}}}\n",
               sg.mean, sg.p50, sg.p90, sg.p99, sg.p999, sg.max);
        fflush(stdout);
    }
    samples_free(&wall);
    samples_free(&gpu_ms);
    pl_tex_destroy(gpu, &tex);
    return ok;
}

static bool render_loop(struct run *r)
{
    const struct cfg *cfg = &r->cfg;
    struct gpu_ctx *g = &r->g;

    struct pl_queue_params qparams = *pl_queue_params(
        .interpolation_threshold = 0.01,
        .timeout = UINT64_MAX,
    );

    // Initial update blocks until the first frames exist; then fully render one frame and wait
    // for the GPU, so shader compilation is not counted against the first vsync (as plplay does).
    struct pl_frame_mix mix;
    switch (pl_queue_update(r->queue, &mix, &qparams)) {
    case PL_QUEUE_OK: break;
    case PL_QUEUE_EOF: snprintf(r->error, sizeof(r->error), "no frames decoded"); return false;
    default: snprintf(r->error, sizeof(r->error), "pl_queue_update failed on the first frame"); return false;
    }

    struct pl_swapchain_frame frame;
    if (!pl_swapchain_start_frame(g->sw, &frame)) {
        snprintf(r->error, sizeof(r->error), "first pl_swapchain_start_frame failed");
        return false;
    }
    if (frame.color_repr.alpha == PL_ALPHA_NONE)
        r->params.background_transparency = 0.0;
    if (!render_frame(r, &frame, &mix)) {
        snprintf(r->error, sizeof(r->error), "first render failed");
        abort_frame(r, &frame);
        return false;
    }
    if (!pl_swapchain_submit_frame(g->sw)) {
        snprintf(r->error, sizeof(r->error), "first submit failed");
        return false;
    }
    pl_gpu_finish(g->gpu);
    r->t_first_render_done_ms = process_age_ms();

    gpu_present(g);
    r->t_first_present_ms = process_age_ms();
    gpu_poll_events(g);

    double ts_start = now_ms();
    r->wall_start_ms = ts_start;
    double cpu_proc0 = process_cpu_s(), cpu_thr0 = thread_cpu_s();
    double ts_prev_swap = ts_start;
    uint64_t prev_sig = 0, base_sig = 0;
    bool have_sig = false, dumped = false;
    int run_len = 0;
    UINT prev_dxgi_count = 0, prev_dxgi_refresh = 0;
    bool have_dxgi_prev = false;

    for (;;) {
        double t_poll0 = now_ms();
        if (!gpu_poll_events(g) || gpu_escape_pressed(g))
            break;
        double t_loop = now_ms();
        if (cfg->seconds > 0 && (t_loop - ts_start) >= cfg->seconds * 1e3)
            break;

        struct refresh_rec rec = {0};
        rec.t_ms = t_loop - ts_start;
        rec.poll_ms = (float) (t_loop - t_poll0); // window message pump (glfwPollEvents)

        double t_a0 = now_ms();
        gpu_wait_frame_latency(g); // no-op unless --own-present
        if (!pl_swapchain_start_frame(g->sw, &frame)) {
            snprintf(r->error, sizeof(r->error), "pl_swapchain_start_frame failed mid-run");
            return false;
        }
        double t_a1 = now_ms();
        rec.acquire_ms = (float) (t_a1 - t_a0);

        qparams.timeout = 0; // non-blocking update
        qparams.radius = pl_frame_mix_radius(&r->params);
        qparams.pts = (t_a1 - ts_start) / 1e3;

        bool stalled = false;
    retry:
        switch (pl_queue_update(r->queue, &mix, &qparams)) {
        case PL_QUEUE_ERR:
            snprintf(r->error, sizeof(r->error), "pl_queue_update failed mid-run");
            abort_frame(r, &frame);
            return false;
        case PL_QUEUE_EOF:
            r->eof = true;
            abort_frame(r, &frame);
            goto finished;
        case PL_QUEUE_OK:
            break;
        case PL_QUEUE_MORE:
            qparams.timeout = UINT64_MAX; // wait for the decoder
            stalled = true;
            goto retry;
        }
        double t_u1 = now_ms();
        rec.update_ms = (float) (t_u1 - t_a1);
        if (stalled) {
            r->stalls++;
            r->stall_ms += t_u1 - t_a1;
            ts_start += t_u1 - t_a1; // do not let waiting for the decoder advance the film clock
        }

        int fi = front_index(&mix);
        uint64_t sig = fi >= 0 ? mix.signatures[fi] : 0;
        rec.n_mix = (uint8_t) mix.num_frames;
        if (!have_sig || sig != prev_sig) {
            rec.new_frame = 1;
            if (have_sig)
                r->run_hist[run_len < 39 ? run_len : 39]++;
            else
                base_sig = sig;
            have_sig = true;
            run_len = 0;
            prev_sig = sig;
        }
        run_len++;

        if (cfg->dump_path && !dumped && fi >= 0 && sig >= base_sig + (uint64_t) cfg->dump_frame) {
            dumped = true;
            if (!dump_frame(r, &mix, fi, cfg->dump_path))
                fprintf(stderr, "perf0: frame dump failed\n");
        }

        r->gpu_acc_ns = 0;
        r->gpu_passes = 0;
        double t_r0 = now_ms();
        if (!render_frame(r, &frame, &mix)) {
            snprintf(r->error, sizeof(r->error), "pl_render_image_mix failed mid-run");
            abort_frame(r, &frame);
            return false;
        }
        double t_r1 = now_ms();
        rec.render_ms = (float) (t_r1 - t_r0);
        if (cfg->sync_render) {
            pl_gpu_finish(g->gpu);
            rec.sync_ms = (float) (now_ms() - t_r1);
        }
        rec.gpu_ms = (float) ((double) r->gpu_acc_ns / 1e6);

        double t_s0 = now_ms();
        if (!pl_swapchain_submit_frame(g->sw)) {
            snprintf(r->error, sizeof(r->error), "pl_swapchain_submit_frame failed mid-run");
            return false;
        }
        double t_s1 = now_ms();
        rec.submit_ms = (float) (t_s1 - t_s0);
        gpu_present(g);
        double t_s2 = now_ms();
        rec.swap_ms = (float) (t_s2 - t_s1);
        rec.interval_ms = (float) (t_s2 - ts_prev_swap);
        ts_prev_swap = t_s2;

        if (r->dxgi) {
            DXGI_FRAME_STATISTICS fs;
            if (SUCCEEDED(IDXGISwapChain_GetFrameStatistics(r->dxgi, &fs))) {
                if (have_dxgi_prev && fs.PresentCount == prev_dxgi_count + 1) {
                    UINT delta = fs.PresentRefreshCount - prev_dxgi_refresh;
                    rec.dxgi_refresh_delta = (int16_t) (delta < 32000 ? delta : 32000);
                    r->dxgi_hist[delta < 7 ? delta : 7]++;
                }
                prev_dxgi_count = fs.PresentCount;
                prev_dxgi_refresh = fs.PresentRefreshCount;
                have_dxgi_prev = true;
            }
        }

        push_rec(r, &rec);
    }

finished:
    r->measured_s = (now_ms() - r->wall_start_ms) / 1e3;
    r->cpu_proc_s = process_cpu_s() - cpu_proc0;
    r->cpu_render_thread_s = thread_cpu_s() - cpu_thr0;
    return true;
}

// ---- output ---------------------------------------------------------------------------------

static void j_summary(FILE *f, const char *name, const struct summary *s, const char *tail)
{
    fprintf(f, "    \"%s\": {\"n\": %zu, \"min\": %.4f, \"mean\": %.4f, \"p50\": %.4f, \"p90\": %.4f, "
            "\"p99\": %.4f, \"p999\": %.4f, \"max\": %.4f}%s\n",
            name, s->n, s->min, s->mean, s->p50, s->p90, s->p99, s->p999, s->max, tail);
}

typedef double (*field_fn)(const struct refresh_rec *);
static double f_render(const struct refresh_rec *x) { return x->render_ms; }
static double f_gpu(const struct refresh_rec *x) { return x->gpu_ms; }
static double f_sync(const struct refresh_rec *x) { return x->sync_ms; }
static double f_submit(const struct refresh_rec *x) { return x->submit_ms; }
static double f_swap(const struct refresh_rec *x) { return x->swap_ms; }
static double f_update(const struct refresh_rec *x) { return x->update_ms; }
static double f_acquire(const struct refresh_rec *x) { return x->acquire_ms; }
static double f_interval(const struct refresh_rec *x) { return x->interval_ms; }

enum subset { SUB_ALL, SUB_STEADY, SUB_NEW, SUB_REPEAT };
#define STEADY_AFTER_MS 2000.0 // skip start-up shader compiles and queue fill for the "steady" view
#define SKIP_FIRST_REFRESHES 8 // the first presents return before the queue of frames is full

static void summarize_field(const struct run *r, field_fn fn, enum subset sub, struct summary *out)
{
    struct samples s = {0};
    for (size_t i = 0; i < r->nrecs; i++) {
        const struct refresh_rec *x = &r->recs[i];
        if (i < SKIP_FIRST_REFRESHES && fn == f_interval)
            continue;
        if (sub == SUB_STEADY && x->t_ms < STEADY_AFTER_MS)
            continue;
        if (sub == SUB_NEW && !(x->new_frame && x->t_ms >= STEADY_AFTER_MS))
            continue;
        if (sub == SUB_REPEAT && (x->new_frame || x->t_ms < STEADY_AFTER_MS))
            continue;
        samples_push(&s, fn(x));
    }
    summarize(s.v, s.n, out);
    samples_free(&s);
}

static void write_json(const struct run *r, const char *path)
{
    FILE *f = fopen(path, "w");
    if (!f) {
        fprintf(stderr, "perf0: cannot write %s\n", path);
        return;
    }
    const struct cfg *c = &r->cfg;
    struct decoder_info di;
    if (r->dec)
        decoder_get_info(r->dec, &di);
    else
        memset(&di, 0, sizeof(di));

    static const char *backends[] = { "d3d11", "vulkan" };
    static const char *decodes[] = { "sw", "d3d11va", "vulkan" };

    fprintf(f, "{\n");
    fprintf(f, "  \"run_id\": ");
    json_string(f, c->run_id);
    fprintf(f, ",\n  \"status\": \"%s\",\n  \"error\": ", r->error[0] ? "error" : "ok");
    json_string(f, r->error);
    fprintf(f, ",\n  \"config\": {\"file\": ");
    json_string(f, c->file);
    fprintf(f, ", \"backend\": \"%s\", \"decode\": \"%s\", \"preset\": \"%s\", "
            "\"window\": [%d, %d], \"seconds\": %.1f, \"loop\": %s, \"worst_case_no_frame_cache\": %s, "
            "\"sync_render\": %s, \"pl_cache_file\": %s, \"vk_swapchain_depth\": %d, \"own_present\": %s},\n",
            backends[c->backend], decodes[c->decode], c->preset, c->win_w, c->win_h, c->seconds,
            c->loop ? "true" : "false", c->worst_case ? "true" : "false", c->sync_render ? "true" : "false",
            c->cache_file ? "true" : "false", c->vk_depth ? c->vk_depth : 3, c->own_present ? "true" : "false");

    fprintf(f, "  \"env\": {\"libplacebo_api\": %d, \"ffmpeg\": \"%s\", \"libavutil\": \"%u.%u.%u\", \"gpu\": ",
            PL_API_VER, av_version_info(),
            avutil_version() >> 16, (avutil_version() >> 8) & 0xff, avutil_version() & 0xff);
    json_string(f, r->g.gpu_name);
    fprintf(f, ", \"monitor\": {\"device\": ");
    json_string(f, r->g.monitor_name);
    fprintf(f, ", \"hz\": %d, \"size\": [%d, %d]}},\n", r->g.monitor_hz, r->g.monitor_w, r->g.monitor_h);

    fprintf(f, "  \"media\": {\"codec\": \"%s\", \"pix_fmt\": \"%s\", \"hw_fmt\": \"%s\", \"size\": [%d, %d], "
            "\"fps\": %.4f, \"duration_s\": %.2f},\n",
            di.codec, di.pix_fmt, di.hw_fmt, di.width, di.height, di.fps, di.duration_s);

    double first_present = r->t_first_present_ms;
    fprintf(f, "  \"startup_ms_since_process_creation\": {\"main_entered\": %.1f, \"window_created\": %.1f, "
            "\"gpu_ready\": %.1f, \"file_opened\": %.1f, \"codec_opened\": %.1f, \"first_frame_decoded\": %.1f, "
            "\"first_render_done\": %.1f, \"first_present\": %.1f},\n",
            r->t_main_ms, r->g.t_window_ms, r->g.t_gpu_ms, di.t_open_ms, di.t_codec_ms, di.t_first_decoded_ms,
            r->t_first_render_done_ms, first_present);
    fprintf(f, "  \"first_frame_ms\": {\"from_process_creation\": %.1f, \"from_main\": %.1f, "
            "\"from_gpu_ready\": %.1f},\n",
            first_present, first_present - r->t_main_ms, first_present - r->g.t_gpu_ms);

    // Refresh period: the mean loop interval after start-up. In a loop that never starves this
    // equals the real refresh period (measured: 240.04 Hz here), whatever the present queue does.
    // Late = loop interval over 1.5x the period (a refresh got no new picture). With a deep Vulkan
    // present queue the loop is bursty, so a long interval there is not a dropped picture:
    // "skipped_refreshes_estimate" (refreshes that elapsed minus pictures submitted) is the
    // queue-depth-independent count.
    struct summary iv;
    summarize_field(r, f_interval, SUB_STEADY, &iv);
    double nominal = iv.mean > 0.0 ? iv.mean : (r->g.monitor_hz ? 1000.0 / r->g.monitor_hz : 4.1667);
    size_t late15 = 0, late2 = 0, hitches = 0, nrefresh_steady = 0;
    double t_first_steady = -1.0, t_last_steady = 0.0, max_poll = 0.0;
    for (size_t i = 0; i < r->nrecs; i++) {
        if (r->recs[i].t_ms < STEADY_AFTER_MS)
            continue;
        if (t_first_steady < 0.0)
            t_first_steady = r->recs[i].t_ms;
        t_last_steady = r->recs[i].t_ms;
        nrefresh_steady++;
        if (r->recs[i].interval_ms > 1.5 * nominal)
            late15++;
        if (r->recs[i].interval_ms > 2.5 * nominal)
            late2++;
        if (r->recs[i].interval_ms > 50.0)
            hitches++;
        if (r->recs[i].poll_ms > max_poll)
            max_poll = r->recs[i].poll_ms;
    }
    // Elapsed refreshes at the display's real period, from the monitor's nominal rate.
    double display_period = r->g.monitor_hz ? 1000.0 / r->g.monitor_hz : nominal;
    double skipped_est = nrefresh_steady > 1
        ? (t_last_steady - t_first_steady) / display_period - (double) (nrefresh_steady - 1) : 0.0;
    size_t film_displayed = 0;
    for (size_t i = 0; i < r->nrecs; i++)
        film_displayed += r->recs[i].new_frame;

    fprintf(f, "  \"playback\": {\"measured_s\": %.3f, \"refreshes_rendered\": %zu, \"refresh_rate_measured_hz\": %.3f, "
            "\"film_frames_front_changes\": %zu, \"queue_estimated_fps\": %.4f, \"queue_estimated_display_fps\": %.4f, "
            "\"eof\": %s},\n",
            r->measured_s, r->nrecs, nominal > 0 ? 1000.0 / nominal : 0.0, film_displayed,
            r->queue ? pl_queue_estimate_fps(r->queue) : 0.0, r->queue ? pl_queue_estimate_vps(r->queue) : 0.0,
            r->eof ? "true" : "false");
    fprintf(f, "  \"counts\": {\"decoded\": %u, \"mapped\": %u, \"dropped_by_queue\": %u, \"queue_stalls\": %u, "
            "\"queue_stall_ms\": %.1f, \"late_refreshes_over_1p5x_steady\": %zu, \"late_refreshes_over_2p5x_steady\": %zu, "
            "\"steady_refreshes\": %zu, \"nominal_interval_ms\": %.4f, \"skipped_refreshes_estimate\": %.1f, "
            "\"hitches_over_50ms_steady\": %zu, \"max_message_pump_ms\": %.2f},\n",
            di.decoded, di.mapped, di.dropped, r->stalls, r->stall_ms, late15, late2, nrefresh_steady, nominal,
            skipped_est, hitches, max_poll);

    fprintf(f, "  \"refreshes_per_film_frame\": {");
    bool first = true;
    for (int i = 0; i < 40; i++) {
        if (!r->run_hist[i])
            continue;
        fprintf(f, "%s\"%d%s\": %u", first ? "" : ", ", i, i == 39 ? "+" : "", r->run_hist[i]);
        first = false;
    }
    fprintf(f, "},\n");
    fprintf(f, "  \"dxgi_refreshes_between_presents\": {");
    first = true;
    for (int i = 0; i < 8; i++) {
        if (!r->dxgi_hist[i])
            continue;
        fprintf(f, "%s\"%d%s\": %u", first ? "" : ", ", i, i == 7 ? "+" : "", r->dxgi_hist[i]);
        first = false;
    }
    fprintf(f, "},\n");

    fprintf(f, "  \"timings_ms\": {\n");
    struct summary s;
    summarize_field(r, f_render, SUB_ALL, &s);    j_summary(f, "render_cpu_all", &s, ",");
    summarize_field(r, f_render, SUB_STEADY, &s); j_summary(f, "render_cpu_steady", &s, ",");
    summarize_field(r, f_render, SUB_NEW, &s);    j_summary(f, "render_cpu_steady_new_film_frame", &s, ",");
    summarize_field(r, f_render, SUB_REPEAT, &s); j_summary(f, "render_cpu_steady_repeated_frame", &s, ",");
    summarize_field(r, f_gpu, SUB_STEADY, &s);    j_summary(f, "gpu_pass_sum_steady", &s, ",");
    summarize_field(r, f_gpu, SUB_NEW, &s);       j_summary(f, "gpu_pass_sum_steady_new_film_frame", &s, ",");
    summarize_field(r, f_gpu, SUB_REPEAT, &s);    j_summary(f, "gpu_pass_sum_steady_repeated_frame", &s, ",");
    if (c->sync_render) {
        summarize_field(r, f_sync, SUB_STEADY, &s); j_summary(f, "gpu_finish_wait_steady", &s, ",");
    }
    summarize_field(r, f_update, SUB_STEADY, &s);   j_summary(f, "queue_update_steady", &s, ",");
    summarize_field(r, f_acquire, SUB_STEADY, &s);  j_summary(f, "acquire_steady", &s, ",");
    summarize_field(r, f_submit, SUB_STEADY, &s);   j_summary(f, "submit_steady", &s, ",");
    summarize_field(r, f_swap, SUB_STEADY, &s);     j_summary(f, "swap_steady", &s, ",");
    summarize_field(r, f_interval, SUB_ALL, &s);    j_summary(f, "present_interval_all", &s, ",");
    summarize_field(r, f_interval, SUB_STEADY, &s); j_summary(f, "present_interval_steady", &s, "");
    fprintf(f, "  },\n");

    double ncpu = (double) GetActiveProcessorCount(ALL_PROCESSOR_GROUPS);
    fprintf(f, "  \"process\": {\"cpu_s\": %.3f, \"cpu_pct_of_one_core\": %.1f, \"cpu_pct_of_all_threads\": %.2f, "
            "\"logical_cpus\": %d, \"render_thread_cpu_pct\": %.1f, \"decode_thread_busy_s_in_libav_calls\": %.3f, "
            "\"decode_thread_cpu_s\": %.3f, \"peak_working_set_mb\": %.0f, \"peak_pagefile_mb\": %.0f}\n",
            r->cpu_proc_s, r->measured_s > 0 ? 100.0 * r->cpu_proc_s / r->measured_s : 0.0,
            r->measured_s > 0 ? 100.0 * r->cpu_proc_s / (r->measured_s * ncpu) : 0.0, (int) ncpu,
            r->measured_s > 0 ? 100.0 * r->cpu_render_thread_s / r->measured_s : 0.0, di.decode_busy_s,
            di.decode_thread_cpu_s, r->peak_ws_mb, r->peak_pagefile_mb);
    fprintf(f, "}\n");
    fclose(f);
}

static void write_csv(const struct run *r, const char *path)
{
    FILE *f = fopen(path, "w");
    if (!f)
        return;
    fprintf(f, "i,t_ms,poll_ms,acquire_ms,update_ms,render_ms,sync_ms,gpu_ms,submit_ms,swap_ms,interval_ms,"
               "dxgi_refresh_delta,new_frame,n_mix\n");
    for (size_t i = 0; i < r->nrecs; i++) {
        const struct refresh_rec *x = &r->recs[i];
        fprintf(f, "%zu,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%.3f,%d,%d,%d\n", i, x->t_ms, x->poll_ms,
                x->acquire_ms, x->update_ms, x->render_ms, x->sync_ms, x->gpu_ms, x->submit_ms, x->swap_ms,
                x->interval_ms, x->dxgi_refresh_delta, x->new_frame, x->n_mix);
    }
    fclose(f);
}

// ---- command line -----------------------------------------------------------------------------

static void usage(const char *argv0)
{
    fprintf(stderr,
        "Usage: %s --file PATH [--backend d3d11|vulkan] [--decode sw|d3d11va|vulkan] [--adapter SUBSTR]\n"
        "          [--preset fast|default|hq] [--size WxH] [--seconds N] [--loop] [--worst-case] [--sync-render]\n"
        "          [--bench-decode] [--bench-render WxH [--bench-iters N]] [--vk-depth N] [--cache FILE]\n"
        "          [--out PREFIX] [--id RUN_ID] [--dump-ppm FILE [--dump-frame N]] [--log-level 0..6] [--probe]\n"
        "  --seconds N      measured playback seconds after the first presented frame (0 = until EOF)\n"
        "  --worst-case     move the source crop 1/1000 px every refresh (defeats the frame cache, as a pan does)\n"
        "  --sync-render    pl_gpu_finish after every render (wall time of CPU+GPU for the render)\n"
        "  --bench-decode   decode only, as fast as possible, for --seconds (hidden window)\n"
        "  --bench-render   headless: draw one frame into an offscreen WxH target repeatedly, caches bypassed\n"
        "  --vk-depth N     Vulkan swapchain depth (0 = libplacebo default 3)\n"
        "  --own-present    D3D11: own waitable DXGI swapchain (max latency 1) wrapped by libplacebo, own Present1\n"
        "  --dump-ppm FILE  write film frame --dump-frame (default 50) rendered alone, for pixel comparison\n",
        argv0);
}

static bool parse_args(struct cfg *c, int argc, char **argv)
{
    *c = (struct cfg) {
        .backend = BACKEND_D3D11,
        .decode = DECODE_SW,
        .preset = "default",
        .win_w = 1280,
        .win_h = 800,
        .seconds = 60.0,
        .dump_frame = 50,
        .log_level = PL_LOG_WARN,
    };

    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--file") && v) { c->file = v; i++; }
        else if (!strcmp(a, "--backend") && v) {
            if (!strcmp(v, "d3d11")) c->backend = BACKEND_D3D11;
            else if (!strcmp(v, "vulkan")) c->backend = BACKEND_VULKAN;
            else return false;
            i++;
        } else if (!strcmp(a, "--decode") && v) {
            if (!strcmp(v, "sw")) c->decode = DECODE_SW;
            else if (!strcmp(v, "d3d11va")) c->decode = DECODE_D3D11VA;
            else if (!strcmp(v, "vulkan")) c->decode = DECODE_VULKAN;
            else return false;
            i++;
        }
        else if (!strcmp(a, "--adapter") && v) { c->adapter = v; i++; }
        else if (!strcmp(a, "--preset") && v) { c->preset = v; i++; }
        else if (!strcmp(a, "--size") && v) {
            if (sscanf(v, "%dx%d", &c->win_w, &c->win_h) != 2) return false;
            i++;
        }
        else if (!strcmp(a, "--seconds") && v) { c->seconds = atof(v); i++; }
        else if (!strcmp(a, "--loop")) c->loop = true;
        else if (!strcmp(a, "--worst-case")) c->worst_case = true;
        else if (!strcmp(a, "--sync-render")) c->sync_render = true;
        else if (!strcmp(a, "--bench-decode")) { c->bench_decode = true; c->hidden = true; }
        else if (!strcmp(a, "--vk-depth") && v) { c->vk_depth = atoi(v); i++; }
        else if (!strcmp(a, "--own-present")) c->own_present = true;
        else if (!strcmp(a, "--bench-render") && v) {
            if (sscanf(v, "%dx%d", &c->bench_w, &c->bench_h) != 2 || c->bench_w < 1 || c->bench_h < 1) return false;
            c->hidden = true;
            i++;
        }
        else if (!strcmp(a, "--bench-iters") && v) { c->bench_iters = atoi(v); i++; }
        else if (!strcmp(a, "--dump-ppm") && v) { c->dump_path = v; i++; }
        else if (!strcmp(a, "--dump-frame") && v) { c->dump_frame = atoi(v); i++; }
        else if (!strcmp(a, "--cache") && v) { c->cache_file = v; i++; }
        else if (!strcmp(a, "--out") && v) { c->out_prefix = v; i++; }
        else if (!strcmp(a, "--id") && v) { c->run_id = v; i++; }
        else if (!strcmp(a, "--log-level") && v) { c->log_level = atoi(v); i++; }
        else if (!strcmp(a, "--probe")) c->probe_only = true;
        else return false;
    }

    if (!c->run_id)
        c->run_id = "run";
    if (!c->probe_only && !c->file)
        return false;
    if (c->own_present && c->backend != BACKEND_D3D11) {
        fprintf(stderr, "perf0: --own-present needs --backend d3d11 (libplacebo's Vulkan swapchain presents inside submit_frame)\n");
        return false;
    }
    if (c->decode == DECODE_D3D11VA && c->backend != BACKEND_D3D11) {
        fprintf(stderr, "perf0: --decode d3d11va needs --backend d3d11\n");
        return false;
    }
    if (c->decode == DECODE_VULKAN && c->backend != BACKEND_VULKAN) {
        fprintf(stderr, "perf0: --decode vulkan needs --backend vulkan\n");
        return false;
    }
    return true;
}

static void cache_load(struct run *r)
{
    if (!r->cfg.cache_file)
        return;
    r->cache = pl_cache_create(pl_cache_params(.log = r->log, .max_total_size = 50 << 20));
    pl_gpu_set_cache(r->g.gpu, r->cache);
    FILE *f = fopen(r->cfg.cache_file, "rb");
    if (f) {
        pl_cache_load_file(r->cache, f);
        r->cache_sig = pl_cache_signature(r->cache);
        fclose(f);
    }
}

static void cache_save(struct run *r)
{
    if (!r->cache)
        return;
    if (pl_cache_signature(r->cache) != r->cache_sig) {
        FILE *f = fopen(r->cfg.cache_file, "wb");
        if (f) {
            pl_cache_save_file(r->cache, f);
            fclose(f);
        }
    }
    pl_cache_destroy(&r->cache);
}

int main(int argc, char **argv)
{
    static struct run run;
    struct run *r = &run;
    r->t_main_ms = process_age_ms();
    timeBeginPeriod(1); // 1 ms timers: same as the plplay window layer does on Windows

    if (!parse_args(&r->cfg, argc, argv)) {
        usage(argv[0]);
        return 2;
    }
    const struct cfg *cfg = &r->cfg;

    if (cfg->probe_only) {
        printf("{\"adapters\": ");
        gpu_print_topology(stdout);
        printf("}\n");
        return 0;
    }

    r->log = pl_log_create(PL_API_VER, pl_log_params(.log_cb = pl_log_simple, .log_level = cfg->log_level));

    int rc = 1;
    if (!gpu_init(&r->g, cfg, r->log)) {
        snprintf(r->error, sizeof(r->error), "gpu_init failed");
        goto done;
    }
    cache_load(r);

    r->params = *preset_params(cfg->preset);
    r->params.info_callback = info_cb;
    r->params.info_priv = r;
    r->params.skip_caching_single_frame = cfg->worst_case;

    r->rr = pl_renderer_create(r->log, r->g.gpu);
    r->queue = pl_queue_create(r->g.gpu);
    if (!r->rr || !r->queue) {
        snprintf(r->error, sizeof(r->error), "renderer or queue creation failed");
        goto done;
    }
    if (r->g.d3d11 && r->g.sw)
        r->dxgi = pl_d3d11_swapchain_unwrap(r->g.sw);

    r->dec = decoder_open(cfg, &r->g, r->log, r->queue);
    if (!r->dec) {
        snprintf(r->error, sizeof(r->error), "decoder_open failed");
        goto done;
    }

    if (cfg->bench_decode) {
        uint32_t frames = 0;
        double elapsed = 0.0, cpu0 = process_cpu_s();
        bool ok = decoder_bench(r->dec, cfg->seconds, &frames, &elapsed);
        double cpu = process_cpu_s() - cpu0;
        struct decoder_info di;
        decoder_get_info(r->dec, &di);
        printf("{\"run_id\": \"%s\", \"bench_decode\": {\"ok\": %s, \"codec\": \"%s\", \"hw_fmt\": \"%s\", "
               "\"frames\": %u, \"elapsed_s\": %.3f, \"fps\": %.1f, \"x_realtime\": %.1f, "
               "\"process_cpu_s\": %.3f, \"cpu_pct_of_one_core\": %.1f, \"stream_fps\": %.4f}}\n",
               cfg->run_id, ok ? "true" : "false", di.codec, di.hw_fmt, frames, elapsed,
               frames / elapsed, di.fps > 0 ? frames / elapsed / di.fps : 0.0, cpu, 100.0 * cpu / elapsed, di.fps);
        fflush(stdout);
        rc = ok ? 0 : 1;
        goto cleanup;
    }

    if (!decoder_start(r->dec)) {
        snprintf(r->error, sizeof(r->error), "decoder thread failed to start");
        goto done;
    }

    if (cfg->bench_w > 0) {
        if (bench_render(r))
            rc = 0;
        goto done;
    }

    if (render_loop(r))
        rc = r->error[0] ? 1 : 0;

done:
    {
        PROCESS_MEMORY_COUNTERS pmc = { .cb = sizeof(pmc) };
        if (GetProcessMemoryInfo(GetCurrentProcess(), &pmc, sizeof(pmc))) {
            r->peak_ws_mb = (double) pmc.PeakWorkingSetSize / (1 << 20);
            r->peak_pagefile_mb = (double) pmc.PeakPagefileUsage / (1 << 20);
        }
        if (r->dec)
            decoder_stop(r->dec);
        if (cfg->out_prefix && cfg->bench_w == 0) {
            char path[1024];
            snprintf(path, sizeof(path), "%s.json", cfg->out_prefix);
            write_json(r, path);
            snprintf(path, sizeof(path), "%s.csv", cfg->out_prefix);
            write_csv(r, path);
        }
        if (r->error[0])
            fprintf(stderr, "perf0: ERROR: %s\n", r->error);
    }

cleanup:
    pl_queue_destroy(&r->queue);          // unmaps queued frames while the GPU still exists
    decoder_destroy(&r->dec);
    pl_renderer_destroy(&r->rr);
    if (r->dxgi)
        IDXGISwapChain_Release(r->dxgi);
    cache_save(r);
    gpu_destroy(&r->g);
    pl_log_destroy(&r->log);
    free(r->recs);
    timeEndPeriod(1);
    return rc;
}
