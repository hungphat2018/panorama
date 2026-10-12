// vk-layers.h: switch Vulkan implicit layers off for this process, and list the DLLs that got injected into it.
//
// This PC registers a stack of implicit layers. The loader injects every one of them into every Vulkan process, and they
// hook vkQueuePresentKHR and friends: eight of them are implicit here, three from the GPU vendors (VK_LAYER_NV_optimus,
// VK_LAYER_NV_present, VK_LAYER_AMD_switchable_graphics) and five third-party (RivaTuner's overlay hook, ReShade, an
// OBS-style capture hook, the Steam overlay and Steam's pipeline cache). The ninth layer the loader lists (Rockstar's) is
// explicit and not injected. In mode "third-party" the GPU vendors' layers stay, because the present path may depend
// on them. VK_LOADER_LAYERS_DISABLE takes comma-separated layer-name globs, plus the special names ~implicit~,
// ~explicit~ and ~all~ (loader 1.3.234 and later).
//
// vk_layers_apply() must run first in main(), before any Vulkan call: the loader reads the variable at vkCreateInstance.
// The same effect without these programs: set VK_LOADER_LAYERS_DISABLE in the shell before starting any Vulkan program,
//   PowerShell:  $env:VK_LOADER_LAYERS_DISABLE = "~implicit~"
//   cmd:         set VK_LOADER_LAYERS_DISABLE=~implicit~
// and each layer also has its own switch in its manifest (disable_environment): DISABLE_RTSS_LAYER=1,
// DISABLE_VK_LAYER_reshade_1=1, DISABLE_VULKAN_OBS_CAPTURE=1, DISABLE_VK_LAYER_VALVE_steam_overlay_1=1,
// DISABLE_VK_LAYER_VALVE_steam_fossilize_1=1, DISABLE_LAYER_NV_OPTIMUS_1=1, DISABLE_LAYER_NV_PRESENT_1=1,
// DISABLE_LAYER_AMD_SWITCHABLE_GRAPHICS_1=1.
#pragma once
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>
#include <wctype.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <psapi.h> // link -lpsapi where vk_layers_foreach_foreign_module is used

// mode: "none" (leave the loader alone), "third-party" (everything except the GPU vendors' layers) or
// "all-implicit". Returns false for an unknown mode.
static inline bool vk_layers_apply(const char *mode)
{
    if (!strcmp(mode, "none"))
        return true;
    if (!strcmp(mode, "all-implicit"))
        return _putenv_s("VK_LOADER_LAYERS_DISABLE", "~implicit~") == 0;
    if (!strcmp(mode, "third-party"))
        return _putenv_s("VK_LOADER_LAYERS_DISABLE",
                         "VK_LAYER_RTSS,VK_LAYER_reshade,VK_LAYER_WEOBS_HOOK,VK_LAYER_VALVE_*,VK_LAYER_EOS_*,"
                         "VK_LAYER_ROCKSTAR_*") == 0;
    return false;
}

typedef void (*vk_module_cb)(const wchar_t *path, void *user);

// Calls cb for every module loaded into this process that lives outside the Windows folder and the MSYS2 runtime: after a
// Vulkan instance exists that is the list of layer and overlay DLLs the loader (or anyone) injected.
static inline void vk_layers_foreach_foreign_module(vk_module_cb cb, void *user)
{
    HMODULE mods[1024];
    DWORD needed = 0;
    if (!EnumProcessModules(GetCurrentProcess(), mods, sizeof(mods), &needed))
        return;
    wchar_t win_dir[MAX_PATH];
    UINT win_len = GetWindowsDirectoryW(win_dir, MAX_PATH);
    size_t n = needed / sizeof(HMODULE);
    if (n > sizeof(mods) / sizeof(mods[0]))
        n = sizeof(mods) / sizeof(mods[0]);
    for (size_t i = 0; i < n; i++) {
        wchar_t path[MAX_PATH];
        if (!GetModuleFileNameW(mods[i], path, MAX_PATH))
            continue;
        if (win_len && !_wcsnicmp(path, win_dir, win_len))
            continue;                      // Windows itself, the GPU drivers in the driver store
        wchar_t lower[MAX_PATH];
        size_t k = 0;
        for (; path[k] && k < MAX_PATH - 1; k++)
            lower[k] = (wchar_t) towlower(path[k]);
        lower[k] = L'\0';
        if (wcsstr(lower, L"\\msys64\\") || wcsstr(lower, L"\\performer-p1-vulkan\\bin\\"))
            continue;                      // the toolchain runtime and this spike's own binaries
        cb(path, user);
    }
}
