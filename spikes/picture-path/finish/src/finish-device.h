// Offscreen libplacebo device creation for the finish test driver: one backend (Vulkan or D3D11) on one named GPU.
// No window, no swapchain and no display is touched; every result is rendered into textures and read back.
// (The passes themselves in finish-passes.h do not use this file: a host program that already owns a pl_gpu just
// fills struct fp_env.)
#pragma once

#include <stdbool.h>
#include <stdio.h>

#include <libplacebo/d3d11.h>
#include <libplacebo/dispatch.h>
#include <libplacebo/gpu.h>
#include <libplacebo/log.h>
#include <libplacebo/vulkan.h>

#include "finish-passes.h"

enum fp_backend { FP_BACKEND_VULKAN = 0, FP_BACKEND_D3D11 = 1 };

struct fp_device {
    pl_log log;
    enum fp_backend backend;
    pl_vk_inst inst;
    pl_vulkan vk;
    pl_d3d11 d3d;
    pl_gpu gpu;
    pl_dispatch dp;
    char name[256];       // adapter name
    unsigned vendor_id;   // 0x10DE NVIDIA, 0x1002 AMD
    bool integrated;
    struct fp_env env;    // ready to hand to the passes
};

const char *fp_backend_name(enum fp_backend b);
bool fp_backend_from_name(const char *name, enum fp_backend *out);

// `gpu_match`: case-insensitive part of the adapter name ("5070", "nvidia", "610m", "amd"); NULL = first hardware GPU.
// `layers_off`: switch the Vulkan implicit layers (overlay and capture hooks installed on this PC) off for this process.
// `log_level`: enum pl_log_level of the stderr filter (PL_LOG_WARN = 3 is quiet, PL_LOG_INFO = 4 shows device choice).
bool fp_device_create(struct fp_device *d, enum fp_backend backend, const char *gpu_match, bool layers_off, int log_level);
void fp_device_destroy(struct fp_device *d);
void fp_device_describe(const struct fp_device *d, FILE *out);
void fp_list_adapters(FILE *out);
