// Timestamps of the frames the supply has queued in every monitor's queue, in presentation order, for the
// current generation. The engine picks frames from this list; the decoder (or the simulation) appends to it.
#include "perf1-engine.h"

#include <stdlib.h>
#include <string.h>

void findex_init(struct frame_index *f)
{
    memset(f, 0, sizeof(*f));
    InitializeCriticalSection(&f->cs);
}

void findex_destroy(struct frame_index *f)
{
    DeleteCriticalSection(&f->cs);
    free(f->pts);
    memset(f, 0, sizeof(*f));
}

void findex_reset(struct frame_index *f, int gen)
{
    EnterCriticalSection(&f->cs);
    f->n = 0;
    f->gen = gen;
    LeaveCriticalSection(&f->cs);
}

bool findex_append(struct frame_index *f, int gen, double pts)
{
    bool ok = false;
    EnterCriticalSection(&f->cs);
    if (gen != f->gen) {
        f->rejected_stale++;
    } else if (f->n && !(pts > f->pts[f->n - 1])) {
        f->rejected_order++;
    } else {
        if (f->n == f->cap) {
            size_t cap = f->cap ? f->cap * 2 : 4096;
            double *p = realloc(f->pts, cap * sizeof(double));
            if (p) {
                f->pts = p;
                f->cap = cap;
            }
        }
        if (f->n < f->cap) {
            f->pts[f->n++] = pts;
            ok = true;
        }
    }
    LeaveCriticalSection(&f->cs);
    return ok;
}

size_t findex_count(struct frame_index *f)
{
    EnterCriticalSection(&f->cs);
    size_t n = f->n;
    LeaveCriticalSection(&f->cs);
    return n;
}

bool findex_get(struct frame_index *f, size_t ord, double *pts)
{
    bool ok = false;
    EnterCriticalSection(&f->cs);
    if (ord < f->n) {
        *pts = f->pts[ord];
        ok = true;
    }
    LeaveCriticalSection(&f->cs);
    return ok;
}

double *findex_copy(struct frame_index *f, size_t *n)
{
    EnterCriticalSection(&f->cs);
    double *r = malloc((f->n ? f->n : 1) * sizeof(double));
    if (r && f->n)
        memcpy(r, f->pts, f->n * sizeof(double));
    *n = r ? f->n : 0;
    LeaveCriticalSection(&f->cs);
    return r;
}
