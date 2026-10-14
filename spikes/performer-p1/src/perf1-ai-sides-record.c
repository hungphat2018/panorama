// The AI sides' player record (see perf1-ai-sides.h): what the player tells the worker through the store's player block:
// the playhead, the seek counter, the GPU pressure the worker's painter must yield to, the side area the room needs, and
// what is on screen.
#include "perf1-ai-sides-internal.h"

#include "perf1-picture-map.h"

#define LEAD_WINDOW_S 0.5
#define MARGIN_STEP_PX 16

// ---- the room's side widths ----------------------------------------------------------------------------------------

struct hash_index {
    uint32_t hash;
    uint32_t index;
};

static int cmp_hash(const void *a, const void *b)
{
    uint32_t x = ((const struct hash_index *) a)->hash, y = ((const struct hash_index *) b)->hash;
    return x < y ? -1 : x > y;
}

// How far leftover pixels reach beyond the crop (source px, left/top/right/bottom), the largest over the cue sheet's
// layouts (shots with the same crop and placement counted once) and the monitors: the side area the worker must cover.
// Monitor borders are sampled every MARGIN_STEP_PX through pass 1's own map. Runs when the sheet in force changes: in the
// warm-up frame for the first one, before the film plays.
void ai_compute_margins(void)
{
    const struct cue_sheet *sheet = cue_store_current(&A.store);
    if (!sheet || sheet == G_AI.margin_sheet || !sheet->n_shots)
        return;
    struct hash_index *hx = malloc(sheet->n_shots * sizeof(*hx));
    if (!hx)
        return;
    for (size_t s = 0; s < sheet->n_shots; s++)
        hx[s] = (struct hash_index) { cue_shot_hash(&sheet->shots[s]), (uint32_t) s };
    qsort(hx, sheet->n_shots, sizeof(*hx), cmp_hash);
    float mg[4] = { 0 };
    for (size_t k = 0; k < sheet->n_shots; k++) {
        if (k && hx[k].hash == hx[k - 1].hash)
            continue;
        struct lay_picture lp;
        layout_shot(&sheet->shots[hx[k].index], A.src_w, A.src_h, A.sar, A.lay_mon, A.nmon, &A.lay_def, &lp);
        struct pic_view v;
        memcpy(v.crop, lp.crop, sizeof(v.crop));
        memcpy(v.rect, lp.rect_mm, sizeof(v.rect));
        for (int m = 0; m < A.nmon; m++) {
            const struct room_geom *g = picture_geom(m);
            if (!g)
                continue;
            for (int e = 0; e < 4; e++) {
                int along = e < 2 ? g->px_w : g->px_h;
                for (int q = 0; q <= along; q += MARGIN_STEP_PX) {
                    double t = (q < along ? q : along - 1) + 0.5;
                    double px = e < 2 ? t : (e == 2 ? 0.5 : g->px_w - 0.5);
                    double py = e < 2 ? (e == 0 ? 0.5 : g->px_h - 0.5) : t;
                    double sx, sy;
                    pic_map_pixel(g, &v, NULL, px, py, &sx, &sy);
                    float d[4] = { (float) (v.crop[0] - sx), (float) (v.crop[1] - sy), (float) (sx - v.crop[2]),
                                   (float) (sy - v.crop[3]) };
                    for (int j = 0; j < 4; j++)
                        if (d[j] > mg[j])
                            mg[j] = d[j];
                }
            }
        }
    }
    free(hx);
    memcpy(G_AI.margins, mg, sizeof(mg));
    G_AI.margins_valid = true;
    G_AI.margin_sheet = sheet;
    printf("ai sides: the room needs sides of %.0f / %.0f / %.0f / %.0f source px (left / top / right / bottom)\n", mg[0], mg[1],
           mg[2], mg[3]);
}

// ---- pressure --------------------------------------------------------------------------------------------------------

// Present slack (the smallest lead of a present before its refresh, over the last half second) and missed refreshes
// (frame statistics: the display's refresh count grew more than the present count between two readings).
void ai_pressure_update(double now)
{
    static double lead_min = 1e9;
    for (int i = 0; i < A.nmon; i++) {
        struct monitor *m = &A.mon[i];
        if (m->n_presents) {
            const struct present_rec *p = &m->presents[m->n_presents - 1];
            double lead = (p->target - p->call) * 1e3;
            if (lead < lead_min)
                lead_min = lead;
        }
        for (size_t j = G_AI.stats_seen[i]; j < m->n_stats; j++) {
            if (j == 0)
                continue;
            const struct stat_rec *s = &m->stats[j], *q = &m->stats[j - 1];
            uint32_t dp = s->present_count - q->present_count, dr = s->present_refresh - q->present_refresh;
            if (dp > 0 && dr > dp && dr - dp < 1000) {
                for (uint32_t k = 0; k < dr - dp; k++)
                    G_AI.miss_t[(G_AI.miss_n++) % 64] = now;
                G_AI.late_total += dr - dp;
            }
        }
        G_AI.stats_seen[i] = m->n_stats;
    }
    if (now - G_AI.lead_window_t > LEAD_WINDOW_S) {
        G_AI.lead_min_ms = lead_min < 1e8 ? lead_min : 0.0;
        lead_min = 1e9;
        G_AI.lead_window_t = now;
    }
}

static uint32_t late_recent(double now)
{
    uint32_t n = 0;
    for (int k = 0; k < 64 && k < G_AI.miss_n; k++)
        n += now - G_AI.miss_t[k] < 1.0;
    return n;
}

static uint32_t player_state(void)
{
    switch (A.engine.state) {
    case ES_PLAYING: return AI_PLAYER_PLAYING;
    case ES_PAUSED: return AI_PLAYER_PAUSED;
    case ES_SEEK_PARK:
    case ES_SEEK_FILL: return AI_PLAYER_SEEKING;
    default: return AI_PLAYER_NONE;
    }
}

// ---- the record ------------------------------------------------------------------------------------------------------

void ai_player_record(double now, const struct pic_refresh *r, const struct ai_showing *sh)
{
    LARGE_INTEGER q;
    QueryPerformanceCounter(&q);
    volatile struct ai_player_block *p = ai_store_player_begin(&G_AI.store);
    p->pid = GetCurrentProcessId();
    p->beat++;
    p->qpc = q.QuadPart;
    p->state = player_state();
    p->epoch = r->epoch;
    p->playhead_s = engine_playhead(&A.engine);
    p->speed = p->state == AI_PLAYER_PLAYING && A.master_period > 0.0 ? A.step_s / A.master_period : 0.0;
    p->slack_ms = (float) G_AI.lead_min_ms;
    p->late_recent = late_recent(now);
    p->late_total = G_AI.late_total;
    for (int i = 0; i < 4; i++)
        p->need_margin_px[i] = G_AI.margins[i];
    p->need_valid = G_AI.margins_valid;
    p->src_w = (uint32_t) A.src_w;
    p->src_h = (uint32_t) A.src_h;
    const struct ai_gpu_canvas *up = G_AI.uploading >= 0 ? &G_AI.gpu[G_AI.uploading] : NULL;
    p->uploading_slot = up ? up->heap_slot : -1;
    p->uploading_gen = up ? up->heap_gen : 0;
    p->showing_shot = sh ? sh->shot : -1;
    p->showing_tier = sh && sh->gpu >= 0 ? sh->tier : 0;
    p->showing_canvas = sh && sh->gpu >= 0 ? (int32_t) sh->canvas_id : -1;
    uint32_t n = 0;
    for (int i = 0; i < AIC.gpu_canvases && n < 16; i++)
        if (G_AI.gpu[i].complete && G_AI.gpu[i].kind == AI_KIND_CANVAS)    // patch pages are not counted
            p->resident_canvas[n++] = (int32_t) G_AI.gpu[i].canvas_id;
    p->resident_n = n;
    p->uploads_done = (uint32_t) G_AI.uploads;
    p->uploads_torn = (uint32_t) G_AI.uploads_torn;
    ai_store_player_end(&G_AI.store);
}
