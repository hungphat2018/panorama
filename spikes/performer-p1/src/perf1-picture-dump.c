// Read-backs (--dump-canvas) and the per-pass cost log of the picture path.
#include "perf1-picture-internal.h"

#include <direct.h>
#include <errno.h>

static bool make_dirs(const char *dir)
{
    char path[512];
    snprintf(path, sizeof(path), "%s", dir);
    for (char *p = path + 1; *p; p++)
        if (*p == '/' || *p == '\\') {
            char c = *p;
            *p = 0;
            if (_mkdir(path) != 0 && errno != EEXIST)
                return false;
            *p = c;
        }
    return _mkdir(path) == 0 || errno == EEXIST;
}

// pass 1's canvas as float32 RGBA (premultiplied by coverage), with the metadata scripts/compare-canvases.py of the
// picture-path harness reads.
static bool write_canvas(const char *name, pl_tex canvas, const char *meta)
{
    int w = canvas->params.w, h = canvas->params.h;
    size_t texel = canvas->params.format->texel_size; // libplacebo moves half floats through 32-bit host buffers
    if (texel != 16) {
        fprintf(stderr, "perf1: unexpected canvas texel size %zu\n", texel);
        return false;
    }
    size_t bytes = (size_t) w * h * texel;
    void *buf = malloc(bytes);
    if (!buf || !pl_tex_download(A.gpu.gpu, pl_tex_transfer_params(.tex = canvas, .ptr = buf))) {
        fprintf(stderr, "perf1: reading the canvas of %s back failed\n", name);
        free(buf);
        return false;
    }
    char path[600];
    snprintf(path, sizeof(path), "%s/%s.f32", PC.dump_dir, name);
    FILE *f = fopen(path, "wb");
    bool ok = f && fwrite(buf, 1, bytes, f) == bytes;
    if (f)
        fclose(f);
    free(buf);
    snprintf(path, sizeof(path), "%s/%s.json", PC.dump_dir, name);
    FILE *j = ok ? fopen(path, "wb") : NULL;
    if (j) {
        fprintf(j, "{\"monitor\":\"%s\",\"width\":%d,\"height\":%d,\"format\":\"rgba32f\",\"stored_as\":\"%s\","
                   "\"producer\":\"perf1\",%s}\n", name, w, h, canvas->params.format->name, meta);
        fclose(j);
    }
    return ok && j;
}

// An 8-bit rendering of the finished frame (passes 2 to 4, the dither working to 8 bits) as a binary PPM.
static bool write_final(struct pic_mon *pm, const struct pic_refresh *r, const struct pic_shot *shot, const char *name)
{
    int w = pm->canvas->params.w, h = pm->canvas->params.h;
    pl_fmt fmt = pl_find_named_fmt(A.gpu.gpu, "rgba8");
    pl_tex tex = fmt ? pl_tex_create(A.gpu.gpu, pl_tex_params(.w = w, .h = h, .format = fmt, .sampleable = true,
                                                              .renderable = true, .host_readable = true, .blit_src = true,
                                                              .blit_dst = true, .debug_tag = PL_DEBUG_TAG))
                     : NULL;
    uint8_t *rgba = tex ? malloc((size_t) w * h * 4) : NULL;
    bool ok = rgba && pic_finish_into(pm, r, shot, tex, 8) &&
              pl_tex_download(A.gpu.gpu, pl_tex_transfer_params(.tex = tex, .ptr = rgba));
    if (ok) {
        char path[600];
        snprintf(path, sizeof(path), "%s/%s-final.ppm", PC.dump_dir, name);
        FILE *f = fopen(path, "wb");
        ok = f != NULL;
        if (f) {
            fprintf(f, "P6\n%d %d\n255\n", w, h);
            for (size_t i = 0; i < (size_t) w * h && ok; i++)
                ok = fwrite(rgba + 4 * i, 1, 3, f) == 3;
            fclose(f);
        }
    }
    if (!ok)
        fprintf(stderr, "perf1: writing the finished frame of %s failed\n", name);
    free(rgba);
    pl_tex_destroy(A.gpu.gpu, &tex);
    return ok;
}

// One view of the picture as JSON members for compare-dump.py: the frame's ordinal, the domain it was sampled in, the shot's
// crop and place (the place in the harness's frame: from the gaze axis, v up), and the slide.
static int view_json(char *buf, size_t n, long ord, bool sigmoid, const struct pic_view *v, const struct pic_slide *sl,
                     double u_axis, double v_eye)
{
    double ctr_u = 0.5 * (v->rect[0] + v->rect[2]) - u_axis, ctr_v = v_eye - 0.5 * (v->rect[1] + v->rect[3]);
    return snprintf(buf, n,
                    "{\"ord\":%ld,\"domain\":\"%s\",\"crop\":[%.6f,%.6f,%.6f,%.6f],\"height_mm\":%.6f,"
                    "\"centre_pp_mm\":[%.6f,%.6f],\"slide\":[%.9g,%.9g,%.9g,%.9g]}",
                    ord, sigmoid ? "sigmoid" : "linear", v->crop[0], v->crop[1], v->crop[2], v->crop[3], v->rect[3] - v->rect[1],
                    ctr_u, ctr_v, sl ? sl->du0 : 0.0, sl ? sl->dv0 : 0.0, sl ? sl->ddu : 0.0, sl ? sl->ddv : 0.0);
}

// What the canvas was made from, for compare-dump.py: the surface and, per frame, its view of the picture and its slide; the
// rolling blend's weights.
void picture_dump(struct pic_mon *pm, const struct pic_refresh *r, const struct pic_shot *shot, const struct pic_pass1_job *job,
                  const struct pic_timing *tm)
{
    const char *name = picture_monitor_name(r->m->slot);
    if (!make_dirs(PC.dump_dir)) {
        fprintf(stderr, "perf1: cannot create %s\n", PC.dump_dir);
        return;
    }
    const struct room_geom *g = picture_geom(r->m->slot);
    double u_axis = 0.0, v_eye = 0.0;
    bool have_origin = picture_surface_origin(&u_axis, &v_eye);
    const bool blend = job->b != NULL;
    const long ord_a = blend && tm->blend.partner < 0 ? r->ord - 1 : r->ord, ord_b = r->ord + (tm->blend.partner > 0);
    char va[500], vb[500] = "null", meta[1800];
    view_json(va, sizeof(va), ord_a, job->sigmoid, job->view, job->slide, u_axis, v_eye);
    if (blend)
        view_json(vb, sizeof(vb), ord_b, job->sigmoid_b, job->view_b, job->slide_b, u_axis, v_eye);
    snprintf(meta, sizeof(meta),
             "\"pts\":%.6f,\"ord\":%ld,\"timed\":%s,\"surface\":\"%s\",\"curvature\":%.12g,\"has_room\":%s,"
             "\"scan_ms\":%.6f,\"a\":%s,\"b\":%s,\"blend\":{\"partner\":%d,\"wa\":%.9g,\"wb\":%.9g}",
             r->pts, r->ord, r->timed ? "true" : "false", g->curve ? "curve" : "fold", g->curvature, have_origin ? "true" : "false",
             r->m->row.timing.scan_s * 1e3, va, vb, blend ? tm->blend.partner : 0, job->wa, job->wb);
    if (write_canvas(name, pm->canvas, meta) && write_final(pm, r, shot, name))
        printf("%s: pass 1 canvas and the finished frame at %.3f s written to %s\n", r->m->name, r->pts, PC.dump_dir);
}

// ---- the cost log ------------------------------------------------------------------------------------------------

static const char *const k_cost_names[COST_COUNT] = { "prepare", "resample", "surround", "finish", "subtitles" };

static void write_cost_file(const struct pic_mon *pm, int i)
{
    char path[1024];
    snprintf(path, sizeof(path), "%s/monitor-%d-passes.json", A.cfg.out_dir, A.mon[i].index);
    FILE *f = fopen(path, "w");
    if (!f) {
        fprintf(stderr, "perf1: cannot write %s\n", path);
        return;
    }
    fprintf(f, "{\"monitor\":%d,\"name\":\"%s\",\"drawn\":%u,\"slide_refreshes\":%u,\"blend_refreshes\":%u,\"blend_missed\":%u,"
               "\"subtitle_draws\":%u,\"subtitle_overflow\":%u,\"kernel\":\"%s\",\"blend\":\"%s\",\"pass_gpu_ms\":{",
            A.mon[i].index, A.mon[i].name, pm->n_drawn, pm->n_slide, pm->n_blend, pm->n_blend_missed, pm->n_sub_draws, pm->n_sub_overflow,
            PC.kernel, PC.blend_rolling ? "rolling" : "off");
    for (int c = 0; c < COST_COUNT; c++) {
        fprintf(f, "%s\"%s\":[", c ? "," : "", k_cost_names[c]);
        for (size_t k = 0; k < pm->cost[c].n; k++)
            fprintf(f, "%s%.4f", k ? "," : "", pm->cost[c].v[k]);
        fputc(']', f);
    }
    fputs("}}\n", f);
    fclose(f);
}

void picture_write_logs(void)
{
    for (int i = 0; i < A.nmon; i++) {
        struct pic_mon *pm = &g_pm[i];
        if (!pm->n_drawn)
            continue;
        if (!A.cfg.headless_hz) // a headless run draws nothing: its counts go to the console only
            write_cost_file(pm, i);
        printf("%s picture path, GPU ms p50 / p99:", A.mon[i].name);
        for (int c = 0; c < COST_COUNT; c++) {
            struct summary s;
            summarize(pm->cost[c].v, pm->cost[c].n, &s);
            printf(" %s %.3f / %.3f;", k_cost_names[c], s.p50, s.p99);
        }
        printf(" slide %u, blended %u (partner gone %u), magnified %u of %u refreshes\n", pm->n_slide, pm->n_blend, pm->n_blend_missed, pm->n_upscaling, pm->n_drawn);
    }
}
