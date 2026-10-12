// What passes 2 and 3 are told, from the cue sheet and the room: the surround's look and time rule per shot, and each
// monitor's colour correction. No GPU.
//
// Cue sheet "surround" object (all keys optional; the defaults are those of --surround):
//   { "mode": "glow" | "black",
//     "gain": 0.8, "falloff_px": 250, "saturation": 1, "max_level": 1, "state_gamma": 1,      what it looks like
//     "tau_s": 0.5, "max_rate": 0,                                                            how fast it follows the picture
//     "cut": "snap" | "blend" | "hold", "tau_blend_s": 0.15, "blend_window_s": 0.5, "hold_s": 0.5 }   what a hard cut does
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "perf1-finish-passes.h"
#include "perf1-json.h"
#include "perf1-room.h"

struct pic_look {
    struct fp_surround_look look;
    struct fp_surround_time time;
};

// The default look: a glow (or black) as the harness measured it (gain 0.8, falloff 250 px, follows within 0.5 s, snaps
// at a cut). The canvas of pass 1 is premultiplied by the picture's coverage.
void pic_look_default(struct pic_look *l, bool glow);
// Overlays the cue's "surround" object on *l. False with a message when a key has the wrong type or range.
bool pic_look_from_cue(struct pic_look *l, const struct json_value *surround, char *err, size_t err_size);

// A monitor's colour: Windows owning the gamut (acm) means relative tweaks only; the player owning it maps BT.709 to
// the panel's primaries. out_bits: the depth of the monitor's swapchain, which the dither works to.
void pic_color_make(const struct room_color *rc, int out_bits, struct fp_color_gpu *out);
