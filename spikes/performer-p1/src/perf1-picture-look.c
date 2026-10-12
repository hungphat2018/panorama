// Surround look and colour parameters, see perf1-picture-look.h.
#include "perf1-picture-look.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

void pic_look_default(struct pic_look *l, bool glow)
{
    memset(l, 0, sizeof(*l));
    l->look = (struct fp_surround_look) { .mode = glow ? FP_SURROUND_GLOW : FP_SURROUND_BLACK, .gain = 0.8f,
                                          .falloff_px = 250.0f, .saturation = 1.0f, .max_level = 1.0f,
                                          .premultiplied_alpha = true, .state_gamma = 1.0f };
    l->time = (struct fp_surround_time) { .tau_steady_s = 0.5f, .cut_rule = FP_CUT_SNAP, .tau_blend_s = 0.15f,
                                          .blend_window_s = 0.5f, .hold_s = 0.5f };
}

static bool bad(char *err, size_t n, const char *fmt, ...) __attribute__((format(gnu_printf, 3, 4)));
static bool bad(char *err, size_t n, const char *fmt, ...)
{
    if (err && n) {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(err, n, fmt, ap);
        va_end(ap);
    }
    return false;
}

// A number member in [lo, hi], absent = unchanged.
static bool num_member(const struct json_value *o, const char *key, double lo, double hi, float *out, char *err, size_t n)
{
    const struct json_value *v = json_member(o, key);
    if (!v || v->type == JSON_NULL)
        return true;
    if (v->type != JSON_NUMBER || !(v->num >= lo && v->num <= hi))
        return bad(err, n, "surround.%s must be a number from %g to %g", key, lo, hi);
    *out = (float) v->num;
    return true;
}

bool pic_look_from_cue(struct pic_look *l, const struct json_value *s, char *err, size_t err_size)
{
    if (!s)
        return true;
    if (s->type != JSON_OBJECT)
        return bad(err, err_size, "surround must be an object");
    const struct json_value *mode = json_member(s, "mode");
    if (mode && (mode->type != JSON_STRING || (strcmp(mode->str, "glow") && strcmp(mode->str, "black"))))
        return bad(err, err_size, "surround.mode must be \"glow\" or \"black\"");
    if (mode)
        l->look.mode = !strcmp(mode->str, "glow") ? FP_SURROUND_GLOW : FP_SURROUND_BLACK;
    const struct json_value *cut = json_member(s, "cut");
    if (cut && (cut->type != JSON_STRING || (strcmp(cut->str, "snap") && strcmp(cut->str, "blend") && strcmp(cut->str, "hold"))))
        return bad(err, err_size, "surround.cut must be \"snap\", \"blend\" or \"hold\"");
    if (cut)
        l->time.cut_rule = !strcmp(cut->str, "snap") ? FP_CUT_SNAP : !strcmp(cut->str, "blend") ? FP_CUT_BLEND : FP_CUT_HOLD;
    return num_member(s, "gain", 0, 4, &l->look.gain, err, err_size) &&
           num_member(s, "falloff_px", 1, 5000, &l->look.falloff_px, err, err_size) &&
           num_member(s, "saturation", 0, 4, &l->look.saturation, err, err_size) &&
           num_member(s, "max_level", 0, 16, &l->look.max_level, err, err_size) &&
           num_member(s, "state_gamma", 0.1, 4, &l->look.state_gamma, err, err_size) &&
           num_member(s, "tau_s", 0, 600, &l->time.tau_steady_s, err, err_size) &&
           num_member(s, "max_rate", 0, 1000, &l->time.max_rate, err, err_size) &&
           num_member(s, "tau_blend_s", 0, 600, &l->time.tau_blend_s, err, err_size) &&
           num_member(s, "blend_window_s", 0, 600, &l->time.blend_window_s, err, err_size) &&
           num_member(s, "hold_s", 0, 600, &l->time.hold_s, err, err_size);
}

void pic_color_make(const struct room_color *rc, int out_bits, struct fp_color_gpu *out)
{
    struct fp_color_monitor c = {0};
    c.transfer = rc->power_trc ? FP_TRC_POWER : FP_TRC_SRGB;
    c.power = rc->power;
    c.gamut_owner = rc->acm ? FP_GAMUT_OS : FP_GAMUT_PLAYER;
    if (!rc->acm && rc->has_primaries) {
        c.panel.r = (struct fp_xy) { rc->primaries[0][0], rc->primaries[0][1] };
        c.panel.g = (struct fp_xy) { rc->primaries[1][0], rc->primaries[1][1] };
        c.panel.b = (struct fp_xy) { rc->primaries[2][0], rc->primaries[2][1] };
        c.panel.w = (struct fp_xy) { rc->primaries[3][0], rc->primaries[3][1] };
    } else {
        c.gamut_owner = FP_GAMUT_OS; // the player owns the gamut but knows no primaries: BT.709 stays BT.709
    }
    for (int i = 0; i < 3; i++) {
        c.gain[i] = (float) (rc->gain[i] * rc->white[i]);
        c.lift[i] = (float) rc->lift[i];
    }
    c.out_bits = out_bits;
    fp_color_make(&c, out);
}
