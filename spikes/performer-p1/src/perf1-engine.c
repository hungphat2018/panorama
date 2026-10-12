// The performer's decision core, see perf1-engine.h for the model.
#include "perf1-engine.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define REAIM_FRAMES 16     // frames of the coming shot scored when choosing a re-aim

// ---- small helpers -------------------------------------------------------------------------------------------

static row_vgrid grid_of(const struct engine *e, int m)
{
    return e->host.grid(e->host.user, m);
}

static int floormod(int64_t a, int b)
{
    int64_t r = a % b;
    return (int) (r < 0 ? r + b : r);
}

static int gcd_int(int a, int b)
{
    while (b) {
        int t = a % b;
        a = b;
        b = t;
    }
    return a;
}

// q master refreshes carry p refreshes of a follower, from the two exact signal rates: the smallest q (up to
// ENGINE_MAX_Q) for which p / q matches within 20 ppm. One GPU clock drives both heads, so the rates keep this
// ratio and the follower's refreshes fall into q alignment classes against the master's. With no small ratio (clocks
// that are independent) the classes collapse to one: q = 1.
static void ratio_pq(double master_hz, double follower_hz, int *p, int *q)
{
    double r = follower_hz / master_hz;
    for (int d = 1; d <= ENGINE_MAX_Q; d++) {
        double n = r * d, ni = floor(n + 0.5);
        if (ni >= 1.0 && fabs(n - ni) <= 2e-5 * n) {
            *p = (int) ni;
            *q = d;
            return;
        }
    }
    *p = (int) floor(r + 0.5);
    if (*p < 1)
        *p = 1;
    *q = 1;
}

static struct frame_start *slot(struct engine *e, long n)
{
    return &e->fs[n % ENGINE_FS_RING];
}

#define GROW(arr, n, cap, init)                                                               \
    do {                                                                                      \
        if ((n) == (cap)) {                                                                   \
            size_t ncap_ = (cap) ? (cap) * 2 : (init);                                        \
            void *p_ = realloc((arr), ncap_ * sizeof(*(arr)));                                \
            if (!p_)                                                                          \
                break;                                                                        \
            (arr) = p_;                                                                       \
            (cap) = ncap_;                                                                    \
        }                                                                                     \
    } while (0)

static void ev_note(struct engine *e, enum ev_kind kind, double t, int gen, int64_t i0, int64_t i1, double d0,
                    double d1, const char *text)
{
    EnterCriticalSection(&e->cs);
    GROW(e->events, e->n_events, e->cap_events, 256);
    if (e->n_events < e->cap_events) {
        struct event_rec *r = &e->events[e->n_events++];
        memset(r, 0, sizeof(*r));
        r->kind = kind;
        r->t = t;
        r->gen = gen;
        r->i0 = i0;
        r->i1 = i1;
        r->d0 = d0;
        r->d1 = d1;
        if (text)
            snprintf(r->text, sizeof(r->text), "%s", text);
    }
    LeaveCriticalSection(&e->cs);
}

// Tells the host (the sound) about a break or an epoch start, if it listens.
static void host_note(struct engine *e, enum engine_note_kind kind, double now, double t, int64_t j0, bool hold)
{
    if (!e->host.note)
        return;
    struct engine_note n = { .kind = kind, .gen = e->gen, .epoch = e->epoch, .now = now, .t = t, .j0 = j0,
                             .step_s = e->cfg.step_s, .hold = hold };
    e->host.note(e->host.user, &n);
}

// ---- set-up ----------------------------------------------------------------------------------------------------

void engine_cfg_defaults(struct engine_cfg *c)
{
    memset(c, 0, sizeof(*c));
    c->hysteresis = true;
    c->reaim = true;
    c->eta_s = 0.05e-3;
    c->svc_margin_s = 0.5e-3;
    c->decide_after_wake_s = 0.3e-3;
    c->restart_margin_s = 3.0e-3;
    c->horizon = 2;
    for (int i = 0; i < ENGINE_MAX_MON; i++)
        c->lead_refreshes[i] = 1;
}

bool engine_init(struct engine *e, const struct engine_cfg *cfg, const struct engine_host *host, struct cue_store *store)
{
    memset(e, 0, sizeof(*e));
    if (cfg->n_mon < 1 || cfg->n_mon > ENGINE_MAX_MON || cfg->master < 0 || cfg->master >= cfg->n_mon ||
        !(cfg->step_s > 0.0))
        return false;
    e->cfg = *cfg;
    e->host = *host;
    e->store = store;
    shot_latch_init(&e->latch);
    findex_init(&e->findex);
    InitializeCriticalSection(&e->cs);
    e->state = ES_IDLE;
    e->epoch = -1;
    for (int m = 0; m < cfg->n_mon; m++) {
        e->cursor[m] = -1;
        if (m == cfg->master)
            continue;
        struct follower_state *f = &e->fol[m];
        f->active = true;
        ratio_pq(cfg->row[cfg->master]->timing.refresh_hz, cfg->row[m]->timing.refresh_hz, &f->p, &f->q);
        int g = gcd_int(f->p, f->q);
        f->p /= g;
        f->q /= g;
        e->n_followers++;
    }
    e->status.state = ES_IDLE;
    return true;
}

void engine_destroy(struct engine *e)
{
    findex_destroy(&e->findex);
    DeleteCriticalSection(&e->cs);
    free(e->events);
    free(e->slog);
    for (int m = 0; m < ENGINE_MAX_MON; m++)
        free(e->rlog[m]);
    for (size_t i = 0; i < e->n_epochs; i++)
        free(e->epochs[i].frame_pts);
    free(e->epochs);
    memset(e, 0, sizeof(*e));
}

// ---- the film clock -----------------------------------------------------------------------------------------------

// The master refresh on which the frame with timestamp pts starts, with re-aim shift S: the first refresh whose film
// time plus half a master refresh reaches pts (the last frame due, with half a master refresh of tolerance).
static int64_t nominal_with_shift(const struct engine *e, double pts, int shift)
{
    double x = (pts - e->origin) / e->cfg.step_s - 0.5 - 1e-9;
    return e->J0 + shift + (int64_t) ceil(x);
}

int64_t engine_nominal_start(const struct engine *e, double pts)
{
    return nominal_with_shift(e, pts, e->shift);
}

#ifdef ENGINE_FAULTS
static double film_time_at(const struct engine *e, int64_t j)
{
    return e->origin + (double) (j - e->J0 - e->shift) * e->cfg.step_s;
}
#endif

// ---- follower decisions -------------------------------------------------------------------------------------------

// Distance, in light at the aim row, between the master's refresh j and the nearest refresh of follower m.
static double follower_error(const struct engine *e, int m, int64_t j, const row_vgrid *gm, const row_vgrid *gf)
{
    const struct engine_cfg *c = &e->cfg;
    int64_t kn = row_follower_refresh(c->row[c->master], gm, c->row[m], gf, j, c->aim_mm);
    return fabs(rowmon_light_at(c->row[m], gf, kn, c->aim_mm) - rowmon_light_at(c->row[c->master], gm, j, c->aim_mm));
}

// Follower m's refresh index for the frame starting on master refresh j. See the file comment for the rule: the
// refresh nearest in light, kept per alignment class until another is better by more than eta and the frame is a cut.
static int64_t decide_follower(struct engine *e, int m, int64_t j, bool cut, const row_vgrid *gm, const row_vgrid *gf)
{
    const struct engine_cfg *c = &e->cfg;
    struct follower_state *f = &e->fol[m];
    if (c->own_mode) {
        // switch on the first refresh at or after the master's refresh time
        double t = row_vgrid_time(gm, j);
        return (int64_t) ceil((t - gf->base) / gf->period - 1e-9);
    }
    int64_t nearest = row_follower_refresh(c->row[c->master], gm, c->row[m], gf, j, c->aim_mm);
    int cls = floormod(j, f->q);
    int64_t chosen = nearest;
    if (c->hysteresis && f->cls[cls].valid) {
        int64_t kept = f->cls[cls].k + ((j - f->cls[cls].j) / f->q) * f->p;
        if (kept != nearest) {
            double lit = rowmon_light_at(c->row[c->master], gm, j, c->aim_mm);
            double err_kept = fabs(rowmon_light_at(c->row[m], gf, kept, c->aim_mm) - lit);
            double err_near = fabs(rowmon_light_at(c->row[m], gf, nearest, c->aim_mm) - lit);
            if (llabs(kept - nearest) > 2) {
                e->class_resyncs++; // the class table no longer matches the clocks (not drift): start over from nearest
            } else if (cut && err_kept - err_near > c->eta_s) {
                e->moves++;
            } else {
                chosen = kept;
            }
        }
    }
    f->cls[cls].valid = true;
    f->cls[cls].j = j;
    f->cls[cls].k = chosen;
    return chosen;
}

// ---- re-aim at cuts -----------------------------------------------------------------------------------------------

// At a cut the master's frame changes may move by one refresh into a better alignment class: candidate shifts S in
// {-1, 0, +1} (so the picture stays within one master refresh of the sound), scored by the worst seam error over the
// frames of the coming shot that are known now. A shift is taken only if it beats the current one by more than eta,
// and never shortens the frame before the cut below one refresh. Returns the start refresh of frame n.
static int64_t reaim_at_cut(struct engine *e, long n, double pts, int64_t j_cur, const row_vgrid *gm)
{
    const struct engine_cfg *c = &e->cfg;
    const struct cue_sheet *sheet = cue_store_current(e->store);
    double shot_end = cue_sheet_shot_end(sheet, cue_sheet_find_shot(sheet, pts));

    double fp[REAIM_FRAMES];
    int nf = 0;
    size_t total = findex_count(&e->findex);
    for (size_t i = (size_t) n; i < total && nf < REAIM_FRAMES; i++) {
        double p;
        if (!findex_get(&e->findex, i, &p) || p >= shot_end)
            break;
        fp[nf++] = p;
    }
    row_vgrid gf[ENGINE_MAX_MON];
    for (int m = 0; m < c->n_mon; m++)
        if (e->fol[m].active)
            gf[m] = grid_of(e, m);

    const int64_t j_prev = slot(e, n - 1)->j;
    const int64_t len_cur = j_cur - j_prev;
    double cost[3];
    bool allowed[3];
    for (int s = -1; s <= 1; s++) {
        int delta = s - e->shift;
        allowed[s + 1] = true;
#ifdef ENGINE_FAULTS
        if (!(c->faults & FAULT_NO_REAIM_GUARD))
#endif
            if (delta != 0 && len_cur + delta < (len_cur >= 1 ? 1 : 0))
                allowed[s + 1] = false; // never shorten the frame before the cut below one refresh
        double worst = 0.0;
        for (int i = 0; i < nf; i++) {
            int64_t j = nominal_with_shift(e, fp[i], s);
            for (int m = 0; m < c->n_mon; m++)
                if (e->fol[m].active) {
                    double err = follower_error(e, m, j, gm, &gf[m]);
                    if (err > worst)
                        worst = err;
                }
        }
        cost[s + 1] = worst;
    }
    int pick = e->shift;
    double pick_cost = cost[e->shift + 1];
    static const int order[3] = { 0, -1, 1 };
    for (int o = 0; o < 3; o++) {
        int s = order[o];
        if (s == e->shift || !allowed[s + 1])
            continue;
        if (cost[s + 1] < pick_cost - 1e-12) {
            pick = s;
            pick_cost = cost[s + 1];
        }
    }
    if (pick == e->shift || !(pick_cost < cost[e->shift + 1] - c->eta_s))
        return j_cur;
    int delta = pick - e->shift;
    e->shift = pick;
    e->reaims++;
    int64_t j_new = j_cur + delta;
    ev_note(e, EV_REAIM, 0.0, e->gen, n, j_new, (double) delta, cost[pick + 1 - delta] - cost[pick + 1], NULL);
    return j_new;
}

// ---- frame starts -------------------------------------------------------------------------------------------------

static bool is_cut(struct engine *e, long n, double pts)
{
    if (n == 0)
        return true;
    double prev;
    if (!findex_get(&e->findex, (size_t) n - 1, &prev))
        return false;
    const struct cue_sheet *s = cue_store_current(e->store);
    return cue_sheet_find_shot(s, pts) != cue_sheet_find_shot(s, prev);
}

static void decide_frame(struct engine *e, long n, double pts)
{
    const struct engine_cfg *c = &e->cfg;
    struct frame_start *f = slot(e, n);
    memset(f, 0, sizeof(*f));
    f->ord = n;
    f->pts = pts;
    f->cut = is_cut(e, n, pts);

    row_vgrid gm = grid_of(e, c->master);
    int64_t j = n == 0 ? e->J0 : nominal_with_shift(e, pts, e->shift);
    if (n > 0 && c->reaim && f->cut && e->n_followers > 0)
        j = reaim_at_cut(e, n, pts, j, &gm);
    if (n > 0 && j < slot(e, n - 1)->j)
        j = slot(e, n - 1)->j;
    f->j = j;
    f->s[c->master] = j;

    for (int m = 0; m < c->n_mon; m++) {
        if (!e->fol[m].active)
            continue;
        row_vgrid gf = grid_of(e, m);
        int64_t s = n == 0 ? e->start_k[m] : decide_follower(e, m, j, f->cut, &gm, &gf);
        if (n > 0) {
            const struct frame_start *prev = slot(e, n - 1);
            int64_t want = s;
            if (j > prev->j && want <= prev->s[m])
                want = prev->s[m] + 1; // a shown frame lasts at least one refresh on every monitor
            else if (j == prev->j && want < prev->s[m])
                want = prev->s[m];
            if (want != s) {
                s = want;
                e->forced_moves++;
                int cls = floormod(j, e->fol[m].q);
                e->fol[m].cls[cls].k = s;
            }
        }
        f->s[m] = s;
    }

    GROW(e->slog, e->n_slog, e->cap_slog, 4096);
    if (e->n_slog < e->cap_slog) {
        struct start_rec *r = &e->slog[e->n_slog++];
        r->ep = e->epoch;
        r->ord = (int32_t) n;
        r->j = j;
        for (int m = 0; m < ENGINE_MAX_MON; m++)
            r->s[m] = f->s[m];
        r->cut = f->cut;
        r->shift = (int8_t) e->shift;
    }
}

// Decides the starts of every frame that is known and starts on a master refresh up to jmax.
static void decide_through(struct engine *e, int64_t jmax)
{
    size_t total = findex_count(&e->findex);
    while ((size_t) e->decided < total) {
        double pts;
        if (!findex_get(&e->findex, (size_t) e->decided, &pts))
            break;
        if (e->decided > 0 && nominal_with_shift(e, pts, e->shift) > jmax)
            break;
        decide_frame(e, e->decided, pts);
        e->decided++;
    }
}

// ---- epochs: planning a restart -----------------------------------------------------------------------------------

void engine_save_epoch_frames(struct engine *e)
{
    if (e->epoch < 0 || e->epoch >= (int) e->n_epochs || e->epochs[e->epoch].frame_pts)
        return;
    size_t n = 0;
    double *p = findex_copy(&e->findex, &n);
    e->epochs[e->epoch].frame_pts = p;
    e->epochs[e->epoch].n_frames = n;
}

// Chooses the master refresh K the new epoch starts on (see the file comment) and sets the epoch up.
static void plan_restart(struct engine *e, double now, double t_ready, double origin_pts)
{
    const struct engine_cfg *c = &e->cfg;
    row_vgrid gm = grid_of(e, c->master);
    const double need = t_ready + c->restart_margin_s;
    // The master prepares refresh K just after the wake-up `lead` refreshes before it.
    int64_t kmin = (int64_t) ceil((need - c->decide_after_wake_s - gm.base) / gm.period - 1e-9) + c->lead_refreshes[c->master];
    int qall = 1;
    for (int m = 0; m < c->n_mon; m++)
        if (e->fol[m].active) {
            int g = gcd_int(qall, e->fol[m].q);
            qall = qall / g * e->fol[m].q;
            if (qall > ENGINE_MAX_Q)
                qall = ENGINE_MAX_Q;
        }

    struct cand { bool ok; int64_t K; double cost; int64_t kf[ENGINE_MAX_MON]; } best = { .ok = false }, cur;
    for (int cls = 0; cls < qall; cls++) {
        for (int extra = 0; extra < 4; extra++) {
            memset(&cur, 0, sizeof(cur));
            cur.K = kmin + cls + (int64_t) extra * qall;
            bool feasible = true;
            for (int m = 0; m < c->n_mon; m++) {
                if (!e->fol[m].active)
                    continue;
                row_vgrid gf = grid_of(e, m);
                cur.kf[m] = row_follower_refresh(c->row[c->master], &gm, c->row[m], &gf, cur.K, c->aim_mm);
                double err = follower_error(e, m, cur.K, &gm, &gf);
                if (err > cur.cost)
                    cur.cost = err;
                // the follower prepares its refresh just after the wake-up `lead` refreshes before it
                if (row_vgrid_time(&gf, cur.kf[m] - c->lead_refreshes[m]) + c->decide_after_wake_s < need)
                    feasible = false;
            }
            if (feasible) {
                if (!best.ok || cur.cost < best.cost - 1e-9) {
                    best = cur;
                    best.ok = true;
                }
                break; // the first refresh in this class that every follower can make
            }
        }
    }
    if (!best.ok) {
        // No class works within four tries (a follower with a very long lead): take a late start rather than none.
        memset(&best, 0, sizeof(best));
        best.K = kmin + (int64_t) 5 * qall;
        for (int m = 0; m < c->n_mon; m++)
            if (e->fol[m].active) {
                row_vgrid gf = grid_of(e, m);
                best.kf[m] = row_follower_refresh(c->row[c->master], &gm, c->row[m], &gf, best.K, c->aim_mm);
            }
        best.ok = true;
    }
#ifdef ENGINE_FAULTS
    if (c->faults & FAULT_INDEPENDENT_START) {
        // every follower starts on its own earliest refresh instead of the one that matches the master's
        for (int m = 0; m < c->n_mon; m++)
            if (e->fol[m].active) {
                row_vgrid gf = grid_of(e, m);
                best.kf[m] = (int64_t) ceil((need + c->decide_after_wake_s - gf.base) / gf.period - 1e-9) +
                             c->lead_refreshes[m];
            }
    }
#endif

    e->epoch++;
    e->J0 = best.K;
    e->origin = origin_pts;
    e->shift = 0;
    e->decided = 0;
    for (int m = 0; m < c->n_mon; m++) {
        e->start_k[m] = m == c->master ? best.K : best.kf[m];
        e->started[m] = false;
        e->cursor[m] = -1;
        memset(e->fol[m].cls, 0, sizeof(e->fol[m].cls));
        if (e->fol[m].active) {
            int cls = floormod(best.K, e->fol[m].q);
            e->fol[m].cls[cls].valid = true;
            e->fol[m].cls[cls].j = best.K;
            e->fol[m].cls[cls].k = best.kf[m];
        }
    }
    e->restarts++;

    GROW(e->epochs, e->n_epochs, e->cap_epochs, 64);
    if (e->n_epochs < e->cap_epochs) {
        struct epoch_rec *r = &e->epochs[e->n_epochs++];
        memset(r, 0, sizeof(*r));
        r->ep = e->epoch;
        r->gen = e->gen;
        r->origin = origin_pts;
        r->step_s = c->step_s;
        r->t_plan = now;
        r->t_ready = t_ready;
        r->cost_s = best.cost;
        r->J0 = best.K;
        for (int m = 0; m < c->n_mon; m++)
            r->start_k[m] = e->start_k[m];
    }
    ev_note(e, EV_RESTART, now, e->gen, e->epoch, best.K, origin_pts, t_ready, NULL);
    host_note(e, NOTE_EPOCH, now, origin_pts, best.K, false);
}

bool engine_start(struct engine *e, double now, double first_pts, double t_ready)
{
    if (findex_count(&e->findex) < 1)
        return false;
    plan_restart(e, now, t_ready, first_pts);
    e->playhead = first_pts;
    e->state = ES_PLAYING;
    return true;
}

// ---- commands and the break state machine -----------------------------------------------------------------------

bool engine_post(struct engine *e, enum cmd_kind kind, double arg, double now)
{
    bool ok = false;
    EnterCriticalSection(&e->cs);
    if (e->n_mail < (int) (sizeof(e->mail) / sizeof(e->mail[0]))) {
        e->mail[e->n_mail++] = (struct engine_cmd) { kind, arg, now };
        ok = true;
    }
    LeaveCriticalSection(&e->cs);
    return ok;
}

static bool seeking(const struct engine *e)
{
    return e->state == ES_SEEK_PARK || e->state == ES_SEEK_FILL;
}

static void begin_seek(struct engine *e, double now, double target, bool pause_after, double posted, bool is_seek_cmd)
{
    e->gen++;
    atomic_store_explicit(&e->sync.seek_target, target, memory_order_relaxed);
    atomic_store_explicit(&e->sync.want_gen, e->gen, memory_order_release);
    e->state = ES_SEEK_PARK;
    e->pause_after_first = pause_after;
    e->break_time = now;
#ifdef ENGINE_FAULTS
    if (!(e->cfg.faults & FAULT_KEEP_LATCH))
#endif
        shot_latch_reset(&e->latch); // a break is where presentation may change
    if (is_seek_cmd)
        e->seeks++;
    ev_note(e, EV_SEEK_CMD, posted, e->gen, is_seek_cmd ? 1 : 0, 0, target, now, NULL);
    host_note(e, NOTE_SEEK, now, target, 0, pause_after);
}

static void apply_cmd(struct engine *e, const struct engine_cmd *c, double now)
{
    switch (c->kind) {
    case CMD_SEEK:
        begin_seek(e, now, c->arg < 0.0 ? 0.0 : c->arg, e->state == ES_PAUSED || (seeking(e) && e->pause_after_first),
                   c->posted, true);
        break;
    case CMD_PAUSE:
        if (e->state == ES_PLAYING) {
            e->held_pts = e->playhead;
            e->state = ES_PAUSED;
            e->break_time = now;
            ev_note(e, EV_PAUSE, c->posted, e->gen, 0, 0, e->held_pts, now, NULL);
            host_note(e, NOTE_PAUSE, now, e->held_pts, 0, true);
        } else if (seeking(e)) {
            e->pause_after_first = true; // show the frame the seek lands on, then hold
            host_note(e, NOTE_HOLD, now, 0.0, 0, true);
        }
        break;
    case CMD_RESUME:
        if (e->state == ES_PAUSED) {
            ev_note(e, EV_RESUME, c->posted, e->gen, 0, 0, e->held_pts, now, NULL);
            begin_seek(e, now, e->held_pts, false, c->posted, false);
        } else if (seeking(e)) {
            e->pause_after_first = false;
            host_note(e, NOTE_HOLD, now, 0.0, 0, false);
        }
        break;
    }
}

static void publish_status(struct engine *e)
{
    EnterCriticalSection(&e->cs);
    e->status.state = e->state;
    e->status.epoch = e->epoch;
    e->status.gen = e->gen;
    e->status.version = cue_store_current(e->store)->version;
    // where the film is: the held frame when paused, the time being sought while a seek is in flight
    if (e->state == ES_PAUSED)
        e->status.playhead = e->held_pts;
    else if (seeking(e))
        e->status.playhead = atomic_load_explicit(&e->sync.seek_target, memory_order_relaxed);
    else
        e->status.playhead = e->playhead;
    e->status.swaps = e->swaps;
    e->status.seeks = e->seeks;
    e->status.restarts = e->restarts;
    LeaveCriticalSection(&e->cs);
}

// An epoch whose first frame is up on some monitors but not yet on all. A break now would leave the screens showing
// different pictures, so commands wait (at most one master refresh) until every monitor has started.
static bool start_in_flight(const struct engine *e)
{
    if (e->state != ES_PLAYING)
        return false;
    int started = 0;
    for (int m = 0; m < e->cfg.n_mon; m++)
        started += e->started[m] ? 1 : 0;
    return started > 0 && started < e->cfg.n_mon;
}

void engine_tick(struct engine *e, double now)
{
    if (e->state == ES_IDLE) // commands wait until the film has started
        return;
    if (!start_in_flight(e)) {
        struct engine_cmd cmds[64];
        int n;
        EnterCriticalSection(&e->cs);
        n = e->n_mail;
        memcpy(cmds, e->mail, (size_t) n * sizeof(cmds[0]));
        e->n_mail = 0;
        LeaveCriticalSection(&e->cs);
        for (int i = 0; i < n; i++)
            apply_cmd(e, &cmds[i], now);
    }

    if (e->state == ES_SEEK_PARK && atomic_load_explicit(&e->sync.parked_gen, memory_order_acquire) == e->gen) {
        engine_save_epoch_frames(e);
        findex_reset(&e->findex, e->gen);
        e->host.reset_queues(e->host.user);
        atomic_store_explicit(&e->sync.reset_gen, e->gen, memory_order_release);
        ev_note(e, EV_PARKED, now, e->gen, 0, 0, 0.0, 0.0, NULL);
        e->state = ES_SEEK_FILL;
    }
    if (e->state == ES_SEEK_FILL && atomic_load_explicit(&e->sync.ready_gen, memory_order_acquire) == e->gen) {
        double pts = atomic_load_explicit(&e->sync.ready_pts, memory_order_relaxed);
        double tr = atomic_load_explicit(&e->sync.ready_time, memory_order_relaxed);
        if (isnan(pts)) {
            // The seek produced no frame (past the end of a file with none after the keyframe, or a read error).
            ev_note(e, EV_SEEK_FAILED, now, e->gen, 0, 0, 0.0, 0.0, NULL);
            e->state = ES_PAUSED;
            host_note(e, NOTE_PAUSE, now, e->held_pts, 0, true);
        } else {
            ev_note(e, EV_READY, now, e->gen, 0, 0, pts, tr, NULL);
            plan_restart(e, now, tr > now ? tr : now, pts);
            e->playhead = pts;
            e->state = ES_PLAYING;
        }
    }
    publish_status(e);
}

// ---- one refresh ----------------------------------------------------------------------------------------------

static void push_rlog(struct engine *e, int m, const struct refresh_rec *r, struct svc_out *out)
{
    if (e->no_refresh_log)
        return;
    if (e->n_rlog[m] >= 4000000) {
        e->rlog_truncated = true;
        return;
    }
    GROW(e->rlog[m], e->n_rlog[m], e->cap_rlog[m], 16384);
    if (e->n_rlog[m] < e->cap_rlog[m]) {
        e->rlog[m][e->n_rlog[m]] = *r;
        out->rec = (long) e->n_rlog[m]++;
    }
}

void engine_note_drawn(struct engine *e, int m, long rec, int drawn_ord)
{
    if (rec >= 0 && (size_t) rec < e->n_rlog[m])
        e->rlog[m][rec].drawn = drawn_ord;
}

#ifdef ENGINE_FAULTS
// The frame the old queue_position shortcut would have shown: pl_queue's nearest frame to the film time shifted by
// half a step and back by half a nominal frame. Only for the negative control.
static long pl_queue_shortcut_pick(struct engine *e, double film_t)
{
    double pos = film_t + 0.5 * e->cfg.step_s - 0.5 * e->cfg.frame_dur_s;
    if (pos < 0.0)
        pos = 0.0;
    size_t total = findex_count(&e->findex);
    long i0 = -1;
    for (size_t i = 0; i < total; i++) {
        double p;
        findex_get(&e->findex, i, &p);
        if (p <= pos)
            i0 = (long) i;
        else
            break;
    }
    if (i0 < 0)
        return total ? 0 : -1;
    double p0, p1;
    findex_get(&e->findex, (size_t) i0, &p0);
    if (!findex_get(&e->findex, (size_t) i0 + 1, &p1))
        return i0;
    return fabs(p1 - pos) < fabs(p0 - pos) ? i0 + 1 : i0;
}
#endif

void engine_service(struct engine *e, int m, double now, struct svc_out *out)
{
    const struct engine_cfg *c = &e->cfg;
    memset(out, 0, sizeof(*out));
    out->action = SVC_HOLD;
    out->ord = -1;
    out->rec = -1;
    engine_tick(e, now);

    row_vgrid gk = grid_of(e, m);
    int64_t k = (int64_t) ceil((now + c->svc_margin_s - gk.base) / gk.period - 1e-9);
    out->k = k;
    out->target = gk.base + (double) k * gk.period;
    out->epoch = e->epoch;

    bool no_hold = false;
#ifdef ENGINE_FAULTS
    no_hold = (c->faults & FAULT_NO_HOLD) && seeking(e) && e->epoch >= 0;
#endif
    if (e->state != ES_PLAYING && !no_hold) {
        out->retry_at = now + 0.002;
        return;
    }
    if (k < e->start_k[m]) {
        // before this monitor's first refresh of the epoch: come back just as it can prepare that refresh
        // (never further than a few milliseconds: a break command can replace the plan before this refresh is due)
        double t = row_vgrid_time(&gk, e->start_k[m] - 1) - c->svc_margin_s + 1e-4;
        out->retry_at = t > now ? (t < now + 0.004 ? t : now + 0.004) : now + 0.001;
        return;
    }
    row_vgrid gm = m == c->master ? gk : grid_of(e, c->master);
    int64_t jt = row_vgrid_index_near(&gm, out->target);
    if (!no_hold)
        decide_through(e, jt + c->horizon);

    long ord;
    double pts;
    int64_t jframe;
#ifdef ENGINE_FAULTS
    if (c->faults & FAULT_PL_QUEUE_PICK) {
        int64_t j = jt;
        if (m != c->master) {
            // largest master refresh whose carrying refresh on this monitor is not later than the target
            j = jt - 2;
            for (int64_t jj = jt - 2; jj <= jt + 2; jj++) {
                int64_t kk = row_follower_refresh(c->row[c->master], &gm, c->row[m], &gk, jj, c->aim_mm);
                if (row_vgrid_time(&gk, kk) <= out->target + 1e-4)
                    j = jj;
            }
        }
        ord = pl_queue_shortcut_pick(e, e->origin + (double) (j - e->J0) * c->step_s);
        if (ord < 0 || !findex_get(&e->findex, (size_t) ord, &pts)) {
            out->retry_at = now + 0.001;
            return;
        }
        jframe = j;
        e->cursor[m] = ord;
    } else
#endif
    {
        long cur = e->cursor[m];
        while (cur + 1 < e->decided && slot(e, cur + 1)->s[m] <= k)
            cur++;
        if (cur < 0) {
            out->retry_at = now + 0.001; // frame 0 is not decided yet: the supply has no frames
            return;
        }
        e->cursor[m] = cur;
        ord = cur;
        pts = slot(e, cur)->pts;
        jframe = slot(e, cur)->j;
    }

    // The latch: the cue comes from the version the frame's shot started with, looked up by the frame's own timestamp.
    double look = pts;
#ifdef ENGINE_FAULTS
    if (c->faults & FAULT_LOOKUP_BY_CLOCK)
        look = film_time_at(e, jt);
#endif
    struct latch_result lr;
#ifdef ENGINE_FAULTS
    if (c->faults & FAULT_NO_LATCH) {
        shot_latch_reset(&e->latch); // every refresh captures the current version afresh
    }
#endif
    lr = shot_latch_resolve(&e->latch, e->store, look);
    if (lr.stale)
        e->stale_lookups++;

    out->action = SVC_PRESENT;
    out->ord = ord;
    out->pts = pts;
    out->j = jframe;
    out->first_of_epoch = !e->started[m];
    out->captured = lr.captured;
    out->stale = lr.stale;
    out->shot = lr.entry;
    out->cue = &lr.entry->sheet->shots[lr.entry->shot];
    out->cue_hash = cue_shot_hash(out->cue);

    struct refresh_rec r = {
        .k = k, .j = jframe, .svc = now, .target = out->target, .pts = pts,
        .sstart = lr.entry->sheet->shots[lr.entry->shot].start,
        .ep = e->epoch, .ord = (int32_t) ord, .vid = lr.entry->sheet->version, .sidx = lr.entry->shot, .drawn = -1,
        .dec = (int32_t) e->decided,
        .ach = out->cue_hash, .lid = lr.entry->id,
        .flags = (uint8_t) ((lr.captured ? 1 : 0) | (out->first_of_epoch ? 2 : 0) | (lr.stale ? 4 : 0)),
    };
    push_rlog(e, m, &r, out);

    if (m == c->master)
        e->playhead = pts;
    if (out->first_of_epoch) {
        e->started[m] = true;
        bool all = true;
        for (int i = 0; i < c->n_mon; i++)
            all = all && e->started[i];
        if (all && e->pause_after_first) {
            e->pause_after_first = false;
            e->held_pts = e->origin;
            e->state = ES_PAUSED; // the seek landed while paused: the frame is up on every monitor, now hold
        }
    }
}

// ---- notes from other threads -----------------------------------------------------------------------------------

void engine_note_load(struct engine *e, double now, int version, bool ok, const char *text)
{
    EnterCriticalSection(&e->cs);
    if (ok)
        e->swaps++;
    LeaveCriticalSection(&e->cs);
    ev_note(e, ok ? EV_LOAD : EV_LOAD_FAIL, now, 0, version, 0, 0.0, 0.0, text);
}

void engine_note_stop(struct engine *e, double now)
{
    ev_note(e, EV_STOP, now, e->gen, 0, 0, 0.0, 0.0, NULL);
}

void engine_set_film(struct engine *e, double step_s, double frame_dur_s)
{
    e->cfg.step_s = step_s;
    e->cfg.frame_dur_s = frame_dur_s;
}

bool engine_on_last_frame(struct engine *e)
{
    long cur = e->cursor[e->cfg.master];
    if (e->state != ES_PLAYING || cur < 0)
        return false;
    return (size_t) cur + 1 == findex_count(&e->findex);
}

void engine_status(struct engine *e, struct engine_status *out)
{
    EnterCriticalSection(&e->cs);
    *out = e->status;
    LeaveCriticalSection(&e->cs);
}

double engine_playhead(struct engine *e)
{
    struct engine_status st;
    engine_status(e, &st);
    return st.playhead;
}
