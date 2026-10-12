// seam-app.h: what the seam tuner's parts share: monitors, the scene the render threads draw, sessions.
//
// The main thread owns the settings and the session (free tuning, film steps, calibration). It publishes a
// Scene: an immutable description of what to draw. Each monitor's render thread reads the latest scene before
// every refresh, works out from the scene and the monitor's row clock what that refresh must show, draws it and
// presents. The scene refers to film frames by reference-monitor refresh numbers, so both monitors derive the same
// film clock from it with no hand-over between threads.
#pragma once
#include "per-monitor-common.h"
#include "seam-blend-model.h"
#include "seam-film-path.h"
#include "seam-row-clock.h"

#include <dxgi1_6.h>

#include <atomic>
#include <memory>
#include <string>
#include <vector>

namespace seam {

struct Monitor {
    int index = 0;                 // left to right
    std::wstring name;             // L"\\\\.\\DISPLAY1"
    std::wstring label;            // L"DISPLAY1"
    RECT rect{};                   // on the desktop, in pixels (the process is per-monitor DPI aware)
    IDXGIOutput* output = nullptr;
    HWND hwnd = nullptr;
    RowClock clock;
    double width_mm = 0, height_mm = 0, pitch_mm = 0.25;
    int view_x = 0, view_y = 0, view_w = 0, view_h = 0;  // the window's client area within the monitor, in pixels
    // Presents compared with DXGI frame statistics, and how many reached a later (late) or an earlier (early) refresh
    // than the plan assumed. Steadily late: raise --lead-ms; steadily early: lower it; a few late: a hiccup.
    std::atomic<int> checked{0}, late{0}, early{0};
};

struct Settings {
    double gap_mm = 20, height_mm = 0;  // height: the right screen's top edge below the left screen's (mm)
    std::vector<double> lag_ms;         // per monitor, relative to the first
    int selected = 1, speed_level = 2;
    bool smooth = true, rows = true, stripes = true, help = true;
};

struct TextBlock {
    std::wstring text;
    float x = 20, y = 20, w = 1380, h = 400;
    int font = 1;  // 0 small (26 px), 1 card (34 px), 2 big label (150 px)
};

enum class Kind { Black, Tuner, Film };
enum class Cond { Aimed, Blend };  // how the follower follows the reference

// Tuner pattern parts.
enum : unsigned { kStripes = 1, kLines = 2, kBars = 4 };

// A film-step clip: the bar's film frame n starts on reference refresh k0 + hold * n.
struct FilmClip {
    Cond cond = Cond::Aimed;
    bool fake_bezel = false;  // the first monitor alone, with a black strip inside it; the others show black
    int64_t k0 = 0;
    int hold = 4;
    int64_t frames = 0;       // film frames in the clip; the bar is absent outside 0 .. frames-1
    BarPath path;
};

struct Scene {
    Kind kind = Kind::Black;
    double gap_mm = 20, height_mm = 0;
    std::vector<double> lag_ms;
    // Tuner pattern: stripes, lines, bars moving in smooth motion, or in film steps (clip.k0 / clip.hold).
    unsigned flags = kStripes | kLines | kBars;
    double speed = 0, half_cycle = 2.5, motion_t0 = 0;
    bool rows = true;
    bool steps = false;
    // Film steps.
    FilmClip clip;
    // Black strip (fake bezel) across the first monitor, world mm; none when x1 <= x0.
    double strip_x0 = 0, strip_x1 = 0;
    // Overlay on the first monitor. With text_until_clip it goes away when the film clip starts.
    std::vector<TextBlock> text;
    bool text_until_clip = false;
};

// The fake bezel: a black strip inside the first monitor, to the right of where the text cards sit.
constexpr double kFakeBezelCentreMm = 250.0;
constexpr double kFakeBezelWidthMm = 20.0;

inline TextBlock card_text(const std::wstring& text, float height = 560) {
    TextBlock t;
    t.text = text;
    t.h = height;
    return t;
}

// A way of using the tuner. The main thread calls these; render threads never do.
class Session {
public:
    virtual ~Session() = default;
    virtual void start() = 0;                          // publish the first scene
    virtual void on_key(WPARAM key, bool shift) = 0;
    virtual void tick(double now) = 0;                 // from the message loop, about every 5 ms
    virtual bool finished() const { return false; }    // the session wants the program to end
    virtual void finish() {}                           // after the render threads stopped: save
};

// Defined in seam-tuner.cpp.
extern std::atomic<bool> g_stop;
extern std::atomic<bool> g_failed;        // a render thread could not start: exit code 1
extern std::vector<Monitor*> g_monitors;  // left to right
extern int g_master;                      // the reference monitor: the slowest
extern bool g_windowed;
extern Settings g_set;
extern double g_lead_s;                   // a present needs this long before the blank starts to make that refresh

// Defined in seam-render.cpp.
void scene_post(std::shared_ptr<const Scene> scene);
void render_thread(Monitor* m, IDXGIAdapter1* adapter);

// Defined in seam-self-check.cpp: needs the monitors' row clocks running; returns the process exit code.
int run_self_check();

inline Wave wave_of(const Monitor& m, double height_mm, double lag_ms) {
    Wave w = m.clock.wave();
    w.lag_s = lag_ms * 1e-3;
    w.pitch_mm = m.pitch_mm;
    w.rows = int(m.rect.bottom - m.rect.top);
    w.top_mm = (m.index == 1) ? height_mm : 0.0;
    return w;
}

// Physical layout: monitors left to right, `gap_mm` apart. World x of a monitor's left edge.
inline double world_left_mm(int index, double gap_mm) {
    double left = 0;
    for (int i = 0; i < index; ++i) left += g_monitors[size_t(i)]->width_mm + gap_mm;
    return left;
}

// The reference (slowest) monitor and monitor `fol`, with the given lags and height; k0 and hold are the caller's.
inline SeamClock make_seam(int fol, double height_mm, const std::vector<double>& lag_ms) {
    auto lag = [&](int i) { return size_t(i) < lag_ms.size() ? lag_ms[size_t(i)] : 0.0; };
    SeamClock s;
    s.ref = wave_of(*g_monitors[size_t(g_master)], height_mm, lag(g_master));
    s.fol = wave_of(*g_monitors[size_t(fol)], height_mm, lag(fol));
    s.aim_mm = shared_aim_mm(s.ref, s.fol);
    return s;
}

inline std::shared_ptr<Scene> new_scene(const Settings& s) {
    auto sc = std::make_shared<Scene>();
    sc->gap_mm = s.gap_mm;
    sc->height_mm = s.height_mm;
    sc->lag_ms = s.lag_ms;
    return sc;
}

// Pattern offset (mm) at time t and its rate (mm/s): slides at `speed`, reversing every half_cycle seconds.
inline void motion(double t, double t0, double half_cycle, double speed, double* pos, double* rate) {
    double phase = std::fmod(t - t0, 2 * half_cycle);
    if (phase < 0) phase += 2 * half_cycle;
    *pos = speed * (phase < half_cycle ? phase : 2 * half_cycle - phase);
    *rate = phase < half_cycle ? speed : -speed;
}

inline std::string utf8(const std::wstring& w) {
    int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, nullptr, 0, nullptr, nullptr);
    std::string s(n > 0 ? size_t(n - 1) : 0, '\0');
    if (n > 1) WideCharToMultiByte(CP_UTF8, 0, w.c_str(), -1, s.data(), n, nullptr, nullptr);
    return s;
}

}  // namespace seam
