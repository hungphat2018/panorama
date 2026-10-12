// Scenario loading, see pp-scenario.h.
#include "pp-scenario.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "pp-json.h"
#include "pp-util.h"

static int monitor_index(const struct pp_room *r, const char *id)
{
    for (int k = 0; k < r->nmon; k++)
        if (!strcmp(r->mon[k].id, id))
            return k;
    return -1;
}

#define FAIL(...) do { snprintf(err, errsz, __VA_ARGS__); pp_json_free(root); free(text); return false; } while (0)

bool pp_scenario_load(struct pp_scenario *s, const char *path, char *err, size_t errsz)
{
    memset(s, 0, sizeof(*s));
    snprintf(s->path, sizeof(s->path), "%s", path);
    char *text = pp_read_file(path, NULL);
    struct pp_json *root = pp_json_parse(text, err, errsz);
    if (!root) {
        free(text);
        return false;
    }
    const char *room_path = pp_json_str(pp_json_get(root, "room"), NULL);
    if (!room_path)
        FAIL("%s: no room", path);
    if (!pp_room_load(&s->room, room_path, err, errsz)) {
        pp_json_free(root);
        free(text);
        return false;
    }
    const struct pp_json *surf = pp_json_get(root, "surface");
    const char *model = pp_json_str(pp_json_get(surf, "model"), "fold");
    if (!strcmp(model, "fold"))
        s->surface.model = PP_SURFACE_FOLD;
    else if (!strcmp(model, "curve"))
        s->surface.model = PP_SURFACE_CURVE;
    else
        FAIL("%s: surface.model must be fold or curve", path);
    s->surface.curvature = pp_json_num(pp_json_get(surf, "curvature"), 0.0);

    const struct pp_json *src = pp_json_get(root, "source");
    snprintf(s->source_file, sizeof(s->source_file), "%s", pp_json_str(pp_json_get(src, "file"), ""));
    s->src_w = (int) pp_json_num(pp_json_get(src, "width"), 0);
    s->src_h = (int) pp_json_num(pp_json_get(src, "height"), 0);
    s->sar = pp_json_num(pp_json_get(src, "sar"), 1.0);
    if (!s->source_file[0] || s->src_w <= 0 || s->src_h <= 0)
        FAIL("%s: source needs file, width and height", path);
    const struct pp_json *srcb = pp_json_get(root, "source_b");
    if (srcb) {
        s->has_b = true;
        snprintf(s->source_b_file, sizeof(s->source_b_file), "%s", pp_json_str(pp_json_get(srcb, "file"), ""));
    }

    const struct pp_json *cue = pp_json_get(root, "cue");
    const struct pp_json *crop = pp_json_get(cue, "crop");
    if (crop) {
        s->has_crop = true;
        for (int i = 0; i < 4; i++)
            s->crop[i] = pp_json_num(pp_json_at(crop, i), NAN);
        if (isnan(s->crop[0] + s->crop[1] + s->crop[2] + s->crop[3]) || s->crop[2] <= s->crop[0] || s->crop[3] <= s->crop[1])
            FAIL("%s: cue.crop must be x0, y0, x1, y1 with positive size", path);
    }
    s->centre_u = pp_json_num(pp_json_at(pp_json_get(cue, "centre_mm"), 0), 0.0);
    s->centre_v = pp_json_num(pp_json_at(pp_json_get(cue, "centre_mm"), 1), 0.0);
    s->height_mm = pp_json_num(pp_json_get(cue, "height_mm"), 0.0);
    if (s->height_mm <= 0)
        FAIL("%s: cue.height_mm is required", path);

    snprintf(s->kernel, sizeof(s->kernel), "%s", pp_json_str(pp_json_get(root, "kernel"), "ewa_lanczossharp"));
    snprintf(s->domain, sizeof(s->domain), "%s", pp_json_str(pp_json_get(root, "domain"), "auto"));
    if (strcmp(s->domain, "auto") && strcmp(s->domain, "linear") && strcmp(s->domain, "sigmoid"))
        FAIL("%s: domain must be auto, linear or sigmoid", path);
    s->antiring = pp_json_num(pp_json_get(root, "antiring"), 0.0);

    const struct pp_json *slide = pp_json_get(root, "slide");
    if (slide) {
        s->has_slide = true;
        s->slide.vu = pp_json_num(pp_json_at(pp_json_get(slide, "velocity_mm_s"), 0), 0.0);
        s->slide.vv = pp_json_num(pp_json_at(pp_json_get(slide, "velocity_mm_s"), 1), 0.0);
        s->slide.t_ref_ms = pp_json_num(pp_json_get(slide, "t_ref_ms"), 0.0);
        const struct pp_json *tt = pp_json_get(slide, "t_top_ms");
        for (int i = 0; tt && i < tt->n; i++) {
            int k = monitor_index(&s->room, tt->keys[i]);
            if (k < 0)
                FAIL("%s: slide.t_top_ms names an unknown monitor %s", path, tt->keys[i]);
            s->slide.t_top_ms[k] = pp_json_num(tt->items[i], 0.0);
        }
    }
    const struct pp_json *bl = pp_json_get(root, "blend");
    for (int i = 0; bl && i < bl->n; i++) {
        int k = monitor_index(&s->room, bl->keys[i]);
        if (k < 0)
            FAIL("%s: blend names an unknown monitor %s", path, bl->keys[i]);
        s->has_blend[k] = true;
        s->blend_wa[k] = pp_json_num(pp_json_get(bl->items[i], "wa"), 0.0);
        s->blend_wb[k] = pp_json_num(pp_json_get(bl->items[i], "wb"), 0.0);
        if (!s->has_b)
            FAIL("%s: blend needs source_b", path);
    }
    pp_json_free(root);
    free(text);
    return true;
}

void pp_scenario_picture(const struct pp_scenario *s, struct pp_picture *p)
{
    pp_picture_init(p, s->src_w, s->src_h, s->sar, s->has_crop ? s->crop : NULL, s->centre_u, s->centre_v, s->height_mm);
}
