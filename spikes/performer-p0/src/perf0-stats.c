// Time base, percentile math and sample buffers for perf0. CC0 / public domain.
#include "perf0.h"

#include <math.h>

double now_ms(void)
{
    static LARGE_INTEGER freq;
    if (!freq.QuadPart)
        QueryPerformanceFrequency(&freq);
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return 1e3 * (double) t.QuadPart / (double) freq.QuadPart;
}

// Wall time since the process was created: DLL loading before main() counts,
// which is what a user waiting for the first picture actually experiences.
double process_age_ms(void)
{
    FILETIME created, exited, kernel, user, now;
    if (!GetProcessTimes(GetCurrentProcess(), &created, &exited, &kernel, &user))
        return 0.0;
    GetSystemTimePreciseAsFileTime(&now);
    ULARGE_INTEGER c = { .LowPart = created.dwLowDateTime, .HighPart = created.dwHighDateTime };
    ULARGE_INTEGER n = { .LowPart = now.dwLowDateTime, .HighPart = now.dwHighDateTime };
    return (double) (n.QuadPart - c.QuadPart) / 1e4; // 100 ns units
}

static int cmp_double(const void *a, const void *b)
{
    double x = *(const double *) a, y = *(const double *) b;
    return (x > y) - (x < y);
}

// Nearest-rank percentile on a sorted array.
static double pct(const double *sorted, size_t n, double q)
{
    size_t idx = (size_t) ceil(q * (double) n);
    if (idx < 1)
        idx = 1;
    if (idx > n)
        idx = n;
    return sorted[idx - 1];
}

void summarize(const double *v, size_t n, struct summary *out)
{
    memset(out, 0, sizeof(*out));
    if (!n)
        return;

    double *t = malloc(n * sizeof(double));
    if (!t)
        return;
    memcpy(t, v, n * sizeof(double));
    qsort(t, n, sizeof(double), cmp_double);

    double sum = 0.0;
    for (size_t i = 0; i < n; i++)
        sum += t[i];

    out->n    = n;
    out->min  = t[0];
    out->mean = sum / (double) n;
    out->p50  = pct(t, n, 0.50);
    out->p90  = pct(t, n, 0.90);
    out->p99  = pct(t, n, 0.99);
    out->p999 = pct(t, n, 0.999);
    out->max  = t[n - 1];
    free(t);
}

void samples_push(struct samples *s, double x)
{
    if (s->n == s->cap) {
        size_t cap = s->cap ? s->cap * 2 : 4096;
        double *v = realloc(s->v, cap * sizeof(double));
        if (!v)
            return; // dropping a sample is better than aborting a timed run
        s->v = v;
        s->cap = cap;
    }
    s->v[s->n++] = x;
}

void samples_free(struct samples *s)
{
    free(s->v);
    *s = (struct samples) {0};
}

// Writes a JSON string literal (with quotes), escaping what JSON requires.
void json_string(FILE *f, const char *s)
{
    fputc('"', f);
    for (; s && *s; s++) {
        unsigned char c = (unsigned char) *s;
        if (c == '"' || c == '\\')
            fprintf(f, "\\%c", c);
        else if (c < 0x20)
            fprintf(f, "\\u%04x", c);
        else
            fputc(c, f);
    }
    fputc('"', f);
}
