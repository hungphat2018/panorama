// The picture path of the performer, per monitor and refresh (what perf1-render.c calls):
//
//   prepare   libplacebo maps the frame (D3D11VA included), decodes its colour to linear light at source size, no scaling
//             (perf1-picture-prepare.c), kept per frame, shared by the monitors
//   pass 1    our one resample, source -> this monitor's pixel grid: room geometry (fold or curve), the shot's crop and
//             place, a camera slide per row, the rolling blend (perf1-picture-pass1.c)
//   pass 2    the surround from the picture's edges, state kept across refreshes (perf1-finish-surround.c)
//   pass 3    one colour correction in linear light over picture and surround, encode, our blue-noise dither, written
//             into the swapchain image (perf1-finish-color.c); passes 2 and 3 share one pass over the canvas
//   pass 4    libass subtitles at native pixels after the dither, on the monitor the cue sheet names (perf1-finish-subs.c)
//
// Per-shot values (crop, place, slide, surround, subtitle monitor) enter the pass of a refresh only through the latched
// shot, so a shot never changes while it is on screen.
#pragma once

#include "perf1-app.h"
#include "perf1-picture-look.h"
#include "perf1-picture-map.h"
#include "perf1-picture-pass1.h"
#include "perf1-picture-plan.h"
#include "perf1-picture-prepare.h"
#include "perf1-room.h"
#include "perf1-subs-load.h"

struct pic_cfg {
    const char *room_file;      // --room FILE
    int surface;                // --surface: -1 as the room file says (fold without one), 0 fold, 1 curve
    bool blend_rolling;         // --blend rolling
    bool surround_glow;         // --surround glow|black: the look of shots whose cue has no "surround"
    const char *kernel;         // --kernel NAME: the resample's radial kernel
    const char *chroma_filter;  // --chroma-filter NAME: libplacebo filter for chroma planes (default: its built-in)
    const char *sub_file;       // --sub FILE: dialogue from an external .ass/.srt/.vtt instead of the film's own
    int sub_stream;             // --sub-stream N: stream index in the film (-1 = the default text track)
    int sub_monitor;            // --sub-monitor N: where dialogue goes when the cue sheet names no monitor (-1 = the first
                                // monitor that shows most of the first shot's picture)
    bool no_subs;               // --no-subs
    const char *dump_dir;       // --dump-canvas DIR: read back pass 1's canvas and the finished frame once per monitor
    double dump_at_s;           // --dump-at S: ... from the first refresh at film time S or later
    bool dump_cut;              // --dump-cut: with --blend rolling, wait for a refresh that mixes frames of two different shots
};
extern struct pic_cfg PC;

// ---- the shot plan: what a latched shot means for the picture, worked out once per shot ----------------------------

struct pic_shot {
    uint32_t id;                // latch entry id (UINT32_MAX = a one-off)
    bool valid;
    const struct cue_shot *cue; // the shot's cue (sheets are immutable and live for the whole run)
    struct lay_picture lay;     // crop, aspect and rectangle on the surface (perf1-layout.c)
    struct pic_view view;
    bool upscales[MAX_MONITORS];
    struct pic_look look;
};

// ---- one refresh, as perf1-render.c hands it over ----------------------------------------------------------------

struct pic_refresh {
    struct monitor *m;
    const struct pl_frame_mix *mix; // the monitor's queue answer around the frame
    int front;                      // index in mix of the frame to show
    const struct cue_shot *cue;
    uint32_t shot_id;               // latch entry id
    double pts;                     // the frame's timestamp, which the dialogue follows
    long ord;                       // ordinal in the epoch (-1 outside the engine)
    int epoch;
    bool first_of_epoch;
    int64_t k;                      // this monitor's refresh index
    double wake;                    // its wake-up time on the host clock
    bool timed;                     // the engine runs: row times, the film clock and the blend are valid
    struct pl_swapchain_frame *sf;  // NULL when only the CPU side is wanted (headless)
};

// perf1-picture-args.c: picture_arg, picture_usage (declared in perf1-app.h)
// perf1-picture-setup.c: the room file, monitor places (picture_load_room and picture_top_mm are in perf1-app.h)
bool picture_geometry(void);                         // each monitor's room_geom, once the master is known
const struct room_geom *picture_geom(int slot);
const struct room *picture_room(void);               // NULL without --room

const struct room_color *picture_color(int slot);    // the monitor's colour block (neutral without a room)
bool picture_surface_origin(double *u_axis, double *v_eye); // surface u of the gaze axis and v of eye level (needs a room)
const char *picture_monitor_name(int slot);          // the room's id for the monitor, else its display name

// perf1-picture-shot.c
const struct pic_shot *picture_shot(uint32_t id, const struct cue_shot *cue);
// The plan of the shot a frame with timestamp pts belongs to, if the latch still holds it (it never captures); else NULL.
const struct pic_shot *picture_shot_of_frame(double pts);

// perf1-picture-timing.c: what the film clock and the row clocks say about one refresh
struct pic_timing {
    bool slide_on;              // the shot has a camera slide
    struct pic_slide slide;     // the picture's displacement per row at this refresh
    struct pic_blend blend;     // partner -1 / +1: a neighbouring frame is mixed in (rolling blend), see perf1-picture-plan.h
    double partner_pts;         // its timestamp
    const struct pic_shot *partner_shot; // the shot it belongs to (NULL: the latch no longer holds it: seen like the front frame)
    bool partner_slide_on;      // that shot has a camera slide
    struct pic_slide partner_slide;
};
void picture_timing(const struct pic_refresh *r, struct pic_timing *out);

// perf1-picture-render.c
bool picture_init_monitor(struct monitor *m);        // GPU objects of one monitor (a real GPU only)
bool picture_init_subs(void);                        // reads the subtitle track and feeds every monitor's libass
void picture_destroy(void);                          // before the device goes
bool picture_draw(const struct pic_refresh *r);
void picture_plan_only(const struct pic_refresh *r); // headless: the CPU side of a refresh (shot plan, row times), no GPU
double picture_last_gpu_ms(int slot);                 // GPU time libplacebo's timers last reported for the monitor's passes
void picture_write_logs(void);                       // monitor-N-passes.json
