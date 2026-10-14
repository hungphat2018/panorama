// GPU sharing levers for the E1 test: render-ahead depth and the presenter's GPU priority (see perf1-gpu-share.h), and the
// player's own VRAM reservation for the AI sides' live path.
#include "perf1-gpu-share.h"

#include <dxgi1_4.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_RECORDS 8
#define STATUS_PROC_NOT_FOUND ((LONG) 0xC0000139) // STATUS_ENTRYPOINT_NOT_FOUND: gdi32.dll has no such entry point

// D3DKMT_SCHEDULINGPRIORITYCLASS (d3dkmthk.h is a WDK header MinGW does not ship): IDLE 0 .. HIGH 4, REALTIME 5.
#define KMT_CLASS_HIGH 4
static const char *const k_class_names[] = { "IDLE", "BELOW_NORMAL", "NORMAL", "ABOVE_NORMAL", "HIGH", "REALTIME" };

typedef LONG (APIENTRY *kmt_set_class_fn)(HANDLE process, INT priority_class);
typedef LONG (APIENTRY *kmt_get_class_fn)(HANDLE process, INT *priority_class);

static struct {
    UINT latency;               // --frame-latency (1: perf1's own value)
    bool high;                  // --gpu-priority high
    UINT buffers;               // what gpu_share_buffer_count handed out
    struct { char name[64]; HRESULT hr; bool read_ok; UINT reads; } mon[MAX_RECORDS];
    int n_mon;
    bool class_set, class_read; // D3DKMT process class: set (high only), read back (always)
    LONG class_status, class_read_status;
    INT class_reads;
    bool thread_set, thread_read; // IDXGIDevice GPU thread priority: set (high only), read back (always)
    HRESULT thread_hr;
    INT thread_reads;
    UINT64 reserve_mb;          // --vram-reserve-mb (0: no reservation, perf1's own behaviour)
    HRESULT reserve_hr;
    bool reserve_read;
    DXGI_QUERY_VIDEO_MEMORY_INFO vram; // the local segment group after the reservation
} G = { .latency = 1 };

void gpu_share_usage(void)
{
    fprintf(stderr,
        "  GPU sharing (E1; real displays only):\n"
        "          [--frame-latency 1..15] [--gpu-priority default|high] [--vram-reserve-mb N]\n"
        "  --frame-latency N  every swapchain's maximum frame latency (default 1): up to N presents queue behind the one on\n"
        "                   screen (N + 1 buffers when N > 2); the engine still plans the next refresh, so a present lands\n"
        "                   N - 1 refreshes after its planned one (pace kept per monitor, seam offset not)\n"
        "  --gpu-priority high  D3DKMT process scheduling class HIGH before the device, then SetGPUThreadPriority(+7);\n"
        "                   return codes and read-backs go to the console and OUT/gpu-share.json\n"
        "  --vram-reserve-mb N  IDXGIAdapter3::SetVideoMemoryReservation of N MB of local VRAM for this process, so another\n"
        "                   process's demand (the AI sides' painter) evicts the player's resources last (E1's rule)\n");
}

int gpu_share_arg(int argc, char **argv, int *i)
{
    const char *a = argv[*i];
    const bool latency = !strcmp(a, "--frame-latency"), priority = !strcmp(a, "--gpu-priority");
    const bool reserve = !strcmp(a, "--vram-reserve-mb");
    if (!latency && !priority && !reserve)
        return 0;
    const char *v = *i + 1 < argc ? argv[*i + 1] : NULL;
    if (!v) {
        fprintf(stderr, "perf1: %s needs a value\n", a);
        return -1;
    }
    bool ok;
    if (reserve) {
        char *end = NULL;
        long long n = strtoll(v, &end, 10);
        ok = end != v && !*end && n >= 0 && n <= 8192;
        if (ok)
            G.reserve_mb = (UINT64) n;
    } else if (latency) {
        // DXGI accepts 1..16 (DXGI_MAX_SWAP_CHAIN_BUFFERS); N + 1 buffers must fit in those 16 as well
        char *end = NULL;
        long n = strtol(v, &end, 10);
        ok = end != v && !*end && n >= 1 && n <= 15;
        if (ok)
            G.latency = (UINT) n;
    } else {
        ok = !strcmp(v, "high") || !strcmp(v, "default");
        G.high = !strcmp(v, "high");
    }
    if (!ok) {
        fprintf(stderr, "perf1: %s: bad value '%s' (--frame-latency 1..15, --gpu-priority default|high, --vram-reserve-mb 0..8192)\n",
                a, v);
        return -1;
    }
    (*i)++;
    return 1;
}

UINT gpu_share_buffer_count(UINT base)
{
    // One buffer on screen plus N queued or being drawn. With N = 1 or 2 perf1's three buffers already have that room.
    G.buffers = G.latency + 1 > base ? G.latency + 1 : base;
    return G.buffers;
}

void gpu_share_frame_latency(IDXGISwapChain2 *sc, const char *monitor)
{
    HRESULT hr = IDXGISwapChain2_SetMaximumFrameLatency(sc, G.latency);
    UINT reads = 0;
    bool read_ok = SUCCEEDED(IDXGISwapChain2_GetMaximumFrameLatency(sc, &reads));
    if (FAILED(hr) || !read_ok || reads != G.latency)
        fprintf(stderr, "perf1: %s: SetMaximumFrameLatency(%u) returned 0x%08lx, reads %u%s\n", monitor, G.latency,
                (unsigned long) hr, reads, read_ok ? "" : " (GetMaximumFrameLatency failed)");
    if (G.n_mon == MAX_RECORDS)
        return;
    G.mon[G.n_mon].hr = hr;
    G.mon[G.n_mon].read_ok = read_ok;
    G.mon[G.n_mon].reads = reads;
    snprintf(G.mon[G.n_mon].name, sizeof(G.mon[G.n_mon].name), "%s", monitor);
    G.n_mon++;
}

// An entry point of gdi32.dll (the D3DKMT thunks), loaded by name like scanline-clock.h does: no WDK header, no import lib.
static FARPROC gdi32_proc(const char *name)
{
    HMODULE m = GetModuleHandleW(L"gdi32.dll");
    if (!m)
        m = LoadLibraryW(L"gdi32.dll");
    return m ? GetProcAddress(m, name) : NULL;
}

void gpu_share_before_device(void)
{
    if (!G.high)
        return;
    // The class is the process's, so it is set before the device and its contexts exist: no context is created under the old
    // class in case the scheduler takes the class only when a context is made.
    kmt_set_class_fn set = (kmt_set_class_fn) (void (*)(void)) gdi32_proc("D3DKMTSetProcessSchedulingPriorityClass");
    G.class_set = true;
    G.class_status = set ? set(GetCurrentProcess(), KMT_CLASS_HIGH) : STATUS_PROC_NOT_FOUND;
}

static const char *class_name(INT c)
{
    return c >= 0 && c < (INT) (sizeof(k_class_names) / sizeof(k_class_names[0])) ? k_class_names[c] : "?";
}

// Monitor names come from DXGI ("DISPLAY1"); anything that could break the JSON string is dropped, as perf1-log.c does.
static void write_name(FILE *f, const char *s)
{
    fputc('"', f);
    for (; *s; s++)
        if ((unsigned char) *s >= 0x20 && *s != '"' && *s != '\\')
            fputc(*s, f);
    fputc('"', f);
}

static void report(const char *out_dir)
{
    printf("gpu share: frame latency %u asked, %u buffers;", G.latency, G.buffers);
    for (int i = 0; i < G.n_mon; i++)
        printf(" %s SetMaximumFrameLatency 0x%08lx, reads %u%s", G.mon[i].name, (unsigned long) G.mon[i].hr, G.mon[i].reads,
               i + 1 < G.n_mon ? ";" : "");
    printf("\ngpu share: priority %s;", G.high ? "high" : "default");
    if (G.class_set)
        printf(" D3DKMTSetProcessSchedulingPriorityClass(HIGH) status 0x%08lx;", (unsigned long) G.class_status);
    if (G.class_read)
        printf(" process class reads %s (%d);", class_name(G.class_reads), G.class_reads);
    else
        printf(" process class not readable (status 0x%08lx);", (unsigned long) G.class_read_status);
    if (G.thread_set)
        printf(" SetGPUThreadPriority(+7) 0x%08lx;", (unsigned long) G.thread_hr);
    if (G.thread_read)
        printf(" GPU thread priority reads %d\n", G.thread_reads);
    else
        printf(" GPU thread priority not readable\n");
    if (G.reserve_mb)
        printf("gpu share: VRAM reservation %llu MB: 0x%08lx; local budget %.0f MB, used %.0f MB, reserved %.0f MB\n",
               (unsigned long long) G.reserve_mb, (unsigned long) G.reserve_hr, G.vram.Budget / 1048576.0,
               G.vram.CurrentUsage / 1048576.0, G.vram.CurrentReservation / 1048576.0);

    char path[1024];
    snprintf(path, sizeof(path), "%s/gpu-share.json", out_dir);
    FILE *f = fopen(path, "w");
    if (!f) {
        fprintf(stderr, "perf1: cannot write %s\n", path);
        return;
    }
    fprintf(f, "{\"schema\":\"panorama.perf1.gpu-share/1\",\"frame_latency\":{\"asked\":%u,\"buffers\":%u,\"monitors\":[",
            G.latency, G.buffers);
    for (int i = 0; i < G.n_mon; i++) {
        fprintf(f, "%s{\"name\":", i ? "," : "");
        write_name(f, G.mon[i].name);
        fprintf(f, ",\"set_hr\":\"0x%08lx\",\"reads\":%d}", (unsigned long) G.mon[i].hr,
                G.mon[i].read_ok ? (int) G.mon[i].reads : -1); // -1: GetMaximumFrameLatency failed
    }
    fprintf(f, "]},\"gpu_priority\":{\"asked\":\"%s\",\"process_class\":{\"set\":%s,\"set_status\":\"0x%08lx\",\"reads\":%d,"
               "\"reads_name\":\"%s\",\"read_status\":\"0x%08lx\"},\"gpu_thread\":{\"set\":%s,\"set_value\":7,\"set_hr\":\"0x%08lx\","
               "\"reads\":%d,\"read_ok\":%s}},\"vram_reservation\":{\"asked_mb\":%llu,\"set_hr\":\"0x%08lx\",\"read_ok\":%s,"
               "\"budget_mb\":%.1f,\"usage_mb\":%.1f,\"reserved_mb\":%.1f}}\n",
            G.high ? "high" : "default", G.class_set ? "true" : "false", (unsigned long) G.class_status,
            G.class_read ? G.class_reads : -1, G.class_read ? class_name(G.class_reads) : "", (unsigned long) G.class_read_status,
            G.thread_set ? "true" : "false", (unsigned long) G.thread_hr, G.thread_read ? G.thread_reads : 0,
            G.thread_read ? "true" : "false", (unsigned long long) G.reserve_mb, (unsigned long) G.reserve_hr,
            G.reserve_read ? "true" : "false", G.vram.Budget / 1048576.0, G.vram.CurrentUsage / 1048576.0,
            G.vram.CurrentReservation / 1048576.0);
    fclose(f);
    printf("wrote %s\n", path);
}

void gpu_share_after_device(ID3D11Device *dev, const char *out_dir)
{
    IDXGIDevice *dx = NULL;
    if (dev && SUCCEEDED(ID3D11Device_QueryInterface(dev, &IID_IDXGIDevice, (void **) &dx))) {
        if (G.high) {
            G.thread_set = true;
            G.thread_hr = IDXGIDevice_SetGPUThreadPriority(dx, 7); // relative priority: the top of -7..+7
        }
        G.thread_read = SUCCEEDED(IDXGIDevice_GetGPUThreadPriority(dx, &G.thread_reads));
        IDXGIAdapter *ad = NULL;
        IDXGIAdapter3 *ad3 = NULL;
        if (SUCCEEDED(IDXGIDevice_GetAdapter(dx, &ad)) &&
            SUCCEEDED(IDXGIAdapter_QueryInterface(ad, &IID_IDXGIAdapter3, (void **) &ad3))) {
            if (G.reserve_mb)
                G.reserve_hr = IDXGIAdapter3_SetVideoMemoryReservation(ad3, 0, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, G.reserve_mb << 20);
            G.reserve_read = SUCCEEDED(IDXGIAdapter3_QueryVideoMemoryInfo(ad3, 0, DXGI_MEMORY_SEGMENT_GROUP_LOCAL, &G.vram));
            IDXGIAdapter3_Release(ad3);
        }
        if (ad)
            IDXGIAdapter_Release(ad);
        IDXGIDevice_Release(dx);
    }
    // read back in both modes, so a default run shows what "default" is
    kmt_get_class_fn get = (kmt_get_class_fn) (void (*)(void)) gdi32_proc("D3DKMTGetProcessSchedulingPriorityClass");
    G.class_read_status = get ? get(GetCurrentProcess(), &G.class_reads) : STATUS_PROC_NOT_FOUND;
    G.class_read = G.class_read_status == 0;
    report(out_dir);
}
