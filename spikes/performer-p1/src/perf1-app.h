// Shared state and the seams between perf1's source files (spike code: one global `A`).
//
//   perf1-main.c      start-up, the film start, the main loop, tear-down
//   perf1-args.c      command line
//   perf1-display.c   adapter, monitors, windows, swapchains, the real GPU
//   perf1-headless.c  virtual displays and libplacebo's dummy GPU (--headless: no window, no graphics hardware)
//   perf1-render.c    what happens on one refresh of one monitor: ask the engine, fetch the frame, draw, present
//   perf1-control.c   commands from a script file and a named pipe
//   perf1-log.c       the per-monitor logs (analyze-perf1.py) and the cue log (analyze-cues.py)
//   perf1-engine*.c   decisions: which frame, which cue, when (shared with the headless simulation)
#pragma once

#define COBJMACROS
#include "perf1.h"
#include "perf1-layout.h"
#include "row-timing.h"

#include <d3d11.h>
#include <dxgi1_3.h>

struct present_rec { uint32_t id; double call, ret, target; int64_t frame; };
struct stat_rec { uint32_t present_count, present_refresh, sync_refresh; double sync_qpc, polled; };

struct fake_display;     // perf1-headless.c

struct monitor {
    int index;                  // DXGI output index (names the log file)
    int slot;                   // position in A.mon[]: the engine's monitor number
    WCHAR device[32];
    char name[64];              // "DISPLAY1"
    RECT rect;                  // desktop coordinates, pixels
    double w_mm, h_mm, left_mm; // physical span along the shared path
    IDXGIOutput *output;
    HWND hwnd;
    int sc_w, sc_h;             // swapchain size in pixels
    struct vclock clock;        // wake-up grid
    row_mon row;                // signal timing, scan-line grid, height / top / lag on the shared vertical axis

    IDXGISwapChain1 *sc;
    IDXGISwapChainMedia *media;
    HANDLE waitable;            // signalled when the monitor can take its next frame (headless: an event)
    pl_swapchain pls;
    pl_renderer rr;
    pl_queue queue;
    struct pl_render_params params;
    uint64_t gpu_acc_ns;        // sum of libplacebo's per-pass GPU timers for the current render

    uint64_t first_sig;         // queue signature of frame 0 of the running epoch (to turn a signature into an ordinal)
    int sig_epoch;              // the epoch first_sig belongs to
    double not_before;          // the engine has nothing for this monitor before this host time
    bool slot_taken;            // a wait took the frame-latency slot and nothing was presented: DXGI's semaphore only
                                // signals again after a present, so the retry must not wait on it
    uint32_t last_stat;

    struct present_rec *presents; size_t n_presents, cap_presents;
    struct stat_rec *stats; size_t n_stats, cap_stats;
    int comp_modes[4];
    struct samples render_cpu, render_gpu, render_sync, lead;
    uint32_t queue_more, empty_mix, render_fail, held_services, drawn_mismatch, skipped;
    char error[160];
    struct fake_display *fake;  // headless only
};

struct app {
    struct cfg cfg;
    struct monitor mon[MAX_MONITORS];
    int nmon;
    struct gpu_ctx gpu;
    struct decoder *dec;
    struct decoder_info di;
    struct cue_store store;
    struct engine engine;
    bool engine_ready;
    struct control *control;
    atomic_bool stop, render_done;

    // film clock
    int master;
    int64_t hold;
    bool locked;
    double t0, step_s, frame_dur, master_period;     // t0: host time the first frame is up on the master

    // where the screens agree (the shared vertical axis, master's top = 0), and what the model predicts for the start
    double aim_mm, band_top_mm, band_bottom_mm;
    double predicted_ms[3];
    bool predicted_valid;

    // picture
    int src_w, src_h;           // visible size of the decoded frames
    double sar;                 // pixel aspect ratio (0 = 1)
    struct lay_defaults lay_def;
    struct lay_monitor lay_mon[MAX_MONITORS];
    struct lay_picture first_pic;                    // the first shot's picture, for the per-monitor logs
    double pic_x0, pic_y0, pic_x1, pic_y1;           // its place on the surface
};

extern struct app A;

// perf1-args.c
bool args_parse(int argc, char **argv, struct cfg *cfg);
void args_usage(const char *argv0);

// perf1-display.c
void to_utf8(const WCHAR *w, char *out, int size);
void fail(struct monitor *m, const char *fmt, ...);
IDXGIAdapter1 *display_pick_adapter(void);
bool display_enumerate(IDXGIAdapter1 *adapter);
bool display_create_windows(void);
bool display_init_gpu(IDXGIAdapter1 *adapter);
bool display_switch_refresh(const wchar_t *device, int hz);
void display_probe(IDXGIAdapter1 *adapter);
void display_destroy(IDXGIAdapter1 *adapter);

// perf1-headless.c
bool headless_init(void);                 // fills A.mon[], A.gpu (dummy GPU), the rows and clocks of the virtual displays
bool headless_start(void);                // starts the virtual vblank threads
void headless_stop(void);
void headless_destroy(void);

// perf1-render.c
void render_init_monitor(struct monitor *m);        // renderer, queue and parameters of one monitor
bool render_warm_up(void);                          // frame 0 on every monitor once, and the picture defaults
void render_thread_start(HANDLE *out);
void render_vblank_thread_start(struct monitor *m, HANDLE *out);
double render_end_time(void);

// perf1-picture-*.c (the picture path, perf1-picture.h)
int picture_arg(int argc, char **argv, int *i);     // one of its options at argv[*i]: 1 taken, 0 not its, -1 malformed
void picture_usage(void);
bool picture_load_room(void);                       // --room: monitor sizes and places, before assign_geometry
double picture_top_mm(int slot, bool follower);     // v of a monitor's top edge: the room's, else --height-offset-mm

// perf1-log.c
void log_write_all(void);                // per-monitor logs + the cue log
