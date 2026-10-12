// latch-sim-model.h: the modelled world of the headless test (latch-sim.c): random numbers, frame-timestamp traces, the two
// displays with the performer's noisy view of their refresh grids, the decoder (frame supply) and the planner stand-in.
// Header-only; everything is static so latch-sim.c is the only translation unit.
#pragma once
#include "src/perf1-engine.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

// ---- random numbers (xorshift64*) -----------------------------------------------------------------------------

struct rng { uint64_t s; };

static uint64_t rng_next(struct rng *r)
{
    r->s ^= r->s >> 12;
    r->s ^= r->s << 25;
    r->s ^= r->s >> 27;
    return r->s * 0x2545F4914F6CDD1DULL;
}
static double rng_u01(struct rng *r) { return (double) (rng_next(r) >> 11) * (1.0 / 9007199254740992.0); }
static double rng_range(struct rng *r, double lo, double hi) { return lo + (hi - lo) * rng_u01(r); }
static double rng_norm(struct rng *r)
{
    double u = rng_u01(r), v = rng_u01(r);
    if (u < 1e-300)
        u = 1e-300;
    return sqrt(-2.0 * log(u)) * cos(6.283185307179586 * v);
}
static void rng_seed(struct rng *r, uint64_t seed)
{
    r->s = seed * 0x9E3779B97F4A7C15ULL + 0x1234567ULL;
    for (int i = 0; i < 8; i++)
        rng_next(r);
}

// ---- the trace --------------------------------------------------------------------------------------------------

struct trace {
    double *pts;
    double *truth;          // exact timestamps when pts are rounded (variable-rate traces, --truth); else NULL
    size_t n;
    size_t cut_every;       // synthetic traces with built-in cuts: a shot starts every this many frames (0 = random plan)
    double frame_dur;       // nominal frame duration (the container's rate)
    char name[64];
};

static bool trace_load(struct trace *t, const char *spec, double seconds_hint, uint64_t seed)
{
    memset(t, 0, sizeof(*t));
    snprintf(t->name, sizeof(t->name), "%s", spec);
    for (char *c = t->name; *c; c++)
        if (*c == '\\' || *c == '"')
            *c = '/'; // the name goes into JSON summaries
    if (!strcmp(spec, "synthetic-vfr")) {
        // sections of 5-60 s at 23.976 (70 %), 29.97 (20 %) and 59.94 fps (10 %), timestamps rounded to milliseconds
        struct rng r;
        rng_seed(&r, seed * 77 + 4);
        size_t cap = (size_t) (seconds_hint * 60.0) + 4096, n = 0;
        double *pts = malloc(cap * sizeof(double)), *truth = malloc(cap * sizeof(double));
        double time = 0.0;
        if (!pts || !truth)
            return false;
        while (time < seconds_hint * 1.3 + 60.0 && n + 4000 < cap) {
            double u = rng_u01(&r), num = u < 0.7 ? 24000 : (u < 0.9 ? 30000 : 60000), den = 1001;
            size_t cnt = (size_t) (rng_range(&r, 5.0, 60.0) * num / den);
            for (size_t i = 0; i < cnt && n < cap; i++) {
                truth[n] = time + (double) i * den / num;
                pts[n] = floor(truth[n] * 1000.0 + 0.5) / 1000.0;
                n++;
            }
            time += (double) cnt * den / num;
        }
        t->pts = pts;
        t->truth = truth;
        t->n = n;
        t->frame_dur = 1001.0 / 24000.0;
        return true;
    }
    if (!strcmp(spec, "synthetic-flip")) {
        // 23.976 fps, except that every 100th frame follows its predecessor after just one master refresh at 96 Hz
        // (the frame before it lasts one refresh) and a shot starts there: each cut flips the alignment class
        size_t n = (size_t) (seconds_hint * 1.3 / (1001.0 / 24000.0)) + 4096;
        double *pts = malloc(n * sizeof(double)), *truth = malloc(n * sizeof(double));
        if (!pts || !truth)
            return false;
        double fd = 1001.0 / 24000.0;
        for (size_t i = 0; i < n; i++) {
            truth[i] = i ? truth[i - 1] + (i % 100 == 0 ? fd / 4.0 : fd) : 0.0;
            pts[i] = floor(truth[i] * 1000.0 + 0.5) / 1000.0;
        }
        t->pts = pts;
        t->truth = truth;
        t->n = n;
        t->cut_every = 100;
        t->frame_dur = fd;
        return true;
    }
    if (!strcmp(spec, "synthetic-60")) {
        // constant 59.94 fps (frames of 1.6 master refreshes at 96 Hz: lengths 1 and 2), milliseconds-rounded
        size_t n = (size_t) (seconds_hint * 1.3 / (1001.0 / 60000.0)) + 4096;
        double *pts = malloc(n * sizeof(double)), *truth = malloc(n * sizeof(double));
        if (!pts || !truth)
            return false;
        for (size_t i = 0; i < n; i++) {
            truth[i] = (double) i * 1001.0 / 60000.0;
            pts[i] = floor(truth[i] * 1000.0 + 0.5) / 1000.0;
        }
        t->pts = pts;
        t->truth = truth;
        t->n = n;
        t->frame_dur = 1001.0 / 60000.0;
        return true;
    }
    FILE *f = fopen(spec, "r");
    if (!f) {
        fprintf(stderr, "latch-sim: cannot open trace %s\n", spec);
        return false;
    }
    size_t cap = 65536, n = 0;
    double *pts = malloc(cap * sizeof(double));
    char line[128];
    while (pts && fgets(line, sizeof(line), f)) {
        char *end;
        double v = strtod(line, &end);
        if (end == line)
            continue;
        if (n == cap) {
            cap *= 2;
            double *np = realloc(pts, cap * sizeof(double));
            if (!np) {
                free(pts);
                pts = NULL;
                break;
            }
            pts = np;
        }
        pts[n++] = v;
    }
    fclose(f);
    if (!pts || n < 8)
        return false;
    for (size_t i = n; i-- > 0;)
        pts[i] -= pts[0];
    // the nominal duration: the average over the film, as a container reports it
    t->frame_dur = pts[n - 1] / (double) (n - 1);
    t->pts = pts;
    t->n = n;
    return true;
}

static size_t trace_index_at(const struct trace *t, double target)
{
    // last frame with pts <= target
    size_t lo = 0, hi = t->n;
    if (target < t->pts[0])
        return 0;
    while (hi - lo > 1) {
        size_t mid = lo + (hi - lo) / 2;
        if (t->pts[mid] <= target)
            lo = mid;
        else
            hi = mid;
    }
    return lo;
}

// ---- the displays -----------------------------------------------------------------------------------------------

struct sim_mon {
    row_mon row;            // what the engine sees
    row_vgrid wake_hat;     // the performer's fitted wake-up grid
    double period, phase;   // true refresh period and the true top-row time of refresh 0
    double blank, scan, wake_delay;
    double wake_sd_s, wake_psd, scan_sd_s, scan_psd;   // fit error models: offset (s) and relative period error
    char name[16];
};

struct world {
    struct sim_mon mon[ENGINE_MAX_MON];
    int n;
    struct rng rng;
    double next_refit;
    unsigned resets;
};

static void make_sim_mon(struct sim_mon *s, const char *name, double rate_hz, unsigned active_h, unsigned total_h,
                         double height_mm, double true_period, double phase, double wake_sd_us, double wake_ppm)
{
    memset(s, 0, sizeof(*s));
    snprintf(s->name, sizeof(s->name), "%s", name);
    row_mon *r = &s->row;
    rowmon_init_empty(r);
    r->timing.refresh_hz = rate_hz;
    r->timing.line_s = 1.0 / (rate_hz * (double) total_h);
    r->timing.scan_s = (double) active_h * r->timing.line_s;
    r->timing.blank_s = (double) (total_h - active_h) * r->timing.line_s;
    r->timing.active_h = active_h;
    r->timing.total_h = total_h;
    r->height_mm = height_mm;
    s->period = true_period;
    s->phase = phase;
    s->blank = r->timing.blank_s;
    s->scan = r->timing.scan_s;
    s->wake_delay = 0.09e-3;
    s->wake_sd_s = wake_sd_us * 1e-6;
    s->wake_psd = wake_ppm * 1e-6;
    s->scan_sd_s = 0.2e-6;
    s->scan_psd = 1e-8;
    r->wake_to_top_s = s->blank - s->wake_delay;
    r->wake_to_top_end_s = r->wake_to_top_s;
    r->have_wake_to_top = r->have_wake_to_top_end = true;
}

// True top-row time of refresh k, and the true time its row at height y_mm lights.
static double true_top(const struct sim_mon *s, int64_t k) { return s->phase + (double) k * s->period; }
static double true_light(const struct sim_mon *s, int64_t k, double y_mm)
{
    return true_top(s, k) + rowmon_row_s(&s->row, y_mm) + s->row.lag_s;
}
static double true_wake(const struct sim_mon *s, int64_t k) { return true_top(s, k) - s->blank + s->wake_delay; }

// The performer refits both of its views of a monitor around time t: errors are drawn afresh, anchored near t.
static void world_refit(struct world *w, double t)
{
    for (int m = 0; m < w->n; m++) {
        struct sim_mon *s = &w->mon[m];
        int64_t kr = (int64_t) floor((t - s->phase) / s->period + 0.5);
        double per = s->period * (1.0 + s->wake_psd * rng_norm(&w->rng));
        double wake = true_wake(s, kr) + s->wake_sd_s * rng_norm(&w->rng);
        s->wake_hat.period = per;
        s->wake_hat.base = wake - (double) kr * per;
        double sper = s->period * (1.0 + s->scan_psd * rng_norm(&w->rng));
        double top = true_top(s, kr) + s->scan_sd_s * rng_norm(&w->rng);
        row_fit f = { .ok = true, .period = sper, .base = top - (double) kr * sper, .samples = 1000, .taken = 1000 };
        s->row.live = f;
        s->row.active = f;
        s->row.fit_start = f;
        s->row.fit_end = f;
    }
    w->next_refit = t + 1.0;
}

static row_vgrid sim_grid(void *user, int m)
{
    return ((struct world *) user)->mon[m].wake_hat;
}

static void sim_reset_queues(void *user)
{
    ((struct world *) user)->resets++;
}

// Setup A: monitor 0 is the laptop (240.0013 Hz, 1600 of 1800 lines, 215 mm), monitor 1 the external at ext_hz
// (the master: the slowest). The GPU clock locks them: p follower refreshes per q master refreshes, plus drift.
static bool make_world(struct world *w, int ext_hz, double drift_ppm, uint64_t seed, double t0)
{
    memset(w, 0, sizeof(*w));
    rng_seed(&w->rng, seed * 31 + 7);
    unsigned total_h;
    double pixel_clock, ext_w = 2000.0;
    int p, q;
    switch (ext_hz) {
    case 96: total_h = 1160; pixel_clock = 222.72e6; p = 5; q = 2; break;
    case 72: total_h = 1545; pixel_clock = 222.48e6; p = 10; q = 3; break;
    case 100: total_h = 1115; pixel_clock = 223.0e6; p = 12; q = 5; break;
    default: return false;
    }
    double ext_rate = pixel_clock / (ext_w * (double) total_h);
    double lap_rate = 1175.045e6 / (2720.0 * 1800.0);
    double p_ext = 1.0 / ext_rate;
    double p_lap = p_ext * (double) q / (double) p * (1.0 + drift_ppm * 1e-6);
    make_sim_mon(&w->mon[0], "DISPLAY1", lap_rate, 1600, 1800, 215.0, p_lap, t0 + rng_u01(&w->rng) * p_lap, 20.0, 4.5);
    make_sim_mon(&w->mon[1], "DISPLAY2", ext_rate, 1080, total_h, 268.0, p_ext, t0 + rng_u01(&w->rng) * p_ext, 17.0, 1.8);
    w->n = 2;
    world_refit(w, t0);
    return true;
}

// ---- the frame supply (the decoder, modelled) -------------------------------------------------------------------

struct supply {
    const struct trace *tr;
    size_t next;                         // next trace index to append
    int gen;
    enum { SUP_NORMAL, SUP_PARKING, SUP_WAIT_RESET, SUP_DECODING } mode;
    double park_at, decode_at;
    size_t first_idx;
    double lookahead;                    // seconds of film queued ahead of the picture
    struct rng rng;
    unsigned seeks_started, seeks_superseded;
};

static void supply_poll(struct supply *s, struct engine *e, double now)
{
    int want = atomic_load_explicit(&e->sync.want_gen, memory_order_acquire);
    if (want != s->gen) {
        if (s->mode == SUP_DECODING || s->mode == SUP_WAIT_RESET || s->mode == SUP_PARKING)
            s->seeks_superseded++;
        s->gen = want;
        s->mode = SUP_PARKING;
        s->park_at = now + rng_range(&s->rng, 0.001, 0.005); // the decoder notices within one push timeout
        s->seeks_started++;
    }
    if (s->mode == SUP_PARKING && now >= s->park_at) {
        atomic_store_explicit(&e->sync.parked_gen, s->gen, memory_order_release);
        s->mode = SUP_WAIT_RESET;
    }
    if (s->mode == SUP_WAIT_RESET && atomic_load_explicit(&e->sync.reset_gen, memory_order_acquire) == s->gen) {
        double target = atomic_load_explicit(&e->sync.seek_target, memory_order_relaxed);
        s->first_idx = trace_index_at(s->tr, target);
        s->decode_at = now + rng_range(&s->rng, 0.030, 0.150);
        s->mode = SUP_DECODING;
    }
    if (s->mode == SUP_DECODING && now >= s->decode_at) {
        size_t i = s->first_idx;
        findex_append(&e->findex, s->gen, s->tr->pts[i]);
        if (i + 1 < s->tr->n)
            findex_append(&e->findex, s->gen, s->tr->pts[i + 1]);
        s->next = i + 2;
        atomic_store_explicit(&e->sync.ready_pts, s->tr->pts[i], memory_order_relaxed);
        atomic_store_explicit(&e->sync.ready_time, s->decode_at, memory_order_relaxed);
        atomic_store_explicit(&e->sync.ready_gen, s->gen, memory_order_release);
        s->mode = SUP_NORMAL;
    }
    if (s->mode == SUP_NORMAL) {
        double head = engine_playhead(e) + s->lookahead;
        while (s->next < s->tr->n && s->tr->pts[s->next] <= head)
            findex_append(&e->findex, s->gen, s->tr->pts[s->next++]);
    }
}

// ---- the planner stand-in ---------------------------------------------------------------------------------------

#define SRC_W 1920
#define SRC_H 1080

struct plan {
    const struct trace *tr;
    double *cuts;                // shot start times of the base plan: halfway between frames
    size_t n_cuts;
    struct rng rng;
    int next_version;
};

static void plan_init(struct plan *p, const struct trace *tr, uint64_t seed)
{
    memset(p, 0, sizeof(*p));
    p->tr = tr;
    rng_seed(&p->rng, seed * 131 + 9);
    p->next_version = 1;
    size_t cap = tr->n / 8 + 16;
    p->cuts = malloc(cap * sizeof(double));
    size_t i = 0;
    p->cuts[p->n_cuts++] = 0.0;
    while (i < tr->n) {
        i += tr->cut_every ? tr->cut_every : (size_t) fmax(1.0, rng_range(&p->rng, 2.0, 8.0) / tr->frame_dur);
        if (i >= tr->n || p->n_cuts >= cap)
            break;
        p->cuts[p->n_cuts++] = 0.5 * (tr->pts[i - 1] + tr->pts[i]); // halfway between the frames
    }
}

// Values of shot `idx` of version `id`: a crop and a placement that differ from one version to the next.
static void cue_values(int id, size_t idx, double *crop, double *place)
{
    uint32_t h = (uint32_t) id * 2654435761u + (uint32_t) idx * 40503u;
    crop[0] = (double) (8 * (1 + h % 20));
    crop[1] = (double) (4 * (1 + (h >> 5) % 30));
    crop[2] = SRC_W - (double) (8 * ((h >> 10) % 15));
    crop[3] = SRC_H - (double) (4 * ((h >> 15) % 25));
    place[0] = 354.0 + (double) ((int) ((h >> 20) % 21) - 10);
    place[1] = 107.5 + (double) ((int) ((h >> 25) % 11) - 5);
    place[2] = 200.0 + (double) ((h >> 8) % 16);
}

// Builds, loads (through the real loader) and publishes the next version. Every shot of the previous version that starts
// before playhead + horizon is copied exactly (start and cue); from there on the base plan's boundaries are used,
// now and then moved by 1-3 frames, with new cue values. A boundary never moves into the frozen part.
static bool plan_publish(struct plan *p, struct engine *e, double playhead, double horizon, double now, char *err, size_t errn)
{
    const struct cue_sheet *prev = cue_store_current(e->store);
    int id = p->next_version;
    const double commit = playhead + horizon;
    size_t cap = (p->n_cuts + prev->n_shots) * 400 + 1024, len = 0, written = 0;
    char *text = malloc(cap);
    if (!text)
        return false;
    len += (size_t) snprintf(text + len, cap - len, "{\"schema\":\"panorama.cues/1\",\"version\":%d,\"file_hash\":\"sim\","
                             "\"room_signature\":\"setup-a\",\"preferences_hash\":\"default\",\"shots\":[", id);
    double last_start = -1e300;
    for (size_t i = 0; i < prev->n_shots && prev->shots[i].start < commit; i++) {
        const struct cue_shot *ps = &prev->shots[i];
        double crop[4], place[3];
        if (ps->has_crop && ps->has_place) {
            memcpy(crop, ps->crop, sizeof(crop));
            place[0] = ps->center_u_mm; place[1] = ps->center_v_mm; place[2] = ps->height_mm;
        } else {
            cue_values(0, i, crop, place); // the built-in default sheet has no values to copy
        }
        len += (size_t) snprintf(text + len, cap - len,
                                 "%s{\"start\":%.17g,\"crop\":[%.17g,%.17g,%.17g,%.17g],"
                                 "\"placement\":{\"center_mm\":[%.17g,%.17g],\"height_mm\":%.17g},\"subtitle_monitor\":%d}",
                                 written ? "," : "", ps->start, crop[0], crop[1], crop[2], crop[3], place[0], place[1], place[2],
                                 (int) (i % 2));
        written++;
        last_start = ps->start;
    }
    for (size_t i = 0; i < p->n_cuts; i++) {
        double start = p->cuts[i];
        const bool first = i == 0 && !written; // nothing frozen: the version starts with the base plan's first shot
        if (!first && (start < commit || i == 0))
            continue;
        if (!first && rng_u01(&p->rng) < 0.10) {
            // move the boundary by 1-3 frames, to another point halfway between frames
            size_t fi = trace_index_at(p->tr, start);
            long d = (long) (rng_range(&p->rng, 1.0, 3.99)) * (rng_u01(&p->rng) < 0.5 ? -1 : 1);
            long ni = (long) fi + d;
            if (ni >= 1 && (size_t) ni < p->tr->n)
                start = 0.5 * (p->tr->pts[ni - 1] + p->tr->pts[ni]);
        }
        if (!first && (start < commit || start <= last_start))
            continue;
        double crop[4], place[3];
        cue_values(id, i, crop, place);
        len += (size_t) snprintf(text + len, cap - len,
                                 "%s{\"start\":%.17g,\"crop\":[%.17g,%.17g,%.17g,%.17g],"
                                 "\"placement\":{\"center_mm\":[%.17g,%.17g],\"height_mm\":%.17g},\"subtitle_monitor\":%d}",
                                 written ? "," : "", start, crop[0], crop[1], crop[2], crop[3], place[0], place[1], place[2],
                                 (int) (i % 2));
        written++;
        last_start = start;
    }
    snprintf(text + len, cap - len, "]}");
    if (!written) { // cannot happen: the first shot of any version starts at or before 0
        free(text);
        snprintf(err, errn, "planner stand-in produced no shots");
        return false;
    }
    char source[48];
    snprintf(source, sizeof(source), "sim-v%d", id);
    struct cue_sheet *s = cue_sheet_parse(text, strlen(text), SRC_W, SRC_H, source, err, errn);
    free(text);
    if (!s)
        return false;
    if (!cue_store_publish(e->store, s, err, errn)) {
        cue_sheet_free(s);
        return false;
    }
    p->next_version++;
    engine_note_load(e, now, id, true, source);
    return true;
}
