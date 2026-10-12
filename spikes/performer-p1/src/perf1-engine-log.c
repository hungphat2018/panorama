// The cue log: everything analyze-cues.py needs to check a run from the outside (what every monitor drew on every
// refresh and why, the versions, the epochs with their frame timestamps, the decided frame starts, the commands).
// The meaning of each field is documented in analyze-cues.py.
#include "perf1-engine.h"

#include <math.h>
#include <string.h>

static const char *kind_name(enum ev_kind k)
{
    switch (k) {
    case EV_LOAD: return "load";
    case EV_LOAD_FAIL: return "load_failed";
    case EV_SEEK_CMD: return "seek_cmd";
    case EV_PAUSE: return "pause";
    case EV_RESUME: return "resume";
    case EV_PARKED: return "parked";
    case EV_READY: return "ready";
    case EV_RESTART: return "restart";
    case EV_REAIM: return "reaim";
    case EV_SEEK_FAILED: return "seek_failed";
    case EV_STOP: return "stop";
    }
    return "?";
}

static void write_event(FILE *f, const struct event_rec *r)
{
    fprintf(f, "{\"kind\":\"%s\",\"t\":%.9f,\"gen\":%d,\"i0\":%lld,\"i1\":%lld,\"d0\":%.9f,\"d1\":%.9f,\"text\":", kind_name(r->kind),
            r->t, r->gen, (long long) r->i0, (long long) r->i1, r->d0, r->d1);
    json_write_string(f, r->text);
    fputc('}', f);
}

bool engine_write_cuelog(struct engine *e, const char *path, const char *const *mon_names)
{
    FILE *f = fopen(path, "wb");
    if (!f)
        return false;
    static char buf[1 << 16];
    setvbuf(f, buf, _IOFBF, sizeof(buf));
    const struct engine_cfg *c = &e->cfg;
    engine_save_epoch_frames(e);

    fprintf(f, "{\"schema\":\"panorama.perf1.cuelog/1\",\"n_monitors\":%d,\"master\":%d,\"monitors\":[", c->n_mon, c->master);
    for (int m = 0; m < c->n_mon; m++) {
        const row_mon *r = c->row[m];
        double aim_off = r->wake_to_top_s + rowmon_row_s(r, c->aim_mm) + r->lag_s;
        fprintf(f, "%s{\"index\":%d,\"name\":", m ? "," : "", m);
        json_write_string(f, mon_names && mon_names[m] ? mon_names[m] : "?");
        fprintf(f, ",\"rate_hz\":%.9f,\"scan_s\":%.9f,\"blank_s\":%.9f,\"aim_offset_s\":%.9f,\"p\":%d,\"q\":%d}",
                r->timing.refresh_hz, r->timing.scan_s, r->timing.blank_s, aim_off, e->fol[m].active ? e->fol[m].p : 1,
                e->fol[m].active ? e->fol[m].q : 1);
    }
    fprintf(f, "],\"config\":{\"step_s\":%.12f,\"frame_dur_s\":%.12f,\"tol_s\":%.12f,\"eta_s\":%.9f,\"hysteresis\":%s,"
               "\"reaim\":%s,\"own_mode\":%s,\"aim_mm\":%.3f,\"horizon\":%d},",
            c->step_s, c->frame_dur_s, 0.5 * c->step_s, c->eta_s, c->hysteresis ? "true" : "false",
            c->reaim ? "true" : "false", c->own_mode ? "true" : "false", c->aim_mm, c->horizon);

    fputs("\"versions\":[", f);
    for (size_t i = 0; i < e->store->n; i++) {
        if (i)
            fputc(',', f);
        cue_sheet_write_json(f, e->store->all[i]);
    }

    fputs("],\"epochs\":[", f);
    for (size_t i = 0; i < e->n_epochs; i++) {
        const struct epoch_rec *r = &e->epochs[i];
        fprintf(f, "%s{\"ep\":%d,\"gen\":%d,\"origin\":%.9f,\"step_s\":%.12f,\"t_plan\":%.9f,\"t_ready\":%.9f,"
                   "\"cost_s\":%.9f,\"J0\":%lld,\"start_k\":[",
                i ? "," : "", r->ep, r->gen, r->origin, r->step_s, r->t_plan, r->t_ready, r->cost_s, (long long) r->J0);
        for (int m = 0; m < c->n_mon; m++)
            fprintf(f, "%s%lld", m ? "," : "", (long long) r->start_k[m]);
        fputs("],\"frame_pts\":[", f);
        for (size_t k = 0; k < r->n_frames; k++)
            fprintf(f, "%s%.9f", k ? "," : "", r->frame_pts[k]);
        fputs("]}", f);
    }

    fprintf(f, "],\"start_fields\":[\"ep\",\"ord\",\"j\",\"cut\",\"shift\"");
    for (int m = 0; m < c->n_mon; m++)
        fprintf(f, ",\"s%d\"", m);
    fputs("],\"starts\":[", f);
    for (size_t i = 0; i < e->n_slog; i++) {
        const struct start_rec *r = &e->slog[i];
        fprintf(f, "%s[%d,%d,%lld,%d,%d", i ? "," : "", r->ep, r->ord, (long long) r->j, r->cut ? 1 : 0, r->shift);
        for (int m = 0; m < c->n_mon; m++)
            fprintf(f, ",%lld", (long long) r->s[m]);
        fputc(']', f);
    }

    fputs("],\"events\":[", f);
    EnterCriticalSection(&e->cs);
    for (size_t i = 0; i < e->n_events; i++) {
        if (i)
            fputc(',', f);
        write_event(f, &e->events[i]);
    }
    LeaveCriticalSection(&e->cs);

    fputs("],\"refresh_fields\":[\"k\",\"j\",\"svc\",\"target\",\"pts\",\"ep\",\"ord\",\"vid\",\"sidx\",\"sstart\","
          "\"drawn\",\"ach\",\"lid\",\"flags\",\"dec\"],\"refreshes\":{",
          f);
    for (int m = 0; m < c->n_mon; m++) {
        fprintf(f, "%s\"%d\":[", m ? "," : "", m);
        for (size_t i = 0; i < e->n_rlog[m]; i++) {
            const struct refresh_rec *r = &e->rlog[m][i];
            fprintf(f, "%s[%lld,%lld,%.9f,%.9f,%.9f,%d,%d,%d,%d,%.9f,%d,%u,%u,%u,%d]", i ? "," : "", (long long) r->k,
                    (long long) r->j, r->svc, r->target, r->pts, r->ep, r->ord, r->vid, r->sidx, r->sstart, r->drawn,
                    r->ach, r->lid, (unsigned) r->flags, r->dec);
        }
        fputc(']', f);
    }
    fprintf(f, "},\"stats\":{\"swaps\":%u,\"seeks\":%u,\"restarts\":%u,\"reaims\":%u,\"moves\":%u,\"forced_moves\":%u,"
               "\"class_resyncs\":%u,\"stale_lookups\":%u,\"latch_captures\":%u,\"truncated\":%s,"
               "\"index_rejected_stale\":%u,\"index_rejected_order\":%u}}\n",
            e->swaps, e->seeks, e->restarts, e->reaims, e->moves, e->forced_moves, e->class_resyncs, e->stale_lookups,
            e->latch.captures, e->rlog_truncated ? "true" : "false", e->findex.rejected_stale, e->findex.rejected_order);
    bool ok = !ferror(f);
    fclose(f);
    return ok;
}
