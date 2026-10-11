// Window + libplacebo GPU bring-up for perf0 (D3D11 or Vulkan). Adapted from
// libplacebo demos/window_glfw.c (CC0 / public domain).
#define COBJMACROS
#define VK_NO_PROTOTYPES
#include "perf0.h"

#define GLFW_INCLUDE_VULKAN
#include <GLFW/glfw3.h>
#define GLFW_EXPOSE_NATIVE_WIN32
#include <GLFW/glfw3native.h>

#include <ctype.h>
#include <dxgi1_3.h> // IDXGISwapChain2: frame-latency waitable object

#include <libavutil/dict.h>
#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_vulkan.h>

// ---- helpers ----------------------------------------------------------------

static void json_escape(FILE *f, const char *s)
{
    for (; *s; s++) {
        if (*s == '"' || *s == '\\')
            fputc('\\', f);
        if ((unsigned char) *s >= 0x20)
            fputc(*s, f);
    }
}

static bool contains_nocase(const char *hay, const char *needle)
{
    size_t n = strlen(needle);
    for (; *hay; hay++) {
        size_t i = 0;
        while (i < n && hay[i] && tolower((unsigned char) hay[i]) == tolower((unsigned char) needle[i]))
            i++;
        if (i == n)
            return true;
    }
    return false;
}

static void wide_to_utf8(const WCHAR *w, char *out, int out_size)
{
    if (!WideCharToMultiByte(CP_UTF8, 0, w, -1, out, out_size, NULL, NULL))
        out[0] = '\0';
}

static void err_cb(int code, const char *desc)
{
    fprintf(stderr, "GLFW error %d: %s\n", code, desc);
}

static void resize_cb(GLFWwindow *win, int width, int height)
{
    struct gpu_ctx *g = glfwGetWindowUserPointer(win);
    if (g && g->sw && width > 0 && height > 0)
        pl_swapchain_resize(g->sw, &width, &height);
}

static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL get_vk_proc_addr(VkInstance instance, const char *name)
{
    return (PFN_vkVoidFunction) glfwGetInstanceProcAddress(instance, name);
}

// ---- DXGI topology: which adapter drives which screen ------------------------------

void gpu_print_topology(FILE *f)
{
    IDXGIFactory1 *fac = NULL;
    fputs("[", f);
    if (FAILED(CreateDXGIFactory1(&IID_IDXGIFactory1, (void **) &fac))) {
        fputs("]", f);
        return;
    }

    for (UINT i = 0;; i++) {
        IDXGIAdapter1 *ad = NULL;
        if (IDXGIFactory1_EnumAdapters1(fac, i, &ad) == DXGI_ERROR_NOT_FOUND)
            break;
        DXGI_ADAPTER_DESC1 d;
        IDXGIAdapter1_GetDesc1(ad, &d);
        char name[256];
        wide_to_utf8(d.Description, name, sizeof(name));
        fprintf(f, "%s{\"index\":%u,\"name\":\"", i ? "," : "", i);
        json_escape(f, name);
        fprintf(f, "\",\"vendor_id\":\"0x%04x\",\"device_id\":\"0x%04x\",\"luid\":\"%08lx:%08lx\","
                "\"dedicated_vram_mb\":%llu,\"software\":%s,\"outputs\":[",
                d.VendorId, d.DeviceId, (unsigned long) d.AdapterLuid.HighPart,
                (unsigned long) d.AdapterLuid.LowPart,
                (unsigned long long) (d.DedicatedVideoMemory >> 20),
                (d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) ? "true" : "false");

        for (UINT o = 0;; o++) {
            IDXGIOutput *out = NULL;
            if (IDXGIAdapter1_EnumOutputs(ad, o, &out) == DXGI_ERROR_NOT_FOUND)
                break;
            DXGI_OUTPUT_DESC od;
            IDXGIOutput_GetDesc(out, &od);
            char dev[64];
            wide_to_utf8(od.DeviceName, dev, sizeof(dev));
            fprintf(f, "%s{\"device\":\"", o ? "," : "");
            json_escape(f, dev);
            fprintf(f, "\",\"rect\":[%ld,%ld,%ld,%ld],\"attached\":%s}",
                    od.DesktopCoordinates.left, od.DesktopCoordinates.top,
                    od.DesktopCoordinates.right, od.DesktopCoordinates.bottom,
                    od.AttachedToDesktop ? "true" : "false");
            IDXGIOutput_Release(out);
        }
        fputs("]}", f);
        IDXGIAdapter1_Release(ad);
    }
    fputs("]", f);
    IDXGIFactory1_Release(fac);
}

static IDXGIAdapter *find_dxgi_adapter(const char *substr)
{
    IDXGIFactory1 *fac = NULL;
    IDXGIAdapter *found = NULL;
    if (FAILED(CreateDXGIFactory1(&IID_IDXGIFactory1, (void **) &fac)))
        return NULL;

    for (UINT i = 0; !found; i++) {
        IDXGIAdapter1 *ad = NULL;
        if (IDXGIFactory1_EnumAdapters1(fac, i, &ad) == DXGI_ERROR_NOT_FOUND)
            break;
        DXGI_ADAPTER_DESC1 d;
        IDXGIAdapter1_GetDesc1(ad, &d);
        char name[256];
        wide_to_utf8(d.Description, name, sizeof(name));
        if (contains_nocase(name, substr))
            found = (IDXGIAdapter *) ad; // keep the reference for the caller
        else
            IDXGIAdapter1_Release(ad);
    }
    IDXGIFactory1_Release(fac);
    return found;
}

static void query_adapter_name(ID3D11Device *dev, char *out, int out_size)
{
    IDXGIDevice *dxgi = NULL;
    IDXGIAdapter *ad = NULL;
    out[0] = '\0';
    if (SUCCEEDED(ID3D11Device_QueryInterface(dev, &IID_IDXGIDevice, (void **) &dxgi))) {
        if (SUCCEEDED(IDXGIDevice_GetAdapter(dxgi, &ad))) {
            DXGI_ADAPTER_DESC d;
            if (SUCCEEDED(IDXGIAdapter_GetDesc(ad, &d)))
                wide_to_utf8(d.Description, out, out_size);
            IDXGIAdapter_Release(ad);
        }
        IDXGIDevice_Release(dxgi);
    }
}

// ---- backends -----------------------------------------------------------------------

// D3D11VA decoding runs on the same ID3D11Device from the decoder thread while the render
// thread uses the immediate context, so the device must serialise its own calls.
static void enable_multithread_protection(ID3D11Device *dev)
{
    ID3D10Multithread *mt = NULL;
    if (SUCCEEDED(ID3D11Device_QueryInterface(dev, &IID_ID3D10Multithread, (void **) &mt))) {
        ID3D10Multithread_SetMultithreadProtected(mt, TRUE);
        ID3D10Multithread_Release(mt);
    }
}

// Our own flip-model swapchain with the frame-latency waitable object and a maximum latency of one
// frame, to be wrapped by libplacebo (pl_d3d11_swapchain_params.swapchain) while Present1 stays ours.
// Returns a reference to the swapchain (also kept in g->own_sc), or NULL.
static IDXGISwapChain *create_own_swapchain(struct gpu_ctx *g)
{
    ID3D11Device *dev = g->d3d11->device;
    IDXGIDevice1 *dxdev = NULL;
    IDXGIAdapter *adapter = NULL;
    IDXGIFactory2 *fac = NULL;
    IDXGISwapChain1 *sc = NULL;
    IDXGISwapChain2 *sc2 = NULL;
    IDXGISwapChain *out = NULL;

    if (FAILED(ID3D11Device_QueryInterface(dev, &IID_IDXGIDevice1, (void **) &dxdev)) ||
        FAILED(IDXGIDevice1_GetAdapter(dxdev, &adapter)) ||
        FAILED(IDXGIAdapter_GetParent(adapter, &IID_IDXGIFactory2, (void **) &fac)))
    {
        fprintf(stderr, "perf0: cannot reach the DXGI factory of the device\n");
        goto done;
    }

    RECT rc;
    GetClientRect(g->hwnd, &rc);
    DXGI_SWAP_CHAIN_DESC1 desc = {
        .Width = rc.right - rc.left,
        .Height = rc.bottom - rc.top,
        .Format = DXGI_FORMAT_R10G10B10A2_UNORM,
        .SampleDesc.Count = 1,
        .BufferUsage = DXGI_USAGE_SHADER_INPUT | DXGI_USAGE_RENDER_TARGET_OUTPUT | DXGI_USAGE_UNORDERED_ACCESS,
        .BufferCount = 3,
        .SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD,
        .Flags = DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT,
    };
    HRESULT hr = IDXGIFactory2_CreateSwapChainForHwnd(fac, (IUnknown *) dev, g->hwnd, &desc, NULL, NULL, &sc);
    if (FAILED(hr)) {
        fprintf(stderr, "perf0: CreateSwapChainForHwnd failed: 0x%08lx\n", (unsigned long) hr);
        goto done;
    }
    IDXGIFactory2_MakeWindowAssociation(fac, g->hwnd, DXGI_MWA_NO_WINDOW_CHANGES | DXGI_MWA_NO_ALT_ENTER);

    if (FAILED(IDXGISwapChain1_QueryInterface(sc, &IID_IDXGISwapChain2, (void **) &sc2))) {
        fprintf(stderr, "perf0: IDXGISwapChain2 not available\n");
        IDXGISwapChain1_Release(sc);
        goto done;
    }
    IDXGISwapChain2_SetMaximumFrameLatency(sc2, 1);
    g->own_waitable = IDXGISwapChain2_GetFrameLatencyWaitableObject(sc2);
    g->own_sc = sc; // keeps the creation reference
    IDXGISwapChain1_QueryInterface(sc, &IID_IDXGISwapChain, (void **) &out);

done:
    if (sc2)
        IDXGISwapChain2_Release(sc2);
    if (fac)
        IDXGIFactory2_Release(fac);
    if (adapter)
        IDXGIAdapter_Release(adapter);
    if (dxdev)
        IDXGIDevice1_Release(dxdev);
    return out;
}

static bool init_d3d11(struct gpu_ctx *g, const struct cfg *cfg, pl_log log)
{
    IDXGIAdapter *adapter = NULL;
    if (cfg->adapter) {
        adapter = find_dxgi_adapter(cfg->adapter);
        if (!adapter) {
            fprintf(stderr, "perf0: no DXGI adapter matching '%s'\n", cfg->adapter);
            return false;
        }
    }

    g->d3d11 = pl_d3d11_create(log, pl_d3d11_params(
        .adapter = adapter,
        .allow_software = false,
        .flags = cfg->decode == DECODE_D3D11VA ? D3D11_CREATE_DEVICE_VIDEO_SUPPORT : 0,
    ));
    if (adapter)
        IDXGIAdapter_Release(adapter);
    if (!g->d3d11) {
        fprintf(stderr, "perf0: pl_d3d11_create failed\n");
        return false;
    }

    if (cfg->decode == DECODE_D3D11VA)
        enable_multithread_protection(g->d3d11->device);
    query_adapter_name(g->d3d11->device, g->gpu_name, sizeof(g->gpu_name));

    g->gpu = g->d3d11->gpu;
    if (!g->hwnd)
        return true; // headless: render targets are offscreen textures

    IDXGISwapChain *own = cfg->own_present ? create_own_swapchain(g) : NULL;
    if (cfg->own_present && !own)
        return false;
    g->sw = pl_d3d11_create_swapchain(g->d3d11, pl_d3d11_swapchain_params(
        .window = own ? NULL : g->hwnd,
        .swapchain = own,
    ));
    if (own)
        IDXGISwapChain_Release(own); // libplacebo holds its own reference
    if (!g->sw) {
        fprintf(stderr, "perf0: pl_d3d11_create_swapchain failed\n");
        return false;
    }
    return true;
}

static VkPhysicalDevice pick_vk_device(pl_vk_inst inst, const char *substr)
{
    if (!substr)
        return VK_NULL_HANDLE;

    PFN_vkEnumeratePhysicalDevices enum_devs = (PFN_vkEnumeratePhysicalDevices)
        inst->get_proc_addr(inst->instance, "vkEnumeratePhysicalDevices");
    PFN_vkGetPhysicalDeviceProperties get_props = (PFN_vkGetPhysicalDeviceProperties)
        inst->get_proc_addr(inst->instance, "vkGetPhysicalDeviceProperties");
    VkPhysicalDevice devs[16];
    uint32_t n = 16;
    if (!enum_devs || !get_props || enum_devs(inst->instance, &n, devs) < 0)
        return VK_NULL_HANDLE;
    for (uint32_t i = 0; i < n; i++) {
        VkPhysicalDeviceProperties props;
        get_props(devs[i], &props);
        if (contains_nocase(props.deviceName, substr))
            return devs[i];
    }
    return VK_NULL_HANDLE;
}

static void query_vk_name(PFN_vkGetInstanceProcAddr gpa, VkInstance inst, VkPhysicalDevice phys,
                          char *out, int out_size)
{
    PFN_vkGetPhysicalDeviceProperties get_props = (PFN_vkGetPhysicalDeviceProperties)
        gpa(inst, "vkGetPhysicalDeviceProperties");
    out[0] = '\0';
    if (get_props) {
        VkPhysicalDeviceProperties props;
        get_props(phys, &props);
        snprintf(out, out_size, "%s", props.deviceName);
    }
}

// libplacebo creates and owns the Vulkan device (software decoding path).
static bool init_vulkan_native(struct gpu_ctx *g, const struct cfg *cfg, pl_log log)
{
    bool headless = !g->win;
    uint32_t n = 0;
    const char **exts = headless ? NULL : glfwGetRequiredInstanceExtensions(&n);
    g->vk_inst = pl_vk_inst_create(log, pl_vk_inst_params(
        .get_proc_addr = headless ? NULL : get_vk_proc_addr,
        .extensions = exts,
        .num_extensions = n,
    ));
    if (!g->vk_inst) {
        fprintf(stderr, "perf0: pl_vk_inst_create failed\n");
        return false;
    }

    VkSurfaceKHR surf = VK_NULL_HANDLE;
    if (!headless) {
        if (glfwCreateWindowSurface(g->vk_inst->instance, g->win, NULL, &surf) != VK_SUCCESS) {
            fprintf(stderr, "perf0: glfwCreateWindowSurface failed\n");
            return false;
        }
        g->vk_surface = (void *) surf;
    }

    VkPhysicalDevice phys = pick_vk_device(g->vk_inst, cfg->adapter);
    if (cfg->adapter && !phys) {
        fprintf(stderr, "perf0: no Vulkan device matching '%s'\n", cfg->adapter);
        return false;
    }

    g->vk = pl_vulkan_create(log, pl_vulkan_params(
        .instance = g->vk_inst->instance,
        .get_proc_addr = g->vk_inst->get_proc_addr,
        .surface = surf,
        .device = phys,
        .allow_software = false,
    ));
    if (!g->vk) {
        fprintf(stderr, "perf0: pl_vulkan_create failed\n");
        return false;
    }
    query_vk_name(g->vk->get_proc_addr, g->vk->instance, g->vk->phys_device,
                  g->gpu_name, sizeof(g->gpu_name));
    g->gpu = g->vk->gpu;
    if (headless)
        return true;

    g->sw = pl_vulkan_create_swapchain(g->vk, pl_vulkan_swapchain_params(
        .surface = surf,
        .present_mode = VK_PRESENT_MODE_FIFO_KHR,
        .swapchain_depth = cfg->vk_depth,
    ));
    if (!g->sw) {
        fprintf(stderr, "perf0: pl_vulkan_create_swapchain failed\n");
        return false;
    }
    return true;
}

// FFmpeg creates the Vulkan device (with the video decode queues and extensions) and
// libplacebo imports it, so decoded VkImages can be sampled with no copy.
static bool init_vulkan_ffmpeg(struct gpu_ctx *g, const struct cfg *cfg, pl_log log)
{
    bool headless = !g->win;
    uint32_t n = 0;
    const char **req = headless ? NULL : glfwGetRequiredInstanceExtensions(&n);
    char inst_ext[512] = "";
    for (uint32_t i = 0; i < n; i++) {
        if (strlen(inst_ext) + strlen(req[i]) + 2 >= sizeof(inst_ext))
            break;
        if (i)
            strcat(inst_ext, "+");
        strcat(inst_ext, req[i]);
    }

    AVDictionary *opts = NULL;
    if (!headless) {
        av_dict_set(&opts, "instance_extensions", inst_ext, 0);
        av_dict_set(&opts, "device_extensions", "VK_KHR_swapchain", 0);
    }

    AVBufferRef *dev = NULL;
    int r = av_hwdevice_ctx_create(&dev, AV_HWDEVICE_TYPE_VULKAN, cfg->adapter, opts, 0);
    av_dict_free(&opts);
    if (r < 0) {
        fprintf(stderr, "perf0: av_hwdevice_ctx_create(vulkan) failed: %s\n", av_err2str(r));
        return false;
    }
    g->ff_vk_device = dev;

    AVHWDeviceContext *dc = (AVHWDeviceContext *) dev->data;
    AVVulkanDeviceContext *vc = dc->hwctx;

    // Queue families FFmpeg enabled: libplacebo wants one each for graphics, compute, transfer.
    struct pl_vulkan_queue qg = {0}, qc = {0}, qt = {0};
    for (int i = 0; i < vc->nb_qf; i++) {
        const AVVulkanDeviceQueueFamily *q = &vc->qf[i];
        struct pl_vulkan_queue pq = { .index = q->idx, .count = q->num };
#if PL_API_VER >= 375
        pq.flags = vc->queue_flags; // queue creation flags exist in newer libplacebo only
#endif
        if (!qg.count && (q->flags & VK_QUEUE_GRAPHICS_BIT))
            qg = pq;
        else if (!qc.count && (q->flags & VK_QUEUE_COMPUTE_BIT) && !(q->flags & VK_QUEUE_GRAPHICS_BIT))
            qc = pq;
        else if (!qt.count && (q->flags & VK_QUEUE_TRANSFER_BIT) &&
                 !(q->flags & (VK_QUEUE_GRAPHICS_BIT | VK_QUEUE_COMPUTE_BIT)))
            qt = pq;
    }
    if (!qg.count) {
        fprintf(stderr, "perf0: FFmpeg's Vulkan device has no graphics queue\n");
        return false;
    }

    void (*lock_fn)(void *, uint32_t, uint32_t) = NULL;
    void (*unlock_fn)(void *, uint32_t, uint32_t) = NULL;
#if FF_API_VULKAN_SYNC_QUEUES
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
    lock_fn = (void (*)(void *, uint32_t, uint32_t)) vc->lock_queue;
    unlock_fn = (void (*)(void *, uint32_t, uint32_t)) vc->unlock_queue;
#pragma GCC diagnostic pop
#endif

    g->vk = pl_vulkan_import(log, pl_vulkan_import_params(
        .instance = vc->inst,
        .get_proc_addr = vc->get_proc_addr,
        .phys_device = vc->phys_dev,
        .device = vc->act_dev,
        .extensions = vc->enabled_dev_extensions,
        .num_extensions = vc->nb_enabled_dev_extensions,
        .features = &vc->device_features,
        .queue_graphics = qg,
        .queue_compute = qc,
        .queue_transfer = qt,
        .lock_queue = lock_fn,
        .unlock_queue = unlock_fn,
        .queue_ctx = dc,
    ));
    if (!g->vk) {
        fprintf(stderr, "perf0: pl_vulkan_import of FFmpeg's device failed\n");
        return false;
    }
    query_vk_name(vc->get_proc_addr, vc->inst, vc->phys_dev, g->gpu_name, sizeof(g->gpu_name));
    g->gpu = g->vk->gpu;
    if (headless)
        return true;

    VkSurfaceKHR surf = VK_NULL_HANDLE;
    if (glfwCreateWindowSurface(vc->inst, g->win, NULL, &surf) != VK_SUCCESS) {
        fprintf(stderr, "perf0: glfwCreateWindowSurface on FFmpeg's instance failed\n");
        return false;
    }
    g->vk_surface = (void *) surf;

    g->sw = pl_vulkan_create_swapchain(g->vk, pl_vulkan_swapchain_params(
        .surface = surf,
        .present_mode = VK_PRESENT_MODE_FIFO_KHR,
        .swapchain_depth = cfg->vk_depth,
    ));
    if (!g->sw) {
        fprintf(stderr, "perf0: pl_vulkan_create_swapchain failed\n");
        return false;
    }
    return true;
}

// ---- public -------------------------------------------------------------------------

static void query_monitor(struct gpu_ctx *g)
{
    HMONITOR mon = MonitorFromWindow(g->hwnd, MONITOR_DEFAULTTONEAREST);
    MONITORINFOEXW mi = { .cbSize = sizeof(mi) };
    if (!GetMonitorInfoW(mon, (MONITORINFO *) &mi))
        return;
    wide_to_utf8(mi.szDevice, g->monitor_name, sizeof(g->monitor_name));
    DEVMODEW dm = { .dmSize = sizeof(dm) };
    if (EnumDisplaySettingsW(mi.szDevice, ENUM_CURRENT_SETTINGS, &dm)) {
        g->monitor_hz = (int) dm.dmDisplayFrequency;
        g->monitor_w = (int) dm.dmPelsWidth;
        g->monitor_h = (int) dm.dmPelsHeight;
    }
}

bool gpu_init(struct gpu_ctx *g, const struct cfg *cfg, pl_log log)
{
    memset(g, 0, sizeof(*g));
    bool headless = cfg->bench_w > 0; // offscreen render benchmark: no window, no swapchain

    GLFWwindow *win = NULL;
    if (!headless) {
        glfwSetErrorCallback(err_cb);
        if (!glfwInit()) {
            fprintf(stderr, "perf0: glfwInit failed\n");
            return false;
        }
        g->glfw_ready = true;
        if (cfg->backend == BACKEND_VULKAN && !glfwVulkanSupported()) {
            fprintf(stderr, "perf0: GLFW reports no Vulkan support\n");
            return false;
        }

        // A normal, fixed-size, decorated window. Never fullscreen, never steals focus.
        glfwWindowHint(GLFW_CLIENT_API, GLFW_NO_API);
        glfwWindowHint(GLFW_RESIZABLE, GLFW_FALSE);
        glfwWindowHint(GLFW_VISIBLE, GLFW_FALSE);
        glfwWindowHint(GLFW_FOCUSED, GLFW_FALSE);
        glfwWindowHint(GLFW_FOCUS_ON_SHOW, GLFW_FALSE);

        char title[128];
        snprintf(title, sizeof(title), "perf0 %s", cfg->run_id ? cfg->run_id : "");
        win = glfwCreateWindow(cfg->win_w, cfg->win_h, title, NULL, NULL);
        if (!win) {
            fprintf(stderr, "perf0: glfwCreateWindow failed\n");
            return false;
        }
        g->win = win;
        g->hwnd = glfwGetWin32Window(win);
        glfwSetWindowUserPointer(win, g);
        glfwSetFramebufferSizeCallback(win, resize_cb);
        glfwSetWindowPos(win, 80, 80); // primary monitor (the laptop panel) top-left area
        if (!cfg->hidden)
            glfwShowWindow(win);
        query_monitor(g);
    }
    g->t_window_ms = process_age_ms();

    bool ok;
    if (cfg->backend == BACKEND_D3D11)
        ok = init_d3d11(g, cfg, log);
    else if (cfg->decode == DECODE_VULKAN)
        ok = init_vulkan_ffmpeg(g, cfg, log);
    else
        ok = init_vulkan_native(g, cfg, log);
    if (!ok)
        return false;

    if (g->sw) {
        int w = 0, h = 0;
        glfwGetFramebufferSize(win, &w, &h);
        pl_swapchain_colorspace_hint(g->sw, &(struct pl_swapchain_colors) {0});
        if (!pl_swapchain_resize(g->sw, &w, &h)) {
            fprintf(stderr, "perf0: pl_swapchain_resize failed\n");
            return false;
        }
    }
    g->t_gpu_ms = process_age_ms();
    return true;
}

void gpu_wait_frame_latency(struct gpu_ctx *g)
{
    if (g->own_waitable)
        WaitForSingleObjectEx(g->own_waitable, 1000, TRUE);
}

void gpu_present(struct gpu_ctx *g)
{
    if (g->own_sc) {
        DXGI_PRESENT_PARAMETERS pp = {0};
        IDXGISwapChain1_Present1(g->own_sc, 1, 0, &pp); // sync interval 1: one vblank per present
    } else {
        pl_swapchain_swap_buffers(g->sw);
    }
}

void gpu_destroy(struct gpu_ctx *g)
{
    pl_swapchain_destroy(&g->sw);
    if (g->own_waitable) {
        CloseHandle(g->own_waitable);
        g->own_waitable = NULL;
    }
    if (g->own_sc) {
        IDXGISwapChain1_Release(g->own_sc);
        g->own_sc = NULL;
    }

    if (g->vk) {
        // Vulkan surfaces belong to whichever instance created the window surface.
        VkInstance inst = g->vk->instance;
        PFN_vkGetInstanceProcAddr gpa = g->vk->get_proc_addr;
        pl_vulkan_destroy(&g->vk);
        if (g->vk_surface) {
            PFN_vkDestroySurfaceKHR destroy_surf =
                (PFN_vkDestroySurfaceKHR) gpa(inst, "vkDestroySurfaceKHR");
            if (destroy_surf)
                destroy_surf(inst, (VkSurfaceKHR) g->vk_surface, NULL);
            g->vk_surface = NULL;
        }
    }
    pl_vk_inst_destroy(&g->vk_inst);
    if (g->ff_vk_device)
        av_buffer_unref((AVBufferRef **) &g->ff_vk_device);

    pl_d3d11_destroy(&g->d3d11);

    if (g->win) {
        glfwDestroyWindow(g->win);
        g->win = NULL;
    }
    if (g->glfw_ready)
        glfwTerminate();
}

bool gpu_poll_events(struct gpu_ctx *g)
{
    glfwPollEvents();
    return !glfwWindowShouldClose(g->win);
}

bool gpu_escape_pressed(struct gpu_ctx *g)
{
    return glfwGetKey(g->win, GLFW_KEY_ESCAPE) == GLFW_PRESS;
}
