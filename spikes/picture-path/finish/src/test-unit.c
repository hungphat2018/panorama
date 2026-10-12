// CPU-only checks of the finish passes: the surround's temporal rule, the colour maths and the libass time rule.
// No GPU, no window. Run: run.cmd finish-unit.exe
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "finish-passes.h"

static int g_fail, g_run;

static void check(int ok, const char *fmt, ...)
{
    char buf[400];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    g_run++;
    printf("  [%s] %s\n", ok ? "pass" : "FAIL", buf);
    if (!ok)
        g_fail++;
}

// One scalar follower driven by the real rule: the same update the shader does on every cell.
static double follow(double s, double target, struct fp_surround_step st)
{
    if (st.alpha >= 1.0f && st.max_delta >= 1.0e8f)
        return target;
    double d = st.alpha * (target - s);
    double lim = st.max_delta;
    d = d < -lim ? -lim : d > lim ? lim : d;
    return s + d;
}

static void test_clock(void)
{
    printf("== surround: temporal rule\n");
    struct fp_surround_time tm = { .tau_steady_s = 0.5f, .cut_rule = FP_CUT_SNAP };

    // A step of the picture at t = 0: the state follows 1 - exp(-t/tau) whatever the refresh rate.
    for (int hz = 60; hz <= 240; hz *= 4) {
        struct fp_surround_clock c;
        fp_surround_clock_reset(&c);
        double s = 0.0, dt = 1.0 / hz;
        struct fp_surround_frame f0 = { .dt_s = dt, .reset = true };
        s = follow(s, 0.0, fp_surround_clock_step(&c, &tm, &f0));
        for (int k = 1; k <= hz; k++) { // one second of refreshes, the target is 1 from the first one
            struct fp_surround_frame f = { .dt_s = dt };
            s = follow(s, 1.0, fp_surround_clock_step(&c, &tm, &f));
        }
        double want = 1.0 - exp(-1.0 / 0.5);
        check(fabs(s - want) < 2e-4, "step response at %d Hz: %.6f after 1 s, exp(-t/tau) says %.6f", hz, s, want);
    }

    // Reset snaps; a pause (dt = 0) freezes the state.
    {
        struct fp_surround_clock c;
        fp_surround_clock_reset(&c);
        struct fp_surround_frame r = { .dt_s = 0.01, .reset = true };
        struct fp_surround_step st = fp_surround_clock_step(&c, &tm, &r);
        check(st.alpha == 1.0f && st.max_delta >= 1.0e8f, "first refresh and reset snap to the picture");
        struct fp_surround_frame p = { .dt_s = 0.0 };
        st = fp_surround_clock_step(&c, &tm, &p);
        check(st.alpha == 0.0f, "a paused refresh (dt = 0) does not move the state");
    }

    // Snap: only the cut refresh jumps.
    {
        struct fp_surround_clock c;
        fp_surround_clock_reset(&c);
        struct fp_surround_frame f = { .dt_s = 1.0 / 240, .reset = true };
        fp_surround_clock_step(&c, &tm, &f);
        f.reset = false;
        int jumps = 0, n = 0;
        for (int k = 0; k < 480; k++) {
            f.cut = k == 100;
            struct fp_surround_step st = fp_surround_clock_step(&c, &tm, &f);
            jumps += st.alpha >= 1.0f;
            n++;
        }
        check(jumps == 1, "snap: exactly one jump in %d refreshes (the cut's own)", n);
    }

    // Blend and hold: how many refreshes the special rule lasts.
    {
        struct fp_surround_time b = { .tau_steady_s = 0.5f, .cut_rule = FP_CUT_BLEND, .tau_blend_s = 0.08f,
                                      .blend_window_s = 0.21f };
        struct fp_surround_time h = { .tau_steady_s = 0.5f, .cut_rule = FP_CUT_HOLD, .hold_s = 0.6f };
        for (int pass = 0; pass < 2; pass++) {
            const struct fp_surround_time *t = pass ? &h : &b;
            struct fp_surround_clock c;
            fp_surround_clock_reset(&c);
            struct fp_surround_frame f = { .dt_s = 1.0 / 240, .reset = true };
            fp_surround_clock_step(&c, t, &f);
            f.reset = false;
            int special = 0, last_special = -1;
            for (int k = 0; k < 480; k++) {
                f.cut = k == 100;
                struct fp_surround_step st = fp_surround_clock_step(&c, t, &f);
                float steady = (float) (1.0 - exp(-(1.0 / 240) / 0.5));
                bool is_special = fabsf(st.alpha - steady) > 1e-6f;
                if (k >= 100 && is_special) {
                    special++;
                    last_special = k;
                }
            }
            double secs = special / 240.0;
            double want = pass ? 0.6 : 0.21;
            check(fabs(secs - want) < 0.012 && last_special >= 100 + special - 1,
                  "%s: the special rule lasts %.3f s after the cut (set %.2f s)", pass ? "hold" : "blend", secs, want);
        }
    }

    // Rate limit: a jump of 1.0 in the target moves the state at most rate * dt per refresh.
    {
        struct fp_surround_time t = { .tau_steady_s = 0.0f, .max_rate = 2.0f, .cut_rule = FP_CUT_SNAP };
        struct fp_surround_clock c;
        fp_surround_clock_reset(&c);
        struct fp_surround_frame f = { .dt_s = 1.0 / 240, .reset = true };
        double s = follow(0.0, 0.0, fp_surround_clock_step(&c, &t, &f));
        f.reset = false;
        double prev = s, worst = 0.0;
        for (int k = 0; k < 240; k++) {
            s = follow(s, 1.0, fp_surround_clock_step(&c, &t, &f));
            if (s - prev > worst)
                worst = s - prev;
            prev = s;
        }
        check(fabs(worst - 2.0 / 240) < 1e-6 && fabs(s - 2.0) < 1.0001 && s <= 1.0 + 1e-9,
              "rate limit: largest step %.6f per refresh (2.0 per second at 240 Hz = %.6f); reaches %.3f after 1 s",
              worst, 2.0 / 240, s);
    }

    // Two monitors with different refresh rates, the same cut: their states agree.
    {
        double states[2];
        int hz[2] = { 240, 96 };
        for (int m = 0; m < 2; m++) {
            struct fp_surround_clock c;
            fp_surround_clock_reset(&c);
            struct fp_surround_time t = { .tau_steady_s = 0.4f, .cut_rule = FP_CUT_BLEND, .tau_blend_s = 0.1f,
                                          .blend_window_s = 0.3f };
            double dt = 1.0 / hz[m], s = 0.0;
            struct fp_surround_frame f = { .dt_s = dt, .reset = true };
            s = follow(s, 0.0, fp_surround_clock_step(&c, &t, &f));
            f.reset = false;
            int n = (int) (2.0 * hz[m]), cut_k = (int) (0.5 * hz[m]);
            for (int k = 1; k <= n; k++) {
                f.cut = k == cut_k;
                s = follow(s, k >= cut_k ? 1.0 : 0.0, fp_surround_clock_step(&c, &t, &f));
            }
            states[m] = s;
        }
        check(fabs(states[0] - states[1]) < 5e-3, "same cut on a 240 Hz and a 96 Hz screen: states %.4f and %.4f after 2 s",
              states[0], states[1]);
    }
}

static void test_color(void)
{
    printf("== finish: colour maths\n");
    struct fp_color_monitor os = { .transfer = FP_TRC_SRGB, .gamut_owner = FP_GAMUT_OS };
    struct fp_color_gpu g;
    fp_color_make(&os, &g);
    int ident = 1;
    for (int i = 0; i < 9; i++)
        ident &= fabsf(g.m[i] - (i % 4 == 0 ? 1.0f : 0.0f)) < 1e-6f;
    check(ident, "Windows owns the gamut: the 3x3 is the identity");
    check(g.gain[0] == 1.0f && g.gamma[1] == 1.0f && g.lift[2] == 0.0f && g.out_bits == 8, "zero data means no change, 8 bits");

    // Display P3 (D65): the textbook linear BT.709 -> P3 matrix.
    struct fp_color_monitor p3 = { .transfer = FP_TRC_POWER, .gamut_owner = FP_GAMUT_PLAYER,
        .panel = { .r = { 0.680, 0.320 }, .g = { 0.265, 0.690 }, .b = { 0.150, 0.060 }, .w = { 0.3127, 0.3290 } } };
    fp_color_make(&p3, &g);
    static const float want[9] = { 0.82246f, 0.17754f, 0.0f, 0.03319f, 0.96681f, 0.0f, 0.01708f, 0.07240f, 0.91052f };
    double worst = 0;
    for (int i = 0; i < 9; i++)
        worst = fmax(worst, fabs(g.m[i] - want[i]));
    check(worst < 6e-4, "player owns the gamut, panel = Display P3: matches the textbook 709->P3 matrix (worst %.1e)", worst);
    double rows = 0;
    for (int r = 0; r < 3; r++)
        rows = fmax(rows, fabs(g.m[r * 3] + g.m[r * 3 + 1] + g.m[r * 3 + 2] - 1.0));
    check(rows < 1e-5, "each row sums to 1: white stays white (worst %.1e)", rows);

    // This PC's laptop panel from its EDID (R .679/.316 G .258/.693 B .147/.052): white to white, red inside the panel.
    struct fp_color_monitor lap = { .transfer = FP_TRC_SRGB, .gamut_owner = FP_GAMUT_PLAYER,
        .panel = { .r = { 0.679, 0.316 }, .g = { 0.258, 0.693 }, .b = { 0.147, 0.052 }, .w = { 0.3127, 0.3290 } } };
    fp_color_make(&lap, &g);
    double enc[3];
    check(g.m[0] > 0.7 && g.m[0] < 1.0 && g.m[3] >= 0.0 && g.m[3] < 0.1 && g.m[6] >= 0.0 && g.m[6] < 0.1,
          "laptop EDID: sRGB red drives (%.3f, %.3f, %.3f) of the panel's linear range, all inside the gamut", g.m[0],
          g.m[3], g.m[6]);

    // The reference formula.
    struct fp_color_monitor plain = { .transfer = FP_TRC_SRGB, .gamut_owner = FP_GAMUT_OS };
    fp_color_make(&plain, &g);
    double mid[3] = { 0.18, 0.18, 0.18 };
    fp_color_reference(&g, mid, enc);
    check(fabs(enc[0] - 0.46132) < 1e-4, "sRGB encode of 0.18 linear is %.5f (0.46132)", enc[0]);
    double dark[3] = { 0.001, 0.001, 0.001 };
    fp_color_reference(&g, dark, enc);
    check(fabs(enc[0] - 12.92 * 0.001) < 1e-9, "sRGB linear segment: 0.001 -> %.6f", enc[0]);

    struct fp_color_monitor tw = { .transfer = FP_TRC_POWER, .power = 2.4, .gamut_owner = FP_GAMUT_OS,
                                   .gain = { 0.8f, 0.8f, 0.8f }, .gamma = { 1.1f, 1.1f, 1.1f }, .lift = { 0.01f, 0.01f, 0.01f } };
    fp_color_make(&tw, &g);
    double white[3] = { 1, 1, 1 }, black[3] = { 0, 0, 0 };
    fp_color_reference(&g, white, enc);
    check(fabs(enc[0] - pow(0.8, 1.0 / 2.4)) < 1e-6, "gain 0.8 on white: encoded %.5f", enc[0]);
    fp_color_reference(&g, black, enc);
    check(fabs(enc[0] - pow(0.01, 1.0 / 2.4)) < 1e-6, "lift 0.01 on black: encoded %.5f", enc[0]);
    double over[3] = { 2.0, 2.0, 2.0 }, neg[3] = { -0.2, 0.5, 0.5 };
    fp_color_reference(&g, over, enc);
    check(enc[0] <= 1.0 + 1e-12, "extended-range input 2.0 is clamped to the panel's white (%.5f)", enc[0]);
    struct fp_color_monitor id = { .transfer = FP_TRC_SRGB, .gamut_owner = FP_GAMUT_OS };
    fp_color_make(&id, &g);
    fp_color_reference(&g, neg, enc);
    check(enc[0] == 0.0, "a negative component (outside the gamut) is clipped to 0");
}

static void test_ass_time(void)
{
    printf("== subtitles: libass time rule\n");
    check(fp_subs_ass_time_ms(5.005) == 5005, "5.005 s is 5005 ms (stored as 5.00499999999999989...)");
    check(fp_subs_ass_time_ms(0.0) == 0 && fp_subs_ass_time_ms(1.0) == 1000, "whole seconds");
    check(fp_subs_ass_time_ms(4.99999) == 4999, "4.99999 s is 4999 ms: still before an event that starts at 5000");
    check(fp_subs_ass_time_ms(3600.0 * 2 + 0.001) == 7200001, "two hours and a millisecond");
    // 23.976 fps: frame k at k * 1001 / 24000 s. The exact millisecond is floor(k * 1001 / 24).
    int bad = 0;
    for (long k = 0; k < 172800; k++) {
        double pts = (double) k * 1001.0 / 24000.0;
        long long exact = (long long) ((k * 1001LL) / 24);
        if (fp_subs_ass_time_ms(pts) != exact)
            bad++;
    }
    check(bad == 0, "172,800 frames at 24000/1001 fps (2 h): %d differ from exact integer arithmetic", bad);
}

int main(void)
{
    test_clock();
    test_color();
    test_ass_time();
    printf("unit: %d checks, %d failed\n", g_run, g_fail);
    return g_fail ? 1 : 0;
}
