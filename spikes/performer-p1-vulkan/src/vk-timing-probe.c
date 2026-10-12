// vk-timing-probe: what do VK_KHR_present_id / present_wait, their "2" versions, and VK_EXT_present_timing do on a
// Win32 surface in a borderless window on this PC? Plain Vulkan (no libplacebo), one monitor at a time.
//
// For one monitor it opens a borderless window, creates a swapchain with present id, present wait and present timing,
// and runs a list of short experiments (default 2.5 s each). Every present is a cheap clear of the swapchain image to a
// slowly changing dark grey (invisible at any speed), so what is measured is the presentation engine, not rendering.
// Experiments differ in how the loop waits (acquire only, or vkWaitForPresent for the previous present), in which API
// version the ids use (v1: VK_KHR_present_id + present_wait, v2: VK_KHR_present_id2 + present_wait2) and in what is asked
// of VK_EXT_present_timing (no target, a relative target of k refreshes, nearest refresh cycle).
//
// Next to the Vulkan reports the probe records the monitor's own clocks, all on the QPC time base:
//   - IDXGIOutput::WaitForVBlank wake-ups (the start of the vertical blank, plus the thread's wake-up latency)
//   - a fitted top-row grid from D3DKMTGetScanLine samples (scanline-clock.h, row-timing.h): when row 0 is drawn
// so analyze-vk-timing.py can say whether "image first pixel out" is the vblank start, the top row, or something else,
// and whether a relative target moves a picture by whole refreshes.
//
// Usage: vk-timing-probe --monitor N [--windowed [--window-scale 0.5]] [--seconds-each 2.5] [--out DIR]
//          [--experiments NAME,NAME,...] [--images 3] [--alpha opaque|premultiplied] [--format bgra8|rgb10a2]
//          [--fse disallowed|default] [--domain auto|qpc|swapchain|stage] [--layers-off none|third-party|all-implicit]
//          [--try-absolute] [--list]
//   --windowed  half-size window on that monitor, not topmost, not activated (the smoke-test mode: never full screen)
//   N           DXGI output index of the adapter that drives the screens (see perf1vk --probe)
// CC0 / public domain. Throwaway spike code.
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#define VK_USE_PLATFORM_WIN32_KHR
#include <windows.h>
#include <dxgi.h>

#include <vulkan/vulkan.h>

#include <math.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <timeapi.h>

#include "row-timing.h"
#include "vk-layers.h"

#define MAX_IMAGES 8
#define MAX_STAGES 4
#define FEEDBACK_QUEUE 256

// ---- configuration and the experiment list -------------------------------------------------------------------

struct cfg {
    int monitor;
    bool windowed;
    double window_scale;
    double seconds_each;
    const char *out_dir;
    const char *only;           // comma-separated experiment names, NULL = all
    int images;
    bool premultiplied;         // composite alpha of the swapchain
    bool rgb10;
    bool fse_disallow;          // VK_FULL_SCREEN_EXCLUSIVE_DISALLOWED_EXT, as libplacebo does
    const char *domain;         // auto | qpc | swapchain | stage
    const char *layers_off;
    bool try_absolute;          // also run the absolute-target experiment (skipped with a note when the surface refuses it)
    bool selftest;              // make the instance and the device for --monitor, print what they offer, exit: no window
    bool list;
};

enum target_kind { TARGET_NONE, TARGET_RELATIVE, TARGET_ABSOLUTE };

struct experiment {
    const char *name;
    int api;                    // 1: VK_KHR_present_id + present_wait, no timing; 2: present_id2 + present_wait2 (+ timing)
    int wait_lag;               // before present N, wait until present N - lag has been shown; 0 = rely on the acquire
    bool timing;                // ask for stage times
    enum target_kind target;
    double target_refreshes;    // relative / absolute target, in refresh durations
    bool nearest;               // VK_PRESENT_TIMING_INFO_PRESENT_AT_NEAREST_REFRESH_CYCLE_BIT_EXT
    bool opt_in;                // only with --try-absolute
};

static const struct experiment EXPERIMENTS[] = {
    { "v1-wait",            1, 1, false, TARGET_NONE,     0.0, false, false },
    { "v2-wait",            2, 1, false, TARGET_NONE,     0.0, false, false },
    { "v2-wait-timing",     2, 1, true,  TARGET_NONE,     0.0, false, false },
    { "v2-acquire-timing",  2, 0, true,  TARGET_NONE,     0.0, false, false },
    { "rel-1",              2, 1, true,  TARGET_RELATIVE, 1.0, false, false },
    { "rel-2",              2, 1, true,  TARGET_RELATIVE, 2.0, false, false },
    { "rel-3",              2, 1, true,  TARGET_RELATIVE, 3.0, false, false },
    { "rel-5",              2, 1, true,  TARGET_RELATIVE, 5.0, false, false },
    { "rel-2.5",            2, 1, true,  TARGET_RELATIVE, 2.5, false, false },
    { "rel-2.5-nearest",    2, 1, true,  TARGET_RELATIVE, 2.5, true,  false },
    { "rel-2.4-nearest",    2, 1, true,  TARGET_RELATIVE, 2.4, true,  false },
    { "rel-2-acquire",      2, 0, true,  TARGET_RELATIVE, 2.0, false, false },
    { "abs-ahead",          2, 1, true,  TARGET_ABSOLUTE, 3.0, false, true  },
};
#define N_EXPERIMENTS ((int) (sizeof(EXPERIMENTS) / sizeof(EXPERIMENTS[0])))

// ---- Vulkan function table ---------------------------------------------------------------------------------

struct fns {
    PFN_vkGetPhysicalDeviceSurfaceCapabilities2KHR surf_caps2;
    PFN_vkGetPhysicalDeviceSurfaceFormatsKHR surf_formats;
    PFN_vkGetPhysicalDeviceSurfacePresentModesKHR surf_modes;
    PFN_vkGetPhysicalDeviceSurfaceSupportKHR surf_support;
    PFN_vkCreateWin32SurfaceKHR create_surface;
    PFN_vkDestroySurfaceKHR destroy_surface;
    PFN_vkGetDeviceProcAddr get_device_proc;
    // device
    PFN_vkCreateSwapchainKHR create_swapchain;
    PFN_vkDestroySwapchainKHR destroy_swapchain;
    PFN_vkGetSwapchainImagesKHR get_images;
    PFN_vkAcquireNextImageKHR acquire;
    PFN_vkQueuePresentKHR queue_present;
    PFN_vkWaitForPresentKHR wait_present1;
    PFN_vkWaitForPresent2KHR wait_present2;
    PFN_vkSetSwapchainPresentTimingQueueSizeEXT set_queue_size;
    PFN_vkGetSwapchainTimingPropertiesEXT get_timing_props;
    PFN_vkGetSwapchainTimeDomainPropertiesEXT get_time_domains;
    PFN_vkGetPastPresentationTimingEXT get_past_timing;
    PFN_vkGetCalibratedTimestampsKHR calibrated;
};

struct vk {
    struct fns f;
    VkInstance inst;
    VkPhysicalDevice phys;
    VkDevice dev;
    VkQueue queue;
    uint32_t qfam;
    VkSurfaceKHR surface;
    VkCommandPool pool;
    char device_name[256];
    bool have_timing, have_id2, have_wait2, have_id1, have_wait1;   // extension enabled and its feature switched on
    bool have_fse;              // VK_EXT_full_screen_exclusive is enabled (so the "disallowed" hint may be chained)
    bool can_calibrate_qpc;     // the device can read QueryPerformanceCounter next to another clock in one call
    VkTimeDomainKHR cal_domains[16];   // the domains vkGetCalibratedTimestampsKHR accepts on this device
    uint32_t n_cal_domains;
};

// ---- records ---------------------------------------------------------------------------------------------

struct present_log {
    uint64_t id;
    double acq_call, acq_ret;   // vkAcquireNextImageKHR
    double wait_call, wait_ret; // vkWaitForPresent for the previous present (0 when the loop does not wait)
    int wait_result;
    double call, ret;           // vkQueuePresentKHR
    int present_result;
    uint64_t target_raw;        // the target time handed over (raw units), 0 = none
};

struct stage_time { uint32_t stage; uint64_t raw; };

struct feedback_log {
    uint64_t id;
    double polled;
    uint64_t target_raw;
    int domain;
    uint64_t domain_id;
    int complete;
    int n_stages;
    struct stage_time stages[MAX_STAGES];
};

struct calib {
    double qpc_s;               // QueryPerformanceCounter domain, seconds
    uint64_t local_raw;         // the swapchain / stage domain, raw
    uint64_t max_dev_ns;
    int stage;                  // 0 = swapchain-local, else a present stage bit
};

struct exp_result {
    const struct experiment *spec;
    char status[200];           // why the run was skipped or stopped early, "" if it ran to the end
    struct present_log *p; size_t np, cap_p;
    struct feedback_log *fb; size_t nfb, cap_fb;
    struct calib *cal; size_t ncal, cap_cal;
    uint64_t refresh_duration_ns, refresh_interval_ns;
    int timing_props_result;
    uint32_t images;
    int domain; uint64_t domain_id;
    int n_domains; int domains[8]; uint64_t dom_ids[8];   // everything the swapchain reports
    int queue_full;
    double t_start, t_end;
};

#define PUSH(arr, n, cap, v) do { \
        if ((n) == (cap)) { \
            size_t nc_ = (cap) ? (cap) * 2 : 1024; \
            void *np_ = realloc((arr), nc_ * sizeof(*(arr))); \
            if (!np_) break; \
            (arr) = np_; (cap) = nc_; \
        } \
        (arr)[(n)++] = (v); \
    } while (0)

// ---- globals ---------------------------------------------------------------------------------------------

static struct cfg g_cfg;
static struct vk g_vk;
static atomic_bool g_stop_threads;
static double g_qpc_freq;

static double now_s(void)
{
    return scan_now_s();
}

static void die(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "vk-timing-probe: ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
}

static const char *vr(VkResult r)
{
    switch (r) {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_NOT_READY: return "VK_NOT_READY";
    case VK_TIMEOUT: return "VK_TIMEOUT";
    case VK_INCOMPLETE: return "VK_INCOMPLETE";
    case VK_SUBOPTIMAL_KHR: return "VK_SUBOPTIMAL_KHR";
    case VK_ERROR_OUT_OF_DATE_KHR: return "VK_ERROR_OUT_OF_DATE_KHR";
    case VK_ERROR_SURFACE_LOST_KHR: return "VK_ERROR_SURFACE_LOST_KHR";
    case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
    case VK_ERROR_PRESENT_TIMING_QUEUE_FULL_EXT: return "VK_ERROR_PRESENT_TIMING_QUEUE_FULL_EXT";
    case VK_ERROR_FULL_SCREEN_EXCLUSIVE_MODE_LOST_EXT: return "VK_ERROR_FULL_SCREEN_EXCLUSIVE_MODE_LOST_EXT";
    case VK_ERROR_VALIDATION_FAILED_EXT: return "VK_ERROR_VALIDATION_FAILED_EXT";
    default: return "other VkResult";
    }
}

static const char *domain_name(int d)
{
    switch (d) {
    case VK_TIME_DOMAIN_DEVICE_KHR: return "DEVICE";
    case VK_TIME_DOMAIN_CLOCK_MONOTONIC_KHR: return "CLOCK_MONOTONIC";
    case VK_TIME_DOMAIN_CLOCK_MONOTONIC_RAW_KHR: return "CLOCK_MONOTONIC_RAW";
    case VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR: return "QUERY_PERFORMANCE_COUNTER";
    case VK_TIME_DOMAIN_PRESENT_STAGE_LOCAL_EXT: return "PRESENT_STAGE_LOCAL";
    case VK_TIME_DOMAIN_SWAPCHAIN_LOCAL_EXT: return "SWAPCHAIN_LOCAL";
    default: return "other";
    }
}

static void to_utf8(const WCHAR *w, char *out, int size)
{
    if (!WideCharToMultiByte(CP_UTF8, 0, w, -1, out, size, NULL, NULL))
        out[0] = '\0';
}

// ---- monitor + window ----------------------------------------------------------------------------------------

struct monitor {
    int index;
    WCHAR device[32];
    char name[64];
    RECT rect;
    double h_mm;
    IDXGIOutput *output;
    HWND hwnd;
    int w, h;                   // client size = swapchain size
    LUID luid;
    row_mon row;
    double *vblanks; size_t nvb, cap_vb;
    CRITICAL_SECTION vb_lock;
    HANDLE vb_thread;
};

static struct monitor g_mon;

static bool find_monitor(int index)
{
    IDXGIFactory1 *fac = NULL;
    if (FAILED(CreateDXGIFactory1(&IID_IDXGIFactory1, (void **) &fac)))
        return false;
    bool found = false;
    for (UINT i = 0; !found; i++) {
        IDXGIAdapter1 *ad = NULL;
        if (IDXGIFactory1_EnumAdapters1(fac, i, &ad) == DXGI_ERROR_NOT_FOUND)
            break;
        DXGI_ADAPTER_DESC1 d;
        IDXGIAdapter1_GetDesc1(ad, &d);
        IDXGIOutput *out = NULL;
        if (!(d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) && SUCCEEDED(IDXGIAdapter1_EnumOutputs(ad, (UINT) index, &out))) {
            DXGI_OUTPUT_DESC od;
            IDXGIOutput_GetDesc(out, &od);
            if (od.AttachedToDesktop) {
                g_mon.index = index;
                wcsncpy(g_mon.device, od.DeviceName, 31);
                char full[64];
                to_utf8(od.DeviceName, full, sizeof(full));
                snprintf(g_mon.name, sizeof(g_mon.name), "%s", strncmp(full, "\\\\.\\", 4) ? full : full + 4);
                g_mon.rect = od.DesktopCoordinates;
                g_mon.output = out;
                g_mon.luid = d.AdapterLuid;
                found = true;
            } else {
                IDXGIOutput_Release(out);
            }
        }
        IDXGIAdapter1_Release(ad);
    }
    IDXGIFactory1_Release(fac);
    if (found) {
        int w = g_mon.rect.right - g_mon.rect.left, h = g_mon.rect.bottom - g_mon.rect.top;
        // Setup A's panel sizes (EDID / ruler); other panels fall back to 0.25 mm per pixel.
        g_mon.h_mm = (w == 2560 && h == 1600) ? 215.0 : (w == 1920 && h == 1080) ? 268.0 : h * 0.25;
    }
    return found;
}

static LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    if ((msg == WM_KEYDOWN && wp == VK_ESCAPE) || msg == WM_CLOSE) {
        atomic_store(&g_stop_threads, true);
        return 0;
    }
    if (msg == WM_SETCURSOR && !g_cfg.windowed) {
        SetCursor(NULL);
        return TRUE;
    }
    if (msg == WM_ERASEBKGND)
        return 1;
    return DefWindowProcW(hwnd, msg, wp, lp);
}

static bool create_window(void)
{
    WNDCLASSW wc = { .lpfnWndProc = window_proc, .hInstance = GetModuleHandleW(NULL), .lpszClassName = L"panorama-vk-timing-probe" };
    RegisterClassW(&wc);
    int mw = g_mon.rect.right - g_mon.rect.left, mh = g_mon.rect.bottom - g_mon.rect.top;
    int x = g_mon.rect.left, y = g_mon.rect.top, w = mw, h = mh;
    DWORD ex = WS_EX_TOPMOST;
    if (g_cfg.windowed) {
        w = (int) lround(mw * g_cfg.window_scale);
        h = (int) lround(mh * g_cfg.window_scale);
        x = g_mon.rect.left + (mw - w) / 2;
        y = g_mon.rect.top + (mh - h) / 2;
        ex = WS_EX_NOACTIVATE;
    }
    g_mon.hwnd = CreateWindowExW(ex, wc.lpszClassName, L"panorama vk-timing-probe", WS_POPUP, x, y, w, h, NULL, NULL,
                                 wc.hInstance, NULL);
    if (!g_mon.hwnd)
        return false;
    ShowWindow(g_mon.hwnd, g_cfg.windowed ? SW_SHOWNOACTIVATE : SW_SHOW);
    RECT rc;
    GetClientRect(g_mon.hwnd, &rc);
    g_mon.w = rc.right - rc.left;
    g_mon.h = rc.bottom - rc.top;
    if (!g_cfg.windowed)
        SetForegroundWindow(g_mon.hwnd);
    return true;
}

static void pump_messages(void)
{
    MSG msg;
    while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }
}

static DWORD WINAPI vblank_thread(LPVOID arg)
{
    SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
    while (!atomic_load(&g_stop_threads)) {
        if (FAILED(IDXGIOutput_WaitForVBlank(g_mon.output))) {
            Sleep(1);
            continue;
        }
        double t = now_s();
        EnterCriticalSection(&g_mon.vb_lock);
        PUSH(g_mon.vblanks, g_mon.nvb, g_mon.cap_vb, t);
        LeaveCriticalSection(&g_mon.vb_lock);
    }
    return 0;
}

// ---- Vulkan bring-up -----------------------------------------------------------------------------------------

static bool has_ext(const VkExtensionProperties *e, uint32_t n, const char *name)
{
    for (uint32_t i = 0; i < n; i++)
        if (!strcmp(e[i].extensionName, name))
            return true;
    return false;
}

static bool vk_init(void)
{
    struct vk *v = &g_vk;
    static const char *const iext[] = { "VK_KHR_surface", "VK_KHR_win32_surface", "VK_KHR_get_surface_capabilities2" };
    VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "vk-timing-probe",
                              .apiVersion = VK_API_VERSION_1_3 };
    VkInstanceCreateInfo ici = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app,
                                 .enabledExtensionCount = 3, .ppEnabledExtensionNames = iext };
    VkResult r = vkCreateInstance(&ici, NULL, &v->inst);
    if (r != VK_SUCCESS) {
        die("vkCreateInstance: %s", vr(r));
        return false;
    }
#define IFN(member, name) v->f.member = (PFN_##name) vkGetInstanceProcAddr(v->inst, #name)
    IFN(surf_caps2, vkGetPhysicalDeviceSurfaceCapabilities2KHR);
    IFN(surf_formats, vkGetPhysicalDeviceSurfaceFormatsKHR);
    IFN(surf_modes, vkGetPhysicalDeviceSurfacePresentModesKHR);
    IFN(surf_support, vkGetPhysicalDeviceSurfaceSupportKHR);
    IFN(create_surface, vkCreateWin32SurfaceKHR);
    IFN(destroy_surface, vkDestroySurfaceKHR);
    IFN(get_device_proc, vkGetDeviceProcAddr);
#undef IFN

    // the GPU whose LUID is the DXGI adapter of the monitor
    uint32_t n = 0;
    VkPhysicalDevice devs[8];
    vkEnumeratePhysicalDevices(v->inst, &n, NULL);
    if (n > 8)
        n = 8;
    vkEnumeratePhysicalDevices(v->inst, &n, devs);
    for (uint32_t i = 0; i < n && !v->phys; i++) {
        VkPhysicalDeviceIDProperties idp = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES };
        VkPhysicalDeviceProperties2 p2 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &idp };
        vkGetPhysicalDeviceProperties2(devs[i], &p2);
        LUID l = {0};
        if (idp.deviceLUIDValid)
            memcpy(&l, idp.deviceLUID, sizeof(l));
        if (idp.deviceLUIDValid && l.LowPart == g_mon.luid.LowPart && l.HighPart == g_mon.luid.HighPart) {
            v->phys = devs[i];
            snprintf(v->device_name, sizeof(v->device_name), "%s", p2.properties.deviceName);
        }
    }
    if (!v->phys) {
        die("no Vulkan device has the LUID of the adapter that drives %s", g_mon.name);
        return false;
    }

    // The queue family that draws and presents: a graphics family that can present to Win32 surfaces. Asking the family
    // (not a surface) lets the device be made before any window exists; vk_attach_surface() checks the real surface later.
    PFN_vkGetPhysicalDeviceWin32PresentationSupportKHR win32_support = (PFN_vkGetPhysicalDeviceWin32PresentationSupportKHR)
        vkGetInstanceProcAddr(v->inst, "vkGetPhysicalDeviceWin32PresentationSupportKHR");
    uint32_t nq = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(v->phys, &nq, NULL);
    VkQueueFamilyProperties *qf = calloc(nq ? nq : 1, sizeof(*qf));
    if (!win32_support || !qf) {
        die("cannot list the queue families (%s)", win32_support ? "out of memory" : "no Win32 presentation query");
        free(qf);
        return false;
    }
    vkGetPhysicalDeviceQueueFamilyProperties(v->phys, &nq, qf);
    int gfx = -1;
    for (uint32_t i = 0; i < nq && gfx < 0; i++)
        if ((qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT) && win32_support(v->phys, i))
            gfx = (int) i;
    free(qf);
    if (gfx < 0) {
        die("no graphics queue family can present to Win32 surfaces");
        return false;
    }
    v->qfam = (uint32_t) gfx;

    uint32_t ne = 0;
    vkEnumerateDeviceExtensionProperties(v->phys, NULL, &ne, NULL);
    VkExtensionProperties *ex = calloc(ne ? ne : 1, sizeof(*ex));
    vkEnumerateDeviceExtensionProperties(v->phys, NULL, &ne, ex);
    if (!has_ext(ex, ne, "VK_KHR_swapchain")) {
        die("the device has no VK_KHR_swapchain");
        free(ex);
        return false;
    }
    // Each extension is enabled only together with the extensions it depends on; its feature is switched on below only
    // if the device reports it.
    v->have_id1 = has_ext(ex, ne, "VK_KHR_present_id");
    v->have_wait1 = v->have_id1 && has_ext(ex, ne, "VK_KHR_present_wait");
    v->have_id2 = has_ext(ex, ne, "VK_KHR_present_id2");
    v->have_wait2 = v->have_id2 && has_ext(ex, ne, "VK_KHR_present_wait2");
    v->have_timing = v->have_id2 && has_ext(ex, ne, "VK_EXT_present_timing") && has_ext(ex, ne, "VK_KHR_calibrated_timestamps");
    v->have_fse = has_ext(ex, ne, "VK_EXT_full_screen_exclusive");
    free(ex);
    const char *dext[12];
    uint32_t nd = 0;
    dext[nd++] = "VK_KHR_swapchain";
    if (v->have_id1) dext[nd++] = "VK_KHR_present_id";
    if (v->have_wait1) dext[nd++] = "VK_KHR_present_wait";
    if (v->have_id2) dext[nd++] = "VK_KHR_present_id2";
    if (v->have_wait2) dext[nd++] = "VK_KHR_present_wait2";
    if (v->have_timing) {
        dext[nd++] = "VK_EXT_present_timing";
        dext[nd++] = "VK_KHR_calibrated_timestamps";
    }
    if (v->have_fse) dext[nd++] = "VK_EXT_full_screen_exclusive";

    // Feature structures only for the extensions that are enabled; the device fills them in with what it supports and the
    // same structures then switch exactly that on.
    VkPhysicalDevicePresentTimingFeaturesEXT f_timing = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_TIMING_FEATURES_EXT };
    VkPhysicalDevicePresentId2FeaturesKHR f_id2 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_ID_2_FEATURES_KHR };
    VkPhysicalDevicePresentWait2FeaturesKHR f_wait2 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_WAIT_2_FEATURES_KHR };
    VkPhysicalDevicePresentIdFeaturesKHR f_id = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_ID_FEATURES_KHR };
    VkPhysicalDevicePresentWaitFeaturesKHR f_wait = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_WAIT_FEATURES_KHR };
    void *fchain = NULL;
#define LINK_FEATURE(have, f) do { if (have) { (f).pNext = fchain; fchain = &(f); } } while (0)
    LINK_FEATURE(v->have_id1, f_id);
    LINK_FEATURE(v->have_wait1, f_wait);
    LINK_FEATURE(v->have_id2, f_id2);
    LINK_FEATURE(v->have_wait2, f_wait2);
    LINK_FEATURE(v->have_timing, f_timing);
#undef LINK_FEATURE
    VkPhysicalDeviceFeatures2 feats = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = fchain };
    vkGetPhysicalDeviceFeatures2(v->phys, &feats);
    memset(&feats.features, 0, sizeof(feats.features));     // nothing from the core feature list is needed
    v->have_id1 = v->have_id1 && f_id.presentId;
    v->have_wait1 = v->have_wait1 && f_wait.presentWait && v->have_id1;
    v->have_id2 = v->have_id2 && f_id2.presentId2;
    v->have_wait2 = v->have_wait2 && f_wait2.presentWait2 && v->have_id2;
    v->have_timing = v->have_timing && f_timing.presentTiming && v->have_id2;
    printf("device features: present_id %u, present_wait %u, present_id2 %u, present_wait2 %u, present_timing %u "
           "(absolute %u, relative %u)\n", (unsigned) v->have_id1, (unsigned) v->have_wait1, (unsigned) v->have_id2,
           (unsigned) v->have_wait2, (unsigned) v->have_timing,
           (unsigned) (v->have_timing && f_timing.presentAtAbsoluteTime), (unsigned) (v->have_timing && f_timing.presentAtRelativeTime));

    // Can this device read QueryPerformanceCounter next to a swapchain clock? Without that the stage times cannot be put
    // on the monitor's clock.
    if (v->have_timing) {
        PFN_vkGetPhysicalDeviceCalibrateableTimeDomainsKHR get_cal = (PFN_vkGetPhysicalDeviceCalibrateableTimeDomainsKHR)
            vkGetInstanceProcAddr(v->inst, "vkGetPhysicalDeviceCalibrateableTimeDomainsKHR");
        uint32_t nc = 16;
        VkResult cr = get_cal ? get_cal(v->phys, &nc, v->cal_domains) : VK_ERROR_EXTENSION_NOT_PRESENT;
        if (cr == VK_SUCCESS || cr == VK_INCOMPLETE) {
            v->n_cal_domains = nc < 16 ? nc : 16;
            for (uint32_t i = 0; i < v->n_cal_domains; i++)
                if (v->cal_domains[i] == VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR)
                    v->can_calibrate_qpc = true;
        }
        printf("device can calibrate (domains:");
        for (uint32_t i = 0; i < v->n_cal_domains; i++)
            printf(" %s", domain_name((int) v->cal_domains[i]));
        printf("): QueryPerformanceCounter against other clocks: %s\n", v->can_calibrate_qpc ? "yes" : "NO");
    }
    float prio = 1.0f;
    VkDeviceQueueCreateInfo qi = { .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = v->qfam,
                                   .queueCount = 1, .pQueuePriorities = &prio };
    VkDeviceCreateInfo di = { .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &feats, .queueCreateInfoCount = 1,
                              .pQueueCreateInfos = &qi, .enabledExtensionCount = nd, .ppEnabledExtensionNames = dext };
    r = vkCreateDevice(v->phys, &di, NULL, &v->dev);
    if (r != VK_SUCCESS) {
        die("vkCreateDevice: %s", vr(r));
        return false;
    }
    vkGetDeviceQueue(v->dev, v->qfam, 0, &v->queue);
#define DFN(member, name) v->f.member = (PFN_##name) v->f.get_device_proc(v->dev, #name)
    DFN(create_swapchain, vkCreateSwapchainKHR);
    DFN(destroy_swapchain, vkDestroySwapchainKHR);
    DFN(get_images, vkGetSwapchainImagesKHR);
    DFN(acquire, vkAcquireNextImageKHR);
    DFN(queue_present, vkQueuePresentKHR);
    DFN(wait_present1, vkWaitForPresentKHR);
    DFN(wait_present2, vkWaitForPresent2KHR);
    DFN(set_queue_size, vkSetSwapchainPresentTimingQueueSizeEXT);
    DFN(get_timing_props, vkGetSwapchainTimingPropertiesEXT);
    DFN(get_time_domains, vkGetSwapchainTimeDomainPropertiesEXT);
    DFN(get_past_timing, vkGetPastPresentationTimingEXT);
    DFN(calibrated, vkGetCalibratedTimestampsKHR);
#undef DFN

    VkCommandPoolCreateInfo pi = { .sType = VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO,
                                   .flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT, .queueFamilyIndex = v->qfam };
    r = vkCreateCommandPool(v->dev, &pi, NULL, &v->pool);
    if (r != VK_SUCCESS) {
        die("vkCreateCommandPool: %s", vr(r));
        return false;
    }
    return true;
}

// The surface of the monitor's window, and the check that the queue family chosen above can present to it.
static bool vk_attach_surface(void)
{
    struct vk *v = &g_vk;
    VkWin32SurfaceCreateInfoKHR sci = { .sType = VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR,
                                        .hinstance = GetModuleHandleW(NULL), .hwnd = g_mon.hwnd };
    VkResult r = v->f.create_surface(v->inst, &sci, NULL, &v->surface);
    if (r != VK_SUCCESS) {
        die("vkCreateWin32SurfaceKHR: %s", vr(r));
        return false;
    }
    VkBool32 can_present = VK_FALSE;
    r = v->f.surf_support(v->phys, v->qfam, v->surface, &can_present);
    if (r != VK_SUCCESS || !can_present) {
        die("queue family %u cannot present to %s's surface (%s)", v->qfam, g_mon.name, vr(r));
        return false;
    }
    return true;
}

// ---- surface capabilities (printed once, written to the JSON) --------------------------------------------------

struct surf_info {
    VkSurfaceCapabilitiesKHR caps;
    VkPresentTimingSurfaceCapabilitiesEXT timing;
    VkSurfaceCapabilitiesPresentId2KHR id2;
    VkSurfaceCapabilitiesPresentWait2KHR wait2;
    VkPresentModeKHR modes[16];
    uint32_t n_modes;
    VkSurfaceFormatKHR formats[32];
    uint32_t n_formats;
    VkExtent2D current_extent;
};

static struct surf_info g_surf;

static bool query_surface(void)
{
    struct vk *v = &g_vk;
    struct surf_info *s = &g_surf;
    memset(s, 0, sizeof(*s));
    s->timing.sType = VK_STRUCTURE_TYPE_PRESENT_TIMING_SURFACE_CAPABILITIES_EXT;
    s->id2.sType = VK_STRUCTURE_TYPE_SURFACE_CAPABILITIES_PRESENT_ID_2_KHR;
    s->id2.pNext = &s->timing;
    s->wait2.sType = VK_STRUCTURE_TYPE_SURFACE_CAPABILITIES_PRESENT_WAIT_2_KHR;
    s->wait2.pNext = &s->id2;
    // Not chained: VkSurfaceCapabilitiesFullScreenExclusiveEXT needs the Win32 info structure in the query, and
    // full-screen exclusive support is already known from vulkaninfo; the swapchains here ask for it to be disallowed.
    VkSurfaceCapabilities2KHR c2 = { .sType = VK_STRUCTURE_TYPE_SURFACE_CAPABILITIES_2_KHR, .pNext = &s->wait2 };
    VkPhysicalDeviceSurfaceInfo2KHR si = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SURFACE_INFO_2_KHR, .surface = v->surface };
    VkResult r = v->f.surf_caps2(v->phys, &si, &c2);
    if (r != VK_SUCCESS) {
        die("vkGetPhysicalDeviceSurfaceCapabilities2KHR: %s", vr(r));
        return false;
    }
    s->caps = c2.surfaceCapabilities;
    s->current_extent = s->caps.currentExtent;
    s->n_modes = 16;
    r = v->f.surf_modes(v->phys, v->surface, &s->n_modes, s->modes);   // VK_INCOMPLETE: more modes than the list holds
    if (r != VK_SUCCESS && r != VK_INCOMPLETE) {
        die("vkGetPhysicalDeviceSurfacePresentModesKHR: %s", vr(r));
        return false;
    }
    s->n_formats = 32;
    r = v->f.surf_formats(v->phys, v->surface, &s->n_formats, s->formats);
    if ((r != VK_SUCCESS && r != VK_INCOMPLETE) || !s->n_formats) {
        die("vkGetPhysicalDeviceSurfaceFormatsKHR: %s, %u formats", vr(r), s->n_formats);
        return false;
    }
    printf("surface %s: extent %ux%u, images %u..%u, alpha 0x%x, usage 0x%x\n", g_mon.name, s->caps.currentExtent.width,
           s->caps.currentExtent.height, s->caps.minImageCount, s->caps.maxImageCount, s->caps.supportedCompositeAlpha,
           s->caps.supportedUsageFlags);
    printf("  present timing: supported %u, absolute %u, relative %u, stage queries 0x%x (queue-ops-end %u, request-dequeued %u, "
           "first-pixel-out %u, first-pixel-visible %u)\n", (unsigned) s->timing.presentTimingSupported,
           (unsigned) s->timing.presentAtAbsoluteTimeSupported, (unsigned) s->timing.presentAtRelativeTimeSupported,
           s->timing.presentStageQueries, !!(s->timing.presentStageQueries & VK_PRESENT_STAGE_QUEUE_OPERATIONS_END_BIT_EXT),
           !!(s->timing.presentStageQueries & VK_PRESENT_STAGE_REQUEST_DEQUEUED_BIT_EXT),
           !!(s->timing.presentStageQueries & VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_OUT_BIT_EXT),
           !!(s->timing.presentStageQueries & VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_VISIBLE_BIT_EXT));
    printf("  present id2 supported %u, present wait2 supported %u\n",
           (unsigned) s->id2.presentId2Supported, (unsigned) s->wait2.presentWait2Supported);
    return true;
}

// ---- one swapchain and its per-image objects ------------------------------------------------------------------

struct chain {
    VkSwapchainKHR sc;
    VkFormat format;
    uint32_t n_images;
    VkImage images[MAX_IMAGES];
    VkCommandBuffer cmd[MAX_IMAGES];
    VkFence fence[MAX_IMAGES];
    VkSemaphore render_done[MAX_IMAGES];
    VkSemaphore acquire_sem[MAX_IMAGES + 1];
    uint32_t acquire_idx;
    VkExtent2D extent;
};

static VkFormat pick_format(void)
{
    VkFormat want = g_cfg.rgb10 ? VK_FORMAT_A2B10G10R10_UNORM_PACK32 : VK_FORMAT_B8G8R8A8_UNORM;
    for (uint32_t i = 0; i < g_surf.n_formats; i++)
        if (g_surf.formats[i].format == want && g_surf.formats[i].colorSpace == VK_COLOR_SPACE_SRGB_NONLINEAR_KHR)
            return want;
    return g_surf.formats[0].format;
}

static bool chain_create(struct chain *c, int api, bool timing, VkSwapchainKHR old)
{
    struct vk *v = &g_vk;
    memset(c, 0, sizeof(*c));
    c->format = pick_format();
    c->extent = g_surf.caps.currentExtent;
    if (c->extent.width == 0xFFFFFFFFu) {
        c->extent.width = (uint32_t) g_mon.w;
        c->extent.height = (uint32_t) g_mon.h;
    }

    VkSurfaceFullScreenExclusiveInfoEXT fse = { .sType = VK_STRUCTURE_TYPE_SURFACE_FULL_SCREEN_EXCLUSIVE_INFO_EXT,
                                                .fullScreenExclusive = VK_FULL_SCREEN_EXCLUSIVE_DISALLOWED_EXT };
    uint32_t images = (uint32_t) g_cfg.images;
    if (images < g_surf.caps.minImageCount)
        images = g_surf.caps.minImageCount;
    if (g_surf.caps.maxImageCount && images > g_surf.caps.maxImageCount)
        images = g_surf.caps.maxImageCount;
    if (images > MAX_IMAGES)
        images = MAX_IMAGES;
    VkCompositeAlphaFlagBitsKHR alpha = g_cfg.premultiplied ? VK_COMPOSITE_ALPHA_PRE_MULTIPLIED_BIT_KHR : VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
    if (!(g_surf.caps.supportedCompositeAlpha & alpha)) {
        die("the surface does not support composite alpha 0x%x (it supports 0x%x)", (unsigned) alpha, g_surf.caps.supportedCompositeAlpha);
        return false;
    }
    // The clear needs transfer-destination usage; colour-attachment usage is added when the surface allows it.
    VkImageUsageFlags usage = (VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT) & g_surf.caps.supportedUsageFlags;
    if (!(usage & VK_IMAGE_USAGE_TRANSFER_DST_BIT)) {
        die("the surface's images cannot be a transfer destination (usage flags 0x%x)", g_surf.caps.supportedUsageFlags);
        return false;
    }
    VkSwapchainCreateInfoKHR sci = {
        .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR,
        .pNext = (g_cfg.fse_disallow && v->have_fse) ? &fse : NULL,
        .surface = v->surface,
        .minImageCount = images,
        .imageFormat = c->format,
        .imageColorSpace = VK_COLOR_SPACE_SRGB_NONLINEAR_KHR,
        .imageExtent = c->extent,
        .imageArrayLayers = 1,
        .imageUsage = usage,
        .imageSharingMode = VK_SHARING_MODE_EXCLUSIVE,
        .preTransform = g_surf.caps.currentTransform,
        .compositeAlpha = alpha,
        .presentMode = VK_PRESENT_MODE_FIFO_KHR,
        .clipped = VK_TRUE,
        .oldSwapchain = old,
    };
    if (api == 2) {
        sci.flags |= VK_SWAPCHAIN_CREATE_PRESENT_ID_2_BIT_KHR | VK_SWAPCHAIN_CREATE_PRESENT_WAIT_2_BIT_KHR;
        if (timing)
            sci.flags |= VK_SWAPCHAIN_CREATE_PRESENT_TIMING_BIT_EXT;
    }
    VkResult r = v->f.create_swapchain(v->dev, &sci, NULL, &c->sc);
    if (r != VK_SUCCESS) {
        die("vkCreateSwapchainKHR (api %d, timing %d): %s", api, (int) timing, vr(r));
        return false;
    }
    c->n_images = MAX_IMAGES;
    r = v->f.get_images(v->dev, c->sc, &c->n_images, c->images);
    if (r != VK_SUCCESS) {      // VK_INCOMPLETE would mean more images than the arrays here hold
        die("vkGetSwapchainImagesKHR: %s (room for %d images)", vr(r), MAX_IMAGES);
        c->n_images = 0;
        return false;
    }
    VkCommandBufferAllocateInfo ai = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO, .commandPool = v->pool,
                                       .level = VK_COMMAND_BUFFER_LEVEL_PRIMARY, .commandBufferCount = c->n_images };
    if (vkAllocateCommandBuffers(v->dev, &ai, c->cmd) != VK_SUCCESS)
        return false;
    VkFenceCreateInfo fi = { .sType = VK_STRUCTURE_TYPE_FENCE_CREATE_INFO, .flags = VK_FENCE_CREATE_SIGNALED_BIT };
    VkSemaphoreCreateInfo semi = { .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO };
    for (uint32_t i = 0; i < c->n_images; i++)
        if (vkCreateFence(v->dev, &fi, NULL, &c->fence[i]) != VK_SUCCESS ||
            vkCreateSemaphore(v->dev, &semi, NULL, &c->render_done[i]) != VK_SUCCESS)
            return false;
    for (uint32_t i = 0; i < c->n_images + 1; i++)
        if (vkCreateSemaphore(v->dev, &semi, NULL, &c->acquire_sem[i]) != VK_SUCCESS)
            return false;
    return true;
}

static void chain_destroy(struct chain *c)
{
    struct vk *v = &g_vk;
    if (!c->sc && !c->n_images)
        return;
    vkDeviceWaitIdle(v->dev);
    for (uint32_t i = 0; i < c->n_images; i++) {
        if (c->fence[i]) vkDestroyFence(v->dev, c->fence[i], NULL);
        if (c->render_done[i]) vkDestroySemaphore(v->dev, c->render_done[i], NULL);
    }
    for (uint32_t i = 0; i < c->n_images + 1; i++)
        if (c->acquire_sem[i]) vkDestroySemaphore(v->dev, c->acquire_sem[i], NULL);
    if (c->n_images && c->cmd[0])
        vkFreeCommandBuffers(v->dev, v->pool, c->n_images, c->cmd);
    if (c->sc)
        v->f.destroy_swapchain(v->dev, c->sc, NULL);
    memset(c, 0, sizeof(*c));
}

// Records "clear the image to this grey and hand it to the presentation engine" into the image's command buffer.
static bool record_clear(struct chain *c, uint32_t image, float grey)
{
    VkCommandBuffer cb = c->cmd[image];
    vkResetCommandBuffer(cb, 0);
    VkCommandBufferBeginInfo bi = { .sType = VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO,
                                    .flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT };
    if (vkBeginCommandBuffer(cb, &bi) != VK_SUCCESS)
        return false;
    VkImageSubresourceRange range = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    VkImageMemoryBarrier to_dst = { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, .dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .oldLayout = VK_IMAGE_LAYOUT_UNDEFINED, .newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = c->images[image], .subresourceRange = range };
    // the acquire semaphore is waited on at the transfer stage, so the layout change waits for it there too
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, NULL, 0, NULL, 1, &to_dst);
    VkClearColorValue color = { .float32 = { grey, grey, grey, 1.0f } };
    vkCmdClearColorImage(cb, c->images[image], VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, &color, 1, &range);
    VkImageMemoryBarrier to_present = { .sType = VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER, .srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT,
        .oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, .newLayout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR,
        .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .image = c->images[image], .subresourceRange = range };
    vkCmdPipelineBarrier(cb, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT, 0, 0, NULL, 0, NULL, 1, &to_present);
    return vkEndCommandBuffer(cb) == VK_SUCCESS;
}

// ---- time domains, calibration, feedback ------------------------------------------------------------------------

struct domain_choice {
    int domain;                 // VkTimeDomainKHR
    uint64_t id;
    bool found;
    int listed;                 // how many the swapchain reports
    int domains[8];
    uint64_t ids[8];
};

static bool device_calibrates(int domain)
{
    for (uint32_t i = 0; i < g_vk.n_cal_domains; i++)
        if ((int) g_vk.cal_domains[i] == domain)
            return true;
    return false;
}

static void query_domains(struct chain *c, struct domain_choice *dc)
{
    struct vk *v = &g_vk;
    memset(dc, 0, sizeof(*dc));
    VkSwapchainTimeDomainPropertiesEXT p = { .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_TIME_DOMAIN_PROPERTIES_EXT };
    uint64_t counter = 0;
    VkResult r = v->f.get_time_domains(v->dev, c->sc, &p, &counter);
    if (r != VK_SUCCESS || !p.timeDomainCount)
        return;
    uint32_t n = p.timeDomainCount;
    VkTimeDomainKHR *doms = calloc(n, sizeof(*doms));
    uint64_t *ids = calloc(n, sizeof(*ids));
    if (!doms || !ids) {
        free(doms);
        free(ids);
        return;
    }
    p.pTimeDomains = doms;
    p.pTimeDomainIds = ids;
    p.timeDomainCount = n;
    r = v->f.get_time_domains(v->dev, c->sc, &p, &counter);
    if (r == VK_SUCCESS || r == VK_INCOMPLETE) {
        for (uint32_t i = 0; i < p.timeDomainCount && i < 8; i++) {
            dc->domains[dc->listed] = (int) doms[i];
            dc->ids[dc->listed] = ids[i];
            dc->listed++;
        }
        // Choose. Preference (auto): QueryPerformanceCounter itself, then the per-stage clock, then the swapchain's clock.
        // The two swapchain clocks can be put on the monitor's clock only through a calibrated read, so they are usable
        // only when the device can calibrate QueryPerformanceCounter and (auto) lists the clock itself. An explicit
        // --domain is tried whatever the device lists: what then happens is a result.
        static const int rank[] = { VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR, VK_TIME_DOMAIN_PRESENT_STAGE_LOCAL_EXT,
                                    VK_TIME_DOMAIN_SWAPCHAIN_LOCAL_EXT };
        static const char *const rank_name[] = { "qpc", "stage", "swapchain" };
        const bool automatic = !strcmp(g_cfg.domain, "auto");
        int pick = -1;
        for (int k = 0; k < 3 && pick < 0; k++) {
            if (!automatic && strcmp(g_cfg.domain, rank_name[k]))
                continue;
            if (rank[k] != VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR && !v->can_calibrate_qpc)
                continue;
            if (automatic && rank[k] != VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR && !device_calibrates(rank[k]))
                continue;
            for (int i = 0; i < dc->listed && pick < 0; i++)
                if (dc->domains[i] == rank[k])
                    pick = i;
        }
        if (pick >= 0) {
            dc->domain = dc->domains[pick];
            dc->id = dc->ids[pick];
            dc->found = true;
        }
    }
    free(doms);
    free(ids);
}

// One calibrated read of the chosen domain against QPC. stage: 0 for the swapchain-local domain, else the present stage.
static bool calibrate(struct chain *c, const struct domain_choice *dc, uint32_t stage, struct calib *out)
{
    struct vk *v = &g_vk;
    if (!v->f.calibrated || !v->can_calibrate_qpc || !dc->found || dc->domain == VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR)
        return false; // the QPC domain is already QPC: its raw values need no conversion
    VkSwapchainCalibratedTimestampInfoEXT sci = { .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CALIBRATED_TIMESTAMP_INFO_EXT,
        .swapchain = c->sc, .presentStage = stage, .timeDomainId = dc->id };
    VkCalibratedTimestampInfoKHR infos[2] = {
        { .sType = VK_STRUCTURE_TYPE_CALIBRATED_TIMESTAMP_INFO_KHR, .pNext = &sci, .timeDomain = (VkTimeDomainKHR) dc->domain },
        { .sType = VK_STRUCTURE_TYPE_CALIBRATED_TIMESTAMP_INFO_KHR, .timeDomain = VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR },
    };
    uint64_t ts[2] = {0}, dev = 0;
    VkResult r = v->f.calibrated(v->dev, 2, infos, ts, &dev);
    if (r != VK_SUCCESS || !ts[0] || !ts[1])
        return false; // a read that comes back zero is not a time
    out->qpc_s = (double) ts[1] / g_qpc_freq;
    out->local_raw = ts[0];
    out->max_dev_ns = dev;
    out->stage = (int) stage;
    return true;
}

// Reads every clock the feedback can be in: one read for the swapchain-local domain, one per stage for the stage-local
// domain (each stage has its own clock). Done at the start, twice a second, and at the end, to see how the clocks drift.
static void calibrate_all(struct chain *c, const struct domain_choice *dc, struct exp_result *r)
{
    struct calib cal;
    if (dc->domain == VK_TIME_DOMAIN_PRESENT_STAGE_LOCAL_EXT) {
        static const uint32_t stages[] = { VK_PRESENT_STAGE_QUEUE_OPERATIONS_END_BIT_EXT, VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_OUT_BIT_EXT };
        for (size_t i = 0; i < sizeof(stages) / sizeof(stages[0]); i++)
            if (calibrate(c, dc, stages[i], &cal))
                PUSH(r->cal, r->ncal, r->cap_cal, cal);
    } else if (dc->domain == VK_TIME_DOMAIN_SWAPCHAIN_LOCAL_EXT) {
        if (calibrate(c, dc, 0, &cal))
            PUSH(r->cal, r->ncal, r->cap_cal, cal);
    }
}

// Reads every finished timing result of this swapchain into r->fb.
static int poll_feedback(struct chain *c, struct exp_result *r, bool partial)
{
    struct vk *v = &g_vk;
    VkPastPresentationTimingInfoEXT info = { .sType = VK_STRUCTURE_TYPE_PAST_PRESENTATION_TIMING_INFO_EXT,
        .flags = partial ? VK_PAST_PRESENTATION_TIMING_ALLOW_PARTIAL_RESULTS_BIT_EXT : 0, .swapchain = c->sc };
    VkPastPresentationTimingPropertiesEXT props = { .sType = VK_STRUCTURE_TYPE_PAST_PRESENTATION_TIMING_PROPERTIES_EXT };
    VkResult res = v->f.get_past_timing(v->dev, &info, &props);
    if ((res != VK_SUCCESS && res != VK_INCOMPLETE) || !props.presentationTimingCount)
        return 0;
    uint32_t n = props.presentationTimingCount;
    VkPastPresentationTimingEXT *out = calloc(n, sizeof(*out));
    VkPresentStageTimeEXT (*stages)[MAX_STAGES] = calloc(n, sizeof(*stages));
    if (!out || !stages) {
        free(out);
        free(stages);
        return 0;
    }
    for (uint32_t i = 0; i < n; i++) {
        out[i].sType = VK_STRUCTURE_TYPE_PAST_PRESENTATION_TIMING_EXT;
        out[i].presentStageCount = MAX_STAGES;
        out[i].pPresentStages = stages[i];
    }
    props.presentationTimingCount = n;
    props.pPresentationTimings = out;
    res = v->f.get_past_timing(v->dev, &info, &props);
    double polled = now_s();
    int got = 0;
    if (res == VK_SUCCESS || res == VK_INCOMPLETE) {
        for (uint32_t i = 0; i < props.presentationTimingCount; i++) {
            struct feedback_log fb = {0};
            fb.id = out[i].presentId;
            fb.polled = polled;
            fb.target_raw = out[i].targetTime;
            fb.domain = (int) out[i].timeDomain;
            fb.domain_id = out[i].timeDomainId;
            fb.complete = out[i].reportComplete;
            fb.n_stages = (int) (out[i].presentStageCount < MAX_STAGES ? out[i].presentStageCount : MAX_STAGES);
            for (int k = 0; k < fb.n_stages; k++) {
                fb.stages[k].stage = stages[i][k].stage;
                fb.stages[k].raw = stages[i][k].time;
            }
            PUSH(r->fb, r->nfb, r->cap_fb, fb);
            got++;
        }
    }
    free(out);
    free(stages);
    return got;
}

// ---- one experiment ---------------------------------------------------------------------------------------------

static void set_status(struct exp_result *r, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(r->status, sizeof(r->status), fmt, ap);
    va_end(ap);
    fprintf(stderr, "  %s: %s\n", r->spec->name, r->status);
}

static void run_experiment(const struct experiment *e, struct exp_result *r)
{
    struct vk *v = &g_vk;
    memset(r, 0, sizeof(*r));
    r->spec = e;
    struct chain c;
    bool timing = e->timing && v->have_timing && g_surf.timing.presentTimingSupported;
    if (e->api == 2 && (!v->have_id2 || !v->have_wait2 || !g_surf.id2.presentId2Supported || !g_surf.wait2.presentWait2Supported)) {
        set_status(r, "skipped: present_id2 / present_wait2 not available on this device or surface");
        return;
    }
    if (e->api == 1 && (!v->have_id1 || !v->have_wait1)) {
        set_status(r, "skipped: VK_KHR_present_id / present_wait not available");
        return;
    }
    if (e->timing && !timing) {
        set_status(r, "skipped: present timing not available on this device or surface");
        return;
    }
    // Ask only for stages the surface can report, and only use a target when the surface accepts that kind.
    const VkPresentStageFlagsEXT stage_queries = (VK_PRESENT_STAGE_QUEUE_OPERATIONS_END_BIT_EXT |
                                                  VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_OUT_BIT_EXT) & g_surf.timing.presentStageQueries;
    if (timing && !(stage_queries & VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_OUT_BIT_EXT)) {
        set_status(r, "skipped: the surface cannot report the image-first-pixel-out stage (stage queries 0x%x)",
                   g_surf.timing.presentStageQueries);
        return;
    }
    if (timing && e->target == TARGET_RELATIVE && !g_surf.timing.presentAtRelativeTimeSupported) {
        set_status(r, "skipped: the surface does not accept relative target times");
        return;
    }
    if (timing && e->target == TARGET_ABSOLUTE && !g_surf.timing.presentAtAbsoluteTimeSupported) {
        set_status(r, "skipped: the surface does not accept absolute target times");
        return;
    }
    if (!chain_create(&c, e->api, timing, VK_NULL_HANDLE)) {
        set_status(r, "swapchain creation failed");
        chain_destroy(&c);
        return;
    }
    r->images = c.n_images;

    struct domain_choice dc = {0};
    uint64_t timing_counter = 0;
    if (timing) {
        VkResult qr = v->f.set_queue_size(v->dev, c.sc, FEEDBACK_QUEUE);
        if (qr != VK_SUCCESS)
            set_status(r, "vkSetSwapchainPresentTimingQueueSizeEXT: %s", vr(qr));
        query_domains(&c, &dc);
        r->domain = dc.domain;
        r->domain_id = dc.id;
        r->n_domains = dc.listed;
        for (int i = 0; i < dc.listed; i++) {
            r->domains[i] = dc.domains[i];
            r->dom_ids[i] = dc.ids[i];
        }
        if (!dc.found) {
            set_status(r, "no usable time domain (the swapchain lists %d; --domain %s; QPC calibration %s)", dc.listed,
                       g_cfg.domain, v->can_calibrate_qpc ? "available" : "NOT available");
            chain_destroy(&c);
            return;
        }
        if (e->target == TARGET_ABSOLUTE && dc.domain == VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR) {
            // whether the engine counts QPC ticks or nanoseconds in this domain is one of the things the probe reports
            set_status(r, "skipped: an absolute target in the QPC domain would need its unit assumed; use --domain stage or swapchain");
            chain_destroy(&c);
            return;
        }
        calibrate_all(&c, &dc, r);
    }

    uint64_t refresh_ns = 0;
    uint64_t id = 0;            // presents attempted so far; the next one carries id + 1
    uint64_t last_ok = 0;       // id of the last present the engine accepted: a wait never names a refused present
    unsigned targeted = 0;      // presents that carried a target time
    double next_cal = now_s() + 0.5;
    r->t_start = now_s();
    double t_end = r->t_start + g_cfg.seconds_each;
    while (now_s() < t_end && !atomic_load(&g_stop_threads) && !r->status[0]) {
        pump_messages();
        struct present_log pl = {0};
        pl.id = id + 1;

        // wait until the present `lag` frames back has been shown
        if (e->wait_lag > 0 && id >= (uint64_t) e->wait_lag) {
            uint64_t wid = id + 1 - (uint64_t) e->wait_lag;
            if (wid > last_ok)
                wid = last_ok;
            if (wid) {
                pl.wait_call = now_s();
                VkResult wr;
                if (e->api == 2) {
                    VkPresentWait2InfoKHR wi = { .sType = VK_STRUCTURE_TYPE_PRESENT_WAIT_2_INFO_KHR, .presentId = wid,
                                                 .timeout = 200ull * 1000 * 1000 };
                    wr = v->f.wait_present2(v->dev, c.sc, &wi);
                } else {
                    wr = v->f.wait_present1(v->dev, c.sc, wid, 200ull * 1000 * 1000);
                }
                pl.wait_ret = now_s();
                pl.wait_result = (int) wr;
                if (wr != VK_SUCCESS && wr != VK_TIMEOUT && wr != VK_SUBOPTIMAL_KHR) {
                    set_status(r, "present wait for id %llu: %s", (unsigned long long) wid, vr(wr));
                    break;
                }
            }
        }
        if (timing)
            poll_feedback(&c, r, false);

        // acquire
        VkSemaphore asem = c.acquire_sem[c.acquire_idx];
        c.acquire_idx = (c.acquire_idx + 1) % (c.n_images + 1);
        uint32_t image = 0;
        pl.acq_call = now_s();
        VkResult ar = v->f.acquire(v->dev, c.sc, 500ull * 1000 * 1000, asem, VK_NULL_HANDLE, &image);
        pl.acq_ret = now_s();
        if (ar != VK_SUCCESS && ar != VK_SUBOPTIMAL_KHR) {
            set_status(r, "vkAcquireNextImageKHR: %s", vr(ar));
            PUSH(r->p, r->np, r->cap_p, pl);
            break;
        }

        // draw: a dark grey that creeps through 8 shades (differences of 1/255)
        vkWaitForFences(v->dev, 1, &c.fence[image], VK_TRUE, UINT64_MAX);
        vkResetFences(v->dev, 1, &c.fence[image]);
        float grey = (0.05f + (float) (id % 8) / 255.0f);
        if (!record_clear(&c, image, grey)) {
            set_status(r, "recording the clear failed");
            break;
        }
        VkPipelineStageFlags wait_stage = VK_PIPELINE_STAGE_TRANSFER_BIT;
        VkSubmitInfo si = { .sType = VK_STRUCTURE_TYPE_SUBMIT_INFO, .waitSemaphoreCount = 1, .pWaitSemaphores = &asem,
            .pWaitDstStageMask = &wait_stage, .commandBufferCount = 1, .pCommandBuffers = &c.cmd[image],
            .signalSemaphoreCount = 1, .pSignalSemaphores = &c.render_done[image] };
        if (vkQueueSubmit(v->queue, 1, &si, c.fence[image]) != VK_SUCCESS) {
            set_status(r, "vkQueueSubmit failed");
            break;
        }

        // present, with id and (optionally) timing
        uint64_t present_id = id + 1;
        VkPresentIdKHR pid1 = { .sType = VK_STRUCTURE_TYPE_PRESENT_ID_KHR, .swapchainCount = 1, .pPresentIds = &present_id };
        VkPresentId2KHR pid2 = { .sType = VK_STRUCTURE_TYPE_PRESENT_ID_2_KHR, .swapchainCount = 1, .pPresentIds = &present_id };
        VkPresentTimingInfoEXT ti = { .sType = VK_STRUCTURE_TYPE_PRESENT_TIMING_INFO_EXT, .timeDomainId = dc.id,
            .presentStageQueries = stage_queries, .targetTimeDomainPresentStage = VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_OUT_BIT_EXT };
        VkPresentTimingsInfoEXT tis = { .sType = VK_STRUCTURE_TYPE_PRESENT_TIMINGS_INFO_EXT, .swapchainCount = 1, .pTimingInfos = &ti };
        if (timing && e->target != TARGET_NONE) {
            if (!refresh_ns) {
                VkSwapchainTimingPropertiesEXT tp = { .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_TIMING_PROPERTIES_EXT };
                if (v->f.get_timing_props(v->dev, c.sc, &tp, &timing_counter) == VK_SUCCESS && tp.refreshDuration) {
                    refresh_ns = tp.refreshDuration;
                    r->refresh_duration_ns = tp.refreshDuration;
                    r->refresh_interval_ns = tp.refreshInterval;
                }
            }
            if (refresh_ns) {
                ti.targetTime = (uint64_t) llround(e->target_refreshes * (double) refresh_ns);
                if (e->target == TARGET_RELATIVE)
                    ti.flags |= VK_PRESENT_TIMING_INFO_PRESENT_AT_RELATIVE_TIME_BIT_EXT;
                if (e->target == TARGET_ABSOLUTE) {
                    // an absolute target: now, read in the target stage's clock, plus a few refreshes (both in nanoseconds)
                    struct calib now_cal;
                    uint32_t cal_stage = dc.domain == VK_TIME_DOMAIN_PRESENT_STAGE_LOCAL_EXT ? VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_OUT_BIT_EXT : 0;
                    if (!calibrate(&c, &dc, cal_stage, &now_cal)) {
                        set_status(r, "cannot read the clock for an absolute target");
                        break;
                    }
                    ti.targetTime += now_cal.local_raw;
                }
                if (e->nearest)
                    ti.flags |= VK_PRESENT_TIMING_INFO_PRESENT_AT_NEAREST_REFRESH_CYCLE_BIT_EXT;
                targeted++;
            }
        }
        void *chain_head = NULL;
        if (e->api == 2) {
            pid2.pNext = timing ? (const void *) &tis : NULL;
            chain_head = &pid2;
        } else {
            chain_head = &pid1;
        }
        VkPresentInfoKHR pi = { .sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR, .pNext = chain_head, .waitSemaphoreCount = 1,
            .pWaitSemaphores = &c.render_done[image], .swapchainCount = 1, .pSwapchains = &c.sc, .pImageIndices = &image };
        pl.target_raw = timing ? ti.targetTime : 0;
        pl.call = now_s();
        VkResult pr = v->f.queue_present(v->queue, &pi);
        pl.ret = now_s();
        pl.present_result = (int) pr;
        PUSH(r->p, r->np, r->cap_p, pl);
        id++;
        if (pr == VK_SUCCESS || pr == VK_SUBOPTIMAL_KHR) {
            last_ok = id;
        } else if (pr == VK_ERROR_PRESENT_TIMING_QUEUE_FULL_EXT) {
            // The engine hands the image back unpresented and the semaphore wait is consumed: this present is lost, and a
            // full queue means results stopped completing. The run ends here and says so.
            r->queue_full++;
            poll_feedback(&c, r, false);
            set_status(r, "present timing queue full at present %llu (%zu results read): results stopped completing",
                       (unsigned long long) id, r->nfb);
        } else {
            set_status(r, "vkQueuePresentKHR: %s", vr(pr));
        }

        // Calibrate again twice a second, to see how the swapchain clocks move against QPC.
        if (timing && now_s() >= next_cal) {
            calibrate_all(&c, &dc, r);
            next_cal = now_s() + 0.5;
        }
    }
    r->t_end = now_s();
    if (timing && e->target != TARGET_NONE && !targeted && !r->status[0])
        set_status(r, "no target time was applied: the swapchain never reported a refresh duration");

    // drain: let the last presents show and collect their feedback
    if (timing) {
        double until = now_s() + 0.6;
        while (now_s() < until && r->nfb < r->np) {
            poll_feedback(&c, r, false);
            Sleep(5);
        }
        poll_feedback(&c, r, true);
        calibrate_all(&c, &dc, r);
        VkSwapchainTimingPropertiesEXT tp = { .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_TIMING_PROPERTIES_EXT };
        VkResult tr = v->f.get_timing_props(v->dev, c.sc, &tp, &timing_counter);
        r->timing_props_result = (int) tr;
        if (tr == VK_SUCCESS) {
            r->refresh_duration_ns = tp.refreshDuration;
            r->refresh_interval_ns = tp.refreshInterval;
        }
    }
    chain_destroy(&c);
}

// ---- output ----------------------------------------------------------------------------------------------------

static void json_str(FILE *f, const char *s)
{
    fputc('"', f);
    for (; *s; s++) {
        if (*s == '"' || *s == '\\')
            fputc('\\', f);
        if ((unsigned char) *s >= 0x20 && (unsigned char) *s < 0x7f)
            fputc(*s, f);
    }
    fputc('"', f);
}

static void write_json(FILE *f, struct exp_result *res, int n_res)
{
    const scan_timing *t = &g_mon.row.timing;
    fprintf(f, "{\"monitor\":{\"index\":%d,\"name\":\"%s\",\"rect\":[%ld,%ld,%ld,%ld],\"window\":[%d,%d],\"windowed\":%s},",
            g_mon.index, g_mon.name, g_mon.rect.left, g_mon.rect.top, g_mon.rect.right, g_mon.rect.bottom, g_mon.w, g_mon.h,
            g_cfg.windowed ? "true" : "false");
    fprintf(f, "\"qpc_freq\":%.0f,\"device\":", g_qpc_freq);
    json_str(f, g_vk.device_name);
    fprintf(f, ",\"config\":{\"images\":%d,\"alpha\":\"%s\",\"format\":\"%s\",\"fse_disallowed\":%s,\"domain_pref\":\"%s\","
               "\"layers_off\":\"%s\",\"seconds_each\":%.2f},",
            g_cfg.images, g_cfg.premultiplied ? "premultiplied" : "opaque", g_cfg.rgb10 ? "rgb10a2" : "bgra8",
            g_cfg.fse_disallow ? "true" : "false", g_cfg.domain, g_cfg.layers_off, g_cfg.seconds_each);
    fprintf(f, "\"surface\":{\"extent\":[%u,%u],\"min_images\":%u,\"max_images\":%u,\"alpha_flags\":%u,"
               "\"present_timing_supported\":%u,\"absolute_supported\":%u,\"relative_supported\":%u,\"stage_queries\":%u,"
               "\"present_id2_supported\":%u,\"present_wait2_supported\":%u,\"present_modes\":[",
            g_surf.caps.currentExtent.width, g_surf.caps.currentExtent.height, g_surf.caps.minImageCount, g_surf.caps.maxImageCount,
            g_surf.caps.supportedCompositeAlpha, (unsigned) g_surf.timing.presentTimingSupported,
            (unsigned) g_surf.timing.presentAtAbsoluteTimeSupported, (unsigned) g_surf.timing.presentAtRelativeTimeSupported,
            g_surf.timing.presentStageQueries, (unsigned) g_surf.id2.presentId2Supported,
            (unsigned) g_surf.wait2.presentWait2Supported);
    for (uint32_t i = 0; i < g_surf.n_modes; i++)
        fprintf(f, "%s%d", i ? "," : "", (int) g_surf.modes[i]);
    fprintf(f, "]},\"time_domains\":[");
    for (int e = 0, first = 1; e < n_res; e++) {
        if (!res[e].n_domains)
            continue;
        for (int i = 0; i < res[e].n_domains; i++, first = 0)
            fprintf(f, "%s{\"domain\":%d,\"name\":\"%s\",\"id\":%llu}", first ? "" : ",", res[e].domains[i],
                    domain_name(res[e].domains[i]), (unsigned long long) res[e].dom_ids[i]);
        break; // the first experiment that asked for timing lists them; the others report the same
    }
    fprintf(f, "],\"timing\":{\"refresh_hz\":%.9f,\"line_s\":%.10g,\"scan_s\":%.10g,\"blank_s\":%.10g},", t->refresh_hz,
            t->line_s, t->scan_s, t->blank_s);
    const row_fit *g = &g_mon.row.fit_end;
    fprintf(f, "\"scan_grid\":{\"ok\":%s,\"base\":%.9f,\"period\":%.12f,\"resid_p50_us\":%.3f,\"resid_p95_us\":%.3f,\"samples\":%zu},",
            g->ok ? "true" : "false", g->base, g->period, g->resid_p50_us, g->resid_p95_us, g->samples);
    fprintf(f, "\"vblanks\":[");
    for (size_t i = 0; i < g_mon.nvb; i++)
        fprintf(f, "%s%.7f", i ? "," : "", g_mon.vblanks[i]);
    fprintf(f, "],\"experiments\":[");
    for (int e = 0; e < n_res; e++) {
        struct exp_result *r = &res[e];
        const struct experiment *s = r->spec;
        fprintf(f, "%s\n{\"name\":\"%s\",\"api\":%d,\"wait_lag\":%d,\"timing\":%s,\"target\":%d,\"target_refreshes\":%.3f,"
                   "\"nearest\":%s,\"status\":", e ? "," : "", s->name, s->api, s->wait_lag, s->timing ? "true" : "false",
                (int) s->target, s->target_refreshes, s->nearest ? "true" : "false");
        json_str(f, r->status);
        fprintf(f, ",\"images\":%u,\"domain\":%d,\"domain_id\":%llu,\"refresh_duration_ns\":%llu,\"refresh_interval_ns\":%llu,"
                   "\"timing_props_result\":%d,\"queue_full\":%d,\"t_start\":%.7f,\"t_end\":%.7f,\"calibrations\":[",
                r->images, r->domain, (unsigned long long) r->domain_id, (unsigned long long) r->refresh_duration_ns,
                (unsigned long long) r->refresh_interval_ns, r->timing_props_result, r->queue_full, r->t_start, r->t_end);
        for (size_t i = 0; i < r->ncal; i++)
            fprintf(f, "%s[%.7f,%llu,%llu,%d]", i ? "," : "", r->cal[i].qpc_s, (unsigned long long) r->cal[i].local_raw,
                    (unsigned long long) r->cal[i].max_dev_ns, r->cal[i].stage);
        fprintf(f, "],\"present_fields\":[\"id\",\"acq_call\",\"acq_ret\",\"wait_call\",\"wait_ret\",\"wait_result\",\"call\","
                   "\"ret\",\"result\",\"target_raw\"],\"presents\":[");
        for (size_t i = 0; i < r->np; i++) {
            const struct present_log *p = &r->p[i];
            fprintf(f, "%s[%llu,%.7f,%.7f,%.7f,%.7f,%d,%.7f,%.7f,%d,%llu]", i ? "," : "", (unsigned long long) p->id, p->acq_call,
                    p->acq_ret, p->wait_call, p->wait_ret, p->wait_result, p->call, p->ret, p->present_result,
                    (unsigned long long) p->target_raw);
        }
        fprintf(f, "],\"feedback\":[");
        for (size_t i = 0; i < r->nfb; i++) {
            const struct feedback_log *b = &r->fb[i];
            fprintf(f, "%s[%llu,%.7f,%llu,%d,%llu,%d,[", i ? "," : "", (unsigned long long) b->id, b->polled,
                    (unsigned long long) b->target_raw, b->domain, (unsigned long long) b->domain_id, b->complete);
            for (int k = 0; k < b->n_stages; k++)
                fprintf(f, "%s[%u,%llu]", k ? "," : "", b->stages[k].stage, (unsigned long long) b->stages[k].raw);
            fprintf(f, "]]");
        }
        fprintf(f, "]}");
    }
    fprintf(f, "\n]}\n");
}

// ---- main -------------------------------------------------------------------------------------------------------

static bool wanted(const char *name)
{
    if (!g_cfg.only)
        return true;
    size_t n = strlen(name);
    for (const char *p = g_cfg.only; *p;) {
        const char *e = strchr(p, ',');
        size_t len = e ? (size_t) (e - p) : strlen(p);
        if (len == n && !strncmp(p, name, n))
            return true;
        if (!e)
            break;
        p = e + 1;
    }
    return false;
}

static void usage(const char *argv0)
{
    fprintf(stderr,
        "Usage: %s --monitor N [--windowed [--window-scale 0.5]] [--seconds-each 2.5] [--out DIR]\n"
        "          [--experiments NAME,NAME,...] [--images 3] [--alpha opaque|premultiplied] [--format bgra8|rgb10a2]\n"
        "          [--fse disallowed|default] [--domain auto|qpc|swapchain|stage]\n"
        "          [--layers-off none|third-party|all-implicit] [--try-absolute] [--selftest] [--list]\n"
        "  --selftest  no window: pick the GPU that drives --monitor, make the device and print what it offers\n", argv0);
}

static bool parse_args(int argc, char **argv)
{
    g_cfg = (struct cfg) { .monitor = -1, .window_scale = 0.5, .seconds_each = 2.5, .out_dir = ".", .images = 3,
                           .fse_disallow = true, .domain = "auto", .layers_off = "none" };
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *val = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--monitor") && val) { g_cfg.monitor = atoi(val); i++; }
        else if (!strcmp(a, "--windowed")) g_cfg.windowed = true;
        else if (!strcmp(a, "--window-scale") && val) { g_cfg.window_scale = atof(val); i++; }
        else if (!strcmp(a, "--seconds-each") && val) { g_cfg.seconds_each = atof(val); i++; }
        else if (!strcmp(a, "--out") && val) { g_cfg.out_dir = val; i++; }
        else if (!strcmp(a, "--experiments") && val) { g_cfg.only = val; i++; }
        else if (!strcmp(a, "--images") && val) { g_cfg.images = atoi(val); i++; }
        else if (!strcmp(a, "--alpha") && val) {
            if (!strcmp(val, "premultiplied")) g_cfg.premultiplied = true;
            else if (strcmp(val, "opaque")) return false;
            i++;
        }
        else if (!strcmp(a, "--format") && val) {
            if (!strcmp(val, "rgb10a2")) g_cfg.rgb10 = true;
            else if (strcmp(val, "bgra8")) return false;
            i++;
        }
        else if (!strcmp(a, "--fse") && val) {
            if (!strcmp(val, "default")) g_cfg.fse_disallow = false;
            else if (strcmp(val, "disallowed")) return false;
            i++;
        }
        else if (!strcmp(a, "--domain") && val) {
            if (strcmp(val, "auto") && strcmp(val, "qpc") && strcmp(val, "swapchain") && strcmp(val, "stage"))
                return false;
            g_cfg.domain = val;
            i++;
        }
        else if (!strcmp(a, "--layers-off") && val) { g_cfg.layers_off = val; i++; }
        else if (!strcmp(a, "--try-absolute")) g_cfg.try_absolute = true;
        else if (!strcmp(a, "--selftest")) g_cfg.selftest = true;
        else if (!strcmp(a, "--list")) g_cfg.list = true;
        else return false;
    }
    if (g_cfg.list)
        return true;
    return g_cfg.monitor >= 0 && g_cfg.seconds_each > 0.2 && g_cfg.images >= 2 && g_cfg.images <= MAX_IMAGES &&
           g_cfg.window_scale > 0.05 && g_cfg.window_scale <= 1.0;
}

int main(int argc, char **argv)
{
    if (!parse_args(argc, argv)) {
        usage(argv[0]);
        return 2;
    }
    if (g_cfg.list) {
        for (int i = 0; i < N_EXPERIMENTS; i++)
            printf("%s%s\n", EXPERIMENTS[i].name, EXPERIMENTS[i].opt_in ? " (only with --try-absolute)" : "");
        return 0;
    }
    if (!vk_layers_apply(g_cfg.layers_off)) {
        fprintf(stderr, "vk-timing-probe: unknown --layers-off value '%s'\n", g_cfg.layers_off);
        return 2;
    }
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    timeBeginPeriod(1);
    LARGE_INTEGER fq;
    QueryPerformanceFrequency(&fq);
    g_qpc_freq = (double) fq.QuadPart;
    int rc = 1;
    struct exp_result *res = NULL;
    int n_res = 0;

    if (!find_monitor(g_cfg.monitor)) {
        die("no output %d on an adapter that drives the desktop", g_cfg.monitor);
        goto done;
    }
    if (g_cfg.selftest) {
        // no window, no surface: the GPU that drives the monitor, the device with the present set, its queue and pool
        if (!vk_init())
            goto done;
        printf("selftest ok: %s drives %s; queue family %u; ids/waits v1 %u/%u, v2 %u/%u, timing %u, calibrate QPC %u, fse %u\n",
               g_vk.device_name, g_mon.name, g_vk.qfam, (unsigned) g_vk.have_id1, (unsigned) g_vk.have_wait1,
               (unsigned) g_vk.have_id2, (unsigned) g_vk.have_wait2, (unsigned) g_vk.have_timing,
               (unsigned) g_vk.can_calibrate_qpc, (unsigned) g_vk.have_fse);
        rc = 0;
        goto done;
    }
    InitializeCriticalSection(&g_mon.vb_lock);
    if (!rowmon_open(&g_mon.row, g_mon.device, g_mon.h_mm)) {
        die("cannot read %s's signal timing or scan line", g_mon.name);
        goto done;
    }
    if (!create_window()) {
        die("CreateWindowEx failed");
        goto done;
    }
    if (!vk_init() || !vk_attach_surface() || !query_surface())
        goto done;
    printf("probe on %s (%s): %dx%d client, %.4f Hz signal, blank %.3f ms, %s\n", g_mon.name, g_vk.device_name, g_mon.w, g_mon.h,
           g_mon.row.timing.refresh_hz, g_mon.row.timing.blank_s * 1e3, g_cfg.windowed ? "windowed, half size" : "full-size borderless");

    g_mon.vb_thread = CreateThread(NULL, 0, vblank_thread, NULL, 0, NULL);
    if (!rowmon_start_sampler(&g_mon.row)) {
        die("scan-line sampler thread failed to start");
        goto done;
    }
    // let the window settle in the compositor before the first present
    double settle_end = now_s() + 1.0;
    while (now_s() < settle_end) {
        pump_messages();
        Sleep(5);
    }

    res = calloc(N_EXPERIMENTS, sizeof(*res));
    for (int i = 0; i < N_EXPERIMENTS && !atomic_load(&g_stop_threads); i++) {
        const struct experiment *e = &EXPERIMENTS[i];
        if (!wanted(e->name) || (e->opt_in && !g_cfg.try_absolute))
            continue;
        printf("experiment %s ...\n", e->name);
        fflush(stdout);
        run_experiment(e, &res[n_res]);
        struct exp_result *r = &res[n_res++];
        printf("  %zu presents, %zu timing results, refresh %.4f ms%s%s\n", r->np, r->nfb, r->refresh_duration_ns / 1e6,
               r->status[0] ? ", note: " : "", r->status);
    }

    // stop the clocks, fit the top-row grid over the whole run
    atomic_store(&g_stop_threads, true);
    if (g_mon.vb_thread) {
        WaitForSingleObject(g_mon.vb_thread, 2000);
        CloseHandle(g_mon.vb_thread);
        g_mon.vb_thread = NULL;
    }
    rowmon_stop_sampler(&g_mon.row);
    if (!rowmon_fit(&g_mon.row, &g_mon.row.fit_end))
        fprintf(stderr, "vk-timing-probe: no scan-line fit (%zu samples)\n", g_mon.row.fit_end.taken);
    else
        printf("scan grid: %zu of %zu samples, residual p50 %.2f us\n", g_mon.row.fit_end.samples, g_mon.row.fit_end.taken,
               g_mon.row.fit_end.resid_p50_us);

    CreateDirectoryA(g_cfg.out_dir, NULL);
    char path[1024];
    snprintf(path, sizeof(path), "%s/probe-%s.json", g_cfg.out_dir, g_mon.name);
    FILE *f = fopen(path, "w");
    if (f) {
        write_json(f, res, n_res);
        fclose(f);
        printf("wrote %s\n", path);
        rc = 0;
    } else {
        die("cannot write %s", path);
    }

done:
    if (g_vk.dev) {
        vkDeviceWaitIdle(g_vk.dev);
        if (g_vk.pool)
            vkDestroyCommandPool(g_vk.dev, g_vk.pool, NULL);
        vkDestroyDevice(g_vk.dev, NULL);
    }
    if (g_vk.inst) {
        if (g_vk.surface)
            g_vk.f.destroy_surface(g_vk.inst, g_vk.surface, NULL);
        vkDestroyInstance(g_vk.inst, NULL);
    }
    if (g_mon.hwnd)
        DestroyWindow(g_mon.hwnd);
    if (g_mon.output)
        IDXGIOutput_Release(g_mon.output);
    rowmon_close(&g_mon.row);
    for (int i = 0; i < n_res; i++) {
        free(res[i].p);
        free(res[i].fb);
        free(res[i].cal);
    }
    free(res);
    free(g_mon.vblanks);
    timeEndPeriod(1);
    return rc;
}
