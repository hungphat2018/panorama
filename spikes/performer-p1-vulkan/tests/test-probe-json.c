// Unit test for the JSON that vk-timing-probe writes (write_json): the probe's state is built by hand, with no Vulkan
// device and no window, one experiment with made-up presents and timing results, and the file goes to
// test-probe-json.py, which checks that it parses and that analyze-vk-timing.py reads it. The probe's own main() is
// renamed so this file can include the whole program and see its static structures.
#define main probe_main
#include "../src/vk-timing-probe.c"
#undef main

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "usage: %s OUT.json\n", argv[0]);
        return 2;
    }
    g_qpc_freq = 10000000.0;
    g_cfg = (struct cfg) { .monitor = 0, .windowed = true, .window_scale = 0.5, .seconds_each = 2.0, .out_dir = ".", .images = 3,
                           .fse_disallow = true, .domain = "auto", .layers_off = "none" };
    g_mon.index = 0;
    snprintf(g_mon.name, sizeof(g_mon.name), "DISPLAY1");
    g_mon.rect = (RECT) { 0, 0, 2560, 1600 };
    g_mon.w = 1280;
    g_mon.h = 800;
    snprintf(g_vk.device_name, sizeof(g_vk.device_name), "Test GPU \"quoted\"");
    g_surf.caps.currentExtent = (VkExtent2D) { 1280, 800 };
    g_surf.caps.minImageCount = 2;
    g_surf.caps.maxImageCount = 8;
    g_surf.caps.supportedCompositeAlpha = 3;
    g_surf.timing.presentTimingSupported = VK_TRUE;
    g_surf.timing.presentAtRelativeTimeSupported = VK_TRUE;
    g_surf.timing.presentStageQueries = 5;
    g_surf.id2.presentId2Supported = VK_TRUE;
    g_surf.wait2.presentWait2Supported = VK_TRUE;
    g_surf.n_modes = 2;
    g_surf.modes[0] = VK_PRESENT_MODE_FIFO_KHR;
    g_surf.modes[1] = VK_PRESENT_MODE_MAILBOX_KHR;
    g_mon.row.timing = (scan_timing) { .refresh_hz = 240.0, .line_s = 2.3148e-6, .scan_s = 3.7037e-3, .blank_s = 0.463e-3 };
    g_mon.row.fit_end = (row_fit) { .ok = true, .base = 1000.0, .period = 1.0 / 240.0, .resid_p50_us = 1.2, .resid_p95_us = 4.0, .samples = 900 };
    for (int i = 0; i < 5; i++) {
        double t = 1000.0 + i / 240.0 - 0.463e-3 + 0.0002;
        PUSH(g_mon.vblanks, g_mon.nvb, g_mon.cap_vb, t);
    }

    struct experiment spec = { "rel-2", 2, 1, true, TARGET_RELATIVE, 2.0, false, false };
    struct exp_result r = {0};
    r.spec = &spec;
    snprintf(r.status, sizeof(r.status), "a \"status\" with a backslash \\ in it");
    r.images = 3;
    r.domain = (int) VK_TIME_DOMAIN_SWAPCHAIN_LOCAL_EXT;
    r.domain_id = 7;
    r.n_domains = 2;
    r.domains[0] = VK_TIME_DOMAIN_SWAPCHAIN_LOCAL_EXT;
    r.dom_ids[0] = 7;
    r.domains[1] = VK_TIME_DOMAIN_PRESENT_STAGE_LOCAL_EXT;
    r.dom_ids[1] = 8;
    r.refresh_duration_ns = 4166666;
    r.refresh_interval_ns = 4166666;
    r.queue_full = 1;
    r.t_start = 1000.0;
    r.t_end = 1000.5;
    struct calib cal0 = { 1000.1, 55000000000ull, 21, 0 }, cal1 = { 1000.6, 55500000000ull, 22, 0 };
    PUSH(r.cal, r.ncal, r.cap_cal, cal0);
    PUSH(r.cal, r.ncal, r.cap_cal, cal1);
    for (int i = 0; i < 4; i++) {
        struct present_log p = { .id = (uint64_t) (i + 1), .acq_call = 1000.0 + i * 0.01, .acq_ret = 1000.0005 + i * 0.01,
                                 .wait_call = 1000.001 + i * 0.01, .wait_ret = 1000.002 + i * 0.01, .wait_result = 0,
                                 .call = 1000.003 + i * 0.01, .ret = 1000.0032 + i * 0.01, .present_result = 0,
                                 .target_raw = 8333332 };
        PUSH(r.p, r.np, r.cap_p, p);
        struct feedback_log b = { .id = (uint64_t) (i + 1), .polled = 1000.02 + i * 0.01, .target_raw = 8333332,
                                  .domain = r.domain, .domain_id = 7, .complete = 1, .n_stages = 2 };
        b.stages[0] = (struct stage_time) { VK_PRESENT_STAGE_QUEUE_OPERATIONS_END_BIT_EXT, 55000000000ull + (uint64_t) i * 10000000 };
        b.stages[1] = (struct stage_time) { VK_PRESENT_STAGE_IMAGE_FIRST_PIXEL_OUT_BIT_EXT, 55004000000ull + (uint64_t) i * 10000000 };
        PUSH(r.fb, r.nfb, r.cap_fb, b);
    }

    FILE *f = fopen(argv[1], "w");
    if (!f) {
        fprintf(stderr, "cannot write %s\n", argv[1]);
        return 1;
    }
    write_json(f, &r, 1);
    fclose(f);
    free(r.p);
    free(r.fb);
    free(r.cal);
    free(g_mon.vblanks);
    return 0;
}
