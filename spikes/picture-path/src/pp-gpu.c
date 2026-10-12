// Offscreen libplacebo GPU creation, see pp-gpu.h.
#define COBJMACROS
#include "pp-gpu.h"

#include <ctype.h>
#include <string.h>
#include <windows.h>
#include <dxgi.h>

#include "pp-util.h"

const char *pp_backend_name(enum pp_backend b)
{
    return b == PP_BACKEND_VULKAN ? "vulkan" : "d3d11";
}

bool pp_backend_from_name(const char *name, enum pp_backend *out)
{
    if (!strcmp(name, "d3d11")) {
        *out = PP_BACKEND_D3D11;
        return true;
    }
    if (!strcmp(name, "vulkan")) {
        *out = PP_BACKEND_VULKAN;
        return true;
    }
    return false;
}

static bool contains_nocase(const char *hay, const char *needle)
{
    size_t n = strlen(needle);
    if (!n)
        return true;
    for (; *hay; hay++) {
        size_t i = 0;
        while (i < n && hay[i] && tolower((unsigned char) hay[i]) == tolower((unsigned char) needle[i]))
            i++;
        if (i == n)
            return true;
    }
    return false;
}

static void log_cb(void *priv, enum pl_log_level level, const char *msg)
{
    (void) priv;
    static const char *const names[] = { "", "FATAL", "ERROR", "WARN", "INFO", "DEBUG", "TRACE" };
    fprintf(stderr, "[pl %s] %s\n", names[(int) level < 7 ? (int) level : 0], msg);
}

// ---- DXGI adapters ------------------------------------------------------------------------------

struct dxgi_adapter {
    char name[256];
    unsigned vendor;
    size_t vram;
    LUID luid;
    bool software;
};

static int enum_adapters(struct dxgi_adapter *out, int max, IDXGIAdapter1 **keep, int keep_idx)
{
    IDXGIFactory1 *fac = NULL;
    if (FAILED(CreateDXGIFactory1(&IID_IDXGIFactory1, (void **) &fac)))
        return 0;
    int n = 0;
    IDXGIAdapter1 *ad = NULL;
    for (UINT i = 0; n < max && IDXGIFactory1_EnumAdapters1(fac, i, &ad) != DXGI_ERROR_NOT_FOUND; i++) {
        DXGI_ADAPTER_DESC1 d;
        if (SUCCEEDED(IDXGIAdapter1_GetDesc1(ad, &d))) {
            struct dxgi_adapter *a = &out[n];
            memset(a, 0, sizeof(*a));
            WideCharToMultiByte(CP_UTF8, 0, d.Description, -1, a->name, (int) sizeof(a->name), NULL, NULL);
            a->vendor = d.VendorId;
            a->vram = d.DedicatedVideoMemory;
            a->luid = d.AdapterLuid;
            a->software = (d.Flags & DXGI_ADAPTER_FLAG_SOFTWARE) != 0;
            if (keep && n == keep_idx)
                *keep = ad;
            else
                IDXGIAdapter1_Release(ad);
            n++;
        } else {
            IDXGIAdapter1_Release(ad);
        }
    }
    IDXGIFactory1_Release(fac);
    return n;
}

void pp_list_adapters(FILE *out)
{
    struct dxgi_adapter a[8];
    int n = enum_adapters(a, 8, NULL, -1);
    for (int i = 0; i < n; i++)
        fprintf(out, "adapter %d: %s  vendor 0x%04X  %zu MiB dedicated%s\n", i, a[i].name, a[i].vendor,
                a[i].vram >> 20, a[i].software ? "  (software)" : "");
}

// ---- backends -----------------------------------------------------------------------------------

static bool create_d3d11(struct pp_gpu *g, const char *match)
{
    struct dxgi_adapter a[8];
    IDXGIAdapter1 *keep = NULL;
    int n = enum_adapters(a, 8, NULL, -1), pick = -1;
    for (int i = 0; i < n && pick < 0; i++)
        if (!a[i].software && (!match || contains_nocase(a[i].name, match)))
            pick = i;
    if (pick < 0) {
        fprintf(stderr, "picture-path: no hardware DXGI adapter matches '%s'\n", match ? match : "(any)");
        return false;
    }
    enum_adapters(a, 8, &keep, pick);
    g->d3d = pl_d3d11_create(g->log, pl_d3d11_params(.adapter = (IDXGIAdapter *) keep, .allow_software = false));
    if (keep)
        IDXGIAdapter1_Release(keep);
    if (!g->d3d) {
        fprintf(stderr, "picture-path: pl_d3d11_create failed on %s\n", a[pick].name);
        return false;
    }
    g->gpu = g->d3d->gpu;
    snprintf(g->adapter_name, sizeof(g->adapter_name), "%s", a[pick].name);
    g->vendor_id = a[pick].vendor;
    return true;
}

static bool create_vulkan(struct pp_gpu *g, const char *match)
{
    g->inst = pl_vk_inst_create(g->log, pl_vk_inst_params());
    if (!g->inst) {
        fprintf(stderr, "picture-path: pl_vk_inst_create failed\n");
        return false;
    }
    VkInstance inst = g->inst->instance;
    PFN_vkGetInstanceProcAddr gpa = g->inst->get_proc_addr;
    PFN_vkEnumeratePhysicalDevices enum_devs = (PFN_vkEnumeratePhysicalDevices) gpa(inst, "vkEnumeratePhysicalDevices");
    PFN_vkGetPhysicalDeviceProperties get_props = (PFN_vkGetPhysicalDeviceProperties) gpa(inst, "vkGetPhysicalDeviceProperties");
    if (!enum_devs || !get_props)
        return false;
    VkPhysicalDevice devs[16];
    uint32_t n = 16;
    if (enum_devs(inst, &n, devs) < 0)
        return false;
    VkPhysicalDevice pick = VK_NULL_HANDLE;
    for (uint32_t i = 0; i < n && !pick; i++) {
        VkPhysicalDeviceProperties p;
        get_props(devs[i], &p);
        if (p.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU)
            continue;
        if (!match || contains_nocase(p.deviceName, match)) {
            pick = devs[i];
            snprintf(g->adapter_name, sizeof(g->adapter_name), "%s", p.deviceName);
            g->vendor_id = p.vendorID;
        }
    }
    if (!pick) {
        fprintf(stderr, "picture-path: no Vulkan device matches '%s'\n", match ? match : "(any)");
        return false;
    }
    g->vk = pl_vulkan_create(g->log, pl_vulkan_params(.instance = inst, .get_proc_addr = gpa, .device = pick,
                                                      .allow_software = false));
    if (!g->vk) {
        fprintf(stderr, "picture-path: pl_vulkan_create failed on %s\n", g->adapter_name);
        return false;
    }
    g->gpu = g->vk->gpu;
    return true;
}

bool pp_gpu_create(struct pp_gpu *g, enum pp_backend backend, const char *adapter_match, int log_level)
{
    memset(g, 0, sizeof(*g));
    g->backend = backend;
    g->log_level = log_level;
    g->log = pl_log_create(PL_API_VER, pl_log_params(.log_cb = log_cb, .log_level = log_level));
    if (!g->log)
        return false;
    bool ok = backend == PP_BACKEND_VULKAN ? create_vulkan(g, adapter_match) : create_d3d11(g, adapter_match);
    if (!ok)
        pp_gpu_destroy(g);
    return ok;
}

void pp_gpu_destroy(struct pp_gpu *g)
{
    if (g->gpu)
        pl_gpu_finish(g->gpu);
    pl_vulkan_destroy(&g->vk);
    pl_vk_inst_destroy(&g->inst);
    pl_d3d11_destroy(&g->d3d);
    pl_log_destroy(&g->log);
    g->gpu = NULL;
}

void pp_gpu_describe(const struct pp_gpu *g, FILE *out)
{
    const struct pl_glsl_version *s = &g->gpu->glsl;
    const struct pl_gpu_limits *l = &g->gpu->limits;
    fprintf(out, "%s on %s: GLSL %d%s, compute %s (shmem %zu B, %u threads/group), max tex %u, thread-safe %s, "
            "fragment queues %u, compute queues %u\n",
            pp_backend_name(g->backend), g->adapter_name, s->version, s->vulkan ? " (vulkan)" : "",
            s->compute ? "yes" : "NO", s->max_shmem_size, (unsigned) s->max_group_threads, l->max_tex_2d_dim,
            l->thread_safe ? "yes" : "no", l->fragment_queues, l->compute_queues);
}

pl_fmt pp_find_fmt(const struct pp_gpu *g, const char *name)
{
    return pl_find_named_fmt(g->gpu, name);
}

pl_tex pp_tex_create_canvas(const struct pp_gpu *g, int w, int h, const char *what)
{
    return pp_tex_create_canvas_fmt(g, w, h, "rgba16f", what);
}

pl_tex pp_tex_create_canvas_fmt(const struct pp_gpu *g, int w, int h, const char *fmt_name, const char *what)
{
    pl_fmt fmt = pl_find_named_fmt(g->gpu, fmt_name);
    if (!fmt) {
        fprintf(stderr, "picture-path: no %s format on %s (%s)\n", fmt_name, g->adapter_name, what);
        return NULL;
    }
    pl_tex t = pl_tex_create(g->gpu, pl_tex_params(
        .w = w, .h = h, .format = fmt, .sampleable = true, .renderable = true, .storable = true,
        .blit_src = true, .blit_dst = true, .host_readable = true, .host_writable = true,
        .debug_tag = PL_DEBUG_TAG,
    ));
    if (!t)
        fprintf(stderr, "picture-path: cannot create the %dx%d %s texture for %s\n", w, h, fmt_name, what);
    return t;
}

void pp_canvas_frame(struct pl_frame *f, pl_tex tex)
{
    *f = (struct pl_frame) {
        .num_planes = 1,
        .planes = {{ .texture = tex, .components = 4, .component_mapping = { 0, 1, 2, 3 } }},
        .repr = { .sys = PL_COLOR_SYSTEM_RGB, .levels = PL_COLOR_LEVELS_FULL, .alpha = PL_ALPHA_PREMULTIPLIED },
        // Black pinned to "infinite contrast": left unset, libplacebo infers a 1000:1 display and lifts the canvas black by 0.001.
        .color = { .primaries = PL_COLOR_PRIM_BT_709, .transfer = PL_COLOR_TRC_LINEAR,
                   .hdr = { .min_luma = PL_COLOR_HDR_BLACK, .max_luma = PL_COLOR_SDR_WHITE } },
        .crop = { 0, 0, (float) tex->params.w, (float) tex->params.h },
    };
}
