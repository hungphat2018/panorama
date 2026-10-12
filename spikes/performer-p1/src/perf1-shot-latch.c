// Per-shot latch, see perf1-shot-latch.h. Used from the render thread only.
#include "perf1-shot-latch.h"

#include <math.h>
#include <string.h>

void shot_latch_init(struct shot_latch *l)
{
    memset(l, 0, sizeof(*l));
}

void shot_latch_reset(struct shot_latch *l)
{
    l->n = 0;
    l->head = 0;
}

static const struct latch_entry *ring_at(const struct shot_latch *l, int age)
{
    return &l->ring[(l->head - age + LATCH_RING) % LATCH_RING];
}

struct latch_result shot_latch_resolve(struct shot_latch *l, struct cue_store *st, double pts)
{
    for (int age = 0; age < l->n; age++) {
        const struct latch_entry *e = ring_at(l, age);
        if (pts >= e->start && pts < e->end)
            return (struct latch_result) { .entry = e };
    }

    // A shot nobody has shown: latch the version that is current now.
    const struct cue_sheet *sheet = cue_store_current(st);
    int shot = cue_sheet_find_shot(sheet, pts);
    struct latch_entry fresh = {
        .sheet = sheet,
        .shot = shot,
        .start = sheet->shots[shot].start,
        .end = cue_sheet_shot_end(sheet, shot),
    };
    if (l->n > 0) {
        const struct latch_entry *newest = ring_at(l, 0);
        if (pts < newest->start) {
            // Behind everything we remember (more than LATCH_RING shots late): answer from the current version
            // without latching, and let the caller count it as a violation.
            l->stale_lookups++;
            l->scratch = fresh;
            l->scratch.id = UINT32_MAX;
            return (struct latch_result) { .entry = &l->scratch, .stale = true };
        }
        if (fresh.start < newest->end)
            fresh.start = newest->end; // spans never overlap
    }
    fresh.id = l->next_id++;
    l->head = (l->head + 1) % LATCH_RING;
    l->ring[l->head] = fresh;
    if (l->n < LATCH_RING)
        l->n++;
    l->captures++;
    return (struct latch_result) { .entry = &l->ring[l->head], .captured = true };
}
