// seam-self-check.cpp: --self-check, a numeric check of the row-timing model and the rolling blend (no windows).
//
// 1. The 240 + 96 Hz setup the film-step test runs on, built from the exact signal timing (laptop 2720 x 1800 at
//    1175.045 MHz; the external's saved 96 Hz mode, 2000 x 1160 at 222.72 MHz), with the two clocks at several
//    phases. Checks: the aimed switch leaves +-2.04 ms from the aim row to the top and bottom rows (research
//    section 3.2) and at most half a class spacing (1.04 ms) at the aim row; the blend's light-weighted switch moment
//    matches the reference row's within 1 microsecond.
// 2. The same blend check on the monitors' real row clocks as they are now (whatever modes they run), for the lags
//    and heights as configured and for two other lag/height pairs.
// The blend check adds up the light a follower row gives off over the refreshes around the switch, using the blend
// numbers the renderer sends (cast to float, clamped like the shader).
#define _CRT_SECURE_NO_WARNINGS
#include "seam-app.h"

#include <cstdio>

namespace seam {

namespace {

// A Wave from exact signal timing: period and line time follow from the pixel clock and the totals.
Wave synthetic_wave(double pixel_clock_hz, int total_w, int total_h, int active_w, int active_h, double width_mm, double phase_s) {
    Wave w;
    w.line_s = double(total_w) / pixel_clock_hz;
    w.period = double(total_w) * double(total_h) / pixel_clock_hz;
    w.scan_s = double(active_h) * w.line_s;
    w.blank_s = w.period - w.scan_s;
    w.ref_time = phase_s;
    w.ref_index = 0;
    w.pitch_mm = width_mm / double(active_w);
    w.rows = active_h;
    return w;
}

// Returns the largest blend error (us); sets *ok false if an aimed-switch figure is off.
double check_setup(const wchar_t* what, SeamClock s, bool print_rows, bool expect_research_figures, bool* ok) {
    s.hold = int(std::max<long long>(1, std::llround(1.0 / (kFilmFps * s.ref.period))));
    double resid = 0;
    s.k0 = aim_start(s, s.ref.index_after(s.ref.ref_time + 1.0), 24, &resid);
    AimedOffsets ao = aimed_offsets(s, 5);
    std::vector<BlendCheckRow> rows;
    double worst = check_blend(s, &rows);
    wprintf(L"   %s: aimed switch leaves (follower - reference, ms) top %+.3f, aim %+.3f, bottom %+.3f; blend error %.6f us\n", what, ao.top_s * 1e3,
            ao.aim_s * 1e3, ao.bottom_s * 1e3, worst);
    if (print_rows) {
        wprintf(L"   frame  row  height_mm  switch inside refresh (us)  new share  light-weighted error (us)\n");
        for (const auto& r : rows)
            wprintf(L"   %5lld %4d  %9.2f  %26.1f  %9.4f  %+.6f\n", (long long)r.film_frame, r.row, r.height_mm, r.straddle_us, r.share_new, r.err_us);
    }
    if (worst >= 1.0) *ok = false;
    if (expect_research_figures) {
        // Top and bottom against the aim row: the reference's rows are 27.6 mm/ms, the follower's 58.1 mm/ms.
        double up = (ao.top_s - ao.aim_s) * 1e3, down = (ao.bottom_s - ao.aim_s) * 1e3;
        if (std::fabs(up - 2.04) > 0.02 || std::fabs(down + 2.04) > 0.02 || std::fabs(ao.aim_s) > 0.5 * 2.0833e-3 + 1e-6) {
            wprintf(L"   FAIL: expected top %+.2f / bottom %+.2f ms from the aim row and |aim| <= 1.04 ms\n", 2.04, -2.04);
            *ok = false;
        }
    }
    return worst;
}

}  // namespace

int run_self_check() {
    bool ok = true;
    double worst_all = 0;

    wprintf(L"\n== 1. 240 + 96 Hz from exact signal timing (344 x 215 mm and 476 x 268 mm panels, tops level)\n");
    const double phases_ms[] = {0.0, 0.37, 1.1, 2.9, 5.3, 8.8};
    for (size_t i = 0; i < sizeof(phases_ms) / sizeof(phases_ms[0]); ++i) {
        SeamClock s;
        s.fol = synthetic_wave(1175.045e6, 2720, 1800, 2560, 1600, 344.0, 0.0);
        s.ref = synthetic_wave(222.72e6, 2000, 1160, 1920, 1080, 476.0, phases_ms[i] * 1e-3);
        s.aim_mm = shared_aim_mm(s.ref, s.fol);
        wchar_t what[64];
        swprintf(what, 64, L"external %.2f ms after the laptop's top row", phases_ms[i]);
        if (i == 0)
            wprintf(L"   laptop %.4f Hz (line %.4f us, scan %.4f ms); external %.4f Hz (line %.4f us, scan %.4f ms, blank %.4f ms); %d refreshes per film frame\n",
                    1.0 / s.fol.period, s.fol.line_s * 1e6, s.fol.scan_s * 1e3, 1.0 / s.ref.period, s.ref.line_s * 1e6, s.ref.scan_s * 1e3,
                    s.ref.blank_s * 1e3, int(std::llround(1.0 / (kFilmFps * s.ref.period))));
        worst_all = std::max(worst_all, check_setup(what, s, i == 0, true, &ok));
    }

    wprintf(L"\n== 2. the monitors' real row clocks\n");
    struct Variant {
        const wchar_t* what;
        double lag_ms, height_mm;
    };
    const Variant variants[] = {
        {L"lag 0 ms, tops level", 0.0, 0.0},
        {L"right screen lag +2.7 ms, 3 mm lower", 2.7, 3.0},
        {L"right screen lag -1.3 ms, 2 mm higher", -1.3, -2.0},
    };
    for (size_t vi = 0; vi < sizeof(variants) / sizeof(variants[0]); ++vi) {
        const Variant& v = variants[vi];
        std::vector<double> lag(g_monitors.size(), 0.0);
        lag[1] = v.lag_ms;
        SeamClock s = make_seam(0, v.height_mm, lag);
        if (vi == 0)
            wprintf(L"   reference %s at %.3f Hz, follower %s at %.3f Hz, aim row at %.1f mm\n", g_monitors[size_t(g_master)]->label.c_str(), 1.0 / s.ref.period,
                    g_monitors[0]->label.c_str(), 1.0 / s.fol.period, s.aim_mm);
        worst_all = std::max(worst_all, check_setup(v.what, s, vi == 0, false, &ok));
    }
    wprintf(L"\nself-check %s: largest blend error %.6f us (limit 1 us)\n", ok ? L"passed" : L"FAILED", worst_all);
    return ok ? 0 : 3;
}

}  // namespace seam
