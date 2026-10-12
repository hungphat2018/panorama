// Logs of a run. Per monitor (monitor-N-<mode>.json): the same fields as the per-monitor presenter spike, read by
// analyze-perf1.py and analyze-per-monitor-pacing.py. And the cue log (perf1-cuelog.json): what every monitor drew on
// every refresh and why, read by analyze-cues.py.
#include "perf1-app.h"

#include <math.h>

static void j_array(FILE *f, const char *name, const struct samples *s)
{
    fprintf(f, ",\"%s\":[", name);
    for (size_t i = 0; i < s->n; i++)
        fprintf(f, "%s%.4f", i ? "," : "", s->v[i]);
    fputc(']', f);
}

static void write_monitor_log(struct monitor *m, const struct decoder_info *di)
{
    const char *mode = A.cfg.sched == SCHED_NEAREST ? "nearest" : "own";
    char path[1024];
    snprintf(path, sizeof(path), "%s/monitor-%d-%s.json", A.cfg.out_dir, m->index, mode);
    FILE *f = fopen(path, "w");
    if (!f) {
        fprintf(stderr, "perf1: cannot write %s\n", path);
        return;
    }
    size_t nv = 0;
    double *vb = vclock_copy_log(&m->clock, &nv);

    fprintf(f, "{\"monitor\":%d,\"name\":\"%s\",\"rect\":[%ld,%ld,%ld,%ld],\"width_mm\":%.1f,\"left_mm\":%.1f,",
            m->index, m->name, m->rect.left, m->rect.top, m->rect.right, m->rect.bottom, m->w_mm, m->left_mm);
    fprintf(f, "\"mode\":\"%s\",\"fps\":%.6f,\"t0\":%.7f,\"master\":%d,\"error\":\"", mode,
            A.locked ? 1.0 / (A.step_s * (double) A.hold) : 1.0 / A.frame_dur, A.t0, A.mon[A.master].index);
    for (const char *c = m->error; *c; c++)
        if ((unsigned char) *c >= 0x20 && *c != '"' && *c != '\\')
            fputc(*c, f);
    fprintf(f, "\",\"composition_modes\":{");
    bool first = true;
    for (int i = 0; i < 4; i++)
        if (m->comp_modes[i]) {
            fprintf(f, "%s\"%d\":%d", first ? "" : ",", i, m->comp_modes[i]);
            first = false;
        }
    fprintf(f, "},\"vblanks\":[");
    for (size_t i = 0; i < nv; i++)
        fprintf(f, "%s%.7f", i ? "," : "", vb[i]);
    fprintf(f, "],\"presents\":[");
    for (size_t i = 0; i < m->n_presents; i++) {
        const struct present_rec *p = &m->presents[i];
        fprintf(f, "%s[%u,%.7f,%.7f,%.7f,%lld]", i ? "," : "", p->id, p->call, p->ret, p->target, (long long) p->frame);
    }
    fprintf(f, "],\"stats\":[");
    for (size_t i = 0; i < m->n_stats; i++) {
        const struct stat_rec *s = &m->stats[i];
        fprintf(f, "%s[%u,%u,%u,%.7f,%.7f]", i ? "," : "", s->present_count, s->present_refresh, s->sync_refresh,
                s->sync_qpc, s->polled);
    }
    fprintf(f, "]");

    // Extras for analyze-perf1.py (one entry per present, in present order): CPU time of the render call,
    // libplacebo's GPU pass timers, time left before the target refresh when Present1 was called.
    j_array(f, "render_cpu_ms", &m->render_cpu);
    j_array(f, "render_gpu_ms", &m->render_gpu);
    j_array(f, "lead_ms", &m->lead);
    if (m->render_sync.n)
        j_array(f, "render_sync_ms", &m->render_sync);
    // The picture of the first epoch's first shot (the later shots are in the cue log's refresh records).
    const struct lay_share *sh = &A.first_pic.share[m->slot];
    fprintf(f, ",\"perf1\":{\"queue_more\":%u,\"empty_mix\":%u,\"render_failed\":%u,\"held_services\":%u,\"drawn_mismatch\":%u,"
               "\"windowed\":%s,\"headless\":%s,"
               "\"swapchain\":[%d,%d],\"target_rect\":[%d,%d,%d,%d],\"has_picture\":%s,\"u\":[%.5f,%.5f],\"v\":[%.5f,%.5f],"
               "\"picture_mm\":[%.2f,%.2f,%.2f,%.2f],\"locked\":%s,\"hold\":%lld,\"step_s\":%.9f,\"frame_dur_s\":%.9f,"
               "\"decoded\":%u,\"mapped\":%u,\"dropped\":%u,\"pushed\":%u,\"decoder_seeks\":%u,\"nonmonotonic\":%u,"
               "\"codec\":\"%s\",\"hw_fmt\":\"%s\",\"preset\":\"%s\",\"format\":\"%s\"}",
            m->queue_more, m->empty_mix, m->render_fail, m->held_services, m->drawn_mismatch,
            A.cfg.windowed ? "true" : "false", A.cfg.headless_hz ? "true" : "false", m->sc_w, m->sc_h,
            sh->tx0, sh->ty0, sh->tx1, sh->ty1, sh->has_picture ? "true" : "false", sh->u0, sh->u1, sh->v0, sh->v1,
            A.pic_x0, A.pic_y0, A.pic_x1, A.pic_y1, A.locked ? "true" : "false", (long long) A.hold, A.step_s, A.frame_dur,
            di->decoded, di->mapped, di->dropped, di->pushed, di->seeks, di->nonmonotonic, di->codec, di->hw_fmt,
            A.cfg.preset, A.cfg.rgb10 ? "rgb10a2" : "bgra8");

    // Row timing (same fields as the presenter spike): signal timing, scan-line grid, geometry, aim, prediction.
    fprintf(f, ",\"windowed\":%s", A.cfg.windowed ? "true" : "false");
    rowmon_write_json(&m->row, f);
    fprintf(f, ",\"aim_mm\":%.3f,\"shared_mm\":[%.3f,%.3f]", A.aim_mm, A.band_top_mm, A.band_bottom_mm);
    if (A.predicted_valid)
        fprintf(f, ",\"predicted_ms\":{\"top\":%.4f,\"aim\":%.4f,\"bottom\":%.4f}", A.predicted_ms[0], A.predicted_ms[1],
                A.predicted_ms[2]);
    fprintf(f, "}\n");
    fclose(f);
    free(vb);

    struct summary cpu, gpu;
    summarize(m->render_cpu.v, m->render_cpu.n, &cpu);
    summarize(m->render_gpu.v, m->render_gpu.n, &gpu);
    printf("%s: %zu presents, render cpu p50 %.3f / p99 %.3f ms, gpu p50 %.3f / p99 %.3f ms, queue_more %u, empty %u, held %u, "
           "drawn!=chosen %u%s%s\n",
           m->name, m->n_presents, cpu.p50, cpu.p99, gpu.p50, gpu.p99, m->queue_more, m->empty_mix, m->held_services,
           m->drawn_mismatch, m->error[0] ? ", ERROR " : "", m->error);
    printf("wrote %s\n", path);
}

void log_write_all(void)
{
    decoder_get_info(A.dec, &A.di);
    if (!A.cfg.headless_hz)
        for (int i = 0; i < A.nmon; i++)
            write_monitor_log(&A.mon[i], &A.di);
    const char *names[MAX_MONITORS];
    for (int i = 0; i < A.nmon; i++)
        names[i] = A.mon[i].name;
    char path[1024];
    snprintf(path, sizeof(path), "%s/perf1-cuelog.json", A.cfg.out_dir);
    if (engine_write_cuelog(&A.engine, path, names))
        printf("wrote %s (%u swaps, %u seeks, %u restarts, %u re-aims, %u follower moves at cuts)\n", path, A.engine.swaps,
               A.engine.seeks, A.engine.restarts, A.engine.reaims, A.engine.moves);
    else
        fprintf(stderr, "perf1: cannot write %s\n", path);
}
