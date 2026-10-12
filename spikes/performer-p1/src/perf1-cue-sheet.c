// Cue sheet loader, validation, lookup and the version store. See perf1-cue-sheet.h for the file format.
#include "perf1-cue-sheet.h"

#include <math.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

// ---- loading -----------------------------------------------------------------------------------------------

struct load_ctx {
    struct cue_sheet *sheet;
    double src_w, src_h;
    char *err;
    size_t err_size;
};

// Records the message and returns false, so a validation step can `return load_fail(...)`.
static bool load_fail(struct load_ctx *c, const char *fmt, ...) __attribute__((format(gnu_printf, 2, 3)));
static bool load_fail(struct load_ctx *c, const char *fmt, ...)
{
    if (c->err && c->err_size) {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(c->err, c->err_size, fmt, ap);
        va_end(ap);
    }
    return false;
}

static void note_unknown(struct load_ctx *c, const struct json_value *obj, const char *const *known)
{
    for (size_t i = 0; i < obj->n; i++) {
        bool ok = false;
        for (const char *const *k = known; *k; k++)
            if (!strcmp(obj->keys[i], *k))
                ok = true;
        if (!ok) {
            if (!c->sheet->ignored_keys++)
                snprintf(c->sheet->first_ignored_key, sizeof(c->sheet->first_ignored_key), "%s", obj->keys[i]);
        }
    }
}

static bool get_finite(const struct json_value *v, double *out)
{
    if (!v || v->type != JSON_NUMBER || !isfinite(v->num))
        return false;
    *out = v->num + 0.0; // turns -0.0 into +0.0, so a fingerprint does not depend on the sign of zero
    return true;
}

static char *dup_string_member(const struct json_value *obj, const char *key)
{
    const struct json_value *v = json_member(obj, key);
    return v && v->type == JSON_STRING ? strdup(v->str) : NULL;
}

static bool load_shot(struct load_ctx *c, size_t idx, const struct json_value *js, struct cue_shot *shot)
{
    static const char *const known[] = { "start", "crop", "placement", "slide", "subtitle_monitor", "surround", "time", NULL };
    if (js->type != JSON_OBJECT)
        return load_fail(c, "shots[%zu]: an object is expected", idx);
    note_unknown(c, js, known);
    shot->subtitle_monitor = -1;

    if (!get_finite(json_member(js, "start"), &shot->start))
        return load_fail(c, "shots[%zu].start: a number is expected", idx);

    const struct json_value *crop = json_member(js, "crop");
    if (crop && crop->type != JSON_NULL) {
        if (crop->type != JSON_ARRAY || crop->n != 4)
            return load_fail(c, "shots[%zu].crop: [x0, y0, x1, y1] is expected", idx);
        for (int i = 0; i < 4; i++)
            if (!get_finite(&crop->items[i], &shot->crop[i]))
                return load_fail(c, "shots[%zu].crop[%d]: a number is expected", idx, i);
        if (!(shot->crop[0] < shot->crop[2]) || !(shot->crop[1] < shot->crop[3]))
            return load_fail(c, "shots[%zu].crop: x1 > x0 and y1 > y0 are required", idx);
        if (shot->crop[0] < 0 || shot->crop[1] < 0 || shot->crop[2] > c->src_w || shot->crop[3] > c->src_h)
            return load_fail(c, "shots[%zu].crop [%g, %g, %g, %g] is outside the %gx%g source", idx, shot->crop[0],
                             shot->crop[1], shot->crop[2], shot->crop[3], c->src_w, c->src_h);
        shot->has_crop = true;
    }

    const struct json_value *pl = json_member(js, "placement");
    if (pl && pl->type != JSON_NULL) {
        static const char *const known_pl[] = { "center_mm", "height_mm", NULL };
        if (pl->type != JSON_OBJECT)
            return load_fail(c, "shots[%zu].placement: an object is expected", idx);
        note_unknown(c, pl, known_pl);
        const struct json_value *ctr = json_member(pl, "center_mm");
        if (!ctr || ctr->type != JSON_ARRAY || ctr->n != 2 || !get_finite(&ctr->items[0], &shot->center_u_mm) ||
            !get_finite(&ctr->items[1], &shot->center_v_mm))
            return load_fail(c, "shots[%zu].placement.center_mm: [u, v] in millimetres is expected", idx);
        if (!get_finite(json_member(pl, "height_mm"), &shot->height_mm) || !(shot->height_mm > 0.0))
            return load_fail(c, "shots[%zu].placement.height_mm: a positive number is expected", idx);
        shot->has_place = true;
    }

    const struct json_value *sl = json_member(js, "slide");
    if (sl && sl->type != JSON_NULL) {
        if (sl->type != JSON_ARRAY || sl->n < 2 || sl->n > CUE_MAX_SLIDE_POINTS)
            return load_fail(c, "shots[%zu].slide: [[t, du, dv], ...] with 2 to %d points is expected", idx,
                             CUE_MAX_SLIDE_POINTS);
        shot->slide = calloc(sl->n, sizeof(*shot->slide));
        if (!shot->slide)
            return load_fail(c, "out of memory");
        shot->slide_n = sl->n;
        for (size_t i = 0; i < sl->n; i++) {
            const struct json_value *pt = &sl->items[i];
            if (pt->type != JSON_ARRAY || pt->n != 3 || !get_finite(&pt->items[0], &shot->slide[i].t) ||
                !get_finite(&pt->items[1], &shot->slide[i].du) || !get_finite(&pt->items[2], &shot->slide[i].dv))
                return load_fail(c, "shots[%zu].slide[%zu]: [t, du, dv] with finite numbers is expected", idx, i);
            if (i && !(shot->slide[i].t > shot->slide[i - 1].t))
                return load_fail(c, "shots[%zu].slide[%zu]: t %.9g does not exceed the previous t %.9g", idx, i,
                                 shot->slide[i].t, shot->slide[i - 1].t);
        }
    }

    const struct json_value *sm = json_member(js, "subtitle_monitor");
    if (sm && sm->type != JSON_NULL) {
        if (!json_is_int_in(sm, 0, 15))
            return load_fail(c, "shots[%zu].subtitle_monitor: a monitor index (0..15) is expected", idx);
        shot->subtitle_monitor = (int) sm->num;
    }
    const struct json_value *su = json_member(js, "surround");
    if (su && su->type != JSON_NULL && !(shot->surround = json_clone(su)))
        return load_fail(c, "out of memory");
    const struct json_value *tm = json_member(js, "time");
    if (tm && tm->type != JSON_NULL && !(shot->time_treatment = json_clone(tm)))
        return load_fail(c, "out of memory");
    return true;
}

void cue_sheet_free(struct cue_sheet *s)
{
    if (!s)
        return;
    for (size_t i = 0; i < s->n_shots; i++) {
        free(s->shots[i].slide);
        json_free(s->shots[i].surround);
        json_free(s->shots[i].time_treatment);
    }
    free(s->shots);
    free(s->file_hash);
    free(s->room_signature);
    free(s->preferences_hash);
    free(s->source);
    free(s);
}

struct cue_sheet *cue_sheet_parse(const char *text, size_t len, double src_w, double src_h, const char *source,
                                  char *err, size_t err_size)
{
    static const char *const known[] = { "schema", "version", "file_hash", "room_signature", "preferences_hash",
                                         "shots", NULL };
    char jerr[200];
    struct json_value *root = json_parse(text, len, jerr, sizeof(jerr));
    if (!root) {
        if (err && err_size)
            snprintf(err, err_size, "%s: not valid JSON: %s", source ? source : "cue sheet", jerr);
        return NULL;
    }
    struct cue_sheet *s = calloc(1, sizeof(*s));
    struct load_ctx c = { .sheet = s, .src_w = src_w, .src_h = src_h, .err = err, .err_size = err_size };
    if (!s) {
        json_free(root);
        load_fail(&c, "out of memory");
        return NULL;
    }
    s->source = strdup(source ? source : "(memory)");
    bool ok = false;
    do {
        if (root->type != JSON_OBJECT) {
            load_fail(&c, "%s: the top level must be an object", s->source);
            break;
        }
        note_unknown(&c, root, known);
        const struct json_value *schema = json_member(root, "schema");
        if (!schema || schema->type != JSON_STRING || strcmp(schema->str, CUE_SCHEMA)) {
            load_fail(&c, "%s: \"schema\" must be \"" CUE_SCHEMA "\"", s->source);
            break;
        }
        const struct json_value *ver = json_member(root, "version");
        if (!json_is_int_in(ver, 1, 2147483647.0)) {
            load_fail(&c, "%s: \"version\" must be an integer from 1", s->source);
            break;
        }
        s->version = (int) ver->num;
        s->file_hash = dup_string_member(root, "file_hash");
        s->room_signature = dup_string_member(root, "room_signature");
        s->preferences_hash = dup_string_member(root, "preferences_hash");

        const struct json_value *shots = json_member(root, "shots");
        if (!shots || shots->type != JSON_ARRAY || shots->n == 0 || shots->n > CUE_MAX_SHOTS) {
            load_fail(&c, "%s: \"shots\" must be a non-empty array of at most %d shots", s->source, CUE_MAX_SHOTS);
            break;
        }
        s->shots = calloc(shots->n, sizeof(*s->shots));
        if (!s->shots) {
            load_fail(&c, "out of memory");
            break;
        }
        s->n_shots = 0;
        bool shots_ok = true;
        for (size_t i = 0; i < shots->n && shots_ok; i++) {
            s->n_shots = i + 1; // so cue_sheet_free releases what was loaded so far
            shots_ok = load_shot(&c, i, &shots->items[i], &s->shots[i]);
            if (shots_ok && i > 0 && !(s->shots[i].start > s->shots[i - 1].start)) {
                load_fail(&c, "shots[%zu].start %.9g does not exceed the previous start %.9g (starts must increase)", i,
                          s->shots[i].start, s->shots[i - 1].start);
                shots_ok = false;
            }
        }
        if (!shots_ok)
            break;
        if (s->shots[0].start > 0.0) {
            load_fail(&c, "shots[0].start is %.9g: the first shot must start at or before 0 so every frame has a shot",
                      s->shots[0].start);
            break;
        }
        ok = true;
    } while (0);
    json_free(root);
    if (!ok) {
        if (err && err_size && !err[0])
            snprintf(err, err_size, "%s: invalid cue sheet", s->source);
        cue_sheet_free(s);
        return NULL;
    }
    return s;
}

struct cue_sheet *cue_sheet_load_file(const char *path, double src_w, double src_h, char *err, size_t err_size)
{
    FILE *f = fopen(path, "rb");
    if (!f) {
        if (err && err_size)
            snprintf(err, err_size, "cannot open cue sheet %s", path);
        return NULL;
    }
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (size < 0 || (unsigned long) size > CUE_MAX_FILE_BYTES) {
        fclose(f);
        if (err && err_size)
            snprintf(err, err_size, "cue sheet %s is empty or larger than %u bytes", path, CUE_MAX_FILE_BYTES);
        return NULL;
    }
    char *buf = malloc((size_t) size + 1);
    if (!buf) {
        fclose(f);
        if (err && err_size)
            snprintf(err, err_size, "out of memory reading %s", path);
        return NULL;
    }
    size_t got = fread(buf, 1, (size_t) size, f);
    fclose(f);
    if (got != (size_t) size) {
        free(buf);
        if (err && err_size)
            snprintf(err, err_size, "short read of cue sheet %s", path);
        return NULL;
    }
    struct cue_sheet *s = cue_sheet_parse(buf, got, src_w, src_h, path, err, err_size);
    free(buf);
    return s;
}

struct cue_sheet *cue_sheet_default(void)
{
    struct cue_sheet *s = calloc(1, sizeof(*s));
    if (!s)
        return NULL;
    s->shots = calloc(1, sizeof(*s->shots));
    s->source = strdup("built-in default");
    if (!s->shots || !s->source) {
        cue_sheet_free(s);
        return NULL;
    }
    s->n_shots = 1;
    s->shots[0].subtitle_monitor = -1;
    return s;
}

// ---- lookup ------------------------------------------------------------------------------------------------

int cue_sheet_find_shot(const struct cue_sheet *s, double pts)
{
    size_t lo = 0, hi = s->n_shots; // the answer is in [lo, hi): last start <= pts
    if (!(pts >= s->shots[0].start))
        return 0;
    while (hi - lo > 1) {
        size_t mid = lo + (hi - lo) / 2;
        if (s->shots[mid].start <= pts)
            lo = mid;
        else
            hi = mid;
    }
    return (int) lo;
}

double cue_sheet_shot_end(const struct cue_sheet *s, int shot)
{
    return (size_t) (shot + 1) < s->n_shots ? s->shots[shot + 1].start : INFINITY;
}

bool cue_slide_eval(const struct cue_shot *shot, double t, double *du, double *dv)
{
    *du = *dv = 0.0;
    if (shot->slide_n < 2)
        return false;
    const struct cue_slide_pt *p = shot->slide;
    size_t n = shot->slide_n;
    if (!(t > p[0].t)) { // also catches NaN: the picture waits at its first position
        *du = p[0].du;
        *dv = p[0].dv;
        return true;
    }
    if (t >= p[n - 1].t) {
        *du = p[n - 1].du;
        *dv = p[n - 1].dv;
        return true;
    }
    size_t lo = 0, hi = n - 1; // p[lo].t < t < p[hi].t
    while (hi - lo > 1) {
        size_t mid = lo + (hi - lo) / 2;
        if (p[mid].t <= t)
            lo = mid;
        else
            hi = mid;
    }
    double f = (t - p[lo].t) / (p[hi].t - p[lo].t);
    *du = p[lo].du + f * (p[hi].du - p[lo].du);
    *dv = p[lo].dv + f * (p[hi].dv - p[lo].dv);
    return true;
}

static uint32_t fnv_bytes(uint32_t h, const void *data, size_t n)
{
    const unsigned char *p = data;
    for (size_t i = 0; i < n; i++)
        h = (h ^ p[i]) * 16777619u;
    return h;
}

uint32_t cue_shot_hash(const struct cue_shot *s)
{
    uint32_t h = 2166136261u;
    unsigned char flag = s->has_crop ? 1 : 0;
    h = fnv_bytes(h, &flag, 1);
    for (int i = 0; i < 4; i++) {
        double v = s->has_crop ? s->crop[i] + 0.0 : 0.0;
        h = fnv_bytes(h, &v, sizeof(v));
    }
    flag = s->has_place ? 1 : 0;
    h = fnv_bytes(h, &flag, 1);
    const double pl[3] = { s->has_place ? s->center_u_mm + 0.0 : 0.0, s->has_place ? s->center_v_mm + 0.0 : 0.0,
                           s->has_place ? s->height_mm + 0.0 : 0.0 };
    for (int i = 0; i < 3; i++)
        h = fnv_bytes(h, &pl[i], sizeof(double));
    return h;
}

void cue_sheet_write_json(FILE *f, const struct cue_sheet *s)
{
    fprintf(f, "{\"id\":%d,\"source\":", s->version);
    json_write_string(f, s->source);
    fputs(",\"shots\":[", f);
    for (size_t i = 0; i < s->n_shots; i++) {
        const struct cue_shot *sh = &s->shots[i];
        fprintf(f, "%s{\"start\":%.17g,\"crop\":", i ? "," : "", sh->start);
        if (sh->has_crop)
            fprintf(f, "[%.17g,%.17g,%.17g,%.17g]", sh->crop[0], sh->crop[1], sh->crop[2], sh->crop[3]);
        else
            fputs("null", f);
        fputs(",\"placement\":", f);
        if (sh->has_place)
            fprintf(f, "{\"center_mm\":[%.17g,%.17g],\"height_mm\":%.17g}", sh->center_u_mm, sh->center_v_mm,
                    sh->height_mm);
        else
            fputs("null", f);
        fputc('}', f);
    }
    fputs("]}", f);
}

// ---- the store ---------------------------------------------------------------------------------------------

static void store_lock(struct cue_store *st)
{
    while (atomic_flag_test_and_set_explicit(&st->lock, memory_order_acquire))
        ;
}

static void store_unlock(struct cue_store *st)
{
    atomic_flag_clear_explicit(&st->lock, memory_order_release);
}

bool cue_store_init(struct cue_store *st)
{
    memset(st->all, 0, sizeof(st->all));
    st->n = 0;
    atomic_flag_clear(&st->lock);
    struct cue_sheet *def = cue_sheet_default();
    if (!def)
        return false;
    st->all[st->n++] = def;
    atomic_store_explicit(&st->current, def, memory_order_release);
    return true;
}

void cue_store_destroy(struct cue_store *st)
{
    atomic_store_explicit(&st->current, NULL, memory_order_release);
    for (size_t i = 0; i < st->n; i++)
        cue_sheet_free((struct cue_sheet *) st->all[i]);
    st->n = 0;
}

bool cue_store_publish(struct cue_store *st, struct cue_sheet *sheet, char *err, size_t err_size)
{
    bool ok = false;
    store_lock(st);
    const struct cue_sheet *cur = atomic_load_explicit(&st->current, memory_order_relaxed);
    if (st->n >= CUE_MAX_VERSIONS) {
        if (err && err_size)
            snprintf(err, err_size, "at most %d cue sheet versions per run", CUE_MAX_VERSIONS);
    } else if (cur && sheet->version <= cur->version) {
        if (err && err_size)
            snprintf(err, err_size, "version %d does not exceed the current version %d", sheet->version, cur->version);
    } else {
        st->all[st->n++] = sheet;
        atomic_store_explicit(&st->current, sheet, memory_order_release);
        ok = true;
    }
    store_unlock(st);
    return ok;
}

const struct cue_sheet *cue_store_current(struct cue_store *st)
{
    return atomic_load_explicit(&st->current, memory_order_acquire);
}

const struct cue_sheet *cue_store_find(struct cue_store *st, int version)
{
    const struct cue_sheet *found = NULL;
    store_lock(st);
    for (size_t i = 0; i < st->n && !found; i++)
        if (st->all[i]->version == version)
            found = st->all[i];
    store_unlock(st);
    return found;
}

size_t cue_store_count(struct cue_store *st)
{
    store_lock(st);
    size_t n = st->n;
    store_unlock(st);
    return n;
}
