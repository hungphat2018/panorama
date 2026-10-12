// scanline-probe: read-only check of the row-timing model (scanline-clock.h) on every monitor.
// No window, no display change.
//
// Per monitor: exact timing from QueryDisplayConfig; IDXGIOutput::WaitForVBlank wake-ups; scan-line samples
// over picture rows, first dense (continuous polling), then sparse (about one per millisecond, as a background
// sampler in the presenters takes them). Reports when the top row comes after each wake-up (expected: the
// blank minus the wake-up delay), the fit residuals, the fitted rate against the signal's exact rate, and how
// far the sparse fit lands from the dense one.
//
// --watch S samples every monitor together for S seconds (one sparse sampler per monitor) and reports, for each
// monitor against the first one, the ratio of their refresh rates as a fraction p/q and how the phase of its top
// rows within the q alignment classes moves over the run (drift in us per second). Two screens on one GPU keep a
// fixed ratio; how well they keep it decides how often the aimed start must be re-checked (ADR 0021).
// --refresh DISPLAYn=HZ (repeatable) switches displays for the run only (CDS_FULLSCREEN: Windows puts the saved
// mode back when the process ends, ADR 0023).
//
//   scanline-probe.exe [--seconds 1.0]
//   scanline-probe.exe --watch 600 [--refresh DISPLAY1=120 --refresh DISPLAY2=96]
#define NOMINMAX
#include "scanline-clock.h"

#include <dxgi1_2.h>
#include <timeapi.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "winmm.lib")

namespace {

struct Stats { double mean = 0, sd = 0, min = 0, p50 = 0, max = 0; size_t n = 0; };

Stats stats_of(std::vector<double> v) {
    Stats s;
    s.n = v.size();
    if (v.empty()) return s;
    std::sort(v.begin(), v.end());
    double sum = 0, sq = 0;
    for (double x : v) sum += x;
    s.mean = sum / double(v.size());
    for (double x : v) sq += (x - s.mean) * (x - s.mean);
    s.sd = v.size() > 1 ? std::sqrt(sq / double(v.size() - 1)) : 0.0;
    s.min = v.front();
    s.p50 = v[v.size() / 2];
    s.max = v.back();
    return s;
}

// Least-squares line through wake-up times, each indexed from its gap to the previous one (as VblankClock).
bool fit_wakes(const std::vector<double>& w, double nominal, double* base, double* period) {
    if (w.size() < 32) return false;
    std::vector<double> k(w.size(), 0.0);
    for (size_t i = 1; i < w.size(); ++i)
        k[i] = k[i - 1] + std::max(1.0, std::round((w[i] - w[i - 1]) / nominal));
    double sk = 0, st = 0, skk = 0, skt = 0, n = double(w.size());
    for (size_t i = 0; i < w.size(); ++i) {
        double ti = w[i] - w[0];
        sk += k[i]; st += ti; skk += k[i] * k[i]; skt += k[i] * ti;
    }
    double den = n * skk - sk * sk;
    if (den <= 0) return false;
    *period = (n * skt - sk * st) / den;
    *base = w[0] + (st - *period * sk) / n;
    return true;
}

void probe_output(IDXGIOutput* output, double seconds) {
    DXGI_OUTPUT_DESC desc{};
    output->GetDesc(&desc);
    char name[64];
    WideCharToMultiByte(CP_UTF8, 0, desc.DeviceName, -1, name, sizeof(name), nullptr, nullptr);

    scan_timing tm{};
    if (!scan_timing_query(desc.DeviceName, &tm)) {
        std::printf("%s: QueryDisplayConfig found no active path\n", name);
        return;
    }
    std::printf("\n%s  %ux%u active, %ux%u total, %.3f MHz pixel clock\n", name, tm.active_w, tm.active_h,
                tm.total_w, tm.total_h, tm.pixel_rate_hz / 1e6);
    std::printf("  refresh %.6f Hz (period %.4f ms); line %.4f us; scan %.4f ms; blank %.4f ms (%u lines)%s\n",
                tm.refresh_hz, 1e3 / tm.refresh_hz, tm.line_s * 1e6, tm.scan_s * 1e3, tm.blank_s * 1e3,
                tm.total_h - tm.active_h, tm.interlaced ? "; INTERLACED" : "");
    std::printf("  check: total lines x line time = %.4f ms\n", double(tm.total_h) * tm.line_s * 1e3);

    scan_source src{};
    if (!scan_source_open(desc.DeviceName, &src)) {
        std::printf("  D3DKMTOpenAdapterFromGdiDisplayName failed\n");
        return;
    }

    std::vector<double> wakes;
    std::atomic<bool> stop{false};
    std::thread waiter([&] {
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
        while (!stop.load()) {
            if (FAILED(output->WaitForVBlank())) { Sleep(1); continue; }
            wakes.push_back(scan_now_s());
        }
    });

    // Dense: continuous polling.
    scan_grid dense{};
    size_t polls = 0;
    double t_end = scan_now_s() + seconds;
    while (scan_now_s() < t_end) {
        double t;
        polls++;
        if (scan_sample_top(&src, &tm, &t)) scan_grid_add(&dense, t);
    }
    double dense_mid = scan_now_s() - 0.5 * seconds;

    // Sparse: one sample about every millisecond, for twice as long.
    scan_grid sparse{};
    t_end = scan_now_s() + 2.0 * seconds;
    while (scan_now_s() < t_end) {
        double t;
        if (scan_sample_top(&src, &tm, &t)) scan_grid_add(&sparse, t);
        Sleep(1);
    }
    stop = true;
    waiter.join();
    scan_source_close(&src);

    double nominal = 1.0 / tm.refresh_hz;
    bool ok_d = scan_grid_fit(&dense, nominal, 0.0);
    bool ok_s = scan_grid_fit(&sparse, nominal, 0.0);
    std::printf("  dense fit: %s, %zu of %zu picture-row samples (%zu polls), residual p50 %.2f us, p95 %.2f us\n",
                ok_d ? "ok" : "FAILED", dense.used, dense.n, polls, dense.resid_p50_us, dense.resid_p95_us);
    std::printf("  sparse fit: %s, %zu of %zu samples, residual p50 %.2f us, p95 %.2f us\n", ok_s ? "ok" : "FAILED",
                sparse.used, sparse.n, sparse.resid_p50_us, sparse.resid_p95_us);
    if (!ok_d) { scan_grid_free(&dense); scan_grid_free(&sparse); return; }
    std::printf("  fitted period %.6f ms = signal rate %+.2f ppm\n", dense.period * 1e3,
                (dense.period / nominal - 1.0) * 1e6);
    if (ok_s) {
        double d = scan_grid_top_nearest(&sparse, dense_mid) - scan_grid_top_nearest(&dense, dense_mid);
        std::printf("  sparse vs dense top row at the dense window's middle: %+.2f us; period %+.2f ppm\n", d * 1e6,
                    (sparse.period / dense.period - 1.0) * 1e6);
    }

    double wbase = 0, wperiod = 0;
    if (fit_wakes(wakes, nominal, &wbase, &wperiod)) {
        std::vector<double> lag_after_wake, wake_resid;
        for (double w : wakes) {
            // Top row of the refresh that starts after this wake-up: the wake comes just after the bottom row.
            lag_after_wake.push_back((scan_grid_top_after(&dense, w) - w) * 1e3);
            double k = std::round((w - wbase) / wperiod);
            wake_resid.push_back((w - (wbase + k * wperiod)) * 1e6);
        }
        Stats a = stats_of(lag_after_wake), r = stats_of(wake_resid);
        std::printf("  %zu wake-ups: top row comes %.4f ms after the wake-up (sd %.4f, min %.4f, max %.4f)\n", a.n,
                    a.mean, a.sd, a.min, a.max);
        std::printf("  so the wake-up comes %.4f ms after the blank starts (blank %.4f ms minus the above)\n",
                    tm.blank_s * 1e3 - a.p50, tm.blank_s * 1e3);
        std::printf("  wake-up jitter around its own fit: sd %.2f us, min %.2f, max %.2f\n", r.sd, r.min, r.max);
        double fitted_offset = scan_grid_top_after(&dense, wbase + 100 * wperiod) - (wbase + 100 * wperiod);
        std::printf("  fitted wake grid -> top-row grid offset: %.4f ms (add this to WaitForVBlank-fitted times)\n",
                    fitted_offset * 1e3);
    } else {
        std::printf("  too few wake-ups (%zu)\n", wakes.size());
    }
    scan_grid_free(&dense);
    scan_grid_free(&sparse);
}

// ---- --watch: drift between the monitors' refresh grids --------------------------------------------------

struct Watched {
    std::string name;
    scan_timing tm{};
    scan_source src{};
    std::vector<double> tops;  // top-row estimates from the sparse sampler
    // How long each D3DKMTGetScanLine call took, split by where the scan was: a call that stalls inside the
    // blank could hold up other work in the display driver (suspected for late presents at 72 Hz).
    std::vector<double> call_us_blank, call_us_rows;
};

// One raw scan-line read, timed: true when the call returned; *in_blank and *us say where the scan was and how
// long the call took.
bool timed_scan_read(const Watched& w, bool* in_blank, double* us) {
    static scan_kmt_fn get = scan_kmt("D3DKMTGetScanLine");
    if (!get) return false;
    scan_kmt_line_t q{};
    q.hAdapter = w.src.adapter;
    q.VidPnSourceId = w.src.source;
    double a = scan_now_s();
    LONG st = get(&q);
    double b = scan_now_s();
    *in_blank = q.InVerticalBlank != 0;
    *us = (b - a) * 1e6;
    return st == 0;
}

void print_call_times(const char* label, std::vector<double> v) {
    if (v.empty()) { std::printf("    %s: no calls\n", label); return; }
    std::sort(v.begin(), v.end());
    size_t over50 = size_t(std::count_if(v.begin(), v.end(), [](double x) { return x > 50.0; }));
    size_t over1ms = size_t(std::count_if(v.begin(), v.end(), [](double x) { return x > 1000.0; }));
    std::printf("    %s: %zu calls, p50 %.1f us, p99 %.1f us, max %.1f us; over 50 us: %zu, over 1 ms: %zu\n", label,
                v.size(), v[v.size() / 2], v[size_t(0.99 * double(v.size() - 1))], v.back(), over50, over1ms);
}

const double kPi = 3.14159265358979323846;

bool fit_range(const std::vector<double>& tops, double t0, double t1, double nominal, scan_grid* g) {
    for (double t : tops)
        if (t >= t0 && t < t1) scan_grid_add(g, t);
    return scan_grid_fit(g, nominal, 0.0);
}

// Smallest q <= 12 with p/q within 50 ppm of the ratio.
bool simple_ratio(double ratio, int* p, int* q) {
    for (int d = 1; d <= 12; ++d) {
        double n = std::round(ratio * d);
        if (n >= 1 && std::fabs(n / d / ratio - 1.0) < 50e-6) { *p = int(n); *q = d; return true; }
    }
    return false;
}

// Where the follower's top rows sit against the reference grid's nearest top row, modulo the class spacing s
// (circular mean over the follower's refreshes in [t0, t1)).
double class_phase(const scan_grid& ref, const scan_grid& fol, double t0, double t1, double s) {
    double c = 0, sn = 0;
    for (double k = std::ceil((t0 - fol.base) / fol.period); fol.base + k * fol.period < t1; k += 1) {
        double tj = fol.base + k * fol.period;
        double a = 2 * kPi * (tj - scan_grid_top_nearest(&ref, tj)) / s;
        c += std::cos(a);
        sn += std::sin(a);
    }
    return std::atan2(sn, c) * s / (2 * kPi);
}

// As in per-monitor-presenter.cpp: held by this process (CDS_FULLSCREEN), put back by Windows when it ends.
bool switch_refresh(const std::wstring& device, int hz) {
    DEVMODEW dm{};
    dm.dmSize = sizeof(dm);
    if (!EnumDisplaySettingsW(device.c_str(), ENUM_CURRENT_SETTINGS, &dm)) return false;
    dm.dmDisplayFrequency = DWORD(hz);
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY;
    if (ChangeDisplaySettingsExW(device.c_str(), &dm, nullptr, CDS_TEST, nullptr) != DISP_CHANGE_SUCCESSFUL) return false;
    return ChangeDisplaySettingsExW(device.c_str(), &dm, nullptr, CDS_FULLSCREEN, nullptr) == DISP_CHANGE_SUCCESSFUL;
}

int watch(IDXGIFactory1* factory, double seconds, const std::vector<std::pair<std::wstring, int>>& refresh) {
    for (const auto& r : refresh) {
        if (!switch_refresh(r.first, r.second)) {
            std::printf("the driver refused %d Hz on %ls\n", r.second, r.first.c_str());
            return 1;
        }
        std::printf("%ls at %d Hz for this run\n", r.first.c_str(), r.second);
    }
    if (!refresh.empty()) Sleep(3000);  // let the monitors sync to the new timing

    std::vector<Watched> mons;
    IDXGIAdapter1* adapter = nullptr;
    for (UINT a = 0; factory->EnumAdapters1(a, &adapter) != DXGI_ERROR_NOT_FOUND; ++a) {
        IDXGIOutput* output = nullptr;
        for (UINT o = 0; adapter->EnumOutputs(o, &output) != DXGI_ERROR_NOT_FOUND; ++o) {
            DXGI_OUTPUT_DESC desc{};
            output->GetDesc(&desc);
            Watched w;
            char name[64];
            WideCharToMultiByte(CP_UTF8, 0, desc.DeviceName, -1, name, sizeof(name), nullptr, nullptr);
            w.name = name;
            if (scan_timing_query(desc.DeviceName, &w.tm) && scan_source_open(desc.DeviceName, &w.src)) {
                std::printf("%s: %.6f Hz, %ux%u total, scan %.4f ms, blank %.4f ms\n", name, w.tm.refresh_hz,
                            w.tm.total_w, w.tm.total_h, w.tm.scan_s * 1e3, w.tm.blank_s * 1e3);
                mons.push_back(w);
            }
            output->Release();
        }
        adapter->Release();
    }
    if (mons.size() < 2) { std::printf("need two monitors\n"); return 1; }

    std::atomic<bool> stop{false};
    std::vector<std::thread> samplers;
    for (auto& w : mons)
        samplers.emplace_back([&stop, &w] {
            SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL);
            while (!stop.load()) {
                double t, us;
                bool in_blank;
                if (scan_sample_top(&w.src, &w.tm, &t)) w.tops.push_back(t);
                if (timed_scan_read(w, &in_blank, &us)) (in_blank ? w.call_us_blank : w.call_us_rows).push_back(us);
                Sleep(1);
            }
        });
    std::printf("sampling every monitor for %.0f s ...\n", seconds);
    std::fflush(stdout);
    for (double end = scan_now_s() + seconds; scan_now_s() < end;) Sleep(200);
    stop = true;
    for (auto& t : samplers) t.join();

    // Grids over the whole run, the first and the last window.
    const double win = std::min(10.0, seconds / 3.0);
    double t_first = 1e300, t_last = -1e300;
    for (const auto& w : mons)
        if (w.tops.size() > 100) { t_first = std::min(t_first, w.tops.front()); t_last = std::max(t_last, w.tops.back()); }
    std::vector<scan_grid> all(mons.size()), head(mons.size()), tail(mons.size());
    bool ok = true;
    for (size_t i = 0; i < mons.size(); ++i) {
        double nominal = 1.0 / mons[i].tm.refresh_hz;
        ok &= fit_range(mons[i].tops, t_first, t_last + 1, nominal, &all[i]);
        ok &= fit_range(mons[i].tops, t_first, t_first + win, nominal, &head[i]);
        ok &= fit_range(mons[i].tops, t_last - win, t_last + 1, nominal, &tail[i]);
        std::printf("%s: %zu samples, period %.6f ms (%+.2f ppm against its signal rate), residual p50 %.2f us\n",
                    mons[i].name.c_str(), all[i].used, all[i].period * 1e3,
                    (all[i].period * mons[i].tm.refresh_hz - 1.0) * 1e6, all[i].resid_p50_us);
        print_call_times("scan-line calls inside the blank", mons[i].call_us_blank);
        print_call_times("scan-line calls over picture rows", mons[i].call_us_rows);
    }
    if (!ok) { std::printf("a grid fit failed\n"); return 1; }
    for (size_t j = 1; j < mons.size(); ++j) {
        int p = 0, q = 0;
        double ratio = all[j].period / all[0].period;
        if (!simple_ratio(ratio, &p, &q)) {
            std::printf("%s vs %s: period ratio %.8f is not a simple fraction (q <= 12)\n", mons[j].name.c_str(),
                        mons[0].name.c_str(), ratio);
            continue;
        }
        double s = all[0].period / q;
        double ph0 = class_phase(head[0], head[j], t_first, t_first + win, s);
        double ph1 = class_phase(tail[0], tail[j], t_last - win, t_last + 1, s);
        double d = ph1 - ph0;
        d -= s * std::round(d / s);  // unwrap: the drift over the run stays under half a class
        double span = (t_last - t_first) - win;
        std::printf("%s vs %s: ratio %d/%d (%+.3f ppm), %d alignment classes %.4f ms apart\n", mons[j].name.c_str(),
                    mons[0].name.c_str(), p, q, (ratio * q / p - 1.0) * 1e6, q, s * 1e3);
        std::printf("  class phase %.4f ms at the start, %.4f ms at the end: drift %+.3f us/s (%+.1f us per hour)\n",
                    ph0 * 1e3, ph1 * 1e3, d / span * 1e6, d / span * 3600 * 1e6);
    }
    for (auto& w : mons) scan_source_close(&w.src);
    for (auto* g : {&all, &head, &tail})
        for (auto& x : *g) scan_grid_free(&x);
    for (const auto& r : refresh) ChangeDisplaySettingsExW(r.first.c_str(), nullptr, nullptr, 0, nullptr);
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    double seconds = 1.0, watch_s = 0.0;
    std::vector<std::pair<std::wstring, int>> refresh;
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--seconds") && i + 1 < argc) seconds = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "--watch") && i + 1 < argc) watch_s = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "--refresh") && i + 1 < argc) {
            std::string a = argv[++i];
            size_t eq = a.find('=');
            if (eq == std::string::npos) { std::printf("--refresh DISPLAYn=HZ\n"); return 2; }
            std::string dev = "\\\\.\\" + a.substr(0, eq);
            refresh.emplace_back(std::wstring(dev.begin(), dev.end()), std::atoi(a.c_str() + eq + 1));
        } else {
            std::printf("usage: scanline-probe [--seconds S] | --watch S [--refresh DISPLAYn=HZ ...]\n");
            return 2;
        }
    }
    if (!refresh.empty() && watch_s <= 0) { std::printf("--refresh goes with --watch\n"); return 2; }
    timeBeginPeriod(1);
    IDXGIFactory1* factory = nullptr;
    if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void**)&factory))) {
        std::printf("CreateDXGIFactory1 failed\n");
        return 1;
    }
    int rc = 0;
    if (watch_s > 0) {
        rc = watch(factory, watch_s, refresh);
    } else {
        IDXGIAdapter1* adapter = nullptr;
        for (UINT a = 0; factory->EnumAdapters1(a, &adapter) != DXGI_ERROR_NOT_FOUND; ++a) {
            IDXGIOutput* output = nullptr;
            for (UINT o = 0; adapter->EnumOutputs(o, &output) != DXGI_ERROR_NOT_FOUND; ++o) {
                probe_output(output, seconds);
                output->Release();
            }
            adapter->Release();
        }
    }
    factory->Release();
    timeEndPeriod(1);
    return rc;
}
