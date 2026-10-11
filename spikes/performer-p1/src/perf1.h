// perf1: two-window presenter on libplacebo (D3D11). Derived from the single-window perf0
// player (itself adapted from libplacebo demos/plplay.c, CC0 / public domain).
//
// One borderless window per monitor, one flip-model waitable DXGI swapchain per window wrapped by
// libplacebo, our own Present1 call. One D3D11 device and one render thread serve every swapchain
// (libplacebo's D3D11 pl_gpu is not thread-safe). One decoder feeds one pl_queue per monitor.
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
#include <libplacebo/gpu.h>
#include <libplacebo/log.h>
#include <libplacebo/renderer.h>
#include <libplacebo/swapchain.h>
#include <libplacebo/utils/frame_queue.h>

#define MAX_MONITORS 4

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
    double picture_height_mm;   // 0 = the shorter screen's height
    double picture_center_mm;   // NaN = middle of the gap
    const char *out_dir;        // monitor-N-<mode>.json
    wchar_t refresh_device[32]; // --refresh DISPLAYn=HZ (CDS_FULLSCREEN, restored when the process ends)
    int refresh_hz;
    int log_level;
    bool probe_only;
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
double *vclock_copy_log(struct vclock *c, size_t *n);   // caller frees

// ---- decoder (perf1-decode.c) -------------------------------------------------------------

struct gpu_ctx {
    pl_log log;
    pl_d3d11 d3d11;
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
    uint32_t decoded, mapped, dropped;
};

// The decoder pushes every decoded frame to each of the `nqueues` queues (an AVFrame reference each).
struct decoder *decoder_open(const struct cfg *cfg, struct gpu_ctx *g, pl_queue *queues, int nqueues);
bool decoder_start(struct decoder *d);
void decoder_stop(struct decoder *d);
void decoder_get_info(struct decoder *d, struct decoder_info *out);
void decoder_destroy(struct decoder **d);
