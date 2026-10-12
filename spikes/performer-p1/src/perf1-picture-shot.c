// The shot plan: what a latched shot means for the picture, worked out once per shot and kept while the latch can still
// be asked for it (a monitor can draw the previous shot's last frame after the other has started the next).
#include "perf1-picture.h"

static struct pic_shot g_shots[2 * LATCH_RING];
static struct pic_shot g_scratch;

const struct pic_shot *picture_shot(uint32_t id, const struct cue_shot *cue)
{
    struct pic_shot *s = id == UINT32_MAX ? &g_scratch : &g_shots[id % (2 * LATCH_RING)];
    if (id != UINT32_MAX && s->valid && s->id == id)
        return s;
    memset(s, 0, sizeof(*s));
    s->id = id;
    s->cue = cue;
    layout_shot(cue, A.src_w, A.src_h, A.sar, A.lay_mon, A.nmon, &A.lay_def, &s->lay);
    memcpy(s->view.crop, s->lay.crop, sizeof(s->view.crop));
    memcpy(s->view.rect, s->lay.rect_mm, sizeof(s->view.rect));
    for (int k = 0; k < A.nmon; k++)
        s->upscales[k] = pic_monitor_upscales(picture_geom(k), &s->view);
    pic_look_default(&s->look, PC.surround_glow);
    char err[200] = "";
    if (!pic_look_from_cue(&s->look, cue->surround, err, sizeof(err))) {
        fprintf(stderr, "perf1: the shot at %.3f s: %s; its surround keeps the defaults\n", cue->start, err);
        pic_look_default(&s->look, PC.surround_glow);
    }
    s->valid = true;
    return s;
}

const struct pic_shot *picture_shot_of_frame(double pts)
{
    const struct shot_latch *l = &A.engine.latch;
    for (int i = 0; i < l->n; i++) {
        const struct latch_entry *e = &l->ring[(l->head - i + LATCH_RING) % LATCH_RING];
        if (pts >= e->start && pts < e->end)
            return picture_shot(e->id, &e->sheet->shots[e->shot]);
    }
    return NULL;
}
