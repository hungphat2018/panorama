// Lists the adapters and checks that every (backend, adapter) pair can make an offscreen device and an RGBA16F
// canvas. Prints one line per pair, no window and no output is touched.
//   bin/gpu-probe.exe
#include <stdio.h>

#include "pp-gpu.h"

int main(void)
{
    pp_list_adapters(stdout);
    static const char *const adapters[] = { "nvidia", "amd" };
    for (int b = 0; b < 2; b++) {
        for (int a = 0; a < 2; a++) {
            struct pp_gpu g;
            if (!pp_gpu_create(&g, (enum pp_backend) b, adapters[a], PL_LOG_WARN)) {
                printf("%s / %s: FAILED\n", pp_backend_name((enum pp_backend) b), adapters[a]);
                continue;
            }
            pp_gpu_describe(&g, stdout);
            pl_tex t = pp_tex_create_canvas(&g, 2560, 1600, "probe");
            printf("  rgba16f 2560x1600 canvas (sampleable, renderable, storable, host-readable): %s\n", t ? "ok" : "FAILED");
            pl_tex_destroy(g.gpu, &t);
            pp_gpu_destroy(&g);
        }
    }
    return 0;
}
