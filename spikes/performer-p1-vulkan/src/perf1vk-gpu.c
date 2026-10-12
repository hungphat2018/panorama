// Vulkan bring-up for perf1vk: libplacebo's VkInstance and VkDevice, one VkSurfaceKHR per window, the in-memory event
// log, and the swapchain backends (libplacebo's own swapchain, or an offscreen target for --no-window runs; the
// own-swapchain backend lives in perf1vk-own-swap.c).
//
// Event log. Everything libplacebo and FFmpeg log from INFO up (DEBUG with --event-level debug), and every note the
// presenter adds, is kept in memory with its QPC time and written into the run's JSON, so a hitch can be matched with
// "Spent 412 ms compiling shader", a swapchain re-creation or a decoder warning. libplacebo reports any CPU step over
// 10 ms at INFO and over 1 s at WARN, which is the range a hitch hunt needs.
#include "perf1vk.h"

#include <ctype.h>
#include <stdarg.h>

#define EVENT_CAP 20000

// ---- event log ----------------------------------------------------------------------------------

static struct {
    CRITICAL_SECTION cs;
    struct log_event *ev;
    size_t n, cap, dropped;
    bool ready, keep_all;
    int stderr_level, keep_level;
} g_ev;

static void events_init(const struct cfg *cfg)
{
    if (g_ev.ready)
        return;
    InitializeCriticalSection(&g_ev.cs);
    g_ev.ev = calloc(EVENT_CAP, sizeof(*g_ev.ev));
    g_ev.cap = g_ev.ev ? EVENT_CAP : 0;
    g_ev.keep_all = cfg->log_all;
    g_ev.keep_level = cfg->event_level;
    g_ev.stderr_level = cfg->log_level;
    g_ev.ready = true;
}

static void event_add(int level, const char *text)
{
    if (!g_ev.ready)
        return;
    EnterCriticalSection(&g_ev.cs);
    if (g_ev.n < g_ev.cap) {
        struct log_event *e = &g_ev.ev[g_ev.n++];
        e->t = now_s();
        e->level = level;
        snprintf(e->text, sizeof(e->text), "%s", text);
    } else {
        g_ev.dropped++;
    }
    LeaveCriticalSection(&g_ev.cs);
}

static void pl_log_cb(void *priv, enum pl_log_level level, const char *msg)
{
    if ((int) level <= g_ev.stderr_level)
        fprintf(stderr, "[pl %d] %s\n", (int) level, msg);
    // The log is chatty at start-up (devices, extensions). Keep that too: it is the record of how the device was made.
    if (g_ev.keep_all || (int) level <= g_ev.keep_level)
        event_add((int) level, msg);
}

void gpu_note(const char *fmt, ...)
{
    char text[216];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof(text), fmt, ap);
    va_end(ap);
    event_add((int) PL_LOG_INFO, text);
}

struct log_event *gpu_log_events(size_t *n)
{
    *n = g_ev.n;
    return g_ev.ev;
}

void gpu_log_free(void)
{
    if (!g_ev.ready)
        return;
    free(g_ev.ev);
    DeleteCriticalSection(&g_ev.cs);
    memset(&g_ev, 0, sizeof(g_ev));
}

// ---- instance, surface, device --------------------------------------------------------------------

bool gpu_instance_create(struct gpu_ctx *g, const struct cfg *cfg)
{
    memset(g, 0, sizeof(*g));
    events_init(cfg);
    int level = cfg->event_level > cfg->log_level ? cfg->event_level : cfg->log_level;
    g->log = pl_log_create(PL_API_VER, pl_log_params(.log_cb = pl_log_cb, .log_level = level));
    if (!g->log)
        return false;

    static const char *const surface_exts[] = { "VK_KHR_surface", "VK_KHR_win32_surface" };
    g->inst = pl_vk_inst_create(g->log, pl_vk_inst_params(
        .extensions = cfg->headless ? NULL : surface_exts,
        .num_extensions = cfg->headless ? 0 : 2,
    ));
    if (!g->inst) {
        fprintf(stderr, "perf1vk: pl_vk_inst_create failed\n");
        return false;
    }
    return true;
}

bool gpu_surface_create(struct gpu_ctx *g, HWND hwnd, VkSurfaceKHR *out)
{
    PFN_vkCreateWin32SurfaceKHR create = (PFN_vkCreateWin32SurfaceKHR)
        g->inst->get_proc_addr(g->inst->instance, "vkCreateWin32SurfaceKHR");
    if (!create) {
        fprintf(stderr, "perf1vk: vkCreateWin32SurfaceKHR not available\n");
        return false;
    }
    VkWin32SurfaceCreateInfoKHR info = {
        .sType = VK_STRUCTURE_TYPE_WIN32_SURFACE_CREATE_INFO_KHR,
        .hinstance = GetModuleHandleW(NULL),
        .hwnd = hwnd,
    };
    VkResult r = create(g->inst->instance, &info, NULL, out);
    if (r != VK_SUCCESS) {
        fprintf(stderr, "perf1vk: vkCreateWin32SurfaceKHR failed: %d\n", (int) r);
        return false;
    }
    return true;
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

// The GPU that drives the screens: the physical device whose LUID is the DXGI adapter's, unless --adapter names one.
// Returns VK_NULL_HANDLE if nothing matches (libplacebo then picks its own favourite).
static VkPhysicalDevice choose_device(struct gpu_ctx *g, const struct cfg *cfg, const LUID *luid)
{
    VkInstance inst = g->inst->instance;
    PFN_vkGetInstanceProcAddr gpa = g->inst->get_proc_addr;
    PFN_vkEnumeratePhysicalDevices enum_devs = (PFN_vkEnumeratePhysicalDevices) gpa(inst, "vkEnumeratePhysicalDevices");
    PFN_vkGetPhysicalDeviceProperties2 get_props2 = (PFN_vkGetPhysicalDeviceProperties2) gpa(inst, "vkGetPhysicalDeviceProperties2");
    if (!enum_devs || !get_props2)
        return VK_NULL_HANDLE;
    VkPhysicalDevice devs[16];
    uint32_t n = 16;
    if (enum_devs(inst, &n, devs) < 0)
        return VK_NULL_HANDLE;
    for (uint32_t i = 0; i < n; i++) {
        VkPhysicalDeviceIDProperties idp = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES };
        VkPhysicalDeviceProperties2 p2 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &idp };
        get_props2(devs[i], &p2);
        bool match;
        if (cfg->adapter) {
            match = contains_nocase(p2.properties.deviceName, cfg->adapter);
        } else {
            LUID dl = {0};
            if (idp.deviceLUIDValid)
                memcpy(&dl, idp.deviceLUID, sizeof(dl));
            match = luid && idp.deviceLUIDValid && dl.LowPart == luid->LowPart && dl.HighPart == luid->HighPart;
        }
        if (match) {
            snprintf(g->device_name, sizeof(g->device_name), "%s", p2.properties.deviceName);
            return devs[i];
        }
    }
    return VK_NULL_HANDLE;
}

static bool has_ext(pl_vulkan vk, const char *name)
{
    for (int i = 0; i < vk->num_extensions; i++)
        if (!strcmp(vk->extensions[i], name))
            return true;
    return false;
}

bool gpu_device_create(struct gpu_ctx *g, const struct cfg *cfg, const LUID *adapter_luid, VkSurfaceKHR surface)
{
    g->phys = choose_device(g, cfg, adapter_luid);
    if (!g->phys) {
        fprintf(stderr, "perf1vk: no Vulkan device matches the DXGI adapter that drives the screens%s%s\n",
                cfg->adapter ? " / --adapter " : "", cfg->adapter ? cfg->adapter : "");
        return false;
    }

    // The own-swapchain backend needs the present-timing set; the other backends leave it off, so a run with
    // libplacebo's swapchain is a plain libplacebo run. Features the device lacks are dropped by libplacebo.
    static const char *const timing_exts[] = {
        "VK_KHR_present_id2", "VK_KHR_present_wait2", "VK_EXT_present_timing", "VK_KHR_calibrated_timestamps",
    };
    // Vulkan Video decoding on this device: the decode queue family and the codec extensions FFmpeg's hwaccels use.
    static const char *const video_exts[] = {
        "VK_KHR_video_queue", "VK_KHR_video_decode_queue", "VK_KHR_video_decode_h264", "VK_KHR_video_decode_h265",
        "VK_KHR_video_decode_av1",
    };
    VkPhysicalDevicePresentTimingFeaturesEXT f_timing = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_TIMING_FEATURES_EXT,
        .presentTiming = VK_TRUE, .presentAtAbsoluteTime = VK_TRUE, .presentAtRelativeTime = VK_TRUE,
    };
    VkPhysicalDevicePresentId2FeaturesKHR f_id2 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_ID_2_FEATURES_KHR, .pNext = &f_timing, .presentId2 = VK_TRUE,
    };
    VkPhysicalDevicePresentWait2FeaturesKHR f_wait2 = {
        .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_WAIT_2_FEATURES_KHR, .pNext = &f_id2, .presentWait2 = VK_TRUE,
    };
    VkPhysicalDeviceFeatures2 features = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &f_wait2 };
    bool want_timing = cfg->backend == BACKEND_OWN && !cfg->headless;
    bool want_video = cfg->decode == DECODE_VULKAN;
    const char *opt_exts[16];
    int n_opt = 0;
    if (want_timing)
        for (size_t i = 0; i < sizeof(timing_exts) / sizeof(timing_exts[0]); i++)
            opt_exts[n_opt++] = timing_exts[i];
    if (want_video)
        for (size_t i = 0; i < sizeof(video_exts) / sizeof(video_exts[0]); i++)
            opt_exts[n_opt++] = video_exts[i];

    g->vk = pl_vulkan_create(g->log, pl_vulkan_params(
        .instance = g->inst->instance,
        .get_proc_addr = g->inst->get_proc_addr,
        .surface = surface,
        .device = g->phys,
        .allow_software = false,
        .opt_extensions = n_opt ? opt_exts : NULL,
        .num_opt_extensions = n_opt,
        .features = want_timing ? &features : NULL,
        .extra_queues = want_video ? VK_QUEUE_VIDEO_DECODE_BIT_KHR : 0,
    ));
    if (!g->vk) {
        fprintf(stderr, "perf1vk: pl_vulkan_create failed\n");
        return false;
    }
    g->gpu = g->vk->gpu;
    g->have_timing = want_timing && has_ext(g->vk, "VK_EXT_present_timing") && has_ext(g->vk, "VK_KHR_present_id2") &&
                     has_ext(g->vk, "VK_KHR_present_wait2");
    gpu_note("device %s, API %u.%u.%u, %d device extensions enabled, present timing set %s", g->device_name,
             VK_API_VERSION_MAJOR(g->vk->api_version), VK_API_VERSION_MINOR(g->vk->api_version),
             VK_API_VERSION_PATCH(g->vk->api_version), g->vk->num_extensions, g->have_timing ? "ON" : "off");

    if (want_video) {
        g->ff_vk_device = hwdevice_from_libplacebo(g);
        if (!g->ff_vk_device) {
            fprintf(stderr, "perf1vk: FFmpeg could not adopt libplacebo's Vulkan device\n");
            return false;
        }
    }

    if (cfg->cache_file) {
        g->cache = pl_cache_create(pl_cache_params(.log = g->log, .max_total_size = 50 << 20));
        pl_gpu_set_cache(g->gpu, g->cache);
        FILE *f = fopen(cfg->cache_file, "rb");
        if (f) {
            pl_cache_load_file(g->cache, f);
            fclose(f);
        }
        g->cache_sig = pl_cache_signature(g->cache);
    }
    return true;
}

void gpu_destroy(struct gpu_ctx *g, const struct cfg *cfg)
{
    hwdevice_free(g);
    if (g->cache) {
        if (cfg->cache_file && pl_cache_signature(g->cache) != g->cache_sig) {
            FILE *f = fopen(cfg->cache_file, "wb");
            if (f) {
                pl_cache_save_file(g->cache, f);
                fclose(f);
            }
        }
        pl_cache_destroy(&g->cache);
    }
    pl_vulkan_destroy(&g->vk);
    pl_vk_inst_destroy(&g->inst);
    pl_log_destroy(&g->log);
}

// ---- swapchains -------------------------------------------------------------------------------------

static VkPresentModeKHR present_mode_of(const char *name)
{
    if (!strcmp(name, "fifo-relaxed")) return VK_PRESENT_MODE_FIFO_RELAXED_KHR;
    if (!strcmp(name, "mailbox")) return VK_PRESENT_MODE_MAILBOX_KHR;
    if (!strcmp(name, "immediate")) return VK_PRESENT_MODE_IMMEDIATE_KHR;
    if (!strcmp(name, "latest-ready")) return VK_PRESENT_MODE_FIFO_LATEST_READY_KHR;
    return VK_PRESENT_MODE_FIFO_KHR;
}

static const char *alpha_name(enum pl_alpha_mode a)
{
    switch (a) {
    case PL_ALPHA_UNKNOWN: return "opaque";
    case PL_ALPHA_NONE: return "none";
    case PL_ALPHA_INDEPENDENT: return "post-multiplied";
    case PL_ALPHA_PREMULTIPLIED: return "premultiplied";
    default: return "?";
    }
}

// What the swapchain really is, once a frame exists: the format and the alpha mode libplacebo asked the compositor for.
static void describe_frame(struct vk_swap *s, const struct pl_swapchain_frame *sf)
{
    snprintf(s->alpha, sizeof(s->alpha), "%s", alpha_name(sf->color_repr.alpha));
    pl_fmt fmt = sf->fbo->params.format;
    snprintf(s->format, sizeof(s->format), "%s", fmt ? fmt->name : "?");
}

// Notes a swapchain image the monitor has not shown before. More distinct images than the swapchain has means it was
// re-created (libplacebo does that on its own after VK_ERROR_OUT_OF_DATE_KHR, VK_SUBOPTIMAL_KHR or a resize).
static void track_images(struct vk_swap *s, pl_tex fbo)
{
    for (int i = 0; i < s->n_seen; i++)
        if (s->seen[i] == fbo)
            return;
    if (s->n_seen < 32) {
        s->seen[s->n_seen++] = fbo;
        if (s->n_seen > 1)
            gpu_note("swapchain image #%d first seen (%dx%d)", s->n_seen, fbo->params.w, fbo->params.h);
    }
}

bool swap_create(struct gpu_ctx *g, const struct cfg *cfg, struct vk_swap *s, int w, int h)
{
    s->w = w;
    s->h = h;
    if (cfg->headless) {
        pl_fmt fmt = pl_find_named_fmt(g->gpu, cfg->rgb10 ? "rgb10a2" : "bgra8");
        if (!fmt)
            fmt = pl_find_named_fmt(g->gpu, "rgba8");
        if (!fmt) {
            fprintf(stderr, "perf1vk: no renderable 8/10-bit format for the offscreen target\n");
            return false;
        }
        s->offscreen = pl_tex_create(g->gpu, pl_tex_params(
            .w = w, .h = h, .format = fmt, .renderable = true, .blit_dst = true, .blit_src = true,
        ));
        if (!s->offscreen) {
            fprintf(stderr, "perf1vk: offscreen target creation failed\n");
            return false;
        }
        snprintf(s->format, sizeof(s->format), "%s", fmt->name);
        snprintf(s->alpha, sizeof(s->alpha), "offscreen");
        return true;
    }

    if (cfg->backend == BACKEND_OWN)
        return own_swap_create(g, cfg, s);

    s->pls = pl_vulkan_create_swapchain(g->vk, pl_vulkan_swapchain_params(
        .surface = s->surface,
        .present_mode = present_mode_of(cfg->present_mode),
        .swapchain_depth = cfg->vk_depth,
        .disable_10bit_sdr = !cfg->rgb10,
    ));
    if (!s->pls) {
        fprintf(stderr, "perf1vk: pl_vulkan_create_swapchain failed\n");
        return false;
    }
    int rw = w, rh = h;
    pl_swapchain_colorspace_hint(s->pls, &(struct pl_swapchain_colors) {0});
    if (!pl_swapchain_resize(s->pls, &rw, &rh)) {
        fprintf(stderr, "perf1vk: pl_swapchain_resize failed\n");
        return false;
    }
    s->w = rw;
    s->h = rh;
    return true;
}

void swap_destroy(struct gpu_ctx *g, struct vk_swap *s)
{
    if (s->own)
        own_swap_destroy(g, s);
    if (s->offscreen)
        pl_tex_destroy(g->gpu, &s->offscreen);
    pl_swapchain_destroy(&s->pls);
    if (s->surface) {
        PFN_vkDestroySurfaceKHR destroy = (PFN_vkDestroySurfaceKHR)
            g->inst->get_proc_addr(g->inst->instance, "vkDestroySurfaceKHR");
        if (destroy)
            destroy(g->inst->instance, s->surface, NULL);
        s->surface = VK_NULL_HANDLE;
    }
}

bool swap_begin(struct gpu_ctx *g, struct vk_swap *s, struct pl_frame *target)
{
    if (s->offscreen) {
        s->sf = (struct pl_swapchain_frame) {
            .fbo = s->offscreen,
            .color_repr = { .sys = PL_COLOR_SYSTEM_RGB, .levels = PL_COLOR_LEVELS_FULL, .alpha = PL_ALPHA_UNKNOWN,
                            .bits = { .sample_depth = s->offscreen->params.format->component_depth[0],
                                      .color_depth = s->offscreen->params.format->component_depth[0] } },
            .color_space = pl_color_space_srgb,
        };
    } else if (s->own) {
        if (!own_swap_begin(g, s, &s->sf))
            return false;
    } else if (!pl_swapchain_start_frame(s->pls, &s->sf)) {
        return false;
    }
    if (!s->described) {
        describe_frame(s, &s->sf);
        s->described = true;
    }
    track_images(s, s->sf.fbo);
    pl_frame_from_swapchain(target, &s->sf);
    return true;
}

bool swap_end(struct gpu_ctx *g, struct vk_swap *s, struct swap_end_times *t)
{
    bool ok = true;
    t->call = now_s();
    if (s->offscreen) {
        pl_gpu_finish(g->gpu); // the offscreen "present" is complete when the GPU is
        t->ret = t->done = now_s();
        return true;
    }
    if (s->own) {
        ok = own_swap_end(g, s, t);
        return ok;
    }
    ok = pl_swapchain_submit_frame(s->pls); // libplacebo calls vkQueuePresentKHR in here
    t->ret = now_s();
    pl_swapchain_swap_buffers(s->pls);      // waits until the GPU has finished with the frame; recycles command buffers
    t->done = now_s();
    return ok;
}
