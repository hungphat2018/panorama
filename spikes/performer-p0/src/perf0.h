// perf0: spike P0 measurement player. Adapted from libplacebo demos/plplay.c
// and demos/window_glfw.c (both CC0 / public domain). Throwaway spike code.
//
// One window (GLFW, no client API), one libplacebo GPU (D3D11 or Vulkan),
// one FFmpeg decoder (software, D3D11VA or Vulkan Video), one pl_queue, one
// render loop paced by the swapchain's vsync present. Writes JSON + CSV.
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
#include <libplacebo/vulkan.h>

enum backend { BACKEND_D3D11, BACKEND_VULKAN };
enum decode_mode { DECODE_SW, DECODE_D3D11VA, DECODE_VULKAN };

struct cfg {
    const char *file;
    enum backend backend;
    enum decode_mode decode;
    const char *adapter;      // substring of the adapter / device name (optional)
    const char *preset;       // fast | default | hq
    int win_w, win_h;         // client size of the windowed (never fullscreen) window
    double seconds;           // measured playback time after the first presented frame; 0 = until EOF
    bool loop;                // loop at EOF (only useful with seconds > 0)
    bool worst_case;          // move the source crop 1/1000 px each refresh: defeats the frame cache, the whole pipeline runs every refresh
    bool sync_render;         // pl_gpu_finish after each render: wall time of CPU+GPU per render
    bool hidden;              // do not show the window (decode benchmark)
    bool bench_decode;        // decode as fast as possible for `seconds`, no render
    int bench_w, bench_h;     // > 0: headless render benchmark into an offscreen target of this size (no window)
    int bench_iters;          // timed renders for the render benchmark
    int vk_depth;             // pl_vulkan_swapchain_params.swapchain_depth (0 = libplacebo default of 3)
    bool own_present;         // D3D11: our own waitable DXGI swapchain wrapped by libplacebo, our own Present1 call
    const char *dump_path;    // write the Nth film frame (see dump_frame) rendered alone to this PPM
    int dump_frame;           // film frame index counted from the first decoded frame; -1 = off
    const char *cache_file;   // pl_cache file: load if present, save at exit
    const char *out_prefix;   // writes <prefix>.json and <prefix>.csv
    const char *run_id;
    int log_level;            // enum pl_log_level
    bool probe_only;          // print adapters / outputs / window monitor, then exit

    // Supported-file matrix switches (spikes/file-matrix). All off by default: the P0 numbers stay reproducible.
    const char *deinterlace;  // weave | bob | yadif | bwdif: libplacebo deinterlacing of frames FFmpeg flags as interlaced
    bool rotation;            // turn the picture by the stream's display matrix (pl_frame.rotation) when fitting it
    bool sw_fallback;         // hardware decode: when the decoder offers no hardware format, decode in software instead
    bool skip_bad_packets;    // carry on after AVERROR_INVALIDDATA from the decoder or demuxer instead of ending the stream
    bool synth_pts;           // frames without any timestamp (raw .264/.hevc streams): count them at the stream's frame rate
    bool sd_passthrough;      // show BT.601 (SD) material with BT.709 primaries, no gamut conversion (what mpv's target-prim=auto does)
};

// ---- time ---------------------------------------------------------------

double now_ms(void);                 // QueryPerformanceCounter, milliseconds
double process_age_ms(void);         // wall time since process creation (includes DLL loading)

// ---- statistics ---------------------------------------------------------

struct summary {
    size_t n;
    double min, mean, p50, p90, p99, p999, max;
};

void summarize(const double *v, size_t n, struct summary *out);
void json_string(FILE *f, const char *s); // quoted + escaped JSON string

struct samples {
    double *v;
    size_t n, cap;
};

void samples_push(struct samples *s, double x);
void samples_free(struct samples *s);

// ---- window + gpu (perf0-gpu.c) -------------------------------------------

struct gpu_ctx {
    void *win;                // GLFWwindow * (NULL when headless)
    bool glfw_ready;
    HWND hwnd;
    pl_gpu gpu;
    pl_swapchain sw;
    pl_d3d11 d3d11;
    pl_vk_inst vk_inst;
    pl_vulkan vk;
    void *vk_surface;         // VkSurfaceKHR
    void *ff_vk_device;       // AVBufferRef * (Vulkan Video: FFmpeg owns the VkDevice, libplacebo imports it)
    IDXGISwapChain1 *own_sc;  // --own-present: the swapchain we created and present on ourselves
    HANDLE own_waitable;      // its frame-latency waitable object (owned by the swapchain)

    char gpu_name[256];
    char monitor_name[64];
    int monitor_hz;           // current display mode of the monitor under the window
    int monitor_w, monitor_h;
    double t_window_ms, t_gpu_ms; // since process creation
};

bool gpu_init(struct gpu_ctx *g, const struct cfg *cfg, pl_log log);
void gpu_destroy(struct gpu_ctx *g);
void gpu_print_topology(FILE *f);            // DXGI adapters and the outputs hanging off them (JSON array)
void gpu_wait_frame_latency(struct gpu_ctx *g); // --own-present: block until DXGI wants the next frame
void gpu_present(struct gpu_ctx *g);         // pl_swapchain_swap_buffers, or our own Present1 with --own-present
bool gpu_poll_events(struct gpu_ctx *g);     // false when the window was closed
bool gpu_escape_pressed(struct gpu_ctx *g);

// ---- decoder (perf0-decode.c) ----------------------------------------------

struct decoder;

struct decoder_info {
    char codec[64];
    char pix_fmt[64];
    char hw_fmt[64];          // "" for software decoding
    int width, height;
    double fps;
    double duration_s;
    double t_open_ms;         // since process creation
    double t_codec_ms;
    double t_first_decoded_ms;
    uint32_t decoded, mapped, dropped;
    double decode_busy_s;     // wall time the decode thread spent inside avcodec send/receive
    double decode_thread_cpu_s; // CPU time of the decode thread itself (libav worker threads are not included)
};

struct decoder *decoder_open(const struct cfg *cfg, struct gpu_ctx *g, pl_log log, pl_queue queue);
bool decoder_start(struct decoder *d);
void decoder_stop(struct decoder *d);
void decoder_get_info(struct decoder *d, struct decoder_info *out);
void decoder_destroy(struct decoder **d);
// Decode as fast as possible, discarding frames, for cfg->seconds. Returns frames decoded.
bool decoder_bench(struct decoder *d, double seconds, uint32_t *frames_out, double *elapsed_s_out);
