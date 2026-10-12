// vk-caps: headless Vulkan capability probe for the performer's presenter. Opens no window and creates no
// surface or swapchain; it only asks the loader, the drivers and DXGI what exists, then creates (and destroys)
// one VkDevice per GPU with the present-timing extension set switched on, to see whether the driver accepts it.
//
// Answers, per GPU:
//   - which implicit layers the loader injects into every Vulkan process on this PC (they hook vkQueuePresentKHR)
//   - which DXGI adapter the GPU is (device LUID), and how many outputs hang off it
//   - queue families, and whether each can present to a Win32 window
//   - which present-related device extensions and features exist, and whether they can be enabled together
//   - which time domains vkGetCalibratedTimestampsKHR can convert between (needed to read present timing in QPC)
//
// Usage: vk-caps [--layers-off third-party|all-implicit|none]
//   --layers-off  set VK_LOADER_LAYERS_DISABLE for this process before the loader starts (default: leave the
//                 loader alone), so the same probe shows what changes without the overlay/capture layers.
// CC0 / public domain. Throwaway spike code.
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#define VK_USE_PLATFORM_WIN32_KHR
#include <windows.h>
#include <dxgi.h>

#include <vulkan/vulkan.h>

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "vk-layers.h"

#define MAX_DEVICES 8
#define MAX_EXTS 1024

// ---- small helpers -------------------------------------------------------------------------------

static const char *res_name(VkResult r)
{
    switch (r) {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_NOT_READY: return "VK_NOT_READY";
    case VK_TIMEOUT: return "VK_TIMEOUT";
    case VK_INCOMPLETE: return "VK_INCOMPLETE";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
    case VK_ERROR_LAYER_NOT_PRESENT: return "VK_ERROR_LAYER_NOT_PRESENT";
    case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    case VK_ERROR_INCOMPATIBLE_DRIVER: return "VK_ERROR_INCOMPATIBLE_DRIVER";
    default: return "other VkResult";
    }
}

static const char *domain_name(VkTimeDomainKHR d)
{
    switch ((int) d) {
    case VK_TIME_DOMAIN_DEVICE_KHR: return "DEVICE";
    case VK_TIME_DOMAIN_CLOCK_MONOTONIC_KHR: return "CLOCK_MONOTONIC";
    case VK_TIME_DOMAIN_CLOCK_MONOTONIC_RAW_KHR: return "CLOCK_MONOTONIC_RAW";
    case VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR: return "QUERY_PERFORMANCE_COUNTER";
    case VK_TIME_DOMAIN_PRESENT_STAGE_LOCAL_EXT: return "PRESENT_STAGE_LOCAL";
    case VK_TIME_DOMAIN_SWAPCHAIN_LOCAL_EXT: return "SWAPCHAIN_LOCAL";
    default: return "other";
    }
}

static void utf16_to_utf8(const WCHAR *w, char *out, int size)
{
    if (!WideCharToMultiByte(CP_UTF8, 0, w, -1, out, size, NULL, NULL))
        out[0] = '\0';
}

static void print_module(const wchar_t *path, void *user)
{
    char utf8[520];
    utf16_to_utf8(path, utf8, sizeof(utf8));
    printf("  %s\n", utf8);
}

static uint32_t ext_revision(const VkExtensionProperties *e, uint32_t n, const char *name)
{
    for (uint32_t i = 0; i < n; i++)
        if (!strcmp(e[i].extensionName, name))
            return e[i].specVersion;
    return 0;
}

// ---- DXGI side: which adapter is this GPU, and does it drive screens ---------------------------------

struct dxgi_adapter {
    char name[256];
    LUID luid;
    int outputs;          // outputs attached to the desktop
    int outputs_total;
};

static int enum_dxgi(struct dxgi_adapter *out, int max)
{
    IDXGIFactory1 *fac = NULL;
    int n = 0;
    if (FAILED(CreateDXGIFactory1(&IID_IDXGIFactory1, (void **) &fac)))
        return 0;
    for (UINT i = 0; n < max; i++) {
        IDXGIAdapter1 *ad = NULL;
        if (IDXGIFactory1_EnumAdapters1(fac, i, &ad) == DXGI_ERROR_NOT_FOUND)
            break;
        DXGI_ADAPTER_DESC1 d;
        IDXGIAdapter1_GetDesc1(ad, &d);
        struct dxgi_adapter *a = &out[n++];
        memset(a, 0, sizeof(*a));
        utf16_to_utf8(d.Description, a->name, sizeof(a->name));
        a->luid = d.AdapterLuid;
        for (UINT o = 0;; o++) {
            IDXGIOutput *op = NULL;
            if (IDXGIAdapter1_EnumOutputs(ad, o, &op) == DXGI_ERROR_NOT_FOUND)
                break;
            DXGI_OUTPUT_DESC od;
            IDXGIOutput_GetDesc(op, &od);
            a->outputs_total++;
            if (od.AttachedToDesktop)
                a->outputs++;
            IDXGIOutput_Release(op);
        }
        IDXGIAdapter1_Release(ad);
    }
    IDXGIFactory1_Release(fac);
    return n;
}

// ---- one GPU ---------------------------------------------------------------------------------------

static const char *const WATCHED_DEVICE_EXTS[] = {
    "VK_KHR_swapchain", "VK_KHR_swapchain_mutable_format", "VK_KHR_swapchain_maintenance1",
    "VK_EXT_swapchain_maintenance1", "VK_KHR_present_id", "VK_KHR_present_id2", "VK_KHR_present_wait",
    "VK_KHR_present_wait2", "VK_EXT_present_timing", "VK_KHR_present_mode_fifo_latest_ready",
    "VK_KHR_calibrated_timestamps", "VK_EXT_calibrated_timestamps", "VK_EXT_full_screen_exclusive",
    "VK_EXT_hdr_metadata", "VK_KHR_incremental_present", "VK_GOOGLE_display_timing", "VK_EXT_display_control",
    "VK_KHR_timeline_semaphore", "VK_KHR_synchronization2", "VK_KHR_internally_synchronized_queues",
    "VK_KHR_external_semaphore_win32", "VK_KHR_external_fence_win32", "VK_KHR_external_memory_win32",
    "VK_KHR_video_queue", "VK_KHR_video_decode_queue", "VK_KHR_video_decode_h264", "VK_KHR_video_decode_h265",
    "VK_KHR_video_decode_av1", "VK_KHR_video_maintenance1", "VK_NV_low_latency2",
};

struct present_fns {
    PFN_vkSetSwapchainPresentTimingQueueSizeEXT set_queue_size;
    PFN_vkGetSwapchainTimingPropertiesEXT get_timing_props;
    PFN_vkGetSwapchainTimeDomainPropertiesEXT get_time_domains;
    PFN_vkGetPastPresentationTimingEXT get_past_timing;
    PFN_vkWaitForPresent2KHR wait_present2;
    PFN_vkGetCalibratedTimestampsKHR calibrated;
    PFN_vkCreateSwapchainKHR create_swapchain;
    PFN_vkQueuePresentKHR queue_present;
};

static void probe_device(VkInstance inst, VkPhysicalDevice pd, int index, const struct dxgi_adapter *dx, int ndx)
{
    PFN_vkGetPhysicalDeviceProperties2 get_props2 =
        (PFN_vkGetPhysicalDeviceProperties2) vkGetInstanceProcAddr(inst, "vkGetPhysicalDeviceProperties2");
    PFN_vkGetPhysicalDeviceFeatures2 get_feats2 =
        (PFN_vkGetPhysicalDeviceFeatures2) vkGetInstanceProcAddr(inst, "vkGetPhysicalDeviceFeatures2");
    PFN_vkGetPhysicalDeviceWin32PresentationSupportKHR win32_support =
        (PFN_vkGetPhysicalDeviceWin32PresentationSupportKHR) vkGetInstanceProcAddr(inst, "vkGetPhysicalDeviceWin32PresentationSupportKHR");
    PFN_vkGetPhysicalDeviceCalibrateableTimeDomainsKHR get_domains =
        (PFN_vkGetPhysicalDeviceCalibrateableTimeDomainsKHR) vkGetInstanceProcAddr(inst, "vkGetPhysicalDeviceCalibrateableTimeDomainsKHR");
    if (!get_props2 || !get_feats2) {
        printf("gpu %d: vkGetPhysicalDeviceProperties2/Features2 missing\n", index);
        return;
    }

    VkPhysicalDeviceIDProperties idp = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ID_PROPERTIES };
    VkPhysicalDeviceDriverProperties drv = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES, .pNext = &idp };
    VkPhysicalDeviceProperties2 p2 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2, .pNext = &drv };
    get_props2(pd, &p2);
    const VkPhysicalDeviceProperties *p = &p2.properties;

    printf("\n== gpu %d: %s\n", index, p->deviceName);
    printf("type %s, vendor 0x%04x device 0x%04x, api %u.%u.%u, driver version 0x%08x, driver \"%s\" \"%s\"\n",
           p->deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU ? "discrete" :
           p->deviceType == VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU ? "integrated" : "other", p->vendorID, p->deviceID,
           VK_API_VERSION_MAJOR(p->apiVersion), VK_API_VERSION_MINOR(p->apiVersion), VK_API_VERSION_PATCH(p->apiVersion),
           p->driverVersion, drv.driverName, drv.driverInfo);
    printf("timestampPeriod %.4f ns, timestampComputeAndGraphics %u\n", (double) p->limits.timestampPeriod,
           (unsigned) p->limits.timestampComputeAndGraphics);

    LUID luid = {0};
    if (idp.deviceLUIDValid)
        memcpy(&luid, idp.deviceLUID, sizeof(luid));
    printf("device LUID %08lx:%08lx (valid %u)", (unsigned long) luid.HighPart, (unsigned long) luid.LowPart,
           (unsigned) idp.deviceLUIDValid);
    for (int i = 0; i < ndx; i++)
        if (idp.deviceLUIDValid && dx[i].luid.HighPart == luid.HighPart && dx[i].luid.LowPart == luid.LowPart)
            printf(" = DXGI adapter \"%s\": %d outputs attached to the desktop (%d in total)", dx[i].name, dx[i].outputs,
                   dx[i].outputs_total);
    printf("\n");

    // queue families
    uint32_t nq = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &nq, NULL);
    VkQueueFamilyProperties *qf = calloc(nq ? nq : 1, sizeof(*qf));
    vkGetPhysicalDeviceQueueFamilyProperties(pd, &nq, qf);
    for (uint32_t i = 0; i < nq; i++)
        printf("queue family %u: x%u flags 0x%x%s%s%s%s%s, timestampValidBits %u, Win32 presentation %s\n", i, qf[i].queueCount,
               qf[i].queueFlags, qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT ? " graphics" : "",
               qf[i].queueFlags & VK_QUEUE_COMPUTE_BIT ? " compute" : "",
               qf[i].queueFlags & VK_QUEUE_TRANSFER_BIT ? " transfer" : "",
               qf[i].queueFlags & VK_QUEUE_VIDEO_DECODE_BIT_KHR ? " video-decode" : "",
               qf[i].queueFlags & VK_QUEUE_VIDEO_ENCODE_BIT_KHR ? " video-encode" : "", qf[i].timestampValidBits,
               win32_support ? (win32_support(pd, i) ? "yes" : "no") : "?");

    // extensions
    uint32_t ne = 0;
    vkEnumerateDeviceExtensionProperties(pd, NULL, &ne, NULL);
    VkExtensionProperties *ex = calloc(ne ? ne : 1, sizeof(*ex));
    vkEnumerateDeviceExtensionProperties(pd, NULL, &ne, ex);
    printf("device extensions: %u in total; watched ones:\n", ne);
    for (size_t i = 0; i < sizeof(WATCHED_DEVICE_EXTS) / sizeof(WATCHED_DEVICE_EXTS[0]); i++) {
        uint32_t rev = ext_revision(ex, ne, WATCHED_DEVICE_EXTS[i]);
        printf("  %-44s %s", WATCHED_DEVICE_EXTS[i], rev ? "yes" : "no");
        if (rev)
            printf(" (revision %u)", rev);
        printf("\n");
    }

    // features
    VkPhysicalDevicePresentTimingFeaturesEXT f_timing = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_TIMING_FEATURES_EXT };
    VkPhysicalDevicePresentId2FeaturesKHR f_id2 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_ID_2_FEATURES_KHR, .pNext = &f_timing };
    VkPhysicalDevicePresentWait2FeaturesKHR f_wait2 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_WAIT_2_FEATURES_KHR, .pNext = &f_id2 };
    VkPhysicalDevicePresentIdFeaturesKHR f_id = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_ID_FEATURES_KHR, .pNext = &f_wait2 };
    VkPhysicalDevicePresentWaitFeaturesKHR f_wait = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_WAIT_FEATURES_KHR, .pNext = &f_id };
    VkPhysicalDeviceSwapchainMaintenance1FeaturesKHR f_maint = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SWAPCHAIN_MAINTENANCE_1_FEATURES_KHR, .pNext = &f_wait };
    VkPhysicalDevicePresentModeFifoLatestReadyFeaturesKHR f_latest = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_MODE_FIFO_LATEST_READY_FEATURES_KHR, .pNext = &f_maint };
    VkPhysicalDeviceFeatures2 feats = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &f_latest };
    get_feats2(pd, &feats);
    printf("features: presentTiming %u (absolute %u, relative %u), presentId %u, presentId2 %u, presentWait %u, presentWait2 %u, "
           "swapchainMaintenance1 %u, presentModeFifoLatestReady %u\n",
           (unsigned) f_timing.presentTiming, (unsigned) f_timing.presentAtAbsoluteTime,
           (unsigned) f_timing.presentAtRelativeTime, (unsigned) f_id.presentId, (unsigned) f_id2.presentId2,
           (unsigned) f_wait.presentWait, (unsigned) f_wait2.presentWait2, (unsigned) f_maint.swapchainMaintenance1,
           (unsigned) f_latest.presentModeFifoLatestReady);

    // time domains that vkGetCalibratedTimestampsKHR can convert between, without any swapchain
    if (get_domains) {
        uint32_t nd = 0;
        VkTimeDomainKHR doms[16];
        if (get_domains(pd, &nd, NULL) == VK_SUCCESS && nd <= 16 && get_domains(pd, &nd, doms) == VK_SUCCESS) {
            printf("calibrateable time domains (device level):");
            for (uint32_t i = 0; i < nd; i++)
                printf(" %s", domain_name(doms[i]));
            printf("\n");
        }
    } else {
        printf("calibrateable time domains: vkGetPhysicalDeviceCalibrateableTimeDomainsKHR not available\n");
    }

    // Create a device with the whole present set switched on, to see whether the driver accepts the combination.
    // The instance has VK_KHR_surface_maintenance1 and VK_KHR_get_surface_capabilities2 enabled (see main).
    static const char *const want[] = {
        "VK_KHR_swapchain", "VK_KHR_present_id2", "VK_KHR_present_wait2", "VK_EXT_present_timing",
        "VK_KHR_calibrated_timestamps", "VK_KHR_swapchain_maintenance1", "VK_KHR_present_id", "VK_KHR_present_wait",
    };
    const char *enabled[16];
    uint32_t nen = 0;
    for (size_t i = 0; i < sizeof(want) / sizeof(want[0]); i++)
        if (ext_revision(ex, ne, want[i]))
            enabled[nen++] = want[i];

    int gfx = -1;
    for (uint32_t i = 0; i < nq && gfx < 0; i++)
        if (qf[i].queueFlags & VK_QUEUE_GRAPHICS_BIT)
            gfx = (int) i;
    if (gfx >= 0 && ext_revision(ex, ne, "VK_KHR_swapchain")) {
        // features chain: only what the extensions we enable need
        VkPhysicalDevicePresentTimingFeaturesEXT e_timing = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_TIMING_FEATURES_EXT,
            .presentTiming = f_timing.presentTiming, .presentAtAbsoluteTime = f_timing.presentAtAbsoluteTime,
            .presentAtRelativeTime = f_timing.presentAtRelativeTime };
        VkPhysicalDevicePresentId2FeaturesKHR e_id2 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_ID_2_FEATURES_KHR,
            .pNext = &e_timing, .presentId2 = f_id2.presentId2 };
        VkPhysicalDevicePresentWait2FeaturesKHR e_wait2 = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_WAIT_2_FEATURES_KHR,
            .pNext = &e_id2, .presentWait2 = f_wait2.presentWait2 };
        VkPhysicalDeviceSwapchainMaintenance1FeaturesKHR e_maint = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SWAPCHAIN_MAINTENANCE_1_FEATURES_KHR,
            .pNext = &e_wait2, .swapchainMaintenance1 = f_maint.swapchainMaintenance1 };
        VkPhysicalDevicePresentIdFeaturesKHR e_id = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_ID_FEATURES_KHR,
            .pNext = &e_maint, .presentId = f_id.presentId };
        VkPhysicalDevicePresentWaitFeaturesKHR e_wait = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_WAIT_FEATURES_KHR,
            .pNext = &e_id, .presentWait = f_wait.presentWait };
        VkPhysicalDeviceFeatures2 e_feats = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2, .pNext = &e_wait };
        float prio = 1.0f;
        VkDeviceQueueCreateInfo qi = { .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO, .queueFamilyIndex = (uint32_t) gfx,
                                       .queueCount = 1, .pQueuePriorities = &prio };
        VkDeviceCreateInfo di = { .sType = VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO, .pNext = &e_feats, .queueCreateInfoCount = 1,
                                  .pQueueCreateInfos = &qi, .enabledExtensionCount = nen, .ppEnabledExtensionNames = enabled };
        VkDevice dev = VK_NULL_HANDLE;
        VkResult r = vkCreateDevice(pd, &di, NULL, &dev);
        printf("vkCreateDevice with the present set (%u extensions):", nen);
        for (uint32_t i = 0; i < nen; i++)
            printf(" %s", enabled[i]);
        printf(" -> %s (%d)\n", res_name(r), (int) r);
        if (r == VK_SUCCESS) {
            PFN_vkGetDeviceProcAddr gdpa = (PFN_vkGetDeviceProcAddr) vkGetInstanceProcAddr(inst, "vkGetDeviceProcAddr");
            struct present_fns f = {
                (PFN_vkSetSwapchainPresentTimingQueueSizeEXT) gdpa(dev, "vkSetSwapchainPresentTimingQueueSizeEXT"),
                (PFN_vkGetSwapchainTimingPropertiesEXT) gdpa(dev, "vkGetSwapchainTimingPropertiesEXT"),
                (PFN_vkGetSwapchainTimeDomainPropertiesEXT) gdpa(dev, "vkGetSwapchainTimeDomainPropertiesEXT"),
                (PFN_vkGetPastPresentationTimingEXT) gdpa(dev, "vkGetPastPresentationTimingEXT"),
                (PFN_vkWaitForPresent2KHR) gdpa(dev, "vkWaitForPresent2KHR"),
                (PFN_vkGetCalibratedTimestampsKHR) gdpa(dev, "vkGetCalibratedTimestampsKHR"),
                (PFN_vkCreateSwapchainKHR) gdpa(dev, "vkCreateSwapchainKHR"),
                (PFN_vkQueuePresentKHR) gdpa(dev, "vkQueuePresentKHR"),
            };
            printf("entry points: vkSetSwapchainPresentTimingQueueSizeEXT %s, vkGetSwapchainTimingPropertiesEXT %s, "
                   "vkGetSwapchainTimeDomainPropertiesEXT %s, vkGetPastPresentationTimingEXT %s, vkWaitForPresent2KHR %s, "
                   "vkGetCalibratedTimestampsKHR %s, vkCreateSwapchainKHR %s, vkQueuePresentKHR %s\n",
                   f.set_queue_size ? "yes" : "NO", f.get_timing_props ? "yes" : "NO", f.get_time_domains ? "yes" : "NO",
                   f.get_past_timing ? "yes" : "NO", f.wait_present2 ? "yes" : "NO", f.calibrated ? "yes" : "NO",
                   f.create_swapchain ? "yes" : "NO", f.queue_present ? "yes" : "NO");

            // A calibrated read of two domains at once, no swapchain involved: QPC against the device clock.
            if (f.calibrated) {
                VkCalibratedTimestampInfoKHR ci[2] = {
                    { .sType = VK_STRUCTURE_TYPE_CALIBRATED_TIMESTAMP_INFO_KHR, .timeDomain = VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR },
                    { .sType = VK_STRUCTURE_TYPE_CALIBRATED_TIMESTAMP_INFO_KHR, .timeDomain = VK_TIME_DOMAIN_DEVICE_KHR },
                };
                uint64_t ts[2] = {0}, dev_max = 0;
                VkResult cr = f.calibrated(dev, 2, ci, ts, &dev_max);
                LARGE_INTEGER q, freq;
                QueryPerformanceCounter(&q);
                QueryPerformanceFrequency(&freq);
                printf("calibrated read (QPC, DEVICE): %s, QPC ticks %llu (QueryPerformanceCounter right after: %llu, %.3f MHz), "
                       "device ticks %llu, max deviation %llu ns\n", res_name(cr), (unsigned long long) ts[0],
                       (unsigned long long) q.QuadPart, (double) freq.QuadPart / 1e6, (unsigned long long) ts[1],
                       (unsigned long long) dev_max);
            }
            vkDestroyDevice(dev, NULL);
        }
    } else {
        printf("vkCreateDevice test skipped (no graphics queue family or no VK_KHR_swapchain)\n");
    }
    free(ex);
    free(qf);
}

int main(int argc, char **argv)
{
    const char *layers_off = "none";
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--layers-off") && i + 1 < argc) {
            layers_off = argv[++i];
        } else {
            fprintf(stderr, "Usage: %s [--layers-off third-party|all-implicit|none]\n", argv[0]);
            return 2;
        }
    }
    if (!vk_layers_apply(layers_off)) {
        fprintf(stderr, "vk-caps: unknown --layers-off value '%s'\n", layers_off);
        return 2;
    }

    uint32_t inst_ver = VK_API_VERSION_1_0;
    PFN_vkEnumerateInstanceVersion enum_ver =
        (PFN_vkEnumerateInstanceVersion) vkGetInstanceProcAddr(VK_NULL_HANDLE, "vkEnumerateInstanceVersion");
    if (enum_ver)
        enum_ver(&inst_ver);
    printf("loader/instance version %u.%u.%u; VK_LOADER_LAYERS_DISABLE=%s\n", VK_API_VERSION_MAJOR(inst_ver),
           VK_API_VERSION_MINOR(inst_ver), VK_API_VERSION_PATCH(inst_ver), getenv("VK_LOADER_LAYERS_DISABLE") ? getenv("VK_LOADER_LAYERS_DISABLE") : "(unset)");

    // Layers the loader would inject into an instance created now (implicit ones come in without being asked for).
    uint32_t nl = 0;
    vkEnumerateInstanceLayerProperties(&nl, NULL);
    VkLayerProperties *lp = calloc(nl ? nl : 1, sizeof(*lp));
    vkEnumerateInstanceLayerProperties(&nl, lp);
    printf("instance layers visible to the loader: %u\n", nl);
    for (uint32_t i = 0; i < nl; i++)
        printf("  %-40s spec %u.%u.%u impl %u  %s\n", lp[i].layerName, VK_API_VERSION_MAJOR(lp[i].specVersion),
               VK_API_VERSION_MINOR(lp[i].specVersion), VK_API_VERSION_PATCH(lp[i].specVersion),
               lp[i].implementationVersion, lp[i].description);
    free(lp);

    uint32_t nie = 0;
    vkEnumerateInstanceExtensionProperties(NULL, &nie, NULL);
    VkExtensionProperties *ie = calloc(nie ? nie : 1, sizeof(*ie));
    vkEnumerateInstanceExtensionProperties(NULL, &nie, ie);
    printf("instance extensions: %u; watched: VK_KHR_surface %s, VK_KHR_win32_surface %s, VK_KHR_get_surface_capabilities2 %s, "
           "VK_KHR_surface_maintenance1 %s, VK_EXT_surface_maintenance1 %s, VK_EXT_swapchain_colorspace %s\n", nie,
           ext_revision(ie, nie, "VK_KHR_surface") ? "yes" : "no", ext_revision(ie, nie, "VK_KHR_win32_surface") ? "yes" : "no",
           ext_revision(ie, nie, "VK_KHR_get_surface_capabilities2") ? "yes" : "no",
           ext_revision(ie, nie, "VK_KHR_surface_maintenance1") ? "yes" : "no",
           ext_revision(ie, nie, "VK_EXT_surface_maintenance1") ? "yes" : "no",
           ext_revision(ie, nie, "VK_EXT_swapchain_colorspace") ? "yes" : "no");

    // No surface extension is *used* to create a surface here; they are enabled so the device-level dry run can
    // enable the extensions that depend on them.
    const char *iext[4];
    uint32_t nx = 0;
    static const char *const iwant[] = { "VK_KHR_surface", "VK_KHR_win32_surface", "VK_KHR_get_surface_capabilities2",
                                         "VK_KHR_surface_maintenance1" };
    for (size_t i = 0; i < 4; i++)
        if (ext_revision(ie, nie, iwant[i]))
            iext[nx++] = iwant[i];
    free(ie);

    VkApplicationInfo app = { .sType = VK_STRUCTURE_TYPE_APPLICATION_INFO, .pApplicationName = "vk-caps",
                              .apiVersion = inst_ver >= VK_API_VERSION_1_3 ? VK_API_VERSION_1_3 : inst_ver };
    VkInstanceCreateInfo ici = { .sType = VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO, .pApplicationInfo = &app,
                                 .enabledExtensionCount = nx, .ppEnabledExtensionNames = iext };
    VkInstance inst = VK_NULL_HANDLE;
    VkResult r = vkCreateInstance(&ici, NULL, &inst);
    if (r != VK_SUCCESS) {
        fprintf(stderr, "vk-caps: vkCreateInstance failed: %s (%d)\n", res_name(r), (int) r);
        return 1;
    }

    struct dxgi_adapter dx[8];
    int ndx = enum_dxgi(dx, 8);
    printf("DXGI adapters: %d\n", ndx);
    for (int i = 0; i < ndx; i++)
        printf("  %d: %s, LUID %08lx:%08lx, %d outputs attached to the desktop (%d in total)\n", i, dx[i].name,
               (unsigned long) dx[i].luid.HighPart, (unsigned long) dx[i].luid.LowPart, dx[i].outputs, dx[i].outputs_total);

    uint32_t npd = 0;
    VkPhysicalDevice pds[MAX_DEVICES];
    vkEnumeratePhysicalDevices(inst, &npd, NULL);
    if (npd > MAX_DEVICES)
        npd = MAX_DEVICES;
    vkEnumeratePhysicalDevices(inst, &npd, pds);
    printf("physical devices: %u\n", npd);
    for (uint32_t i = 0; i < npd; i++)
        probe_device(inst, pds[i], (int) i, dx, ndx);

    printf("\nmodules loaded into this process from outside the Windows folder and the MSYS2 runtime "
           "(the layer and overlay DLLs the loader injected):\n");
    vk_layers_foreach_foreign_module(print_module, NULL);

    vkDestroyInstance(inst, NULL);
    return 0;
}
