// Own-swapchain backend for perf1vk: our VkSwapchainKHR over the window's surface, its images wrapped for libplacebo
// (pl_vulkan_wrap), so libplacebo still draws every picture, while we control what libplacebo's own swapchain cannot:
//   - the composite alpha (libplacebo asks for premultiplied alpha when the surface offers it, which makes the window
//     translucent as far as the compositor is concerned; this backend asks for opaque),
//   - present ids and vkWaitForPresent2KHR, which stand in for DXGI's frame-latency waitable object: before drawing
//     frame N the thread waits until frame N - lag has been shown, so a present lands on the next refresh,
//   - VK_EXT_present_timing: for every present the time of "queue operations end" and "image first pixel out" are read
//     back, which says on which refresh a present really landed (libplacebo's swapchain reports nothing).
//
// Handing images between us and libplacebo follows what libplacebo's own swapchain does: after vkAcquireNextImageKHR the
// image is released to libplacebo with the acquire semaphore (pl_vulkan_release_ex); once the picture is drawn it is
// held back with a semaphore libplacebo signals (pl_vulkan_hold_ex) and that semaphore is what vkQueuePresentKHR waits on.
// Images are shared between all queue families libplacebo uses (VK_SHARING_MODE_CONCURRENT), which pl_vulkan_wrap requires.
//
// What a present that fails does (spec, vkQueuePresentKHR): with VK_ERROR_PRESENT_TIMING_QUEUE_FULL_EXT the semaphore wait
// has happened and the image is handed back unpresented, so nothing may be retried with the same semaphore or id, and no
// wait may name that id. The backend counts it, reads the results out and carries on with the next frame.
//
// Feedback is kept raw (domain ticks) with the calibrated reads that convert it to QPC; analyze-perf1vk.py does the maths.
#include "perf1vk.h"

#include <math.h>

#define OWN_MAX_IMAGES 8
#define FEEDBACK_QUEUE 256
#define MAX_CALIBRATIONS 400
#define STAGES (VK_PRESENT_STAGE_QUEUE_OPERATIONS_END_BIT_EXT | VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_OUT_BIT_EXT)

struct own_present {
    uint64_t id;
    double wait_call, wait_ret, acq_call, acq_ret, call, ret;
    int wait_result, acq_result, present_result;
};

struct own_feedback {
    uint64_t id;
    double polled;
    uint64_t queue_end_raw, first_pixel_out_raw;   // 0 = not reported
    int domain, complete;
    uint64_t domain_id;                            // the engine may fall back to another domain than the one asked for
};

struct own_calib {
    double qpc_s;
    uint64_t local_raw, max_dev_ns;
    int stage;
};

struct own_swap {
    PFN_vkCreateSwapchainKHR create_swapchain;
    PFN_vkDestroySwapchainKHR destroy_swapchain;
    PFN_vkGetSwapchainImagesKHR get_images;
    PFN_vkAcquireNextImageKHR acquire;
    PFN_vkQueuePresentKHR queue_present;
    PFN_vkQueueWaitIdle queue_wait_idle;
    PFN_vkCreateSemaphore create_semaphore;
    PFN_vkDestroySemaphore destroy_semaphore;
    PFN_vkWaitForPresent2KHR wait_present2;
    PFN_vkSetSwapchainPresentTimingQueueSizeEXT set_queue_size;
    PFN_vkGetSwapchainTimingPropertiesEXT get_timing_props;
    PFN_vkGetSwapchainTimeDomainPropertiesEXT get_time_domains;
    PFN_vkGetPastPresentationTimingEXT get_past_timing;
    PFN_vkGetCalibratedTimestampsKHR calibrated;

    VkDevice dev;
    VkQueue queue;
    uint32_t qfam;
    VkSwapchainKHR sc;
    VkFormat format;
    VkExtent2D extent;
    VkImageUsageFlags usage;
    bool concurrent;            // images shared between libplacebo's queue families
    uint32_t n;
    VkImage vkimg[OWN_MAX_IMAGES];
    pl_tex tex[OWN_MAX_IMAGES];
    VkSemaphore acq_sem[OWN_MAX_IMAGES + 1];
    uint32_t acq_idx;
    VkSemaphore done_sem[OWN_MAX_IMAGES];
    uint32_t cur;               // image between begin and end
    bool in_frame;

    uint64_t next_id;           // id of the next present, from 1
    uint64_t last_ok_id;        // the newest present that vkQueuePresentKHR accepted: the only ids a wait may name
    int wait_lag;               // wait for present (id - lag) before drawing frame id; 0 = rely on the acquire
    bool id2, wait2, waits;     // ids on; wait2 usable; the loop actually waits (wait2 and lag > 0)
    bool timing;
    VkPresentStageFlagsEXT stage_mask;   // the stages asked for: the two we want that the surface can report
    bool surf_absolute, surf_relative;   // what this surface says about absolute / relative target times (we send neither)
    int domain;
    uint64_t domain_id;
    uint64_t refresh_ns, interval_ns;
    double next_cal;
    int queue_full, wait_timeouts, queue_idle_waits;
    bool premultiplied;

    struct own_present *p; size_t np, cap_p;
    struct own_feedback *fb; size_t nfb, cap_fb;
    struct own_calib cal[MAX_CALIBRATIONS]; int ncal;
    struct own_present cur_rec; // the record of the loop in progress
};

#define PUSH(arr, n, cap, v) do { \
        if ((n) == (cap)) { \
            size_t nc_ = (cap) ? (cap) * 2 : 4096; \
            void *np_ = realloc((arr), nc_ * sizeof(*(arr))); \
            if (!np_) break; \
            (arr) = np_; (cap) = nc_; \
        } \
        (arr)[(n)++] = (v); \
    } while (0)

static bool has_dev_ext(pl_vulkan vk, const char *name)
{
    for (int i = 0; i < vk->num_extensions; i++)
        if (!strcmp(vk->extensions[i], name))
            return true;
    return false;
}

// A feature structure of the device's enabled-features chain, or NULL.
static const void *find_feature(pl_vulkan vk, VkStructureType type)
{
    for (const VkBaseInStructure *p = (const VkBaseInStructure *) vk->features; p; p = p->pNext)
        if (p->sType == type)
            return p;
    return NULL;
}

// One calibrated read of the swapchain's time domain against QPC (two reads for the stage-local domain: one per stage).
// The QPC domain needs none. A read whose domain value is 0 means the engine no longer supports that domain id: dropped.
static void calibrate(struct own_swap *o)
{
    if (!o->timing || !o->calibrated || o->domain == VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR)
        return;
    uint32_t stages[2] = { 0, 0 };
    int n = 1;
    if (o->domain == VK_TIME_DOMAIN_PRESENT_STAGE_LOCAL_EXT) {
        stages[0] = VK_PRESENT_STAGE_QUEUE_OPERATIONS_END_BIT_EXT;
        stages[1] = VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_OUT_BIT_EXT;
        n = 2;
    }
    for (int i = 0; i < n; i++) {
        if (o->ncal >= MAX_CALIBRATIONS)
            return;
        VkSwapchainCalibratedTimestampInfoEXT sci = { .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CALIBRATED_TIMESTAMP_INFO_EXT,
            .swapchain = o->sc, .presentStage = stages[i], .timeDomainId = o->domain_id };
        VkCalibratedTimestampInfoKHR infos[2] = {
            { .sType = VK_STRUCTURE_TYPE_CALIBRATED_TIMESTAMP_INFO_KHR, .pNext = &sci, .timeDomain = (VkTimeDomainKHR) o->domain },
            { .sType = VK_STRUCTURE_TYPE_CALIBRATED_TIMESTAMP_INFO_KHR, .timeDomain = VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR },
        };
        uint64_t ts[2] = {0}, dev = 0;
        if (o->calibrated(o->dev, 2, infos, ts, &dev) != VK_SUCCESS || ts[0] == 0)
            continue;
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        o->cal[o->ncal++] = (struct own_calib) { (double) ts[1] / (double) f.QuadPart, ts[0], dev, (int) stages[i] };
    }
}

// Picks the time domain the results are reported in, among the ones the swapchain offers: QPC (raw values need no
// conversion), else the present-stage-local domain (listed as calibrateable by the device, and required of every
// implementation), else the swapchain-local one. A domain whose clock cannot be calibrated against QPC is of no use:
// vkGetCalibratedTimestampsKHR accepts only the domains the physical device lists.
static bool choose_domain(struct own_swap *o, const VkTimeDomainKHR *calibrateable, uint32_t n_calibrateable)
{
    VkSwapchainTimeDomainPropertiesEXT p = { .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_TIME_DOMAIN_PROPERTIES_EXT };
    uint64_t counter = 0;
    if (o->get_time_domains(o->dev, o->sc, &p, &counter) != VK_SUCCESS || !p.timeDomainCount)
        return false;
    uint32_t n = p.timeDomainCount;
    VkTimeDomainKHR *doms = calloc(n, sizeof(*doms));
    uint64_t *ids = calloc(n, sizeof(*ids));
    if (!doms || !ids) {
        free(doms);
        free(ids);
        return false;
    }
    p.pTimeDomains = doms;
    p.pTimeDomainIds = ids;
    p.timeDomainCount = n;
    VkResult r = o->get_time_domains(o->dev, o->sc, &p, &counter);
    int best = -1, best_rank = 0;
    if (r == VK_SUCCESS || r == VK_INCOMPLETE) {
        for (uint32_t i = 0; i < p.timeDomainCount; i++) {
            bool calibrates = false;
            for (uint32_t k = 0; k < n_calibrateable; k++)
                calibrates |= calibrateable[k] == doms[i];
            int rank = 0;
            if (doms[i] == VK_TIME_DOMAIN_QUERY_PERFORMANCE_COUNTER_KHR)
                rank = 3;
            else if ((int) doms[i] == VK_TIME_DOMAIN_PRESENT_STAGE_LOCAL_EXT && calibrates)
                rank = 2;
            else if ((int) doms[i] == VK_TIME_DOMAIN_SWAPCHAIN_LOCAL_EXT && calibrates)
                rank = 1;
            if (rank > best_rank) {
                best_rank = rank;
                best = (int) i;
            }
        }
    }
    if (best >= 0) {
        o->domain = (int) doms[best];
        o->domain_id = ids[best];
    }
    free(doms);
    free(ids);
    return best >= 0;
}

bool own_swap_create(struct gpu_ctx *g, const struct cfg *cfg, struct vk_swap *s)
{
    pl_vulkan vk = g->vk;
    struct own_swap *o = calloc(1, sizeof(*o));
    if (!o)
        return false;
    s->own = o;
    o->dev = vk->device;
    o->qfam = vk->queue_graphics.index;
    o->wait_lag = cfg->wait_lag;
    o->premultiplied = cfg->premultiplied;

    PFN_vkGetDeviceProcAddr gdpa = (PFN_vkGetDeviceProcAddr) vk->get_proc_addr(vk->instance, "vkGetDeviceProcAddr");
#define DFN(member, name) o->member = (PFN_##name) gdpa(o->dev, #name)
    DFN(create_swapchain, vkCreateSwapchainKHR);
    DFN(destroy_swapchain, vkDestroySwapchainKHR);
    DFN(get_images, vkGetSwapchainImagesKHR);
    DFN(acquire, vkAcquireNextImageKHR);
    DFN(queue_present, vkQueuePresentKHR);
    DFN(queue_wait_idle, vkQueueWaitIdle);
    DFN(create_semaphore, vkCreateSemaphore);
    DFN(destroy_semaphore, vkDestroySemaphore);
    DFN(wait_present2, vkWaitForPresent2KHR);
    DFN(set_queue_size, vkSetSwapchainPresentTimingQueueSizeEXT);
    DFN(get_timing_props, vkGetSwapchainTimingPropertiesEXT);
    DFN(get_time_domains, vkGetSwapchainTimeDomainPropertiesEXT);
    DFN(get_past_timing, vkGetPastPresentationTimingEXT);
    DFN(calibrated, vkGetCalibratedTimestampsKHR);
#undef DFN
#define IFN(var, name) PFN_##name var = (PFN_##name) vk->get_proc_addr(vk->instance, #name)
    IFN(get_queue2, vkGetDeviceQueue2);
    IFN(get_caps, vkGetPhysicalDeviceSurfaceCapabilitiesKHR);
    IFN(get_caps2, vkGetPhysicalDeviceSurfaceCapabilities2KHR);
    IFN(get_formats, vkGetPhysicalDeviceSurfaceFormatsKHR);
    IFN(get_support, vkGetPhysicalDeviceSurfaceSupportKHR);
    IFN(get_fmt_props, vkGetPhysicalDeviceFormatProperties);
    IFN(get_cal_domains, vkGetPhysicalDeviceCalibrateableTimeDomainsKHR);
#undef IFN
    if (!o->create_swapchain || !o->acquire || !o->queue_present || !o->queue_wait_idle || !get_queue2 || !get_caps ||
        !get_formats || !get_support || !get_fmt_props) {
        fprintf(stderr, "perf1vk: swapchain entry points missing\n");
        return false;
    }
    // libplacebo creates its queues with the flags in pl_vulkan_queue.flags (INTERNALLY_SYNCHRONIZED where the driver has
    // it); a queue made with flags can only be fetched with vkGetDeviceQueue2 and the same flags.
    VkDeviceQueueInfo2 qinfo = { .sType = VK_STRUCTURE_TYPE_DEVICE_QUEUE_INFO_2, .flags = vk->queue_graphics.flags,
                                 .queueFamilyIndex = o->qfam, .queueIndex = 0 };
    get_queue2(o->dev, &qinfo, &o->queue);
    if (!o->queue) {
        fprintf(stderr, "perf1vk: vkGetDeviceQueue2 returned no queue\n");
        return false;
    }
    VkBool32 can_present = VK_FALSE;
    if (get_support(vk->phys_device, o->qfam, s->surface, &can_present) != VK_SUCCESS || !can_present) {
        fprintf(stderr, "perf1vk: the graphics queue family cannot present to this window's surface\n");
        return false;
    }

    // Present ids, present_wait2 and present timing: each needs its extension, its feature, and the surface's say-so
    // (the capabilities are per surface, and the second monitor's surface is a different one).
    const VkPhysicalDevicePresentId2FeaturesKHR *f_id2 = find_feature(vk, VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_ID_2_FEATURES_KHR);
    const VkPhysicalDevicePresentWait2FeaturesKHR *f_wait2 = find_feature(vk, VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_WAIT_2_FEATURES_KHR);
    const VkPhysicalDevicePresentTimingFeaturesEXT *f_timing = find_feature(vk, VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PRESENT_TIMING_FEATURES_EXT);
    VkPresentTimingSurfaceCapabilitiesEXT sc_timing = { .sType = VK_STRUCTURE_TYPE_PRESENT_TIMING_SURFACE_CAPABILITIES_EXT };
    VkSurfaceCapabilitiesPresentId2KHR sc_id2 = { .sType = VK_STRUCTURE_TYPE_SURFACE_CAPABILITIES_PRESENT_ID_2_KHR, .pNext = &sc_timing };
    VkSurfaceCapabilitiesPresentWait2KHR sc_wait2 = { .sType = VK_STRUCTURE_TYPE_SURFACE_CAPABILITIES_PRESENT_WAIT_2_KHR, .pNext = &sc_id2 };
    bool surf_caps2_ok = false;
    if (get_caps2) {
        VkSurfaceCapabilities2KHR c2 = { .sType = VK_STRUCTURE_TYPE_SURFACE_CAPABILITIES_2_KHR, .pNext = &sc_wait2 };
        VkPhysicalDeviceSurfaceInfo2KHR si = { .sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SURFACE_INFO_2_KHR, .surface = s->surface };
        surf_caps2_ok = get_caps2(vk->phys_device, &si, &c2) == VK_SUCCESS;
    }
    o->id2 = surf_caps2_ok && has_dev_ext(vk, "VK_KHR_present_id2") && f_id2 && f_id2->presentId2 && sc_id2.presentId2Supported;
    o->wait2 = o->id2 && o->wait_present2 && has_dev_ext(vk, "VK_KHR_present_wait2") && f_wait2 && f_wait2->presentWait2 &&
               sc_wait2.presentWait2Supported;
    o->waits = o->wait2 && o->wait_lag > 0;
    o->stage_mask = STAGES & sc_timing.presentStageQueries;
    o->surf_absolute = surf_caps2_ok && sc_timing.presentAtAbsoluteTimeSupported;
    o->surf_relative = surf_caps2_ok && sc_timing.presentAtRelativeTimeSupported;
    o->timing = g->have_timing && o->id2 && surf_caps2_ok && f_timing && f_timing->presentTiming && sc_timing.presentTimingSupported &&
                (o->stage_mask & VK_PRESENT_STAGE_QUEUE_OPERATIONS_END_BIT_EXT) && o->set_queue_size && o->get_timing_props &&
                o->get_time_domains && o->get_past_timing;

    VkSurfaceCapabilitiesKHR caps;
    if (get_caps(vk->phys_device, s->surface, &caps) != VK_SUCCESS) {
        fprintf(stderr, "perf1vk: vkGetPhysicalDeviceSurfaceCapabilitiesKHR failed\n");
        return false;
    }
    VkFormat want = cfg->rgb10 ? VK_FORMAT_A2B10G10R10_UNORM_PACK32 : VK_FORMAT_B8G8R8A8_UNORM;
    uint32_t nf = 0;
    get_formats(vk->phys_device, s->surface, &nf, NULL);
    VkSurfaceFormatKHR *formats = calloc(nf ? nf : 1, sizeof(*formats));
    if (!formats)
        return false;
    get_formats(vk->phys_device, s->surface, &nf, formats);
    o->format = VK_FORMAT_UNDEFINED;
    for (uint32_t i = 0; i < nf; i++)
        if (formats[i].format == want && formats[i].colorSpace == VK_COLOR_SPACE_SRGB_NONLINEAR_KHR)
            o->format = want;
    free(formats);
    if (o->format == VK_FORMAT_UNDEFINED) {
        fprintf(stderr, "perf1vk: the surface has no %s format\n", cfg->rgb10 ? "rgb10a2" : "bgra8");
        return false;
    }

    o->extent = caps.currentExtent.width != 0xFFFFFFFFu ? caps.currentExtent : (VkExtent2D) { (uint32_t) s->w, (uint32_t) s->h };
    // Usage, as libplacebo's swapchain picks it: rendering and blitting are required, storage is a bonus.
    VkFormatProperties fp;
    get_fmt_props(vk->phys_device, o->format, &fp);
    VkImageUsageFlags usage = caps.supportedUsageFlags & (VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT | VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_STORAGE_BIT);
    if (!(fp.optimalTilingFeatures & VK_FORMAT_FEATURE_COLOR_ATTACHMENT_BIT)) usage &= ~VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT;
    if (!(fp.optimalTilingFeatures & VK_FORMAT_FEATURE_TRANSFER_DST_BIT)) usage &= ~VK_IMAGE_USAGE_TRANSFER_DST_BIT;
    if (!(fp.optimalTilingFeatures & VK_FORMAT_FEATURE_STORAGE_IMAGE_BIT)) usage &= ~VK_IMAGE_USAGE_STORAGE_BIT;
    if (!(usage & VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT) || !(usage & VK_IMAGE_USAGE_TRANSFER_DST_BIT)) {
        fprintf(stderr, "perf1vk: the swapchain format cannot be rendered to and blitted to\n");
        return false;
    }
    o->usage = usage;

    VkCompositeAlphaFlagBitsKHR alpha = o->premultiplied ? VK_COMPOSITE_ALPHA_PRE_MULTIPLIED_BIT_KHR : VK_COMPOSITE_ALPHA_OPAQUE_BIT_KHR;
    if (!(caps.supportedCompositeAlpha & alpha)) {
        fprintf(stderr, "perf1vk: the surface does not offer composite alpha %s\n", o->premultiplied ? "premultiplied" : "opaque");
        return false;
    }
    uint32_t images = (uint32_t) cfg->vk_depth + 1;
    if (images < caps.minImageCount) images = caps.minImageCount;
    if (caps.maxImageCount && images > caps.maxImageCount) images = caps.maxImageCount;
    if (images > OWN_MAX_IMAGES) images = OWN_MAX_IMAGES;

    // pl_vulkan_wrap wants the image usable by every queue family libplacebo draws or copies on (libplacebo's own swapchain
    // makes its images CONCURRENT over those families too). --sharing exclusive opts out: only safe while everything is drawn
    // on the graphics queue, which holds with libplacebo's default of one queue per family.
    uint32_t fams[3];
    uint32_t nfam = 0;
    const struct pl_vulkan_queue *qs[3] = { &vk->queue_graphics, &vk->queue_compute, &vk->queue_transfer };
    for (int i = 0; i < 3; i++) {
        bool seen = !qs[i]->count;
        for (uint32_t k = 0; k < nfam; k++)
            seen |= fams[k] == qs[i]->index;
        if (!seen)
            fams[nfam++] = qs[i]->index;
    }
    o->concurrent = !cfg->sharing_exclusive && nfam > 1;

    VkSurfaceFullScreenExclusiveInfoEXT fse = { .sType = VK_STRUCTURE_TYPE_SURFACE_FULL_SCREEN_EXCLUSIVE_INFO_EXT,
                                                .fullScreenExclusive = VK_FULL_SCREEN_EXCLUSIVE_DISALLOWED_EXT };
    VkSwapchainCreateInfoKHR sci = {
        .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_CREATE_INFO_KHR,
        .pNext = has_dev_ext(vk, "VK_EXT_full_screen_exclusive") ? &fse : NULL, // like libplacebo: never exclusive full screen
        .surface = s->surface,
        .minImageCount = images,
        .imageFormat = o->format,
        .imageColorSpace = VK_COLOR_SPACE_SRGB_NONLINEAR_KHR,
        .imageExtent = o->extent,
        .imageArrayLayers = 1,
        .imageUsage = usage,
        .imageSharingMode = o->concurrent ? VK_SHARING_MODE_CONCURRENT : VK_SHARING_MODE_EXCLUSIVE,
        .queueFamilyIndexCount = o->concurrent ? nfam : 0,
        .pQueueFamilyIndices = o->concurrent ? fams : NULL,
        .preTransform = VK_SURFACE_TRANSFORM_IDENTITY_BIT_KHR,
        .compositeAlpha = alpha,
        .presentMode = VK_PRESENT_MODE_FIFO_KHR,
        .clipped = VK_TRUE,
    };
    // The flags follow what the surface supports, not how this run paces: wait2 is usable even at --wait-lag 0.
    if (o->id2)
        sci.flags |= VK_SWAPCHAIN_CREATE_PRESENT_ID_2_BIT_KHR;
    if (o->wait2)
        sci.flags |= VK_SWAPCHAIN_CREATE_PRESENT_WAIT_2_BIT_KHR;
    if (o->timing)
        sci.flags |= VK_SWAPCHAIN_CREATE_PRESENT_TIMING_BIT_EXT;
    VkResult r = o->create_swapchain(o->dev, &sci, NULL, &o->sc);
    if (r != VK_SUCCESS) {
        fprintf(stderr, "perf1vk: vkCreateSwapchainKHR failed: %d\n", (int) r);
        return false;
    }
    o->n = OWN_MAX_IMAGES;
    r = o->get_images(o->dev, o->sc, &o->n, o->vkimg);
    if (r != VK_SUCCESS) { // VK_INCOMPLETE would mean more images than the per-image arrays here hold
        fprintf(stderr, "perf1vk: vkGetSwapchainImagesKHR returned %d (more than %d images?)\n", (int) r, OWN_MAX_IMAGES);
        return false;
    }
    s->images = (int) o->n;
    s->w = (int) o->extent.width;
    s->h = (int) o->extent.height;

    VkSemaphoreCreateInfo semi = { .sType = VK_STRUCTURE_TYPE_SEMAPHORE_CREATE_INFO };
    for (uint32_t i = 0; i < o->n; i++) {
        o->tex[i] = pl_vulkan_wrap(g->gpu, pl_vulkan_wrap_params(.image = o->vkimg[i], .width = (int) o->extent.width,
                                                                 .height = (int) o->extent.height, .format = o->format,
                                                                 .usage = usage));
        if (!o->tex[i]) {
            fprintf(stderr, "perf1vk: pl_vulkan_wrap of swapchain image %u failed\n", i);
            return false;
        }
        if (o->create_semaphore(o->dev, &semi, NULL, &o->done_sem[i]) != VK_SUCCESS)
            return false;
    }
    for (uint32_t i = 0; i < o->n + 1; i++)
        if (o->create_semaphore(o->dev, &semi, NULL, &o->acq_sem[i]) != VK_SUCCESS)
            return false;

    o->next_id = 1;
    if (o->timing) {
        VkTimeDomainKHR cal_domains[16];
        uint32_t ncd = 0;   // the domains the physical device can calibrate against QPC
        if (get_cal_domains) {
            ncd = 16;
            VkResult cr = get_cal_domains(vk->phys_device, &ncd, cal_domains);
            if ((cr != VK_SUCCESS && cr != VK_INCOMPLETE) || ncd > 16)
                ncd = cr == VK_INCOMPLETE ? 16 : 0;
        }
        r = o->set_queue_size(o->dev, o->sc, FEEDBACK_QUEUE);
        if (r != VK_SUCCESS) {
            gpu_note("vkSetSwapchainPresentTimingQueueSizeEXT failed: %d; present timing off", (int) r);
            o->timing = false;
        } else if (!choose_domain(o, cal_domains, ncd)) {
            gpu_note("no usable time domain (QPC, or a calibrateable stage / swapchain domain) offered by the swapchain; present timing off");
            o->timing = false;
        } else {
            calibrate(o);
        }
    }
    gpu_note("own swapchain: %u images %ux%u %s %s, composite alpha %s, ids %s, present wait2 %s (lag %d), present timing %s "
             "(stages 0x%x, time domain %d; this surface takes absolute targets %s, relative %s)", o->n, o->extent.width,
             o->extent.height, o->format == VK_FORMAT_A2B10G10R10_UNORM_PACK32 ? "rgb10a2" : "bgra8",
             o->concurrent ? "concurrent" : "exclusive", o->premultiplied ? "premultiplied" : "opaque", o->id2 ? "on" : "off",
             o->wait2 ? (o->waits ? "on" : "available") : "off", o->wait_lag, o->timing ? "on" : "off",
             (unsigned) o->stage_mask, o->domain, o->surf_absolute ? "yes" : "no", o->surf_relative ? "yes" : "no");
    return true;
}

void own_swap_destroy(struct gpu_ctx *g, struct vk_swap *s)
{
    struct own_swap *o = s->own;
    if (!o)
        return;
    pl_gpu_finish(g->gpu);
    // pl_gpu_finish waits for libplacebo's own work; the presents still wait on our semaphores. Idle the queue so none is
    // pending when the semaphores go (libplacebo's own swapchain does the same).
    if (o->queue && o->queue_wait_idle) {
        g->vk->lock_queue(g->vk, g->vk->queue_graphics.index, 0);
        o->queue_wait_idle(o->queue);
        g->vk->unlock_queue(g->vk, g->vk->queue_graphics.index, 0);
    }
    for (uint32_t i = 0; i < o->n; i++) {
        pl_tex_destroy(g->gpu, &o->tex[i]);
        if (o->done_sem[i])
            o->destroy_semaphore(o->dev, o->done_sem[i], NULL);
    }
    for (uint32_t i = 0; i < o->n + 1; i++)
        if (o->acq_sem[i])
            o->destroy_semaphore(o->dev, o->acq_sem[i], NULL);
    if (o->sc)
        o->destroy_swapchain(o->dev, o->sc, NULL);
    free(o->p);
    free(o->fb);
    free(o);
    s->own = NULL;
}

// Reads every finished timing result into o->fb. Cheap when nothing is pending.
static void poll_feedback(struct own_swap *o)
{
    VkPastPresentationTimingInfoEXT info = { .sType = VK_STRUCTURE_TYPE_PAST_PRESENTATION_TIMING_INFO_EXT, .swapchain = o->sc };
    VkPastPresentationTimingPropertiesEXT props = { .sType = VK_STRUCTURE_TYPE_PAST_PRESENTATION_TIMING_PROPERTIES_EXT };
    VkResult res = o->get_past_timing(o->dev, &info, &props);
    if ((res != VK_SUCCESS && res != VK_INCOMPLETE) || !props.presentationTimingCount)
        return;
    uint32_t n = props.presentationTimingCount;
    VkPastPresentationTimingEXT *out = calloc(n, sizeof(*out));
    VkPresentStageTimeEXT (*stages)[4] = calloc(n, sizeof(*stages));
    if (!out || !stages) {
        free(out);
        free(stages);
        return;
    }
    for (uint32_t i = 0; i < n; i++) {
        out[i].sType = VK_STRUCTURE_TYPE_PAST_PRESENTATION_TIMING_EXT;
        out[i].presentStageCount = 4;
        out[i].pPresentStages = stages[i];
    }
    props.presentationTimingCount = n;
    props.pPresentationTimings = out;
    res = o->get_past_timing(o->dev, &info, &props);
    double polled = now_s();
    if (res == VK_SUCCESS || res == VK_INCOMPLETE) {
        for (uint32_t i = 0; i < props.presentationTimingCount; i++) {
            struct own_feedback fb = { .id = out[i].presentId, .polled = polled, .domain = (int) out[i].timeDomain,
                                       .complete = out[i].reportComplete, .domain_id = out[i].timeDomainId };
            for (uint32_t k = 0; k < out[i].presentStageCount && k < 4; k++) {
                if (stages[i][k].stage == VK_PRESENT_STAGE_QUEUE_OPERATIONS_END_BIT_EXT)
                    fb.queue_end_raw = stages[i][k].time;
                else if (stages[i][k].stage == VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_OUT_BIT_EXT)
                    fb.first_pixel_out_raw = stages[i][k].time;
            }
            PUSH(o->fb, o->nfb, o->cap_fb, fb);
        }
    }
    free(out);
    free(stages);
}

bool own_swap_begin(struct gpu_ctx *g, struct vk_swap *s, struct pl_swapchain_frame *out)
{
    struct own_swap *o = s->own;
    struct own_present rec = { .id = o->next_id };

    // Latency-one pacing: draw frame N only once frame N - lag is on the screen. Only an id that vkQueuePresentKHR accepted
    // may be waited for (a failed present has no id to wait on), so the wait names the newest accepted id at most.
    bool synced = false;   // true when the wait confirms that everything submitted before the acquire has completed
    if (o->waits && o->next_id > (uint64_t) o->wait_lag) {
        uint64_t wid = o->next_id - (uint64_t) o->wait_lag;
        if (wid > o->last_ok_id)
            wid = o->last_ok_id;
        if (wid > 0) {
            VkPresentWait2InfoKHR wi = { .sType = VK_STRUCTURE_TYPE_PRESENT_WAIT_2_INFO_KHR, .presentId = wid,
                                         .timeout = 100ull * 1000 * 1000 };
            rec.wait_call = now_s();
            VkResult wr = o->wait_present2(o->dev, o->sc, &wi);
            rec.wait_ret = now_s();
            rec.wait_result = (int) wr;
            if (wr == VK_TIMEOUT)
                o->wait_timeouts++;
            else if (wr != VK_SUCCESS && wr != VK_SUBOPTIMAL_KHR) {
                gpu_note("vkWaitForPresent2KHR(%llu) failed: %d", (unsigned long long) wid, (int) wr);
                return false;
            }
            // present N - lag is up, so the render submissions up to it, and with them the waits on the acquire semaphores
            // of frames up to it, are done; the ring of n + 1 acquire semaphores then never reuses a pending one
            synced = wr != VK_TIMEOUT && wid + (uint64_t) o->wait_lag >= o->next_id && (uint32_t) o->wait_lag <= o->n + 1;
        }
    }
    if (o->timing) {
        poll_feedback(o);
        if (now_s() >= o->next_cal) {
            calibrate(o);
            o->next_cal = now_s() + 0.5;
        }
    }

    VkSemaphore sem = o->acq_sem[o->acq_idx];
    // An acquire semaphore may only be reused when its earlier signal and wait are done. Without a confirming wait
    // (--wait-lag 0, a timed-out wait, the first frames after a failed present) idle the queue first: slower, always correct.
    if (!synced && o->next_id > 1) {
        g->vk->lock_queue(g->vk, g->vk->queue_graphics.index, 0);
        o->queue_wait_idle(o->queue);
        g->vk->unlock_queue(g->vk, g->vk->queue_graphics.index, 0);
        o->queue_idle_waits++;
    }
    o->acq_idx = (o->acq_idx + 1) % (o->n + 1);
    uint32_t image = 0;
    rec.acq_call = now_s();
    VkResult ar = o->acquire(o->dev, o->sc, 500ull * 1000 * 1000, sem, VK_NULL_HANDLE, &image);
    rec.acq_ret = now_s();
    rec.acq_result = (int) ar;
    if ((ar != VK_SUCCESS && ar != VK_SUBOPTIMAL_KHR) || image >= o->n) {
        gpu_note("vkAcquireNextImageKHR failed: %d (image %u)", (int) ar, image);
        return false;
    }
    o->cur = image;
    o->in_frame = true;
    o->cur_rec = rec;

    pl_vulkan_release_ex(g->gpu, pl_vulkan_release_params(
        .tex = o->tex[image], .layout = VK_IMAGE_LAYOUT_UNDEFINED, .qf = VK_QUEUE_FAMILY_IGNORED, .semaphore = { sem },
    ));
    pl_fmt fmt = o->tex[image]->params.format;
    int bits = 0;
    for (int i = 0; i < fmt->num_components; i++)
        bits = fmt->component_depth[i] > bits ? fmt->component_depth[i] : bits;
    *out = (struct pl_swapchain_frame) {
        .fbo = o->tex[image],
        .flipped = false,
        .color_repr = { .sys = PL_COLOR_SYSTEM_RGB, .levels = PL_COLOR_LEVELS_FULL,
                        .alpha = o->premultiplied ? PL_ALPHA_PREMULTIPLIED : PL_ALPHA_UNKNOWN,
                        .bits = { .sample_depth = bits, .color_depth = bits } },
        .color_space = pl_color_space_srgb,
    };
    return true;
}

bool own_swap_end(struct gpu_ctx *g, struct vk_swap *s, struct swap_end_times *t)
{
    struct own_swap *o = s->own;
    uint32_t image = o->cur;
    t->call = now_s();   // before the hold, like libplacebo's swapchain whose submit_frame includes it
    bool held = pl_vulkan_hold_ex(g->gpu, pl_vulkan_hold_params(
        .tex = o->tex[image], .layout = VK_IMAGE_LAYOUT_PRESENT_SRC_KHR, .qf = VK_QUEUE_FAMILY_IGNORED,
        .semaphore = { o->done_sem[image] },
    ));
    o->in_frame = false;
    if (!held) {
        gpu_note("holding swapchain image %u for presentation failed", image);
        t->ret = t->done = now_s();
        return false;
    }
    pl_gpu_flush(g->gpu);   // what libplacebo's submit_frame does around its present: rotate queues, release unused memory

    uint64_t id = o->next_id++;
    VkPresentId2KHR pid = { .sType = VK_STRUCTURE_TYPE_PRESENT_ID_2_KHR, .swapchainCount = 1, .pPresentIds = &id };
    VkPresentTimingInfoEXT ti = { .sType = VK_STRUCTURE_TYPE_PRESENT_TIMING_INFO_EXT, .timeDomainId = o->domain_id,
        .presentStageQueries = o->stage_mask, .targetTimeDomainPresentStage = VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_OUT_BIT_EXT };
    VkPresentTimingsInfoEXT tis = { .sType = VK_STRUCTURE_TYPE_PRESENT_TIMINGS_INFO_EXT, .swapchainCount = 1, .pTimingInfos = &ti };
    const void *chain = NULL;
    if (o->id2) {
        pid.pNext = o->timing ? (const void *) &tis : NULL;
        chain = &pid;
    }
    VkPresentInfoKHR pi = { .sType = VK_STRUCTURE_TYPE_PRESENT_INFO_KHR, .pNext = chain, .waitSemaphoreCount = 1,
        .pWaitSemaphores = &o->done_sem[image], .swapchainCount = 1, .pSwapchains = &o->sc, .pImageIndices = &image };

    g->vk->lock_queue(g->vk, g->vk->queue_graphics.index, 0);
    double t_present = now_s();
    VkResult pr = o->queue_present(o->queue, &pi);
    t->ret = now_s();
    g->vk->unlock_queue(g->vk, g->vk->queue_graphics.index, 0);
    t->done = t->ret;
    t->id = id;
    o->cur_rec.id = id;
    o->cur_rec.call = t_present;
    o->cur_rec.ret = t->ret;
    o->cur_rec.present_result = (int) pr;
    PUSH(o->p, o->np, o->cap_p, o->cur_rec);
    if (pr == VK_SUCCESS || pr == VK_SUBOPTIMAL_KHR) {
        o->last_ok_id = id;
        return true;
    }
    if (pr == VK_ERROR_PRESENT_TIMING_QUEUE_FULL_EXT) {
        // The semaphore wait happened and the image went back unpresented: this frame is lost, nothing is retried, and no
        // wait will name this id. Read the results out so the next present has room.
        o->queue_full++;
        poll_feedback(o);
        return true;
    }
    gpu_note("vkQueuePresentKHR failed: %d", (int) pr);
    return false;
}

// Writes the backend's own log block: ,"own":{...} (leading comma, braces included) into a JSON object being written.
void own_swap_write_json(FILE *f, struct vk_swap *s)
{
    struct own_swap *o = s->own;
    if (!o) {
        fprintf(f, ",\"own\":null");
        return;
    }
    LARGE_INTEGER qf;
    QueryPerformanceFrequency(&qf);
    fprintf(f, ",\"own\":{\"qpc_freq\":%.0f,\"images\":%u,\"sharing\":\"%s\",\"id2\":%s,\"wait2\":%s,\"waits\":%s,\"wait_lag\":%d,"
               "\"timing\":%s,\"stage_mask\":%u,\"surface_absolute\":%s,\"surface_relative\":%s,\"composite_alpha\":\"%s\","
               "\"domain\":%d,\"domain_id\":%llu,"
               "\"refresh_duration_ns\":%llu,\"refresh_interval_ns\":%llu,\"queue_full\":%d,\"wait_timeouts\":%d,"
               "\"queue_idle_waits\":%d,\"calibrations\":[",
            (double) qf.QuadPart, o->n, o->concurrent ? "concurrent" : "exclusive", o->id2 ? "true" : "false",
            o->wait2 ? "true" : "false", o->waits ? "true" : "false", o->wait_lag, o->timing ? "true" : "false",
            (unsigned) o->stage_mask, o->surf_absolute ? "true" : "false", o->surf_relative ? "true" : "false",
            o->premultiplied ? "premultiplied" : "opaque", o->domain,
            (unsigned long long) o->domain_id, (unsigned long long) o->refresh_ns, (unsigned long long) o->interval_ns,
            o->queue_full, o->wait_timeouts, o->queue_idle_waits);
    for (int i = 0; i < o->ncal; i++)
        fprintf(f, "%s[%.7f,%llu,%llu,%d]", i ? "," : "", o->cal[i].qpc_s, (unsigned long long) o->cal[i].local_raw,
                (unsigned long long) o->cal[i].max_dev_ns, o->cal[i].stage);
    fprintf(f, "],\"present_fields\":[\"id\",\"wait_call\",\"wait_ret\",\"wait_result\",\"acq_call\",\"acq_ret\",\"acq_result\",\"call\","
               "\"ret\",\"result\"],\"presents\":[");
    for (size_t i = 0; i < o->np; i++) {
        const struct own_present *p = &o->p[i];
        fprintf(f, "%s[%llu,%.7f,%.7f,%d,%.7f,%.7f,%d,%.7f,%.7f,%d]", i ? "," : "", (unsigned long long) p->id, p->wait_call,
                p->wait_ret, p->wait_result, p->acq_call, p->acq_ret, p->acq_result, p->call, p->ret, p->present_result);
    }
    fprintf(f, "],\"feedback_fields\":[\"id\",\"polled\",\"queue_end_raw\",\"first_pixel_out_raw\",\"domain\",\"domain_id\",\"complete\"],"
               "\"feedback\":[");
    for (size_t i = 0; i < o->nfb; i++) {
        const struct own_feedback *b = &o->fb[i];
        fprintf(f, "%s[%llu,%.7f,%llu,%llu,%d,%llu,%d]", i ? "," : "", (unsigned long long) b->id, b->polled,
                (unsigned long long) b->queue_end_raw, (unsigned long long) b->first_pixel_out_raw, b->domain,
                (unsigned long long) b->domain_id, b->complete);
    }
    fprintf(f, "]}");
}

// At the end of a run: drain the last results, then read the refresh duration the swapchain reports.
void own_swap_finish(struct gpu_ctx *g, struct vk_swap *s)
{
    struct own_swap *o = s->own;
    if (!o || !o->timing)
        return;
    pl_gpu_finish(g->gpu);
    double until = now_s() + 0.4;
    while (now_s() < until && o->nfb < o->np) {
        poll_feedback(o);
        Sleep(5);
    }
    calibrate(o);
    VkSwapchainTimingPropertiesEXT tp = { .sType = VK_STRUCTURE_TYPE_SWAPCHAIN_TIMING_PROPERTIES_EXT };
    uint64_t counter = 0;
    if (o->get_timing_props(o->dev, o->sc, &tp, &counter) == VK_SUCCESS) {
        o->refresh_ns = tp.refreshDuration;
        o->interval_ns = tp.refreshInterval;
    }
}
