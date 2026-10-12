// FFmpeg Vulkan Video decoding on libplacebo's VkDevice: "the application creates the device, FFmpeg adopts it".
//
// P0 did it the other way round (FFmpeg created the device, libplacebo imported it), which cannot switch on features FFmpeg
// does not know about, such as VK_EXT_present_timing. Here libplacebo creates the device (with the video decode queue
// family and the decode extensions requested through pl_vulkan_params) and this file describes it to FFmpeg: the instance,
// the physical and logical device, the enabled extensions and features, and the queue families with their video
// capabilities. Decoded frames are then AVVkFrame images on the same device, which libplacebo samples directly
// (pl_map_avframe_ex).
//
// Queue locking: when the device was created with VK_KHR_internally_synchronized_queues (libplacebo does this where the
// driver has it) neither side locks. Otherwise FFmpeg is pointed at libplacebo's lock functions, so both serialise on the
// same mutex per queue.
#include "perf1vk.h"

#include <libavutil/hwcontext.h>
#include <libavutil/hwcontext_vulkan.h>

#define MAX_FAMILIES 16

static pl_vulkan g_lock_vk; // the device whose lock functions FFmpeg is pointed at (one device per process)

static void lock_cb(struct AVHWDeviceContext *ctx, uint32_t queue_family, uint32_t index)
{
    g_lock_vk->lock_queue(g_lock_vk, queue_family, index);
}

static void unlock_cb(struct AVHWDeviceContext *ctx, uint32_t queue_family, uint32_t index)
{
    g_lock_vk->unlock_queue(g_lock_vk, queue_family, index);
}

static bool internally_synchronized(pl_vulkan vk)
{
    for (const VkBaseInStructure *p = (const VkBaseInStructure *) vk->features; p; p = p->pNext)
        if (p->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_INTERNALLY_SYNCHRONIZED_QUEUES_FEATURES_KHR)
            return ((const VkPhysicalDeviceInternallySynchronizedQueuesFeaturesKHR *) p)->internallySynchronizedQueues;
    return false;
}

void *hwdevice_from_libplacebo(struct gpu_ctx *g)
{
    pl_vulkan vk = g->vk;
    PFN_vkGetPhysicalDeviceQueueFamilyProperties2 get_qf2 = (PFN_vkGetPhysicalDeviceQueueFamilyProperties2)
        vk->get_proc_addr(vk->instance, "vkGetPhysicalDeviceQueueFamilyProperties2");
    if (!get_qf2) {
        fprintf(stderr, "perf1vk: vkGetPhysicalDeviceQueueFamilyProperties2 not available\n");
        return NULL;
    }

    AVBufferRef *ref = av_hwdevice_ctx_alloc(AV_HWDEVICE_TYPE_VULKAN);
    if (!ref)
        return NULL;
    AVHWDeviceContext *dc = (AVHWDeviceContext *) ref->data;
    AVVulkanDeviceContext *vc = dc->hwctx;
    vc->get_proc_addr = vk->get_proc_addr;
    vc->inst = vk->instance;
    vc->phys_dev = vk->phys_device;
    vc->act_dev = vk->device;
    vc->device_features = *vk->features;
    vc->enabled_inst_extensions = g->inst->extensions;
    vc->nb_enabled_inst_extensions = g->inst->num_extensions;
    vc->enabled_dev_extensions = vk->extensions;
    vc->nb_enabled_dev_extensions = vk->num_extensions;

    // The queue families libplacebo enabled: its graphics / compute / transfer picks, then every video decode family
    // (all queues of each; libplacebo creates devices with every queue of a family it uses).
    uint32_t nq = 0;
    get_qf2(vk->phys_device, &nq, NULL);
    if (nq > MAX_FAMILIES)
        nq = MAX_FAMILIES;
    VkQueueFamilyVideoPropertiesKHR vid[MAX_FAMILIES];
    VkQueueFamilyProperties2 props[MAX_FAMILIES];
    for (uint32_t i = 0; i < nq; i++) {
        vid[i] = (VkQueueFamilyVideoPropertiesKHR) { .sType = VK_STRUCTURE_TYPE_QUEUE_FAMILY_VIDEO_PROPERTIES_KHR };
        props[i] = (VkQueueFamilyProperties2) { .sType = VK_STRUCTURE_TYPE_QUEUE_FAMILY_PROPERTIES_2, .pNext = &vid[i] };
    }
    get_qf2(vk->phys_device, &nq, props);

    uint32_t picks[3] = { vk->queue_graphics.index, vk->queue_compute.index, vk->queue_transfer.index };
    bool taken[MAX_FAMILIES] = {0};
    vc->nb_qf = 0;
    for (int pass = 0; pass < 2; pass++) {
        for (uint32_t i = 0; i < nq && vc->nb_qf < 64; i++) {
            bool is_pick = (i == picks[0] || i == picks[1] || i == picks[2]);
            bool is_video = (props[i].queueFamilyProperties.queueFlags & VK_QUEUE_VIDEO_DECODE_BIT_KHR) != 0;
            if (taken[i] || (pass == 0 && !is_pick) || (pass == 1 && !is_video))
                continue;
            taken[i] = true;
            vc->qf[vc->nb_qf++] = (AVVulkanDeviceQueueFamily) {
                .idx = (int) i,
                .num = (int) props[i].queueFamilyProperties.queueCount,
                .flags = (VkQueueFlagBits) props[i].queueFamilyProperties.queueFlags,
                .video_caps = (VkVideoCodecOperationFlagBitsKHR) vid[i].videoCodecOperations,
            };
        }
    }

    if (internally_synchronized(vk)) {
        vc->queue_flags = VK_DEVICE_QUEUE_CREATE_INTERNALLY_SYNCHRONIZED_BIT_KHR;
    } else {
        g_lock_vk = vk;
#if FF_API_VULKAN_SYNC_QUEUES
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
        vc->lock_queue = lock_cb;
        vc->unlock_queue = unlock_cb;
#pragma GCC diagnostic pop
#endif
    }

    int r = av_hwdevice_ctx_init(ref);
    if (r < 0) {
        fprintf(stderr, "perf1vk: av_hwdevice_ctx_init for libplacebo's Vulkan device failed: %s\n", av_err2str(r));
        av_buffer_unref(&ref);
        return NULL;
    }
    gpu_note("FFmpeg adopted libplacebo's VkDevice: %d queue families, queues %s", vc->nb_qf,
             internally_synchronized(vk) ? "internally synchronised" : "locked through libplacebo");
    return ref;
}

void hwdevice_free(struct gpu_ctx *g)
{
    AVBufferRef *ref = g->ff_vk_device;
    av_buffer_unref(&ref);
    g->ff_vk_device = NULL;
}
