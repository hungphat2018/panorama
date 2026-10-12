// The scenario file shared by the harness and reference-render.py (format in pp_reference.py): the room, the surface
// model, the source picture and its placement, the kernel, a camera slide and per-monitor blend weights.
#pragma once

#include <stdbool.h>

#include "pp-geometry.h"

struct pp_scenario {
    char path[260];
    struct pp_room room;
    struct pp_surface surface;
    char source_file[260];
    int src_w, src_h;               // from source.width / source.height (checked against the file when it is loaded)
    double sar;
    char source_b_file[260];
    bool has_b;
    bool has_crop;
    double crop[4];
    double centre_u, centre_v, height_mm;
    char kernel[48];
    char domain[16];                // "auto" | "linear" | "sigmoid"
    double antiring;
    bool has_slide;
    struct pp_slide slide;
    bool has_blend[PP_MAX_MON];
    double blend_wa[PP_MAX_MON], blend_wb[PP_MAX_MON];
};

bool pp_scenario_load(struct pp_scenario *s, const char *path, char *err, size_t errsz);
void pp_scenario_picture(const struct pp_scenario *s, struct pp_picture *p);
