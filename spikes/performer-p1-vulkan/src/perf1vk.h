// perf1vk: two-window presenter on libplacebo's Vulkan backend. Derived from perf1 (D3D11, spikes/performer-p1) and
// from the single-window perf0 player (itself adapted from libplacebo demos/plplay.c, CC0 / public domain).
//
// One borderless window per monitor, one Vulkan swapchain per window, one VkDevice (libplacebo's pl_vulkan) for
// everything. libplacebo's Vulkan pl_gpu is thread-safe, so every monitor gets its own render thread; a thread blocks
// in the swapchain's image acquire, which with two images paces it to its monitor's refresh. One decoder feeds one
// pl_queue per monitor. Throwaway spike code.
#pragma once

#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WIN32_LEAN_AND_MEAN
#define VK_USE_PLATFORM_WIN32_KHR
#include <windows.h>

#include <libplacebo/cache.h>
#include <libplacebo/gpu.h>
#include <libplacebo/log.h>
#include <libplacebo/renderer.h>
#include <libplacebo/swapchain.h>
#include <libplacebo/utils/frame_queue.h>
#include <libplacebo/vulkan.h>

#define MAX_MONITORS 4

enum decode_mode { DECODE_SW, DECODE_VULKAN };
enum sched_mode { SCHED_OWN, SCHED_NEAREST };
enum swap_backend { BACKEND_LIBPLACEBO, BACKEND_OWN };

struct cfg {
    const char *file;
    enum decode_mode decode;
    const char *adapter;        // substring of the adapter name (default: first adapter that has outputs)
    const char *preset;         // fast | default | hq
    double seconds;             // playback time after the film clock starts
    bool windowed;              // smoke-test mode: half-size windows, not topmost, no focus steal
    double window_scale;        // --windowed: window size as a fraction of its monitor
    bool headless;              // --no-window: no window, no swapchain; renders to offscreen targets, waits for the vblank
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
    const char *out_dir;        // monitor-N-<mode>.json
    wchar_t refresh_device[32]; // --refresh DISPLAYn=HZ (CDS_FULLSCREEN, restored when the process ends)
    int refresh_hz;
    int log_level;              // stderr level of the libplacebo log
    int event_level;            // level of libplacebo messages kept in the in-memory event log (INFO; DEBUG with --event-level debug)
    bool probe_only;
    bool device_test;           // --device-test: make the instance and the device this backend / decoder would use, no window

    // Vulkan
    enum swap_backend backend;  // libplacebo's swapchain, or our own VkSwapchainKHR with present id / wait / timing
    int vk_depth;               // libplacebo swapchain_depth (images = depth + 1); own backend: image count = depth + 1
    const char *present_mode;   // fifo | fifo-relaxed | mailbox | immediate | latest-ready
    const char *layers_off;     // none | third-party | all-implicit (Vulkan implicit layers, see vk-layers.h)
    const char *cache_file;     // pl_cache file: loaded at start, saved at exit when it changed
    double hitch_ms;            // a present loop longer than this is a hitch: the log keeps what happened around it
    bool log_all;               // keep every libplacebo log line in the event log, not only the timing ones
    bool low_priority;          // below-normal process priority and no MMCSS boost: for self-tests next to a running game
    int wait_lag;               // own backend: before drawing frame N wait until frame N - lag is on screen (0 = acquire only)
    bool premultiplied;         // own backend: composite alpha of the swapchain (default opaque)
    bool sharing_exclusive;     // own backend: images owned by one queue family (default: shared by all of libplacebo's, as its wrap contract wants)
};

// ---- time + statistics (perf1vk-stats.c) ------------------------------------------------

double now_s(void);   // QueryPerformanceCounter, seconds
double now_ms(void);

struct summary { size_t n; double min, mean, p50, p90, p99, p999, max; };
void summarize(const double *v, size_t n, struct summary *out);

struct samples { double *v; size_t n, cap; };
void samples_push(struct samples *s, double x);
void samples_free(struct samples *s);

// ---- refresh grid of one monitor (perf1vk-clock.c) ----------------------------------------
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

// ---- Vulkan device, swapchains (perf1vk-gpu.c) --------------------------------------------

// One entry of the in-memory event log: a libplacebo or FFmpeg log line, or one of our own notes, with its QPC time.
struct log_event { double t; int level; char text[216]; };

struct own_swap;   // the own-swapchain backend's private state (perf1vk-own-swap.c)

struct gpu_ctx {
    pl_log log;
    pl_vk_inst inst;
    pl_vulkan vk;
    pl_gpu gpu;
    pl_cache cache;
    uint64_t cache_sig;
    VkPhysicalDevice phys;
    char device_name[256];
    bool have_timing;           // the device was created with VK_EXT_present_timing, present_id2 and present_wait2 on
    void *ff_vk_device;         // AVBufferRef *: FFmpeg's view of this device, for Vulkan Video decoding (NULL otherwise)
};

// One per monitor.
struct vk_swap {
    VkSurfaceKHR surface;
    int w, h;                   // size in pixels
    pl_swapchain pls;           // libplacebo backend
    pl_tex offscreen;           // --no-window: the target the picture is rendered into
    struct pl_swapchain_frame sf; // the frame between swap_begin and swap_end
    struct own_swap *own;       // own backend
    bool described;             // alpha and format below are filled (at the first frame)
    char alpha[24];             // alpha mode of the swapchain as libplacebo reports it: opaque / premultiplied / ...
    char format[24];            // pl_fmt name of the swapchain images (rgb10a2, bgra8)
    int images;                 // swapchain image count, 0 when unknown
    pl_tex seen[32];            // distinct swapchain images this monitor has drawn into
    int n_seen;
};

// Times around the present of one frame (QPC seconds): call = before the submit/present call, ret = it returned,
// done = everything the end of the frame waits for (libplacebo: the GPU has finished the frame).
struct swap_end_times { double call, ret, done; uint64_t id; };

// Event log: every libplacebo log line at DEBUG and above lands here with a timestamp (the stderr level is cfg.log_level).
void gpu_note(const char *fmt, ...);
struct log_event *gpu_log_events(size_t *n);   // valid until the process ends; not thread safe against writers
void gpu_log_free(void);

// Creates the Vulkan instance (with the Win32 surface extensions unless headless) and libplacebo's log.
bool gpu_instance_create(struct gpu_ctx *g, const struct cfg *cfg);
// Creates a VkSurfaceKHR for a window.
bool gpu_surface_create(struct gpu_ctx *g, HWND hwnd, VkSurfaceKHR *out);
// Picks the GPU that drives the screens (the DXGI adapter with outputs, by LUID; or --adapter), creates the device.
// surface: the first window's surface (NULL when headless).
bool gpu_device_create(struct gpu_ctx *g, const struct cfg *cfg, const LUID *adapter_luid, VkSurfaceKHR surface);
void gpu_destroy(struct gpu_ctx *g, const struct cfg *cfg);

// FFmpeg adopts libplacebo's VkDevice (perf1vk-hwdevice.c). Returns an AVBufferRef *, NULL on failure.
void *hwdevice_from_libplacebo(struct gpu_ctx *g);
void hwdevice_free(struct gpu_ctx *g);

bool swap_create(struct gpu_ctx *g, const struct cfg *cfg, struct vk_swap *s, int w, int h);
void swap_destroy(struct gpu_ctx *g, struct vk_swap *s);
// Starts a frame: blocks until the swapchain has an image free (this is what paces a monitor), fills `target`.
bool swap_begin(struct gpu_ctx *g, struct vk_swap *s, struct pl_frame *target);
// Submits the frame started by swap_begin and presents it. A started frame is always submitted.
bool swap_end(struct gpu_ctx *g, struct vk_swap *s, struct swap_end_times *t);

// Own-swapchain backend (perf1vk-own-swap.c): our VkSwapchainKHR over the surface, its images wrapped for libplacebo,
// present ids, present_wait2 pacing and VK_EXT_present_timing feedback.
bool own_swap_create(struct gpu_ctx *g, const struct cfg *cfg, struct vk_swap *s);
void own_swap_destroy(struct gpu_ctx *g, struct vk_swap *s);
bool own_swap_begin(struct gpu_ctx *g, struct vk_swap *s, struct pl_swapchain_frame *out);
bool own_swap_end(struct gpu_ctx *g, struct vk_swap *s, struct swap_end_times *t);
void own_swap_finish(struct gpu_ctx *g, struct vk_swap *s);      // end of run: read the last timing results
void own_swap_write_json(FILE *f, struct vk_swap *s);            // ,"own":{...} for the monitor log

// ---- decoder (perf1vk-decode.c) -----------------------------------------------------------

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

// The decoder pushes every decoded frame to each of the `nqueues` queues (an AVFrame reference each). hw_device: the
// AVBufferRef * from hwdevice_from_libplacebo for DECODE_VULKAN, else NULL.
struct decoder *decoder_open(const struct cfg *cfg, pl_gpu gpu, void *hw_device, pl_queue *queues, int nqueues);
bool decoder_start(struct decoder *d);
void decoder_stop(struct decoder *d);
void decoder_get_info(struct decoder *d, struct decoder_info *out);
void decoder_destroy(struct decoder **d);
