// Pass timing capture and sample statistics, see pp-timing.h.
#include "pp-timing.h"

#include <stdlib.h>
#include <string.h>
#include <windows.h>

#include "pp-util.h"

double pp_now_ms(void)
{
    static LARGE_INTEGER freq;
    LARGE_INTEGER t;
    if (!freq.QuadPart)
        QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&t);
    return 1000.0 * (double) t.QuadPart / (double) freq.QuadPart;
}

static int cmp_double(const void *a, const void *b)
{
    double x = *(const double *) a, y = *(const double *) b;
    return (x > y) - (x < y);
}

void pp_sort_doubles(double *v, int n)
{
    qsort(v, (size_t) n, sizeof(double), cmp_double);
}

double pp_percentile_sorted(double *v, int n, double p)
{
    if (n <= 0)
        return 0.0;
    double pos = p / 100.0 * (n - 1);
    int lo = (int) pos;
    int hi = lo + 1 < n ? lo + 1 : lo;
    return v[lo] + (v[hi] - v[lo]) * (pos - lo);
}

void pp_series_add(struct pp_series *s, double x)
{
    if (s->n == s->cap) {
        s->cap = s->cap ? s->cap * 2 : 1024;
        s->v = pp_xrealloc(s->v, (size_t) s->cap * sizeof(double));
    }
    s->v[s->n++] = x;
}

void pp_series_free(struct pp_series *s)
{
    free(s->v);
    memset(s, 0, sizeof(*s));
}

void pp_series_stats(const struct pp_series *s, double *p50, double *p99, double *min, double *max, double *mean)
{
    *p50 = *p99 = *min = *max = *mean = 0.0;
    if (s->n <= 0)
        return;
    double *c = pp_xrealloc(NULL, (size_t) s->n * sizeof(double));
    memcpy(c, s->v, (size_t) s->n * sizeof(double));
    pp_sort_doubles(c, s->n);
    double sum = 0.0;
    for (int i = 0; i < s->n; i++)
        sum += c[i];
    *p50 = pp_percentile_sorted(c, s->n, 50.0);
    *p99 = pp_percentile_sorted(c, s->n, 99.0);
    *min = c[0];
    *max = c[s->n - 1];
    *mean = sum / s->n;
    free(c);
}

enum pp_pass_kind pp_classify_pass(const char *d)
{
    // Our own passes label themselves "pp-resample ..." / "pp-prepare ..." / "pp-blend ...".
    if (strstr(d, "pp-resample"))
        return PP_PASS_RESAMPLE;
    if (strstr(d, "noop scaling"))
        return PP_PASS_OTHER;
    // A 4:2:0 source: libplacebo scales the chroma planes in the pass that also decodes the colour (YUV -> RGB).
    if (strstr(d, "scaling (") && strstr(d, "color decoding"))
        return PP_PASS_CHROMA;
    if (strstr(d, "scaling (") || strstr(d, "bicubic") || strstr(d, "hermite") || strstr(d, "gaussian") ||
        strstr(d, "bilinear") || strstr(d, "nearest") || strstr(d, "distortion"))
        return PP_PASS_RESAMPLE;
    return PP_PASS_OTHER;
}

void pp_timing_reset(struct pp_timing *t)
{
    t->npass = 0;
}

void pp_timing_add(struct pp_timing *t, const char *desc, unsigned long long ns, int mon)
{
    if (t->npass >= PP_MAX_PASSES)
        return;
    struct pp_pass_rec *r = &t->pass[t->npass++];
    snprintf(r->desc, sizeof(r->desc), "%s", desc ? desc : "?");
    r->ms = (double) ns / 1e6;
    r->kind = pp_classify_pass(r->desc);
    r->mon = mon;
}

void pp_timing_render_cb(void *priv, const struct pl_render_info *info)
{
    struct pp_timing *t = priv;
    pp_timing_add(t, info->pass->shader->description, info->pass->last, t->cur_mon);
}

void pp_timing_dispatch_cb(void *priv, const struct pl_dispatch_info *info)
{
    struct pp_timing *t = priv;
    pp_timing_add(t, info->shader->description, info->last, t->cur_mon);
}

double pp_timing_mon_ms(const struct pp_timing *t, int mon)
{
    double sum = 0.0;
    for (int i = 0; i < t->npass; i++)
        if (t->pass[i].mon == mon)
            sum += t->pass[i].ms;
    return sum;
}

int pp_timing_mon_count(const struct pp_timing *t, int mon, enum pp_pass_kind kind)
{
    int n = 0;
    for (int i = 0; i < t->npass; i++)
        n += t->pass[i].mon == mon && t->pass[i].kind == kind;
    return n;
}

double pp_timing_total_ms(const struct pp_timing *t)
{
    double sum = 0.0;
    for (int i = 0; i < t->npass; i++)
        sum += t->pass[i].ms;
    return sum;
}

int pp_timing_count(const struct pp_timing *t, enum pp_pass_kind kind)
{
    int n = 0;
    for (int i = 0; i < t->npass; i++)
        n += t->pass[i].kind == kind;
    return n;
}
