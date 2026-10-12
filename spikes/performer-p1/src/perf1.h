// perf1 (the P3 build): two-window presenter on libplacebo (D3D11) with cue sheets. Derived from the single-window perf0
// player (itself adapted from libplacebo demos/plplay.c, CC0 / public domain).
//
// One borderless window per monitor, one flip-model waitable DXGI swapchain per window wrapped by libplacebo, our own
// Present1 call. One D3D11 device and one render thread serve every swapchain (libplacebo's D3D11 pl_gpu is not
// thread-safe). One decoder feeds one pl_queue per monitor. The decisions (which frame each monitor shows on each refresh,
// which cue applies, how a seek restarts the clock) live in perf1-engine.c; this program supplies time, frames and pixels.
// Throwaway spike code.
#pragma once

#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <libplacebo/cache.h>
#include <libplacebo/d3d11.h>
#include <libplacebo/dummy.h>
#include <libplacebo/gpu.h>
#include <libplacebo/log.h>
#include <libplacebo/renderer.h>
#include <libplacebo/swapchain.h>
#include <libplacebo/utils/frame_queue.h>

#include "perf1-engine.h"

#define MAX_MONITORS ENGINE_MAX_MON

enum decode_mode { DECODE_SW, DECODE_D3D11VA };
enum sched_mode { SCHED_OWN, SCHED_NEAREST };

struct cfg {
    const char *file;
    enum decode_mode decode;
    const char *adapter;        // substring of the adapter name (default: first adapter that has outputs)
    const char *preset;         // fast | default | hq
    double seconds;             // playback time after the film clock starts
    bool windowed;              // smoke-test mode: half-size windows, not topmost, no focus steal
    double window_scale;        // --windowed: window size as a fraction of its monitor
    enum sched_mode sched;      // own | nearest
    int master;                 // output index of the master monitor, -1 = slowest
    bool lock_speed;            // lock playback speed to the master's refresh count when within 1 %
    bool sync_render;           // pl_gpu_finish after every render: wall time of CPU + GPU
    bool rgb10;                 // swapchain format: rgb10a2 (default) or bgra8
    double gap_mm;              // physical gap between the two screens' active areas
    double picture_height_mm;   // 0 = the height both screens share
    double picture_center_mm;   // NaN = middle of the gap
    double height_offset_mm;    // follower's top below the master's top (0 = tops level)
    double aim_mm;              // where the screens agree, from the master's top; NaN = middle of the shared height
    struct { wchar_t device[32]; double ms; } lags[MAX_MONITORS]; // --lag-ms DISPLAYn=X: panel lag per display
    int n_lags;
    const char *out_dir;        // monitor-N-<mode>.json, perf1-cuelog.json
    wchar_t refresh_device[32]; // --refresh DISPLAYn=HZ (CDS_FULLSCREEN, restored when the process ends)
    int refresh_hz;
    int log_level;
    bool probe_only;
    // cue sheets and control
    const char *cues_file;      // --cues FILE: version loaded before the film starts
    const char *control_script; // --control-script FILE: JSON lines, see perf1-control.h
    const char *pipe_name;      // --pipe NAME: named-pipe control (\\.\pipe\NAME)
    // clock rules
    bool no_hysteresis, no_reaim;
    double eta_ms;              // hysteresis and re-aim threshold
    double restart_margin_ms;   // render time reserved for the first frame after a break
    // no windows, no GPU: virtual displays (Setup A numbers) and libplacebo's dummy GPU, software decoding
    int headless_hz;            // --headless EXT_HZ (96, 72 or 100): the external's rate
    double headless_drift_ppm;  // --headless-drift-ppm: drift of the laptop's clock against the external's
    // sound (perf1-audio.h)
    int audio_track;            // --audio-track N: the Nth audio stream (0 based); -1 = FFmpeg's best
    bool no_audio;              // --no-audio
    bool audio_null;            // --audio-null: no device, a stand-in sink on the host clock (tests)
    double audio_volume;        // --audio-volume 0..1: this stream's volume (the device's session volume, not persisted)
    double audio_offset_ms;     // --audio-offset-ms: delay the sound against the picture (+) or advance it (-)
    double audio_null_drift_ppm; // --audio-null-drift-ppm: the stand-in device's clock runs this fast against the host clock
    double audio_null_latency_ms; // --audio-null-latency-ms: the stand-in device's own delay between taking a frame and sounding it
};

// ---- time + statistics (perf1-stats.c) ------------------------------------------------

double now_s(void);   // QueryPerformanceCounter, seconds
double now_ms(void);

struct summary { size_t n; double min, mean, p50, p90, p99, p999, max; };
void summarize(const double *v, size_t n, struct summary *out);

struct samples { double *v; size_t n, cap; };
void samples_push(struct samples *s, double x);
void samples_free(struct samples *s);

// ---- refresh grid of one monitor (perf1-clock.c) ------------------------------------------
// Fitted to the monitor's recent vblank times: vblank k at base + k * period. Thread safe.

struct vclock {
    CRITICAL_SECTION cs;
    double *log;
    int64_t *idx;
    size_t n, cap;
    double base, period;
};

void vclock_init(struct vclock *c);
void vclock_destroy(struct vclock *c);
void vclock_add(struct vclock *c, double t);
double vclock_period(struct vclock *c);
double vclock_next_after(struct vclock *c, double t);   // first predicted vblank at or after t
double vclock_nearest(struct vclock *c, double t);
int64_t vclock_index_near(struct vclock *c, double t);
double vclock_time_of(struct vclock *c, int64_t i);
void vclock_line(struct vclock *c, double *base, double *period);   // the fitted line, read under one lock
double *vclock_copy_log(struct vclock *c, size_t *n);   // caller frees

// ---- decoder (perf1-decode.c) -------------------------------------------------------------

struct gpu_ctx {
    pl_log log;
    pl_d3d11 d3d11;             // NULL in headless mode
    pl_gpu gpu;
};

struct decoder;

struct decoder_info {
    char codec[64];
    char pix_fmt[64];
    char hw_fmt[64];            // "" for software decoding
    int width, height;
    double fps;
    double duration_s;
    uint32_t decoded, mapped, dropped, pushed, seeks, nonmonotonic;
    bool eof;
};

// The decoder pushes every decoded frame to each of the `nqueues` queues (an AVFrame reference each), then appends its
// timestamp to the engine's frame index. It follows the engine's supply handshake for seeks (perf1-engine.h):
// park, wait for the queues to be reset, seek, decode forward to the frame that contains the sought time, queue it
// and the next one, announce them as ready.
struct decoder *decoder_open(const struct cfg *cfg, struct gpu_ctx *g, pl_queue *queues, int nqueues, struct engine *engine);
bool decoder_start(struct decoder *d);
void decoder_stop(struct decoder *d);
void decoder_get_info(struct decoder *d, struct decoder_info *out);
void decoder_destroy(struct decoder **d);
