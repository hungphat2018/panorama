// Refresh grid of one monitor, fitted to its recent vblank times (C port of the VblankClock in
// spikes/per-monitor-presenter/per-monitor-common.h, same maths).
//
// Each vblank is indexed from the gap to the previous one (robust to jitter and to a missed wake-up),
// then a least-squares line over the last 240 vblanks gives period and phase.
#include "perf1vk.h"

#include <math.h>

void vclock_init(struct vclock *c)
{
    memset(c, 0, sizeof(*c));
    InitializeCriticalSection(&c->cs);
}

void vclock_destroy(struct vclock *c)
{
    DeleteCriticalSection(&c->cs);
    free(c->log);
    free(c->idx);
    memset(c, 0, sizeof(*c));
}

static int cmp_double(const void *a, const void *b)
{
    double x = *(const double *) a, y = *(const double *) b;
    return (x > y) - (x < y);
}

static int64_t next_index(const struct vclock *c, size_t i)
{
    double gap = (c->log[i] - c->log[i - 1]) / c->period;
    int64_t step = (int64_t) llround(gap);
    return c->idx[i - 1] + (step < 1 ? 1 : step);
}

void vclock_add(struct vclock *c, double t)
{
    EnterCriticalSection(&c->cs);
    if (c->n == c->cap) {
        size_t cap = c->cap ? c->cap * 2 : 8192;
        double *l = realloc(c->log, cap * sizeof(double));
        int64_t *x = realloc(c->idx, cap * sizeof(int64_t));
        if (!l || !x) {
            if (l) c->log = l;
            if (x) c->idx = x;
            goto out; // out of memory: stop growing the grid, keep the fit we have
        }
        c->log = l;
        c->idx = x;
        c->cap = cap;
    }
    c->log[c->n++] = t;

    if (c->period == 0) {
        if (c->n < 32)
            goto out;
        // first estimate: median spacing of the first vblanks
        double d[64];
        size_t m = c->n - 1 < 64 ? c->n - 1 : 64;
        for (size_t i = 0; i < m; i++)
            d[i] = c->log[i + 1] - c->log[i];
        qsort(d, m, sizeof(double), cmp_double);
        c->period = d[m / 2];
        c->idx[0] = 0;
        for (size_t i = 1; i < c->n; i++)
            c->idx[i] = next_index(c, i);
    } else {
        c->idx[c->n - 1] = next_index(c, c->n - 1);
    }

    size_t first = c->n > 240 ? c->n - 240 : 0;
    double sk = 0, st = 0, skk = 0, skt = 0, n = (double) (c->n - first);
    for (size_t i = first; i < c->n; i++) {
        double k = (double) c->idx[i], ti = c->log[i] - c->log[first];
        sk += k; st += ti; skk += k * k; skt += k * ti;
    }
    double denom = n * skk - sk * sk;
    if (denom > 0) {
        double period = (n * skt - sk * st) / denom;
        if (period > 0.001 && period < 0.1) {
            c->period = period;
            c->base = c->log[first] + (st - period * sk) / n; // time of vblank index 0 on the fitted line
        }
    }
out:
    LeaveCriticalSection(&c->cs);
}

double vclock_period(struct vclock *c)
{
    EnterCriticalSection(&c->cs);
    double p = c->period;
    LeaveCriticalSection(&c->cs);
    return p;
}

double vclock_next_after(struct vclock *c, double t)
{
    EnterCriticalSection(&c->cs);
    double k = ceil((t - c->base) / c->period - 1e-9);
    double r = c->base + k * c->period;
    LeaveCriticalSection(&c->cs);
    return r;
}

double vclock_nearest(struct vclock *c, double t)
{
    EnterCriticalSection(&c->cs);
    double r = c->base + round((t - c->base) / c->period) * c->period;
    LeaveCriticalSection(&c->cs);
    return r;
}

int64_t vclock_index_near(struct vclock *c, double t)
{
    EnterCriticalSection(&c->cs);
    int64_t r = (int64_t) llround((t - c->base) / c->period);
    LeaveCriticalSection(&c->cs);
    return r;
}

double vclock_time_of(struct vclock *c, int64_t i)
{
    EnterCriticalSection(&c->cs);
    double r = c->base + (double) i * c->period;
    LeaveCriticalSection(&c->cs);
    return r;
}

void vclock_line(struct vclock *c, double *base, double *period)
{
    EnterCriticalSection(&c->cs);
    *base = c->base;
    *period = c->period;
    LeaveCriticalSection(&c->cs);
}

double *vclock_copy_log(struct vclock *c, size_t *n)
{
    EnterCriticalSection(&c->cs);
    double *r = malloc((c->n ? c->n : 1) * sizeof(double));
    if (r && c->n)
        memcpy(r, c->log, c->n * sizeof(double));
    *n = r ? c->n : 0;
    LeaveCriticalSection(&c->cs);
    return r;
}
