// seam-free-session.cpp: the key-driven tuner (see seam-free-session.h).
#define _CRT_SECURE_NO_WARNINGS
#include "seam-free-session.h"

#include "seam-settings.h"

#include <cstdio>

namespace seam {

namespace {

const double kSpeeds[] = {0, 150, 300, 600};

const wchar_t* kHelp =
    L"How to tune (H hides this):\n"
    L"1. Press 1 (still). Thin horizontal lines: PgUp / PgDn until each line runs straight across the bezel, no step.\n"
    L"2. Still. Diagonal stripes: Left / Right (gap) until each stripe runs on as one straight line, as if behind the bezel.\n"
    L"    Pass: straight now, and one press either way makes a visible bend. Shift = big steps.\n"
    L"3. Press 3 or 4 (moving). Watch the stripes at the bezel, moving right, then left. Up / Down (lag of the right screen):\n"
    L"    breaks one way moving right and the other way moving left = lag, keep adjusting;\n"
    L"    breaks the same way in both directions = gap, go back to step 2. Pass: straight both ways.\n"
    L"4. Watch a white bar cross. Still a delay? Try S (film steps) and R (row timing) to compare.\n"
    L"5. Enter saves. Esc quits.\n"
    L"A forced choice instead of adjusting by eye: restart with --mode calibrate (gap, height, lag) or --mode x2 (film steps).";

class FreeSession : public Session {
public:
    explicit FreeSession(const std::wstring& out_dir) : out_(out_dir) {}

    void start() override {
        motion_t0_ = now_s();
        publish();
    }

    void on_key(WPARAM key, bool big) override {
        if (key == VK_RETURN) {
            save_settings(out_ + L"\\seam-settings-" + local_stamp() + L".json", g_set, nullptr);
            return;
        }
        const double mm = big ? 5.0 : 0.5, ms = big ? 5.0 : 0.5;
        switch (key) {
            case VK_LEFT: g_set.gap_mm = std::max(0.0, g_set.gap_mm - mm); break;
            case VK_RIGHT: g_set.gap_mm += mm; break;
            case VK_PRIOR: g_set.height_mm -= mm; break;
            case VK_NEXT: g_set.height_mm += mm; break;
            case VK_UP: g_set.lag_ms[size_t(g_set.selected)] += ms; break;
            case VK_DOWN: g_set.lag_ms[size_t(g_set.selected)] -= ms; break;
            case VK_TAB: g_set.selected = (g_set.selected + 1) % int(g_set.lag_ms.size()); break;
            case '1': case '2': case '3': case '4': g_set.speed_level = int(key - '1'); break;
            case 'S':
                g_set.smooth = !g_set.smooth;
                if (!g_set.smooth) start_steps();
                break;
            case 'R': g_set.rows = !g_set.rows; break;
            case 'P': g_set.stripes = !g_set.stripes; break;
            case 'H': g_set.help = !g_set.help; break;
            default: return;
        }
        publish();
    }

    // Keeps the HUD's timing counters fresh.
    void tick(double now) override {
        if (now - last_publish_ >= 0.5) publish();
    }

    void finish() override { save_settings(out_ + L"\\seam-settings-last.json", g_set, nullptr); }

private:
    // Film steps run on the reference monitor's refresh count, started on the best-aligned refresh (aimed start).
    void start_steps() {
        SeamClock s = make_seam(0, g_set.height_mm, g_set.lag_ms);
        s.hold = int(std::max<long long>(1, std::llround(1.0 / (kFilmFps * s.ref.period))));
        k0_ = aim_start(s, s.ref.index_after(now_s() + 0.3), 24, nullptr);
        hold_ = s.hold;
    }

    std::wstring hud() const {
        std::wstring lags;
        for (size_t i = 0; i < g_monitors.size(); ++i) {
            wchar_t one[96];
            swprintf(one, 96, L"%s%s %+.1f ms%s", i ? L",  " : L"", i == 0 ? L"left" : (i == 1 ? L"right" : L"third"), g_set.lag_ms[i],
                     int(i) == g_set.selected ? L" [selected]" : L"");
            lags += one;
        }
        wchar_t timing[200] = L"";
        if (!g_windowed) {
            int n = 0;
            for (auto* m : g_monitors)
                n += swprintf(timing + n, 200 - n, L"%s%s %d late, %d early of %d", n ? L",  " : L"", m->label.c_str(), m->late.load(), m->early.load(),
                              m->checked.load());
        }
        wchar_t buf[900];
        swprintf(buf, 900,
                 L"gap %.1f mm (← →)    height %+.1f mm (PgUp PgDn)    lag: %s (↑ ↓, Tab)\n"
                 L"speed %.0f mm/s (1-4)    %s (S)    rows %s (R)    stripes %s (P)    Enter save    Esc quit\n"
                 L"presents reaching the predicted refresh: %s",
                 g_set.gap_mm, g_set.height_mm, lags.c_str(), kSpeeds[g_set.speed_level], g_set.smooth ? L"smooth" : L"film steps",
                 g_set.rows ? L"on" : L"off", g_set.stripes ? L"on" : L"off", g_windowed ? L"not checked in a window" : timing);
        return buf;
    }

    void publish() {
        last_publish_ = now_s();
        auto sc = new_scene(g_set);
        sc->kind = Kind::Tuner;
        sc->flags = kBars | (g_set.stripes ? (kStripes | kLines) : 0u);
        sc->speed = kSpeeds[g_set.speed_level];
        sc->half_cycle = 2.5;
        sc->motion_t0 = motion_t0_;
        sc->rows = g_set.rows;
        sc->steps = !g_set.smooth;
        sc->clip.k0 = k0_;
        sc->clip.hold = hold_;
        TextBlock bar;
        bar.text = hud();
        bar.x = 20; bar.y = 20; bar.w = 1700; bar.h = 130; bar.font = 0;
        sc->text.push_back(bar);
        if (g_set.help) {  // left half of the laptop screen, away from the bezel
            TextBlock help;
            help.text = kHelp;
            help.x = 20; help.y = 170; help.w = 1200; help.h = 470; help.font = 0;
            sc->text.push_back(help);
        }
        scene_post(sc);
    }

    std::wstring out_;
    double motion_t0_ = 0, last_publish_ = 0;
    int64_t k0_ = 0;
    int hold_ = 4;
};

}  // namespace

std::unique_ptr<Session> make_free_session(const std::wstring& out_dir) {
    return std::make_unique<FreeSession>(out_dir);
}

}  // namespace seam
