// Offscreen libplacebo device creation, see finish-device.h.
#define COBJMACROS
#include "finish-device.h"

#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include <windows.h>
#include <dxgi.h>

const char *fp_backend_name(enum fp_backend b)
{
    return b == FP_BACKEND_VULKAN ? "vulkan" : "d3d11";
}

bool fp_backend_from_name(const char *name, enum fp_backend *out)
{
    if (!strcmp(name, "vulkan")) {
        *out = FP_BACKEND_VULKAN;
        return true;
    }
    if (!strcmp(name, "d3d11")) {
        *out = FP_BACKEND_D3D11;
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

// ---- DXGI adapters (D3D11 backend, and the list shown to the user) ----------------------------------------------

struct dxgi_adapter {
    char name[256];
    unsigned vendor;
    size_t vram;
    bool software;
};

// Fills `out` and, if keep_idx >= 0, returns a reference to that adapter in *keep.
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

void fp_list_adapters(FILE *out)
{
    struct dxgi_adapter a[8];
    int n = enum_adapters(a, 8, NULL, -1);
    for (int i = 0; i < n; i++)
        fprintf(out, "adapter %d: %s  vendor 0x%04X  %zu MiB dedicated%s\n", i, a[i].name, a[i].vendor, a[i].vram >> 20,
                a[i].software ? "  (software)" : "");
}

static bool create_d3d11(struct fp_device *d, const char *match)
{
    struct dxgi_adapter a[8];
    int n = enum_adapters(a, 8, NULL, -1), pick = -1;
    for (int i = 0; i < n && pick < 0; i++)
        if (!a[i].software && (!match || contains_nocase(a[i].name, match)))
            pick = i;
    if (pick < 0) {
        fprintf(stderr, "finish: no hardware DXGI adapter matches '%s'\n", match ? match : "(any)");
        return false;
    }
    IDXGIAdapter1 *keep = NULL;
    enum_adapters(a, 8, &keep, pick);
    d->d3d = pl_d3d11_create(d->log, pl_d3d11_params(.adapter = (IDXGIAdapter *) keep, .allow_software = false));
    if (keep)
        IDXGIAdapter1_Release(keep);
    if (!d->d3d) {
        fprintf(stderr, "finish: pl_d3d11_create failed on %s\n", a[pick].name);
        return false;
    }
    d->gpu = d->d3d->gpu;
    snprintf(d->name, sizeof(d->name), "%s", a[pick].name);
    d->vendor_id = a[pick].vendor;
    d->integrated = a[pick].vram < ((size_t) 1 << 30);
    return true;
}

// ---- Vulkan --------------------------------------------------------------------------------------------------

static bool create_vulkan(struct fp_device *d, const char *match)
{
    d->inst = pl_vk_inst_create(d->log, pl_vk_inst_params());
    if (!d->inst) {
        fprintf(stderr, "finish: pl_vk_inst_create failed\n");
        return false;
    }
    VkInstance inst = d->inst->instance;
    PFN_vkGetInstanceProcAddr gpa = d->inst->get_proc_addr;
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
            snprintf(d->name, sizeof(d->name), "%s", p.deviceName);
            d->vendor_id = p.vendorID;
            d->integrated = p.deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU;
        }
    }
    if (!pick) {
        fprintf(stderr, "finish: no Vulkan device matches '%s'\n", match ? match : "(any)");
        return false;
    }
    d->vk = pl_vulkan_create(d->log, pl_vulkan_params(.instance = inst, .get_proc_addr = gpa, .device = pick,
                                                      .allow_software = false));
    if (!d->vk) {
        fprintf(stderr, "finish: pl_vulkan_create failed on %s\n", d->name);
        return false;
    }
    d->gpu = d->vk->gpu;
    return true;
}

bool fp_device_create(struct fp_device *d, enum fp_backend backend, const char *gpu_match, bool layers_off, int log_level)
{
    memset(d, 0, sizeof(*d));
    d->backend = backend;
    // This PC registers overlay and capture hooks as implicit Vulkan layers; the loader injects all of them into every
    // Vulkan process. Off for measurements: the loader reads the variable at vkCreateInstance, so it has to be set first.
    if (layers_off && backend == FP_BACKEND_VULKAN)
        _putenv_s("VK_LOADER_LAYERS_DISABLE", "~implicit~");
    d->log = pl_log_create(PL_API_VER, pl_log_params(.log_cb = log_cb, .log_level = log_level));
    if (!d->log)
        return false;
    bool ok = backend == FP_BACKEND_VULKAN ? create_vulkan(d, gpu_match) : create_d3d11(d, gpu_match);
    if (ok) {
        d->dp = pl_dispatch_create(d->log, d->gpu);
        ok = d->dp != NULL;
    }
    if (!ok) {
        fp_device_destroy(d);
        return false;
    }
    d->env = (struct fp_env) { .log = d->log, .gpu = d->gpu, .dp = d->dp, .timer = NULL };
    return true;
}

void fp_device_destroy(struct fp_device *d)
{
    if (d->gpu)
        pl_gpu_finish(d->gpu);
    pl_dispatch_destroy(&d->dp);
    pl_vulkan_destroy(&d->vk);
    pl_vk_inst_destroy(&d->inst);
    pl_d3d11_destroy(&d->d3d);
    pl_log_destroy(&d->log);
    d->gpu = NULL;
}

void fp_device_describe(const struct fp_device *d, FILE *out)
{
    const struct pl_glsl_version *s = &d->gpu->glsl;
    const struct pl_gpu_limits *l = &d->gpu->limits;
    fprintf(out, "%s on %s%s: GLSL %d%s, compute %s (shmem %zu B), max tex %u, thread-safe %s, fragment queues %u, compute queues %u\n",
            fp_backend_name(d->backend), d->name, d->integrated ? " (integrated)" : "", s->version,
            s->vulkan ? " vulkan" : "", s->compute ? "yes" : "NO", (size_t) s->max_shmem_size,
            (unsigned) l->max_tex_2d_dim, l->thread_safe ? "yes" : "no", (unsigned) l->fragment_queues,
            (unsigned) l->compute_queues);
}
