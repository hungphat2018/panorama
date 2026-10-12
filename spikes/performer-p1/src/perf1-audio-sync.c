// Device clock, write log and correction loop, see perf1-audio-sync.h.
#include "perf1-audio-sync.h"

#include <math.h>
#include <string.h>

// ---- device clock -------------------------------------------------------------------------------------------------

void devclock_reset(struct dev_clock *c, int rate)
{
    memset(c, 0, sizeof(*c));
    c->rate = (double) rate;
}

void devclock_add(struct dev_clock *c, uint64_t frames, double host_s)
{
    if (frames == 0)
        return;
    double o = (double) frames - c->rate * host_s;
    if (c->n == DC_WINDOW) {
        c->sum -= c->off[c->head];
        c->off[c->head] = o;
        c->head = (c->head + 1) % DC_WINDOW;
    } else {
        c->off[(c->head + c->n) % DC_WINDOW] = o;
        c->n++;
    }
    c->sum += o;
    if (c->n == 1)
        c->first_s = host_s;
    c->last_s = host_s;
    c->c = c->sum / (double) c->n;
    double ss = 0.0;
    for (int i = 0; i < c->n; i++) {
        double d = c->off[i] - c->c;
        ss += d * d;
    }
    c->resid_s = sqrt(ss / (double) c->n) / c->rate;
}

bool devclock_valid(const struct dev_clock *c)
{
    return c->n >= 3 && c->last_s - c->first_s >= 0.02;
}

double devclock_frame_at(const struct dev_clock *c, double host_s)
{
    return c->rate * host_s + c->c;
}

double devclock_time_of(const struct dev_clock *c, double frame)
{
    return (frame - c->c) / c->rate;
}

// ---- write log ----------------------------------------------------------------------------------------------------

void seglog_clear(struct seg_log *l)
{
    l->head = l->count = 0;
}

void seglog_add(struct seg_log *l, int64_t i0, int n, bool data, double t0, double t1)
{
    if (n <= 0)
        return;
    if (l->count == SEG_MAX) {
        l->head = (l->head + 1) % SEG_MAX;
        l->count--;
    }
    l->s[(l->head + l->count) % SEG_MAX] = (struct seg) { i0, n, data, t0, t1 };
    l->count++;
}

bool seglog_film_at(const struct seg_log *l, double x, double *film_s)
{
    for (int k = l->count - 1; k >= 0; k--) {
        const struct seg *s = &l->s[(l->head + k) % SEG_MAX];
        if (x >= (double) s->i0 && x < (double) (s->i0 + s->n)) {
            if (!s->data)
                return false;
            *film_s = s->t0 + (x - (double) s->i0) / (double) s->n * (s->t1 - s->t0);
            return true;
        }
    }
    return false;
}

// ---- control ------------------------------------------------------------------------------------------------------

void sync_ctl_reset(struct sync_ctl *c, bool keep_clock_error)
{
    double integ = keep_clock_error ? c->integ : 0.0; // the integral is the audio clock's drift against the film clock
    memset(c, 0, sizeof(*c));
    c->integ = integ;
    c->corr = -SYNC_KI * integ;
}

double sync_ctl_update(struct sync_ctl *c, double e_s, double dt)
{
    if (!c->primed) {
        c->e_f = e_s;
        c->primed = true;
    } else {
        double a = dt / (SYNC_TAU_S + dt);
        c->e_f += a * (e_s - c->e_f);
    }
    c->integ += c->e_f * dt;
    const double lim = SYNC_MAX_CORR / SYNC_KI;
    if (c->integ > lim)
        c->integ = lim;
    else if (c->integ < -lim)
        c->integ = -lim;
    double u = -(SYNC_KP * c->e_f + SYNC_KI * c->integ);
    c->corr = u > SYNC_MAX_CORR ? SYNC_MAX_CORR : u < -SYNC_MAX_CORR ? -SYNC_MAX_CORR : u;
    return c->corr;
}
