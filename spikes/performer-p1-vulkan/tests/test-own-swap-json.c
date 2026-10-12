// Unit test for the JSON that the own-swapchain backend writes (own_swap_write_json): builds a backend state by hand, with
// no Vulkan device, writes the block to a file, and test-own-swap-json.py checks that the file parses and that
// analyze-perf1vk.py can use it. The .c file is included so the test sees the private structures.
#include "../src/perf1vk-own-swap.c"

// The two functions the backend calls from elsewhere in the program.
double now_s(void)
{
    return 0.0;
}

void gpu_note(const char *fmt, ...)
{
    (void) fmt;
}

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "usage: %s OUT.json\n", argv[0]);
        return 2;
    }
    struct own_swap o = {0};
    struct vk_swap s = {0};
    s.own = &o;
    o.n = 3;
    o.id2 = true;
    o.wait2 = true;
    o.waits = true;
    o.concurrent = true;
    o.wait_lag = 1;
    o.timing = true;
    o.stage_mask = 5;
    o.surf_absolute = false;
    o.surf_relative = true;
    o.queue_idle_waits = 4;
    o.domain = (int) VK_TIME_DOMAIN_SWAPCHAIN_LOCAL_EXT;
    o.domain_id = 7;
    o.refresh_ns = 4166666;
    o.interval_ns = 4166666;
    o.queue_full = 2;
    o.wait_timeouts = 1;
    o.premultiplied = false;

    for (int i = 0; i < 3; i++) {
        struct own_present p = { .id = (uint64_t) (i + 1), .wait_call = 100.0 + i, .wait_ret = 100.002 + i, .acq_call = 100.003 + i,
                                 .acq_ret = 100.0031 + i, .call = 100.004 + i, .ret = 100.0041 + i, .wait_result = 0,
                                 .acq_result = 0, .present_result = 0 };
        PUSH(o.p, o.np, o.cap_p, p);
        struct own_feedback fb = { .id = (uint64_t) (i + 1), .polled = 100.01 + i, .queue_end_raw = 55000000000ull + (uint64_t) i,
                                   .first_pixel_out_raw = 55000100000ull + (uint64_t) i, .domain = o.domain,
                                   .domain_id = o.domain_id, .complete = 1 };
        PUSH(o.fb, o.nfb, o.cap_fb, fb);
    }
    o.cal[0] = (struct own_calib) { 99.5, 55000000000ull, 21, 0 };
    o.cal[1] = (struct own_calib) { 100.5, 56000000000ull, 24, 0 };
    o.ncal = 2;

    FILE *f = fopen(argv[1], "w");
    if (!f) {
        fprintf(stderr, "cannot write %s\n", argv[1]);
        return 1;
    }
    fputs("{\"name\":\"DISPLAY1\"", f);
    own_swap_write_json(f, &s);
    fputs("}\n", f);
    // and the case without a backend
    struct vk_swap none = {0};
    fputs("{\"name\":\"DISPLAY2\"", f);
    own_swap_write_json(f, &none);
    fputs("}\n", f);
    fclose(f);
    free(o.p);
    free(o.fb);
    return 0;
}
