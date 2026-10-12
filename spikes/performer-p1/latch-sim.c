// latch-sim: deterministic headless test of the performer's decision core (src/perf1-engine.c and the modules below
// it). No GPU, no window, no OS timer: virtual time, a modelled decoder, planner and control schedule drive the very
// same engine, latch, cue store and loader that perf1 runs. The output is the cue log perf1 writes (analyze-cues.py
// judges it) and one line per run.
//
//   latch-sim --scenario swapseek|scrub --trace TRACE --setup 96|72|100 --seconds 400 --seed N --out DIR [--fault NAME]
//   latch-sim --scenario picks  --trace TRACE --setup 96 --seconds 300 --truth cfr:24000/1001 --out DIR [--fault pl-queue-pick]
//   latch-sim --scenario clock  --trace TRACE --setup 96 --hours 2 --drift-ppm 0.5 [--no-hysteresis] [--no-reaim]
//                               [--align-frac 0.5] --out DIR
//
// TRACE: a text file of frame timestamps in seconds (one per line, ascending), or "synthetic-vfr" (sections of
// 23.976 / 29.97 / 59.94 fps with millisecond-rounded timestamps, like the clock simulation's variable-rate trace).
//
// The world (see latch-sim-model.h): the laptop (240.0013 Hz) and the external (96, 72 or 100 Hz, the master) share a
// GPU clock, so their refreshes keep a fixed ratio up to --drift-ppm of drift. The performer sees noisy fits of both
// grids, refitted once a second. The decoder keeps the queues --lookahead seconds ahead of the picture and answers a
// seek after a park delay and a 30-150 ms decode. The planner stand-in publishes a new version every 1.5-4.5 s that
// changes every shot starting a second or more after the playhead; with probability 0.75 a seek lands between 50 ms
// before and 150 ms after the next swap, near a shot boundary; now and then a pause and resume. "scrub" adds bursts of
// 50 random seeks at 5 per second; "swapseek" has one burst. --fault injects a deliberate defect (negative control).
#define _CRT_SECURE_NO_WARNINGS
#include "latch-sim-model.h"

// ---- the control schedule ---------------------------------------------------------------------------------------

enum sim_ev { SE_SWAP, SE_SEEK, SE_PAUSE, SE_RESUME, SE_BURST };
struct sim_event { double t; enum sim_ev kind; double arg; int aux; };
struct sched { struct sim_event q[512]; int n; };

static void sched_add(struct sched *s, double t, enum sim_ev kind, double arg, int aux)
{
    if (s->n >= (int) (sizeof(s->q) / sizeof(s->q[0])))
        return;
    int i = s->n++;
    while (i > 0 && s->q[i - 1].t > t) {
        s->q[i] = s->q[i - 1];
        i--;
    }
    s->q[i] = (struct sim_event) { t, kind, arg, aux };
}

// ---- options ----------------------------------------------------------------------------------------------------

struct opts {
    const char *trace, *out, *scenario, *truth;
    int setup;                  // the external's refresh rate: the master
    double seconds, hours, drift_ppm, align_frac, lookahead, burst_every;
    uint64_t seed;
    unsigned faults;
    bool hysteresis, reaim, own;
};

static unsigned fault_from_name(const char *n)
{
    static const struct { const char *name; unsigned bit; } t[] = {
        { "no-latch", FAULT_NO_LATCH }, { "lookup-by-clock", FAULT_LOOKUP_BY_CLOCK }, { "keep-latch", FAULT_KEEP_LATCH },
        { "pl-queue-pick", FAULT_PL_QUEUE_PICK }, { "independent-start", FAULT_INDEPENDENT_START },
        { "no-hold", FAULT_NO_HOLD }, { "no-reaim-guard", FAULT_NO_REAIM_GUARD },
    };
    for (size_t i = 0; i < sizeof(t) / sizeof(t[0]); i++)
        if (!strcmp(n, t[i].name))
            return t[i].bit;
    return 0;
}

// ---- one simulated run ------------------------------------------------------------------------------------------

struct sim {
    struct world world;
    struct supply sup;
    struct plan plan;
    struct engine eng;
    struct cue_store store;
    struct sched sched;
    struct rng rng;
    struct trace tr;
    double now, next_svc[ENGINE_MAX_MON];
    unsigned n_swaps, n_seeks_posted, n_pauses, n_resumes, n_bursts, n_failed_loads;
    bool eof_pending;
    double step, hold_lock;
    int hold;
};

// First service time (just after a wake-up) at or after t for a monitor.
static double first_service_after(const struct sim_mon *s, double t, double after_wake)
{
    int64_t k = (int64_t) ceil((t - after_wake - true_wake(s, 0)) / s->period);
    return true_wake(s, k) + after_wake;
}

static bool sim_setup(struct sim *S, const struct opts *o, double t0)
{
    if (!make_world(&S->world, o->setup, o->drift_ppm, o->seed, t0))
        return false;
    if (o->align_frac >= 0.0) {
        // park class 0's follower refresh at this fraction of a follower refresh from the master's (at the aim row)
        struct sim_mon *lap = &S->world.mon[0], *ext = &S->world.mon[1];
        lap->phase = ext->phase + rowmon_row_s(&ext->row, 107.5) - rowmon_row_s(&lap->row, 107.5) + o->align_frac * lap->period;
        world_refit(&S->world, t0);
    }
    rng_seed(&S->rng, o->seed * 17 + 3);
    struct engine_cfg cfg;
    engine_cfg_defaults(&cfg);
    cfg.n_mon = 2;
    cfg.master = 1;
    cfg.row[0] = &S->world.mon[0].row;
    cfg.row[1] = &S->world.mon[1].row;
    cfg.aim_mm = 107.5;
    cfg.frame_dur_s = S->tr.frame_dur;
    double mp = S->world.mon[1].period;
    S->hold = (int) llround(S->tr.frame_dur / mp);
    if (S->hold < 1)
        S->hold = 1;
    S->hold_lock = S->tr.frame_dur / ((double) S->hold * mp);
    bool locked = fabs(S->hold_lock - 1.0) <= 0.01;
    cfg.step_s = S->step = locked ? S->tr.frame_dur / (double) S->hold : mp;
    cfg.hysteresis = o->hysteresis;
    cfg.reaim = o->reaim;
    cfg.own_mode = o->own;
    cfg.faults = o->faults;
    struct engine_host host = { .user = &S->world, .grid = sim_grid, .reset_queues = sim_reset_queues };
    if (!cue_store_init(&S->store) || !engine_init(&S->eng, &cfg, &host, &S->store))
        return false;
    S->sup.tr = &S->tr;
    S->sup.mode = SUP_NORMAL;
    S->sup.lookahead = o->lookahead;
    rng_seed(&S->sup.rng, o->seed * 5 + 1);
    plan_init(&S->plan, &S->tr, o->seed);
    return true;
}

static void sim_start_engine(struct sim *S, double t0)
{
    supply_poll(&S->sup, &S->eng, t0);
    engine_start(&S->eng, t0, S->tr.pts[0], t0 + 0.5);
    for (int m = 0; m < S->world.n; m++)
        S->next_svc[m] = first_service_after(&S->world.mon[m], t0, 0.3e-3);
}

static double service_jitter(struct sim *S)
{
    double j = 0.3e-3 + fabs(rng_norm(&S->rng)) * 0.08e-3;
    if (rng_u01(&S->rng) < 0.003)
        j += rng_range(&S->rng, 1.0e-3, 2.5e-3); // the odd late wake-up
    return j;
}

static void do_control(struct sim *S, const struct sim_event *ev)
{
    struct engine *e = &S->eng;
    double now = S->now, dur = S->tr.pts[S->tr.n - 1];
    switch (ev->kind) {
    case SE_SWAP: {
        char err[200] = "";
        if (plan_publish(&S->plan, e, engine_playhead(e), 1.0, now, err, sizeof(err))) {
            S->n_swaps++;
        } else {
            S->n_failed_loads++;
            fprintf(stderr, "latch-sim: publish failed: %s\n", err);
        }
        double next = now + rng_range(&S->rng, 1.5, 4.5);
        sched_add(&S->sched, next, SE_SWAP, 0, 0);
        if (rng_u01(&S->rng) < 0.75) {
            // a seek between 50 ms before and 150 ms after the next swap, to a point near one of the plan's shot boundaries
            size_t c = 1 + (size_t) (rng_u01(&S->rng) * (double) (S->plan.n_cuts > 1 ? S->plan.n_cuts - 1 : 1));
            if (c >= S->plan.n_cuts)
                c = S->plan.n_cuts - 1;
            double goal = S->plan.cuts[c] + rng_range(&S->rng, -2.0, 2.0) * S->tr.frame_dur;
            goal = fmin(fmax(goal, 0.0), dur - 1.0);
            sched_add(&S->sched, next + rng_range(&S->rng, -0.050, 0.150), SE_SEEK, goal, 0);
        }
        if (rng_u01(&S->rng) < 0.12) {
            double tp = next + rng_range(&S->rng, -0.050, 0.150);
            sched_add(&S->sched, tp, SE_PAUSE, 0, 0);
            sched_add(&S->sched, tp + rng_range(&S->rng, 0.2, 1.2), SE_RESUME, 0, 0);
        }
        break;
    }
    case SE_SEEK:
        if (engine_post(e, CMD_SEEK, ev->arg, now))
            S->n_seeks_posted++;
        S->eof_pending = false;
        break;
    case SE_PAUSE:
        if (engine_post(e, CMD_PAUSE, 0, now))
            S->n_pauses++;
        break;
    case SE_RESUME:
        if (engine_post(e, CMD_RESUME, 0, now))
            S->n_resumes++;
        break;
    case SE_BURST: {
        double goal = rng_range(&S->rng, 0.0, dur - 2.0);
        if (engine_post(e, CMD_SEEK, goal, now))
            S->n_seeks_posted++;
        S->n_bursts++;
        if (ev->aux > 1)
            sched_add(&S->sched, now + 0.2 + rng_range(&S->rng, -0.02, 0.02), SE_BURST, 0, ev->aux - 1);
        break;
    }
    }
}

// Runs the clock until t_end (virtual seconds), executing the schedule. Returns the number of presents.
static unsigned long sim_run(struct sim *S, double t_end, unsigned long *services_out)
{
    unsigned long services = 0, presents = 0;
    for (;;) {
        double t_ev = S->sched.n ? S->sched.q[0].t : 1e300;
        int mon = -1;
        for (int m = 0; m < S->world.n; m++)
            if (S->next_svc[m] < t_ev) {
                t_ev = S->next_svc[m];
                mon = m;
            }
        if (t_ev > t_end)
            break;
        S->now = t_ev;
        if (S->now >= S->world.next_refit)
            world_refit(&S->world, S->now);
        supply_poll(&S->sup, &S->eng, S->now);
        if (mon < 0) {
            struct sim_event ev = S->sched.q[0];
            memmove(&S->sched.q[0], &S->sched.q[1], (size_t) (S->sched.n - 1) * sizeof(S->sched.q[0]));
            S->sched.n--;
            do_control(S, &ev);
            continue;
        }
        struct svc_out out;
        engine_service(&S->eng, mon, S->now, &out);
        services++;
        if (out.action == SVC_PRESENT) {
            presents++;
            engine_note_drawn(&S->eng, mon, out.rec, (int) out.ord);
            if (mon == S->eng.cfg.master && !S->eof_pending && S->sched.n < 400 && out.pts >= S->tr.pts[S->tr.n - 1] - 1e-9) {
                S->eof_pending = true;
                sched_add(&S->sched, S->now + 1.0, SE_SEEK, 0.0, 0); // the film ended: start again, like a user would
            }
        }
        S->next_svc[mon] = true_wake(&S->world.mon[mon], out.k) + service_jitter(S);
    }
    *services_out = services;
    return presents;
}

static void make_dirs(const char *path)
{
    CreateDirectoryA(path, NULL);
}

static int run_functional(const struct opts *o)
{
    struct sim *S = calloc(1, sizeof(*S));
    if (!S || !trace_load(&S->tr, o->trace, o->seconds, o->seed)) {
        fprintf(stderr, "latch-sim: cannot load trace %s\n", o->trace);
        return 2;
    }
    const double t0 = 100.0;
    if (!sim_setup(S, o, t0)) {
        fprintf(stderr, "latch-sim: set-up failed\n");
        return 2;
    }
    sim_start_engine(S, t0);
    const double t_end = t0 + o->seconds;
    sched_add(&S->sched, t0 + 1.5, SE_SWAP, 0, 0);
    if (!strcmp(o->scenario, "scrub"))
        for (double t = t0 + 8.0; t < t_end - 12.0; t += o->burst_every)
            sched_add(&S->sched, t, SE_BURST, 0, 50); // 50 seeks at about 5 per second
    else
        sched_add(&S->sched, t0 + o->seconds * 0.5, SE_BURST, 0, 50);

    unsigned long services = 0;
    unsigned long presents = sim_run(S, t_end, &services);
    engine_note_stop(&S->eng, S->now);

    make_dirs(o->out);
    char path[512];
    snprintf(path, sizeof(path), "%s/cuelog.json", o->out);
    const char *names[2] = { S->world.mon[0].name, S->world.mon[1].name };
    bool ok = engine_write_cuelog(&S->eng, path, names);
    printf("latch-sim %s: trace %s (%zu frames, %.4f fps nominal) at 240+%d Hz, %.0f s virtual, seed %llu%s: "
           "%u swaps, %u seeks posted (%u executed), %u pauses, %u resumes, %u restarts, %lu services, %lu presents, "
           "supply: %u seeks started, %u superseded; reaims %u, moves %u, latch captures %u, stale lookups %u%s\n",
           o->scenario, S->tr.name, S->tr.n, 1.0 / S->tr.frame_dur, o->setup, o->seconds, (unsigned long long) o->seed,
           o->faults ? " [FAULT INJECTED]" : "", S->n_swaps, S->n_seeks_posted, S->eng.seeks, S->n_pauses, S->n_resumes,
           S->eng.restarts, services, presents, S->sup.seeks_started, S->sup.seeks_superseded, S->eng.reaims,
           S->eng.moves, S->eng.latch.captures, S->eng.stale_lookups, ok ? "" : " (LOG WRITE FAILED)");
    return ok ? 0 : 1;
}

// ---- scenario "picks": which frame does the master show on every refresh? ----------------------------------------

static size_t last_at_or_before(const double *v, size_t n, double x)
{
    size_t lo = 0, hi = n;
    if (n == 0 || x < v[0])
        return (size_t) -1;
    while (hi - lo > 1) {
        size_t mid = lo + (hi - lo) / 2;
        if (v[mid] <= x)
            lo = mid;
        else
            hi = mid;
    }
    return lo;
}

// One epoch, no commands. For every master refresh the intended frame is the last one due on the exact timestamps
// (film time plus half a master refresh); the stored timestamps are what the container reports (rounded to milliseconds).
static int run_picks(const struct opts *o)
{
    struct sim *S = calloc(1, sizeof(*S));
    if (!S || !trace_load(&S->tr, o->trace, o->seconds, o->seed)) {
        fprintf(stderr, "latch-sim: cannot load trace %s\n", o->trace);
        return 2;
    }
    if (o->truth && !strncmp(o->truth, "cfr:", 4)) {
        double num = 0, den = 1;
        sscanf(o->truth + 4, "%lf/%lf", &num, &den);
        S->tr.truth = malloc(S->tr.n * sizeof(double));
        for (size_t i = 0; i < S->tr.n && num > 0; i++)
            S->tr.truth[i] = (double) i * den / num;
    }
    const double t0 = 100.0;
    if (!sim_setup(S, o, t0))
        return 2;
    sim_start_engine(S, t0);
    unsigned long services = 0;
    sim_run(S, t0 + o->seconds, &services);

    const double *truth = S->tr.truth ? S->tr.truth : S->tr.pts;
    const struct epoch_rec *ep = &S->eng.epochs[0];
    unsigned long checked = 0, wrong = 0, wrong_late = 0;
    double worst_late_ms = 0.0;
    for (size_t i = 0; i < S->eng.n_rlog[1]; i++) {
        const struct refresh_rec *r = &S->eng.rlog[1][i];
        if (r->ep != 0 || r->k < ep->J0 + 2) // epoch 0 only: the film ending starts another one
            continue;
        double film_t = (double) (r->k - ep->J0) * S->step;
        size_t want = last_at_or_before(truth, S->tr.n, film_t + 0.5 * S->step);
        checked++;
        if ((size_t) r->ord != want) {
            wrong++;
            if ((size_t) r->ord < want) {
                wrong_late++;
                double late = (truth[want] - truth[r->ord]) * 1e3;
                if (late > worst_late_ms)
                    worst_late_ms = late;
            }
        }
    }
    make_dirs(o->out);
    char path[512];
    snprintf(path, sizeof(path), "%s/picks.json", o->out);
    FILE *f = fopen(path, "w");
    if (f) {
        fprintf(f, "{\"scenario\":\"picks\",\"trace\":\"%s\",\"setup\":%d,\"frame_dur_s\":%.9f,\"step_s\":%.9f,\"hold\":%d,"
                   "\"exact_truth\":%s,\"fault\":%u,\"master_refreshes_checked\":%lu,\"wrong\":%lu,\"wrong_fraction\":%.6f,"
                   "\"showing_an_earlier_frame\":%lu,\"worst_late_ms\":%.3f}\n",
                S->tr.name, o->setup, S->tr.frame_dur, S->step, S->hold, S->tr.truth ? "true" : "false", o->faults, checked,
                wrong, checked ? (double) wrong / (double) checked : 0.0, wrong_late, worst_late_ms);
        fclose(f);
    }
    printf("latch-sim picks: trace %s at %d Hz (hold %d, speed %.4f): %lu master refreshes, %lu on the wrong frame (%.3f %%), "
           "%lu of them an earlier frame than due (worst %.1f ms late)%s%s\n",
           S->tr.name, o->setup, S->hold, S->hold_lock, checked, wrong, checked ? 100.0 * (double) wrong / (double) checked : 0.0,
           wrong_late, worst_late_ms, S->tr.truth ? "" : " [timestamps are their own truth]", o->faults ? " [FAULT INJECTED]" : "");
    return 0;
}

// ---- scenario "clock": hysteresis and re-aim over hours, measured on the true grids ---------------------------------

static int cmp_double(const void *a, const void *b)
{
    double x = *(const double *) a, y = *(const double *) b;
    return (x > y) - (x < y);
}

static double pct_of(double *v, size_t n, double q)
{
    qsort(v, n, sizeof(double), cmp_double);
    return v[(size_t) (q * (double) (n - 1))];
}

static void trace_loop_to(struct trace *t, double seconds)
{
    if (t->pts[t->n - 1] >= seconds)
        return;
    double span = t->pts[t->n - 1] + t->frame_dur;
    size_t reps = (size_t) ceil(seconds / span) + 1;
    double *pts = malloc(t->n * reps * sizeof(double));
    for (size_t r = 0; r < reps; r++)
        for (size_t i = 0; i < t->n; i++)
            pts[r * t->n + i] = t->pts[i] + (double) r * span;
    free(t->pts);
    t->pts = pts;
    t->n *= reps;
}

static int run_clock(const struct opts *o)
{
    struct sim *S = calloc(1, sizeof(*S));
    double seconds = o->hours * 3600.0;
    if (!S || !trace_load(&S->tr, o->trace, seconds, o->seed)) {
        fprintf(stderr, "latch-sim: cannot load trace %s\n", o->trace);
        return 2;
    }
    if (strcmp(o->trace, "synthetic-vfr"))
        trace_loop_to(&S->tr, seconds + 60.0);
    const double t0 = 100.0;
    if (!sim_setup(S, o, t0))
        return 2;
    S->eng.no_refresh_log = true;
    sim_start_engine(S, t0);
    char err[200];
    if (!plan_publish(&S->plan, &S->eng, -1e6, 0.0, t0, err, sizeof(err))) {
        fprintf(stderr, "latch-sim: %s\n", err);
        return 2;
    }
    unsigned long services = 0;
    sim_run(S, t0 + seconds, &services);

    struct engine *e = &S->eng;
    const struct sim_mon *lap = &S->world.mon[0], *ext = &S->world.mon[1];
    const int q = e->fol[0].q, p = e->fol[0].p;
    size_t n = e->n_slog, na = 0;
    double *aim = malloc(n * sizeof(double)), *top = malloc(n * sizeof(double)), *bot = malloc(n * sizeof(double));
    int64_t last_j[ENGINE_MAX_Q] = {0}, last_s[ENGINE_MAX_Q] = {0};
    bool have[ENGINE_MAX_Q] = {false};
    double last_t[ENGINE_MAX_Q] = {0};
    int last_dir[ENGINE_MAX_Q] = {0};
    unsigned mid_slips = 0, cut_slips = 0, flipflops = 0;
    int64_t master_min = INT64_MAX, follower_min = INT64_MAX;
    int shift_min = 0, shift_max = 0;
    double t_first = 0, t_last = 0;
    const struct start_rec *prev = NULL;
    for (size_t i = 0; i < n; i++) {
        const struct start_rec *r = &e->slog[i];
        double t = true_light(ext, r->j, 107.5);
        if (!na)
            t_first = t;
        t_last = t;
        aim[na] = (true_light(lap, r->s[0], 107.5) - t) * 1e3;
        top[na] = (true_light(lap, r->s[0], 0.0) - true_light(ext, r->j, 0.0)) * 1e3;
        bot[na] = (true_light(lap, r->s[0], 215.0) - true_light(ext, r->j, 215.0)) * 1e3;
        na++;
        if (prev) {
            if (r->j - prev->j < master_min) master_min = r->j - prev->j;
            if (r->s[0] - prev->s[0] < follower_min) follower_min = r->s[0] - prev->s[0];
        }
        prev = r;
        if (r->shift < shift_min) shift_min = r->shift;
        if (r->shift > shift_max) shift_max = r->shift;
        int c = (int) (((r->j % q) + q) % q);
        if (have[c]) {
            int64_t expect = (int64_t) p * ((r->j - last_j[c]) / q);
            int64_t d = (r->s[0] - last_s[c]) - expect;
            if (d != 0) {
                if (r->cut)
                    cut_slips++;
                else
                    mid_slips++;
                int dir = d > 0 ? 1 : -1;
                if (last_dir[c] && last_dir[c] == -dir && t - last_t[c] < 2.0)
                    flipflops++;
                last_t[c] = t;
                last_dir[c] = dir;
            }
        }
        have[c] = true;
        last_j[c] = r->j;
        last_s[c] = r->s[0];
    }
    double hours = (t_last - t_first) / 3600.0;
    if (hours <= 0 || na < 10) {
        fprintf(stderr, "latch-sim: no frames decided\n");
        return 1;
    }
    // pct_of sorts in place, so the extremes are at the ends afterwards
    double a5 = pct_of(aim, na, 0.05), a95 = pct_of(aim, na, 0.95);
    double t5 = pct_of(top, na, 0.05), t95 = pct_of(top, na, 0.95);
    double b5 = pct_of(bot, na, 0.05), b95 = pct_of(bot, na, 0.95);
    double amax = fmax(fabs(aim[0]), fabs(aim[na - 1]));
    double tmax = fmax(fabs(top[0]), fabs(top[na - 1]));
    double bmax = fmax(fabs(bot[0]), fabs(bot[na - 1]));

    make_dirs(o->out);
    char path[512];
    snprintf(path, sizeof(path), "%s/clock-summary.json", o->out);
    FILE *f = fopen(path, "w");
    char line[2048];
    snprintf(line, sizeof(line),
             "{\"scenario\":\"clock\",\"trace\":\"%s\",\"setup\":%d,\"hours\":%.3f,\"drift_ppm\":%.2f,\"align_frac\":%.2f,"
             "\"hysteresis\":%s,\"reaim\":%s,\"frames\":%zu,\"p\":%d,\"q\":%d,"
             "\"mid_shot_slips\":%u,\"mid_shot_slips_per_hour\":%.3f,\"slips_at_cuts\":%u,\"slips_at_cuts_per_hour\":%.3f,"
             "\"flip_flops\":%u,\"reaims\":%u,\"reaims_per_hour\":%.3f,\"moves_at_cuts\":%u,\"forced_moves\":%u,"
             "\"class_resyncs\":%u,\"shift_range\":[%d,%d],"
             "\"aim_ms\":{\"p5\":%.3f,\"p95\":%.3f,\"max_abs\":%.3f},\"top_ms\":{\"p5\":%.3f,\"p95\":%.3f,\"max_abs\":%.3f},"
             "\"bottom_ms\":{\"p5\":%.3f,\"p95\":%.3f,\"max_abs\":%.3f},"
             "\"master_min_frame_refreshes\":%lld,\"follower_min_frame_refreshes\":%lld}",
             S->tr.name, o->setup, hours, o->drift_ppm, o->align_frac, o->hysteresis ? "true" : "false",
             o->reaim ? "true" : "false", na, p, q, mid_slips, mid_slips / hours, cut_slips, cut_slips / hours, flipflops,
             e->reaims, e->reaims / hours, e->moves, e->forced_moves, e->class_resyncs, shift_min, shift_max, a5, a95, amax,
             t5, t95, tmax, b5, b95, bmax, (long long) master_min, (long long) follower_min);
    if (f) {
        fprintf(f, "%s\n", line);
        fclose(f);
    }
    printf("%s\n", line);
    return 0;
}

// ---- command line -----------------------------------------------------------------------------------------------

static void usage(void)
{
    fprintf(stderr,
            "Usage: latch-sim --scenario swapseek|scrub|picks|clock --trace TRACE|synthetic-vfr --out DIR\n"
            "         [--setup 96|72|100] [--seconds N] [--hours H] [--seed N] [--drift-ppm X] [--align-frac F]\n"
            "         [--truth cfr:NUM/DEN] [--lookahead S] [--burst-every S] [--fault NAME] [--no-hysteresis]\n"
            "         [--no-reaim] [--own]\n"
            "  faults: no-latch lookup-by-clock keep-latch pl-queue-pick independent-start no-hold no-reaim-guard\n");
}

int main(int argc, char **argv)
{
    SetPriorityClass(GetCurrentProcess(), IDLE_PRIORITY_CLASS); // a test tool: never compete with a live session
    struct opts o = { .scenario = "swapseek", .setup = 96, .seconds = 400.0, .hours = 0.25, .drift_ppm = 0.5, .align_frac = -1.0,
                      .lookahead = 0.5, .burst_every = 30.0, .seed = 1, .hysteresis = true, .reaim = true };
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--scenario") && v) { o.scenario = v; i++; }
        else if (!strcmp(a, "--trace") && v) { o.trace = v; i++; }
        else if (!strcmp(a, "--out") && v) { o.out = v; i++; }
        else if (!strcmp(a, "--truth") && v) { o.truth = v; i++; }
        else if (!strcmp(a, "--setup") && v) { o.setup = atoi(v); i++; }
        else if (!strcmp(a, "--seconds") && v) { o.seconds = atof(v); i++; }
        else if (!strcmp(a, "--hours") && v) { o.hours = atof(v); i++; }
        else if (!strcmp(a, "--seed") && v) { o.seed = (uint64_t) atoll(v); i++; }
        else if (!strcmp(a, "--drift-ppm") && v) { o.drift_ppm = atof(v); i++; }
        else if (!strcmp(a, "--align-frac") && v) { o.align_frac = atof(v); i++; }
        else if (!strcmp(a, "--lookahead") && v) { o.lookahead = atof(v); i++; }
        else if (!strcmp(a, "--burst-every") && v) { o.burst_every = atof(v); i++; }
        else if (!strcmp(a, "--fault") && v) {
            unsigned bit = fault_from_name(v);
            if (!bit) {
                fprintf(stderr, "latch-sim: unknown fault %s\n", v);
                return 2;
            }
            o.faults |= bit;
            i++;
        }
        else if (!strcmp(a, "--no-hysteresis")) o.hysteresis = false;
        else if (!strcmp(a, "--no-reaim")) o.reaim = false;
        else if (!strcmp(a, "--own")) o.own = true;
        else {
            usage();
            return 2;
        }
    }
    if (!o.trace || !o.out || o.seconds <= 0 || o.hours <= 0) {
        usage();
        return 2;
    }
    if (!strcmp(o.scenario, "swapseek") || !strcmp(o.scenario, "scrub"))
        return run_functional(&o);
    if (!strcmp(o.scenario, "picks"))
        return run_picks(&o);
    if (!strcmp(o.scenario, "clock"))
        return run_clock(&o);
    usage();
    return 2;
}
