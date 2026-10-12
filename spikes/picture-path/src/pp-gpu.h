// Offscreen libplacebo GPU creation for the picture-path harness: one backend (D3D11 or Vulkan) on one named
// adapter. No window, no swapchain, no output is touched; everything is rendered into textures and read back.
#pragma once

#include <stdbool.h>
#include <stdio.h>

#include <libplacebo/gpu.h>
#include <libplacebo/log.h>
#include <libplacebo/renderer.h>
#include <libplacebo/d3d11.h>
#include <libplacebo/vulkan.h>

enum pp_backend { PP_BACKEND_D3D11 = 0, PP_BACKEND_VULKAN = 1 };

struct pp_gpu {
    pl_log log;
    enum pp_backend backend;
    pl_d3d11 d3d;
    pl_vk_inst inst;
    pl_vulkan vk;
    pl_gpu gpu;
    char adapter_name[256];
    unsigned vendor_id;      // PCI vendor: 0x10DE NVIDIA, 0x1002 AMD
    int log_level;           // enum pl_log_level of the callback filter
};

const char *pp_backend_name(enum pp_backend b);
bool pp_backend_from_name(const char *name, enum pp_backend *out);

// Creates the GPU. adapter_match is a case-insensitive substring of the adapter's name ("nvidia", "5070", "amd",
// "610m"); NULL takes the first hardware adapter. Returns false (and prints why) on failure.
bool pp_gpu_create(struct pp_gpu *g, enum pp_backend backend, const char *adapter_match, int log_level);
void pp_gpu_destroy(struct pp_gpu *g);

// Lists the hardware adapters DXGI knows (name, vendor, dedicated memory) to `out`.
void pp_list_adapters(FILE *out);

// One-line summary of the limits the passes depend on (compute, shared memory, group size, texture size).
void pp_gpu_describe(const struct pp_gpu *g, FILE *out);

// A texture used as a monitor canvas or a prepared frame: RGBA16F, sampleable, renderable, storable, host-readable.
// Returns NULL (with a message) if the GPU has no such format.
pl_tex pp_tex_create_canvas(const struct pp_gpu *g, int w, int h, const char *what);
// The same with another RGBA format by name ("rgba32f" for the map debug dump).
pl_tex pp_tex_create_canvas_fmt(const struct pp_gpu *g, int w, int h, const char *fmt_name, const char *what);

// Finds a texture format by libplacebo's name ("rgba16f", "r8", "rg8", ...), NULL if the GPU has none.
pl_fmt pp_find_fmt(const struct pp_gpu *g, const char *name);

// A pl_frame that describes `tex` as a render target for pass 1's output contract: RGBA16F, linear light, BT.709 primaries,
// 1.0 = SDR white, premultiplied alpha = picture coverage. crop covers the whole texture.
void pp_canvas_frame(struct pl_frame *f, pl_tex tex);
