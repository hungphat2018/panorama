// picture-path.exe: the offscreen harness for the per-monitor picture pass (pass 1 of the picture path).
//   render       one scenario under one structure: canvases (RGBA16F, linear light) and the pass list
//   bench        the same, looped, with GPU pass timings (run only while holding the GPU timing lock)
//   dump-kernel  the kernel table the S2 shader uses, for the test against the numpy kernel
// Offscreen only: no window, no swapchain, no display setting is touched.
#include <stdio.h>
#include <string.h>
#include <windows.h>

#include "pp-gpu.h"
#include "pp-output.h"
#include "pp-run.h"
#include "pp-s2.h"
#include "pp-timing.h"
#include "pp-util.h"

struct args {
    const char *cmd;
    const char *scenario, *out, *json, *label;
    const char *backend, *adapter;
    struct pp_run_cfg cfg;
    int iters, warmup;
    double warmup_ms;
    bool vary;
    enum pp_phase phase;
    double pace_hz;
    int verbose;
};

static void usage(void)
{
    fprintf(stderr,
        "usage: picture-path.exe render|bench --scenario FILE [--structure s1|s1-strips|s1-blend|s1-margin|s1-hook|s1-twopass|s2] [--strips N]\n"
        "           [--backend d3d11|vulkan] [--adapter nvidia|amd] [--src-fmt rgb8|yuv420p8|yuv420p10] [--kernel NAME]\n"
        "           [--chroma NAME] [--shader-dir DIR] [--uniform-slide] [--debug-map] [--out DIR] [-v]\n"
        "       bench also: [--iters N] [--warmup N] [--warmup-ms MS] [--mode static|refresh] [--phase refresh|prepare] [--pace-hz H]\n"
        "           [--json FILE] [--label TEXT]\n"
        "       picture-path.exe dump-kernel --kernel NAME\n");
}

static bool parse(int argc, char **argv, struct args *a)
{
    memset(a, 0, sizeof(*a));
    a->backend = "d3d11";
    a->adapter = "nvidia";
    a->out = "out/run";
    a->iters = 300;
    a->warmup = 100;
    a->warmup_ms = 1000.0;
    a->vary = true;
    snprintf(a->cfg.shader_dir, sizeof(a->cfg.shader_dir), "src/shaders");
    a->cfg.st = PP_ST_S2;
    a->cfg.strips = 16;
    if (argc < 2)
        return false;
    a->cmd = argv[1];
    for (int i = 2; i < argc; i++) {
        const char *k = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
        #define NEED() do { if (!v) { fprintf(stderr, "picture-path: %s needs a value\n", k); return false; } i++; } while (0)
        if (!strcmp(k, "--scenario")) { NEED(); a->scenario = v; }
        else if (!strcmp(k, "--out")) { NEED(); a->out = v; }
        else if (!strcmp(k, "--json")) { NEED(); a->json = v; }
        else if (!strcmp(k, "--label")) { NEED(); a->label = v; }
        else if (!strcmp(k, "--backend")) { NEED(); a->backend = v; }
        else if (!strcmp(k, "--adapter")) { NEED(); a->adapter = v; }
        else if (!strcmp(k, "--structure")) {
            NEED();
            if (!pp_structure_from_name(v, &a->cfg.st)) { fprintf(stderr, "picture-path: unknown structure %s\n", v); return false; }
        } else if (!strcmp(k, "--strips")) { NEED(); a->cfg.strips = atoi(v); }
        else if (!strcmp(k, "--src-fmt")) {
            NEED();
            if (!pp_src_fmt_from_name(v, &a->cfg.fmt)) { fprintf(stderr, "picture-path: unknown source format %s\n", v); return false; }
        } else if (!strcmp(k, "--kernel")) { NEED(); snprintf(a->cfg.kernel, sizeof(a->cfg.kernel), "%s", v); }
        else if (!strcmp(k, "--kernel2")) { NEED(); snprintf(a->cfg.kernel2, sizeof(a->cfg.kernel2), "%s", v); }
        else if (!strcmp(k, "--chroma")) { NEED(); snprintf(a->cfg.chroma, sizeof(a->cfg.chroma), "%s", v); }
        else if (!strcmp(k, "--shader-dir")) { NEED(); snprintf(a->cfg.shader_dir, sizeof(a->cfg.shader_dir), "%s", v); }
        else if (!strcmp(k, "--uniform-slide")) a->cfg.uniform_slide = true;
        else if (!strcmp(k, "--debug-map")) a->cfg.debug_map = true;
        else if (!strcmp(k, "--iters")) { NEED(); a->iters = atoi(v); }
        else if (!strcmp(k, "--warmup")) { NEED(); a->warmup = atoi(v); }
        else if (!strcmp(k, "--warmup-ms")) { NEED(); a->warmup_ms = atof(v); }
        else if (!strcmp(k, "--pace-hz")) { NEED(); a->pace_hz = atof(v); }
        else if (!strcmp(k, "--mode")) { NEED(); a->vary = strcmp(v, "static") != 0; }
        else if (!strcmp(k, "--phase")) { NEED(); a->phase = !strcmp(v, "prepare") ? PP_PHASE_PREPARE : PP_PHASE_REFRESH; }
        else if (!strcmp(k, "-v")) a->verbose = 1;
        else { fprintf(stderr, "picture-path: unknown option %s\n", k); return false; }
        #undef NEED
    }
    return true;
}

static const char *kind_name(enum pp_pass_kind k)
{
    return k == PP_PASS_RESAMPLE ? "resample" : (k == PP_PASS_CHROMA ? "chroma" : "other");
}

// The passes of monitor `mon` (mon < 0: all of `t`) as a JSON array.
static void write_pass_list(FILE *f, const struct pp_timing *t, int mon)
{
    fputc('[', f);
    bool first = true;
    for (int i = 0; i < t->npass; i++) {
        const struct pp_pass_rec *p = &t->pass[i];
        if (mon >= 0 && p->mon != mon)
            continue;
        fprintf(f, "%s{\"desc\":", first ? "" : ",");
        first = false;
        pp_json_write_string(f, p->desc);
        fprintf(f, ",\"ms\":%.4f,\"kind\":\"%s\"}", p->ms, kind_name(p->kind));
    }
    fputc(']', f);
}

static int count_range(const struct pp_run *r, int mon, enum pp_pass_kind kind)
{
    return pp_timing_mon_count(&r->tm, mon, kind);
}

static void write_stats(FILE *f, const struct pp_series *s)
{
    double p50, p99, mn, mx, mean;
    pp_series_stats(s, &p50, &p99, &mn, &mx, &mean);
    fprintf(f, "{\"p50\":%.4f,\"p99\":%.4f,\"min\":%.4f,\"max\":%.4f,\"mean\":%.4f,\"n\":%d}", p50, p99, mn, mx, mean, s->n);
}

// ---- render -----------------------------------------------------------------------------------------

static int cmd_render(const struct args *a, const struct pp_gpu *g)
{
    struct pp_run r;
    if (!pp_run_init(&r, g, a->scenario, &a->cfg))
        return 1;
    int rc = 1;
    // Three rounds each: shader compilation first, then the timings of finished passes show up in the lists.
    struct pp_timing *prep_list = pp_xcalloc(1, sizeof(*prep_list)); // the once-per-frame passes (S2), kept apart from the refresh
    for (int i = 0; i < 3; i++) {
        if (!pp_run_prepare(&r))
            goto done;
        pl_gpu_finish(g->gpu);
        pp_run_collect(&r);
    }
    memcpy(prep_list, &r.tm, sizeof(*prep_list));
    r.flush_cache = true; // every refresh runs every pass, so the pass list below is the whole pipeline
    for (int i = 0; i < 3; i++) {
        if (!pp_run_refresh(&r, false))
            goto done;
        pl_gpu_finish(g->gpu);
        pp_run_collect(&r);
    }
    if (!pp_mkdirs(a->out))
        goto done;
    char path[600];
    snprintf(path, sizeof(path), "%s/run.json", a->out);
    FILE *f = fopen(path, "wb");
    if (!f) {
        fprintf(stderr, "picture-path: cannot write %s\n", path);
        goto done;
    }
    fprintf(f, "{\"scenario\":");
    pp_json_write_string(f, a->scenario);
    fprintf(f, ",\"structure\":\"%s\",\"backend\":\"%s\",\"adapter\":", pp_structure_name(a->cfg.st), pp_backend_name(g->backend));
    pp_json_write_string(f, g->adapter_name);
    fprintf(f, ",\"src_fmt\":\"%s\",\"kernel\":", pp_src_fmt_name(a->cfg.fmt));
    pp_json_write_string(f, r.scn.kernel);
    fprintf(f, ",\"monitors\":[");
    for (int k = 0; k < r.scn.room.nmon; k++) {
        const struct pp_monitor *m = &r.scn.room.mon[k];
        fprintf(f, "%s{\"id\":\"%s\",\"width\":%d,\"height\":%d,\"domain\":\"%s\",\"resample_passes\":%d,\"chroma_passes\":%d,\"passes\":",
                k ? "," : "", m->id, m->w, m->h, r.sigmoid[k] ? "sigmoid" : "linear", count_range(&r, k, PP_PASS_RESAMPLE),
                count_range(&r, k, PP_PASS_CHROMA));
        write_pass_list(f, &r.tm, k);
        fputc('}', f);
    }
    fprintf(f, "],\"prepare_passes\":");
    write_pass_list(f, prep_list, -1);
    fprintf(f, "}\n");
    fclose(f);
    char scn_path[300];
    snprintf(scn_path, sizeof(scn_path), "%s", a->scenario);
    for (char *c = scn_path; *c; c++)
        if (*c == '\\' || *c == '"')
            *c = '/'; // the canvas header is JSON text: keep the path free of escapes
    for (int k = 0; k < r.scn.room.nmon; k++) {
        char meta[512];
        snprintf(meta, sizeof(meta), "\"structure\":\"%s\",\"backend\":\"%s\",\"kernel\":\"%s\",\"domain\":\"%s\",\"scenario\":\"%s\"",
                 pp_structure_name(a->cfg.st), pp_backend_name(g->backend), r.scn.kernel, r.sigmoid[k] ? "sigmoid" : "linear",
                 scn_path);
        if (!pp_output_write_canvas(a->out, r.scn.room.mon[k].id, g, r.canvas[k], meta))
            goto done;
    }
    printf("rendered %s with %s on %s / %s into %s\n", a->scenario, pp_structure_name(a->cfg.st), pp_backend_name(g->backend),
           g->adapter_name, a->out);
    for (int k = 0; k < r.scn.room.nmon; k++)
        printf("  %s: %d resample pass(es), %d chroma pass(es), domain %s\n", r.scn.room.mon[k].id,
               count_range(&r, k, PP_PASS_RESAMPLE), count_range(&r, k, PP_PASS_CHROMA), r.sigmoid[k] ? "sigmoid" : "linear");
    rc = 0;
done:
    free(prep_list);
    pp_run_destroy(&r);
    return rc;
}

// ---- bench ------------------------------------------------------------------------------------------

struct pass_series {
    char desc[112];
    int mon;
    enum pp_pass_kind kind;
    struct pp_series s;
};

static void spin_until(double t_ms)
{
    while (pp_now_ms() < t_ms)
        Sleep(0);
}

static int cmd_bench(const struct args *a, const struct pp_gpu *g)
{
    struct pp_run r;
    if (!pp_run_init(&r, g, a->scenario, &a->cfg))
        return 1;
    int rc = 1;
    int nmon = r.scn.room.nmon;
    struct pp_series gpu_total = {0}, cpu = {0}, wall = {0}, gpu_mon[PP_MAX_MON] = {{0}};
    struct pass_series ps[PP_MAX_PASSES];
    memset(ps, 0, sizeof(ps));
    int nps = 0;
    bool unstable = false;
    const bool prep = a->phase == PP_PHASE_PREPARE;
    if (prep && a->cfg.st != PP_ST_S2) {
        fprintf(stderr, "picture-path: --phase prepare is for the S2 structure\n");
        goto done;
    }
    if (!pp_run_prepare(&r))
        goto done;
    // Warm-up: at least `warmup` iterations and `warmup_ms` of running, so the GPU has left its idle clocks, the shaders are
    // compiled and the caches are filled before the first measured iteration.
    double period = a->pace_hz > 0 ? 1000.0 / a->pace_hz : 0.0, start = pp_now_ms();
    int measured = 0;
    for (int i = 0; measured < a->iters; i++) {
        if (period > 0)
            spin_until(start + i * period);
        double t0 = pp_now_ms();
        bool ok = prep ? pp_run_prepare(&r) : pp_run_refresh(&r, a->vary);
        double t1 = pp_now_ms();
        pl_gpu_finish(g->gpu);
        double t2 = pp_now_ms();
        if (!ok)
            goto done;
        pp_run_collect(&r); // our own passes: this iteration's GPU times; libplacebo's passes report the previous iteration's
        if (i < a->warmup || pp_now_ms() - start < a->warmup_ms)
            continue;
        measured++;
        pp_series_add(&cpu, t1 - t0);
        pp_series_add(&wall, t2 - t0);
        pp_series_add(&gpu_total, pp_timing_total_ms(&r.tm));
        if (!prep) {
            for (int k = 0; k < nmon; k++)
                pp_series_add(&gpu_mon[k], pp_timing_mon_ms(&r.tm, k));
        }
        int np = r.tm.npass;
        if (nps == 0)
            nps = np;
        if (np != nps) {
            unstable = true;
            continue;
        }
        for (int p = 0; p < np; p++) {
            if (!ps[p].desc[0]) {
                snprintf(ps[p].desc, sizeof(ps[p].desc), "%s", r.tm.pass[p].desc);
                ps[p].kind = r.tm.pass[p].kind;
                ps[p].mon = r.tm.pass[p].mon;
            }
            pp_series_add(&ps[p].s, r.tm.pass[p].ms);
        }
    }
    FILE *f = a->json ? fopen(a->json, "wb") : stdout;
    if (!f) {
        fprintf(stderr, "picture-path: cannot write %s\n", a->json);
        goto done;
    }
    fprintf(f, "{\"label\":");
    pp_json_write_string(f, a->label ? a->label : "");
    fprintf(f, ",\"scenario\":");
    pp_json_write_string(f, a->scenario);
    fprintf(f, ",\"backend\":\"%s\",\"adapter\":", pp_backend_name(g->backend));
    pp_json_write_string(f, g->adapter_name);
    fprintf(f, ",\"structure\":\"%s\",\"phase\":\"%s\",\"mode\":\"%s\",\"src_fmt\":\"%s\",\"kernel\":", pp_structure_name(a->cfg.st),
            prep ? "prepare" : "refresh", a->vary ? "per-refresh" : "static", pp_src_fmt_name(a->cfg.fmt));
    pp_json_write_string(f, r.scn.kernel);
    fprintf(f, ",\"iters\":%d,\"warmup\":%d,\"pace_hz\":%.1f,\"unstable_pass_list\":%s,\"gpu_ms\":", a->iters, a->warmup, a->pace_hz,
            unstable ? "true" : "false");
    write_stats(f, &gpu_total);
    fprintf(f, ",\"cpu_ms\":");
    write_stats(f, &cpu);
    fprintf(f, ",\"wall_ms\":");
    write_stats(f, &wall);
    fprintf(f, ",\"monitors\":[");
    for (int k = 0; k < nmon && !prep; k++) {
        const struct pp_monitor *m = &r.scn.room.mon[k];
        fprintf(f, "%s{\"id\":\"%s\",\"width\":%d,\"height\":%d,\"gpu_ms\":", k ? "," : "", m->id, m->w, m->h);
        write_stats(f, &gpu_mon[k]);
        fprintf(f, ",\"resample_passes\":%d}", count_range(&r, k, PP_PASS_RESAMPLE));
    }
    fprintf(f, "],\"passes\":[");
    for (int p = 0; p < nps; p++) {
        fprintf(f, "%s{\"mon\":%d,\"kind\":\"%s\",\"desc\":", p ? "," : "", ps[p].mon, kind_name(ps[p].kind));
        pp_json_write_string(f, ps[p].desc);
        fprintf(f, ",\"ms\":");
        write_stats(f, &ps[p].s);
        fputc('}', f);
    }
    fprintf(f, "]}\n");
    if (a->json)
        fclose(f);
    double g50, g99, c50, w50, mn, mx, me, unused;
    pp_series_stats(&gpu_total, &g50, &g99, &mn, &mx, &me);
    pp_series_stats(&cpu, &c50, &unused, &mn, &mx, &me);
    pp_series_stats(&wall, &w50, &unused, &mn, &mx, &me);
    printf("bench %s %s/%s %s %s: gpu p50 %.3f p99 %.3f ms, cpu p50 %.3f ms, wall p50 %.3f ms\n", a->label ? a->label : "",
           pp_backend_name(g->backend), g->adapter_name, pp_structure_name(a->cfg.st), a->vary ? "per-refresh" : "static",
           g50, g99, c50, w50);
    rc = 0;
done:
    pp_series_free(&gpu_total);
    pp_series_free(&cpu);
    pp_series_free(&wall);
    for (int k = 0; k < PP_MAX_MON; k++)
        pp_series_free(&gpu_mon[k]);
    for (int p = 0; p < PP_MAX_PASSES; p++)
        pp_series_free(&ps[p].s);
    pp_run_destroy(&r);
    return rc;
}

// ---- dump-kernel ------------------------------------------------------------------------------------

static int cmd_dump_kernel(const struct args *a)
{
    const char *name = a->cfg.kernel[0] ? a->cfg.kernel : "ewa_lanczossharp";
    double support;
    if (!pp_kernel_weight(name, 0.0, NULL, &support)) {
        fprintf(stderr, "picture-path: unknown kernel %s\n", name);
        return 1;
    }
    printf("{\"kernel\":\"%s\",\"support\":%.12g,\"t\":[", name, support);
    for (int i = 0; i <= 64; i++)
        printf("%s%.12g", i ? "," : "", support * i / 64.0);
    printf("],\"w\":[");
    for (int i = 0; i <= 64; i++) {
        double w;
        pp_kernel_weight(name, support * i / 64.0, &w, &support);
        printf("%s%.12g", i ? "," : "", w);
    }
    printf("]}\n");
    return 0;
}

int main(int argc, char **argv)
{
    struct args a;
    if (!parse(argc, argv, &a)) {
        usage();
        return 2;
    }
    if (!strcmp(a.cmd, "dump-kernel"))
        return cmd_dump_kernel(&a);
    if (strcmp(a.cmd, "render") && strcmp(a.cmd, "bench")) {
        usage();
        return 2;
    }
    if (!a.scenario) {
        usage();
        return 2;
    }
    enum pp_backend backend;
    if (!pp_backend_from_name(a.backend, &backend)) {
        fprintf(stderr, "picture-path: backend must be d3d11 or vulkan\n");
        return 2;
    }
    struct pp_gpu g;
    if (!pp_gpu_create(&g, backend, a.adapter, a.verbose ? PL_LOG_INFO : PL_LOG_WARN))
        return 1;
    pp_gpu_describe(&g, stdout);
    int rc = !strcmp(a.cmd, "render") ? cmd_render(&a, &g) : cmd_bench(&a, &g);
    pp_gpu_destroy(&g);
    return rc;
}
