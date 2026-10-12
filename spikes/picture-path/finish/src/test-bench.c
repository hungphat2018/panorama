// Cost of passes 2 to 4 per refresh: GPU time from libplacebo's GPU timers (timestamp queries around each dispatch),
// p50 and p99 over many refreshes after a warm-up, at several canvas sizes. Run it only while holding the GPU-timing
// lock folder (the caller's job): other programs on the same GPU change the numbers.
//
//   finish-test bench [--backend vulkan|d3d11] [--gpu SUBSTR] [--seconds N] [--only paced]
#include <windows.h>

#include "test-canvas.h"
#include "test-main.h"

#define WARMUP 500
#define SAMPLES 1000
#define REFRESH_MS (1000.0 / 240.0)

struct size_case {
    int w, h;
    const char *name;
};

struct stats {
    double p50, p99, mean, max;
    int n;
};

static struct stats summarize(double *v, int n)
{
    struct stats s = { 0 };
    if (n <= 0)
        return s;
    tu_sort(v, n);
    s.n = n;
    s.p50 = tu_percentile(v, n, 50);
    s.p99 = tu_percentile(v, n, 99);
    s.max = v[n - 1];
    double sum = 0;
    for (int i = 0; i < n; i++)
        sum += v[i];
    s.mean = sum / n;
    return s;
}

// What one benchmark loop runs. `timer` collects the GPU time of every dispatch the call makes.
typedef bool (*bench_fn)(void *ctx, int iter);

struct bench_ctx {
    struct fp_env *env;
    pl_tex canvas, filled, out8;
    struct fp_surround *surround;
    struct fp_finish *finish;
    struct fp_subs *subs;
    struct fp_surround_look look;
    struct fp_surround_time time;
    struct fp_color_gpu color;
};

static bool run_update(void *p, int iter)
{
    struct bench_ctx *c = p;
    struct fp_surround_frame fr = { .dt_s = 1.0 / 240, .reset = iter == 0 };
    return fp_surround_update(c->surround, c->canvas, &c->look, &c->time, &fr);
}

static bool run_fill_standalone(void *p, int iter)
{
    struct bench_ctx *c = p;
    pl_shader sh = pl_dispatch_begin(c->env->dp);
    if (!fp_append_canvas_fetch(sh, c->canvas) || !fp_surround_append_fill(c->surround, sh, &c->look) ||
        !pl_dispatch_finish(c->env->dp, pl_dispatch_params(.shader = &sh, .target = c->filled, .timer = c->env->timer))) {
        pl_dispatch_abort(c->env->dp, &sh);
        return false;
    }
    (void) iter;
    return true;
}

static bool run_finish(void *p, int iter)
{
    struct bench_ctx *c = p;
    (void) iter;
    return fp_finish_run(c->finish, c->filled, c->out8, &c->color, FP_DITHER_BLUE_NOISE);
}

static bool run_fused(void *p, int iter)
{
    struct bench_ctx *c = p;
    (void) iter;
    return fp_finish_run_fused(c->finish, c->surround, c->canvas, c->out8, &c->look, &c->color, FP_DITHER_BLUE_NOISE);
}

static bool run_subs_cached(void *p, int iter)
{
    struct bench_ctx *c = p;
    (void) iter;
    return fp_subs_draw(c->subs, c->out8, 2.0, NULL);
}

static bool run_subs_changing(void *p, int iter)
{
    struct bench_ctx *c = p;
    return fp_subs_draw(c->subs, c->out8, iter % 2 ? 2.0 : 5.0, NULL); // two different lines: repack and upload each time
}

// Runs one loop; returns GPU-time stats (ms, summed over every dispatch of the call) and wall-clock stats of the call
// including the wait for the GPU. With `paced`, each iteration starts on a 240 Hz tick, like a render thread would.
static bool bench_loop(struct bench_ctx *c, bench_fn fn, bool paced, struct stats *gpu, struct stats *wall)
{
    pl_timer timer = pl_timer_create(c->env->gpu);
    if (!timer) {
        fprintf(stderr, "finish-test: this device has no GPU timers\n");
        return false;
    }
    c->env->timer = timer;
    double *g = malloc(sizeof(double) * SAMPLES), *w = malloc(sizeof(double) * SAMPLES);
    int ng = 0, nw = 0;
    double next = tu_now_ms();
    bool ok = true;
    for (int i = 0; i < WARMUP + SAMPLES && ok; i++) {
        if (paced) {
            next += REFRESH_MS;
            double now;
            while ((now = tu_now_ms()) < next) {
                if (next - now > 2.0)
                    Sleep(1);
            }
        }
        double t0 = tu_now_ms();
        pl_dispatch_reset_frame(c->env->dp);
        ok = fn(c, i);
        pl_gpu_finish(c->env->gpu);
        double t1 = tu_now_ms();
        uint64_t ns, sum = 0;
        int got = 0;
        while ((ns = pl_timer_query(c->env->gpu, timer)) != 0) {
            sum += ns;
            got++;
        }
        if (i >= WARMUP && got) {
            g[ng++] = sum / 1.0e6;
            w[nw++] = t1 - t0;
        }
    }
    c->env->timer = NULL;
    *gpu = summarize(g, ng);
    *wall = summarize(w, nw);
    free(g);
    free(w);
    pl_timer_destroy(c->env->gpu, &timer);
    return ok;
}

static void report(FILE *csv, const struct fp_device *dev, const char *size, const char *what, bool paced, const struct stats *g,
                   const struct stats *w)
{
    printf("  %-34s %s  GPU p50 %6.3f ms  p99 %6.3f ms  max %6.3f   (call incl. GPU wait: p50 %6.3f p99 %6.3f)  n=%d\n", what,
           paced ? "paced " : "tight ", g->p50, g->p99, g->max, w->p50, w->p99, g->n);
    if (csv)
        fprintf(csv, "%s,%s,%s,%s,%s,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%d\n", dev->name, fp_backend_name(dev->backend), size, what,
                paced ? "paced" : "tight", g->p50, g->p99, g->mean, g->max, w->p50, w->p99, g->n);
}

int test_bench(struct tctx *t)
{
    printf("== bench (GPU time per refresh)\n");
    struct fp_env *env = &t->dev.env;
    char path[512];
    snprintf(path, sizeof(path), "%s/bench-%s-%s.csv", t->out, fp_backend_name(t->dev.backend), t->dev.integrated ? "igpu" : "dgpu");
    FILE *csv = fopen(path, "w");
    if (csv)
        fprintf(csv, "gpu,backend,size,pass,load,gpu_p50_ms,gpu_p99_ms,gpu_mean_ms,gpu_max_ms,call_p50_ms,call_p99_ms,n\n");

    static const struct size_case sizes[] = { { 1920, 1080, "1920x1080" }, { 2560, 1600, "2560x1600" }, { 3840, 2160, "3840x2160" } };
    size_t font_size = 0;
    char *font = NULL;
    FILE *ff = fopen(t->font_path, "rb");
    if (ff) {
        fseek(ff, 0, SEEK_END);
        font_size = (size_t) ftell(ff);
        fseek(ff, 0, SEEK_SET);
        font = malloc(font_size + 1);
        if (fread(font, 1, font_size, ff) != font_size) {
            free(font);
            font = NULL;
        }
        fclose(ff);
    }
    bool paced_only = t->only && !strcmp(t->only, "paced");
    for (size_t si = 0; si < TU_ARRAY_SIZE(sizes); si++) {
        const int W = sizes[si].w, H = sizes[si].h;
        printf("-- %s on %s (%s)\n", sizes[si].name, t->dev.name, fp_backend_name(t->dev.backend));
        struct bench_ctx c = { .env = env };
        c.canvas = tu_canvas(env, W, H, "bench canvas");
        c.filled = tu_canvas(env, W, H, "bench filled");
        c.out8 = tu_target8(env, W, H, false, "bench out");
        c.surround = fp_surround_create(env, W, H, t->cell_px);
        c.finish = fp_finish_create(env);
        c.look = (struct fp_surround_look) { .mode = FP_SURROUND_GLOW, .gain = 0.8f, .falloff_px = 250.0f, .saturation = 1.0f, .max_level = 1.0f };
        c.time = (struct fp_surround_time) { .tau_steady_s = 0.5f, .cut_rule = FP_CUT_SNAP };
        struct fp_color_monitor mon = { .transfer = FP_TRC_SRGB, .gamut_owner = FP_GAMUT_PLAYER,
            .panel = { .r = { 0.679, 0.316 }, .g = { 0.258, 0.693 }, .b = { 0.147, 0.052 }, .w = { 0.3127, 0.3290 } },
            .gain = { 0.9f, 0.9f, 0.9f }, .gamma = { 1.05f, 1.05f, 1.05f } };
        fp_color_make(&mon, &c.color);
        if (!c.canvas || !c.filled || !c.out8 || !c.surround || !c.finish) {
            tu_check(false, "bench objects for %s", sizes[si].name);
            continue;
        }
        // letterboxed picture: full width, 2.39:1 (a quarter to a third of the canvas is leftover)
        float lh = (float) W / 2.39f, top = ((float) H - lh) / 2;
        struct tc_shot letterbox = { .rect = { 0, top, (float) W, top + lh }, .left = { 0.6f, 0.3f, 0.2f }, .right = { 0.2f, 0.3f, 0.6f }, .shade = 0.3f, .texture = 0.1f, .grain = 0.02f };
        // small picture (worst case for the fill: most of the canvas is leftover)
        struct tc_shot small = { .rect = { W * 0.3f, H * 0.3f, W * 0.7f, H * 0.7f }, .left = { 0.6f, 0.3f, 0.2f }, .right = { 0.2f, 0.3f, 0.6f }, .shade = 0.3f, .texture = 0.1f };
        pl_dispatch_reset_frame(env->dp);
        tc_gen_shot(env, c.canvas, &letterbox, 0);

        struct { const char *what; bench_fn fn; bool needs_filled_prep; } cases[] = {
            { "pass 2 update (analysis+state+blurs)", run_update, false },
            { "pass 2 fill (stand-alone)", run_fill_standalone, false },
            { "pass 3 finish (stand-alone)", run_finish, true },
            { "pass 2 fill + pass 3 fused", run_fused, false },
        };
        struct stats g, w;
        for (int paced = 0; paced < 2; paced++) {
            if ((paced_only && !paced) || (paced && sizes[si].w != 2560))
                continue;
            for (size_t k = 0; k < TU_ARRAY_SIZE(cases); k++) {
                if (cases[k].needs_filled_prep) {
                    pl_dispatch_reset_frame(env->dp);
                    struct fp_surround_frame fr = { .dt_s = 1.0 / 240, .reset = true };
                    fp_surround_apply(c.surround, c.canvas, c.filled, &c.look, &c.time, &fr);
                }
                if (bench_loop(&c, cases[k].fn, paced, &g, &w))
                    report(csv, &t->dev, sizes[si].name, cases[k].what, paced, &g, &w);
            }
            // the picture-size worst case for the fused pass
            pl_dispatch_reset_frame(env->dp);
            tc_gen_shot(env, c.canvas, &small, 0);
            struct fp_surround_frame fr = { .dt_s = 1.0 / 240, .reset = true };
            fp_surround_update(c.surround, c.canvas, &c.look, &c.time, &fr);
            if (bench_loop(&c, run_fused, paced, &g, &w))
                report(csv, &t->dev, sizes[si].name, "pass 2 fill + pass 3 fused, small picture", paced, &g, &w);
            if (bench_loop(&c, run_update, paced, &g, &w))
                report(csv, &t->dev, sizes[si].name, "pass 2 update, small picture", paced, &g, &w);
            pl_dispatch_reset_frame(env->dp);
            tc_gen_shot(env, c.canvas, &letterbox, 0);

            if (font) {
                char events[512];
                snprintf(events, sizeof(events),
                         "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\an2}A line of dialogue that is on screen for two seconds.\n"
                         "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,{\\an2}Another line, so the picture changes between calls.\n");
                char script[2048];
                snprintf(script, sizeof(script),
                         "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 0\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, "
                         "PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, "
                         "Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,DejaVu Sans,%d,"
                         "&H00FFFFFF,&H000000FF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,20,20,%d,1\n\n[Events]\nFormat: Layer, Start, "
                         "End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n%s",
                         W, H, H / 22, H / 14, events);
                c.subs = fp_subs_create(env, W, H);
                if (c.subs) {
                    fp_subs_add_font(c.subs, "dejavu", font, font_size);
                    fp_subs_load_ass(c.subs, script, strlen(script));
                    if (bench_loop(&c, run_subs_cached, paced, &g, &w))
                        report(csv, &t->dev, sizes[si].name, "pass 4 subtitles, line unchanged", paced, &g, &w);
                    if (bench_loop(&c, run_subs_changing, paced, &g, &w))
                        report(csv, &t->dev, sizes[si].name, "pass 4 subtitles, new line every call", paced, &g, &w);
                    fp_subs_destroy(&c.subs);
                }
            }
        }
        fp_surround_destroy(&c.surround);
        fp_finish_destroy(&c.finish);
        pl_tex_destroy(env->gpu, &c.canvas);
        pl_tex_destroy(env->gpu, &c.filled);
        pl_tex_destroy(env->gpu, &c.out8);
    }
    if (csv) {
        fclose(csv);
        printf("wrote %s\n", path);
    }
    free(font);
    return 0;
}
