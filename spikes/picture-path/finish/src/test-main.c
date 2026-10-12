// The finish test driver: offscreen tests and measurements of passes 2 to 4. No window, no swapchain, no display setting.
//
//   finish-test adapters
//   finish-test <smoke|surround|color|subs|bench|all> [--backend vulkan|d3d11] [--gpu SUBSTR] [--out DIR]
//                [--layers-off] [--log-level N] [--cell N] [--only NAME] [--seconds N] [--font FILE]
#include "test-main.h"

#include <string.h>
#include <windows.h>

static void usage(void)
{
    fputs("usage: finish-test adapters\n"
          "       finish-test <smoke|surround|color|subs|bench|all> [--backend vulkan|d3d11] [--gpu SUBSTR] [--out DIR]\n"
          "                   [--layers-off] [--log-level N] [--cell N] [--only NAME] [--seconds N] [--font FILE]\n",
          stderr);
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        usage();
        return 2;
    }
    const char *cmd = argv[1];
    if (!strcmp(cmd, "adapters")) {
        fp_list_adapters(stdout);
        return 0;
    }

    struct tctx t;
    memset(&t, 0, sizeof(t));
    snprintf(t.out, sizeof(t.out), "out");
    snprintf(t.font_path, sizeof(t.font_path), "%s", "");
    t.seconds = 3;
    enum fp_backend backend = FP_BACKEND_VULKAN;
    const char *gpu = "5070";
    bool layers_off = false;
    int log_level = PL_LOG_WARN;
    for (int i = 2; i < argc; i++) {
        const char *a = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--backend") && v) {
            if (!fp_backend_from_name(v, &backend)) {
                usage();
                return 2;
            }
            i++;
        } else if (!strcmp(a, "--gpu") && v) {
            gpu = v;
            i++;
        } else if (!strcmp(a, "--out") && v) {
            snprintf(t.out, sizeof(t.out), "%s", v);
            i++;
        } else if (!strcmp(a, "--layers-off")) {
            layers_off = true;
        } else if (!strcmp(a, "--log-level") && v) {
            log_level = atoi(v);
            i++;
        } else if (!strcmp(a, "--cell") && v) {
            t.cell_px = atoi(v);
            i++;
        } else if (!strcmp(a, "--only") && v) {
            t.only = v;
            i++;
        } else if (!strcmp(a, "--seconds") && v) {
            t.seconds = atoi(v);
            i++;
        } else if (!strcmp(a, "--font") && v) {
            snprintf(t.font_path, sizeof(t.font_path), "%s", v);
            i++;
        } else {
            usage();
            return 2;
        }
    }
    tu_mkdir_p(t.out);
    if (!t.font_path[0]) {
        // the project's own copy of DejaVu Sans (two folders above finish/), else the one Windows has here
        char exe[MAX_PATH];
        GetModuleFileNameA(NULL, exe, sizeof(exe));
        char *slash = strrchr(exe, '\\');
        if (slash)
            *slash = 0; // ...\finish\bin
        slash = strrchr(exe, '\\');
        if (slash)
            *slash = 0; // ...\finish
        snprintf(t.font_path, sizeof(t.font_path), "%s\\..\\..\\file-matrix\\tools\\fonts\\DejaVuSans.ttf", exe);
        FILE *f = fopen(t.font_path, "rb");
        if (f)
            fclose(f);
        else
            snprintf(t.font_path, sizeof(t.font_path), "C:\\Windows\\Fonts\\DejaVuSans.ttf");
    }
    if (!fp_device_create(&t.dev, backend, gpu, layers_off, log_level)) {
        fprintf(stderr, "finish-test: cannot create a %s device on '%s'\n", fp_backend_name(backend), gpu);
        return 3;
    }
    printf("device: ");
    fp_device_describe(&t.dev, stdout);

    int rc = 0;
    bool all = !strcmp(cmd, "all");
    if (!strcmp(cmd, "smoke") || all)
        rc |= test_smoke(&t);
    if (!strcmp(cmd, "surround") || all)
        rc |= test_surround(&t);
    if (!strcmp(cmd, "color") || all)
        rc |= test_color(&t);
    if (!strcmp(cmd, "subs") || all)
        rc |= test_subs(&t);
    if (!strcmp(cmd, "bench"))
        rc |= test_bench(&t);
    if (strcmp(cmd, "smoke") && strcmp(cmd, "surround") && strcmp(cmd, "color") && strcmp(cmd, "subs") &&
        strcmp(cmd, "bench") && !all) {
        usage();
        rc = 2;
    }
    fp_device_destroy(&t.dev);
    int failed = tu_failures();
    printf("%s: %d check(s) failed\n", cmd, failed);
    return rc | (failed ? 1 : 0);
}
