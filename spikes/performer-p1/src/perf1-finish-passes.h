// The narrow API of passes 2 to 4 of the per-monitor picture path (surround, finish, subtitles). Ported from
// spikes/picture-path/finish/src (names kept); the track functions below are additions for container subtitle streams.
//
// Input of pass 2 (and of the fused pass 2+3): pass 1's canvas, one per monitor, RGBA16F ("rgba16f" in libplacebo),
// native pixel size of the monitor, linear light, BT.709 primaries with extended range allowed, 1.0 = the room's common
// SDR reference white, alpha = picture coverage (0 in the leftover area), straight (not premultiplied) unless a call
// says otherwise. Output of pass 3: the monitor's own 8-bit (or 10-bit) frame buffer, encoded and dithered. Pass 4
// draws on top of that.
//
// All three modules use only libplacebo's public API (pl_dispatch, pl_shader, pl_tex, pl_renderer), so they run on
// every libplacebo backend. Nothing here creates a device or a window: the caller owns those. A module is not
// thread-safe; use one instance per monitor, from the monitor's own render thread.
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <libplacebo/dispatch.h>
#include <libplacebo/gpu.h>
#include <libplacebo/log.h>
#include <libplacebo/renderer.h>
#include <libplacebo/shaders.h>

#ifdef __cplusplus
extern "C" {
#endif

// What every pass needs from the host program. `timer` is optional: when set, every GPU dispatch of the next call is
// recorded into it (the host reads it back with pl_timer_query()). A module keeps a POINTER to the host's fp_env and
// reads `timer` at every call, so the env must outlive the module and the host may change `timer` between calls.
struct fp_env {
    pl_log log;
    pl_gpu gpu;
    pl_dispatch dp;
    pl_timer timer;
};

// ============================================================================================================
// Pass 2: surround
// ============================================================================================================
//
// Fills the leftover area (canvas alpha < 1) with a glow made from the colours at the picture's edge. The picture
// itself is never touched or resampled; the fill is computed per canvas pixel at 1:1.
//
// How it works (details in the report): the canvas is reduced to a coarse grid (one cell = cell_px x cell_px canvas
// pixels, alpha-weighted mean colour and coverage). The grid is the persistent state: it is smoothed over time on the
// GPU (an exponential follower, optionally rate-limited) and at a cut it follows the cut rule from the cue. Only the
// cells at the picture's edge are kept; they are blurred at two scales, and the fill takes the fine scale near the
// edge and the coarse scale farther out. The brightness falls off with a blurred copy of the coverage.

enum fp_surround_mode {
    FP_SURROUND_BLACK = 0, // leftover area stays black: nothing is computed
    FP_SURROUND_GLOW = 1,
};

// How the surround moves at a hard cut (the cue sets it per shot).
enum fp_cut_rule {
    FP_CUT_SNAP = 0,  // the state jumps to the new shot's edge colours on the cut's first refresh
    FP_CUT_BLEND = 1, // a faster time constant for blend_window_s after the cut, then the steady one
    FP_CUT_HOLD = 2,  // the state stays frozen for hold_s after the cut, then follows with the steady constant
};

// What it looks like. Planner / user decisions, not measurements.
struct fp_surround_look {
    enum fp_surround_mode mode;
    float gain;        // glow at the picture's edge relative to the edge colour: 1 = same brightness, 0.5 = half
    float falloff_px;  // sigma (canvas pixels) of the blur that dims the glow with distance: the glow is about a third
                       // as bright one sigma from the edge and 5 % at two sigma
    float saturation;  // 1 = keep; 0 = grey
    float max_level;   // linear-light ceiling for the glow (1.0 = SDR white); keeps highlights from lighting the room
    bool premultiplied_alpha; // the canvas rgb is already multiplied by its alpha (default: straight alpha)
    float state_gamma;        // the state, its follower and its blur work on colours raised to this power: 1 (the default
                              // for 0) = linear light, as light mixes; about 0.45 = roughly perceptual, so a fade of
                              // the glow looks even instead of speeding up in the dark
};

// How it moves in time. Cue settings.
struct fp_surround_time {
    float tau_steady_s;       // time constant of the follower inside a shot; <= 0 follows every frame exactly
    float max_rate;           // largest change per second in linear light (rate limit); <= 0 = off
    enum fp_cut_rule cut_rule;
    float tau_blend_s;        // FP_CUT_BLEND: time constant right after a cut
    float blend_window_s;     // FP_CUT_BLEND: how long that constant lasts
    float hold_s;             // FP_CUT_HOLD: how long the state is frozen
};

// Per refresh.
struct fp_surround_frame {
    double dt_s;  // seconds of FILM time since this monitor's previous refresh (not wall time: pauses must not age it)
    bool cut;     // this refresh is the first of a new shot (as the cue sheet says; all monitors flag the same refresh)
    bool reset;   // forget everything (seek, pause, file start): the state snaps to the current frame
};

// The per-refresh decision of the temporal rule, on the CPU: how far the state moves toward the new target.
// Exposed so tests and the planner can run the rule without a GPU.
struct fp_surround_clock {
    double since_cut_s;
    bool primed; // false until the first update after create/reset
};

struct fp_surround_step {
    float alpha;      // fraction of the way to the target this refresh (1 = jump)
    float max_delta;  // rate limit for this refresh in linear light (huge = off)
};

void fp_surround_clock_reset(struct fp_surround_clock *c);
struct fp_surround_step fp_surround_clock_step(struct fp_surround_clock *c, const struct fp_surround_time *t,
                                               const struct fp_surround_frame *f);

struct fp_surround;

// canvas_w x canvas_h: the monitor's native size. cell_px: grid cell size (32 is the default when 0).
struct fp_surround *fp_surround_create(const struct fp_env *env, int canvas_w, int canvas_h, int cell_px);
void fp_surround_destroy(struct fp_surround **s);

// The small passes: read the canvas, update the persistent state, blur the edge cells into the levels. Does not write
// the canvas.
// Must be called once per refresh before the fill. No-op in FP_SURROUND_BLACK mode.
bool fp_surround_update(struct fp_surround *s, pl_tex canvas, const struct fp_surround_look *look,
                        const struct fp_surround_time *time, const struct fp_surround_frame *frame);

// Appends the per-pixel fill to a shader that already holds the canvas texel in `vec4 color` (straight alpha). After
// the call `color` is the picture composited over the surround (alpha 1). For the fused pass 2+3; see
// fp_finish_run_fused(). In FP_SURROUND_BLACK mode it composites over black.
bool fp_surround_append_fill(struct fp_surround *s, pl_shader sh, const struct fp_surround_look *look);

// The AI sides (perf1-ai-sides.c): `fill` appends, before the glow, a fragment that composites the AI canvas under the
// picture in `color` (premultiplied) and raises color.a by what it covers, so the glow fills only the rest. Called by
// fp_surround_append_fill in glow mode on a premultiplied canvas; it may append nothing (no AI picture ready). NULL removes it.
void fp_surround_set_ai_fill(struct fp_surround *s, bool (*fill)(void *priv, pl_shader sh), void *priv);

// Stand-alone pass 2: update + fill. `out` is a canvas-sized RGBA16F render target (it may not be `canvas`).
bool fp_surround_apply(struct fp_surround *s, pl_tex canvas, pl_tex out, const struct fp_surround_look *look,
                       const struct fp_surround_time *time, const struct fp_surround_frame *frame);

// Appends "color = texelFetch(canvas, pixel)" as the start of a fused shader (straight alpha, nearest, 1:1 pixels).
bool fp_append_canvas_fetch(pl_shader sh, pl_tex canvas);

// For tests: the persistent state read back (grid_w x grid_h cells, 4 floats each: premultiplied colour, coverage).
bool fp_surround_read_state(struct fp_surround *s, float *rgbw, int *grid_w, int *grid_h);

// ============================================================================================================
// Pass 3: finish (colour correction in linear light, encode, dither)
// ============================================================================================================

enum fp_transfer {
    FP_TRC_SRGB = 0, // piecewise sRGB: what Windows' automatic colour management assumes for an 8-bit swap chain
    FP_TRC_POWER,    // pure power law `power` (2.2, 2.4 ...): what most panels do natively; also BT.1886 without a black level
};

enum fp_gamut_owner {
    FP_GAMUT_OS = 0,     // Windows maps sRGB content to the panel (automatic colour management is on): relative tweaks only
    FP_GAMUT_PLAYER = 1, // the player maps BT.709 to the panel's own primaries (automatic colour management is off)
};

struct fp_xy {
    double x, y;
};
struct fp_primaries {
    struct fp_xy r, g, b, w;
};

enum fp_dither {
    FP_DITHER_NONE = 0,
    FP_DITHER_BLUE_NOISE, // libplacebo's 64x64 blue-noise table (its default): the pick
    FP_DITHER_ORDERED,    // libplacebo's fixed 16x16 ordered pattern (no table)
    FP_DITHER_WHITE,      // white noise (the bad reference)
};

// One monitor's colour data: what the room model and the by-eye tuning hold. Zero-initialise, then set what differs.
struct fp_color_monitor {
    enum fp_transfer transfer;
    double power;                // FP_TRC_POWER exponent; 0 = 2.2
    enum fp_gamut_owner gamut_owner;
    struct fp_primaries panel;   // FP_GAMUT_PLAYER: the panel's real primaries and white (EDID or measured)
    double tweak_matrix[9];      // row-major relative 3x3 in linear light after the gamut step; all zero = identity
    float gain[3];               // per channel white level; 0 = 1.0 (so zero-initialised means "no change")
    float gamma[3];              // per channel exponent on linear light (1.0 = none); 0 = 1.0
    float lift[3];               // per channel black lift as a fraction of white, linear light (0 = none)
    int out_bits;                // 8 on both Setup A panels; 0 = 8
};

// The prepared form of a monitor's data: what the shader (and the CPU reference) use.
struct fp_color_gpu {
    float m[9];       // final 3x3, row-major: out = M * rgb (linear light)
    float gain[3];
    float gamma[3];
    float lift[3];
    enum fp_transfer transfer;
    float power;
    int out_bits;
};

void fp_color_make(const struct fp_color_monitor *in, struct fp_color_gpu *out);
// CPU reference of the shader, same formulas in double precision. rgb_lin: working space (linear). Returns the encoded
// value in [0,1] (before dither and quantisation).
void fp_color_reference(const struct fp_color_gpu *c, const double rgb_lin[3], double enc_out[3]);

struct fp_finish;
struct fp_finish *fp_finish_create(const struct fp_env *env);
void fp_finish_destroy(struct fp_finish **f);

// Appends correction + encode + dither to a shader that holds linear light in `vec4 color` (alpha already 1).
// `frame_index` only matters for temporal dither (not used by default).
bool fp_finish_append(struct fp_finish *f, pl_shader sh, const struct fp_color_gpu *c, enum fp_dither dither);

// Stand-alone pass 3: canvas (alpha 1 everywhere, e.g. pass 2's output) -> the monitor's frame buffer.
bool fp_finish_run(struct fp_finish *f, pl_tex in, pl_tex out, const struct fp_color_gpu *c, enum fp_dither dither);

// Fused passes 2 (fill) + 3 in one pass over the canvas. Call fp_surround_update() first for this refresh.
bool fp_finish_run_fused(struct fp_finish *f, struct fp_surround *s, pl_tex canvas, pl_tex out,
                         const struct fp_surround_look *look, const struct fp_color_gpu *c, enum fp_dither dither);

// libplacebo's error diffusion (compute shaders, one work group), for comparison with the ordered / blue-noise dither.
// `in` is the encoded-but-unquantised frame (float), `out` the 8-bit result. Stand-alone helper for the dither study.
bool fp_finish_error_diffusion(struct fp_finish *f, pl_tex in_float, pl_tex out, int bits);

// ============================================================================================================
// Pass 4: subtitles
// ============================================================================================================

enum fp_subs_overflow {
    FP_SUBS_CLIP = 0,   // draw what fits inside the monitor, and report the overflow
    FP_SUBS_REFUSE = 1, // draw nothing for a frame whose subtitle does not fit, and report it
};

struct fp_subs_result {
    bool drawn;          // anything was blended into the target this call
    bool changed;        // the subtitle picture is not the one drawn on the previous call
    bool overflow;       // the line reaches the edge of the allowed box: libass cuts what does not fit, so it may be cut
    bool edge_hit[4];    // ... on the left, top, right, bottom side
    int n_images;        // libass bitmaps in this frame
    int bbox[4];         // x0, y0, x1, y1 of all bitmaps in monitor pixels
};

struct fp_subs;

// mon_w x mon_h: the monitor's native size. libass renders at exactly that size: one libass pixel is one monitor pixel.
struct fp_subs *fp_subs_create(const struct fp_env *env, int mon_w, int mon_h);
void fp_subs_destroy(struct fp_subs **s);
bool fp_subs_add_font(struct fp_subs *s, const char *name, const void *data, size_t size);
bool fp_subs_load_ass(struct fp_subs *s, const char *data, size_t size);
// A track made from a container's subtitle stream instead of a whole script: start it with the stream's header (the codec
// private data, may be empty), then add one chunk per packet (ASS dialogue line as libavcodec produces it, times in ms).
bool fp_subs_track_start(struct fp_subs *s, const char *header, size_t size);
void fp_subs_track_chunk(struct fp_subs *s, const char *data, size_t size, long long start_ms, long long duration_ms);
// GPU time (ns) libplacebo reported for the overlay passes since the last call (0 when none has finished yet).
uint64_t fp_subs_take_gpu_ns(struct fp_subs *s);
// Where the dialogue must fit, in monitor pixels (default: the whole monitor). The text is laid out inside this box.
void fp_subs_set_box(struct fp_subs *s, int x0, int y0, int x1, int y1);
void fp_subs_set_overflow(struct fp_subs *s, enum fp_subs_overflow mode);
// Blends the subtitle that is on at film time `pts_s` over `target` (an 8-bit render target, already finished).
// libass colours are blended as raw target code values (white text is code 255 whatever the target's transfer).
bool fp_subs_draw(struct fp_subs *s, pl_tex target, double pts_s, struct fp_subs_result *res);
// The same lookup without drawing and without touching the GPU: where the line lands and whether it fits. For the
// planner and for diagnostics.
bool fp_subs_measure(struct fp_subs *s, double pts_s, struct fp_subs_result *res);
// libass time for a film time: the largest whole millisecond not after pts (an event starting at 5000 ms is on for the
// first frame whose time is 5.000 s or later, and off for the first frame at its end time or later).
long long fp_subs_ass_time_ms(double pts_s);

#ifdef __cplusplus
}
#endif
