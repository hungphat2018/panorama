// Command line of perf1 (the P3 build).
#include "perf1-app.h"
#include "perf1-ai-sides.h"
#include "perf1-gpu-share.h"

#include <math.h>

void args_usage(const char *argv0)
{
    fprintf(stderr,
        "Usage: %s --file PATH [--windowed [--window-scale 0.5]] [--seconds N] [--mode nearest|own]\n"
        "          [--decode d3d11va|sw] [--preset fast|default|hq] [--master OUTPUT_INDEX] [--no-lock]\n"
        "          [--refresh DISPLAYn=HZ] [--gap-mm 20] [--picture-height-mm N] [--picture-center-mm N]\n"
        "          [--lag-ms DISPLAYn=X] [--height-offset-mm X] [--aim-mm X]\n"
        "          [--cues FILE] [--control-script FILE] [--pipe NAME]\n"
        "          [--no-hysteresis] [--no-reaim] [--eta-ms 0.05] [--restart-margin-ms 3]\n"
        "          [--headless 96|72|100 [--headless-drift-ppm 0.5]]\n"
        "          [--audio-track N] [--no-audio] [--audio-volume 1.0] [--audio-offset-ms 0] [--audio-null [--audio-null-drift-ppm 0] [--audio-null-latency-ms 0]]\n"
        "          [--format rgb10a2|bgra8] [--sync-render] [--adapter SUBSTR] [--out DIR] [--log-level 0..6] [--probe]\n"
        "  --windowed       smoke test: half-size borderless windows, not topmost, no focus steal, no display change\n"
        "  --seconds N      playback time after the film clock starts (default 20); Esc stops every window early\n"
        "  --refresh        switch that display for the run with CDS_FULLSCREEN (restored when the process ends)\n"
        "  --lag-ms         that panel's lag: its light comes X ms after the signal (repeatable, default 0)\n"
        "  --height-offset-mm  how far the follower's top is below the master's top (default 0, tops level)\n"
        "  --aim-mm         height, from the master's top, where the screens agree (default: middle of the shared height)\n"
        "  --no-lock        play at 1.0 speed even when the master's refresh rate would allow a 1 %% lock\n"
        "  --cues FILE      cue sheet (panorama.cues/1) applied from the start; see perf1-cue-sheet.h\n"
        "  --control-script FILE   JSON lines: {\"at\":12.0,\"cmd\":\"seek\",\"t\":55} ... see perf1-control.h\n"
        "  --pipe NAME      named-pipe control at \\\\.\\pipe\\NAME: load, seek, pause, resume, status, stop\n"
        "  --no-hysteresis  follower picks its nearest refresh every frame (the first P1 rule)\n"
        "  --no-reaim       the master's frame changes never move at cuts\n"
        "  --headless EXT_HZ  no windows, no graphics hardware: virtual laptop (240 Hz) + external (EXT_HZ) displays,\n"
        "                   libplacebo's dummy GPU, software decoding; the decisions and the logs are the real ones\n"
        "  --audio-track N  play the Nth audio stream of the file (0 based; default: FFmpeg's best); --no-audio: no sound\n"
        "  --audio-volume   this stream's volume, 0..1 (default 1); not persisted in the system mixer\n"
        "  --audio-offset-ms  delay the sound against the picture (+) or advance it (-): the device's own delay, not measured here\n"
        "  --audio-null     no sound device: a stand-in sink on the host clock (tests; --audio-null-drift-ppm skews its clock, --audio-null-latency-ms gives it a driver delay)\n"
        "  --probe          print the outputs of the adapter that drives the screens, then exit\n",
        argv0);
    picture_usage();
    gpu_share_usage();
    ai_sides_usage();
}

// DISPLAYn=VALUE into a wide device name ("\\.\DISPLAYn") and the text after '='.
static bool split_device(const char *v, wchar_t *dev, size_t dev_chars, const char **value)
{
    const char *eq = strchr(v, '=');
    if (!eq || eq == v)
        return false;
    char name[32];
    snprintf(name, sizeof(name), "%s%.*s", strncmp(v, "\\\\.\\", 4) ? "\\\\.\\" : "", (int) (eq - v), v);
    if (!MultiByteToWideChar(CP_UTF8, 0, name, -1, dev, (int) dev_chars))
        return false;
    *value = eq + 1;
    return true;
}

bool args_parse(int argc, char **argv, struct cfg *cfg)
{
    *cfg = (struct cfg) {
        .decode = DECODE_D3D11VA,
        .preset = "default",
        .seconds = 20.0,
        .window_scale = 0.5,
        .sched = SCHED_NEAREST,
        .master = -1,
        .lock_speed = true,
        .rgb10 = true,
        .gap_mm = 20.0,
        .picture_center_mm = NAN,
        .aim_mm = NAN,
        .out_dir = ".",
        .log_level = PL_LOG_WARN,
        .eta_ms = 0.05,
        .restart_margin_ms = 3.0,
        .headless_drift_ppm = 0.5,
        .audio_track = -1,
        .audio_volume = 1.0,
    };
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!strcmp(a, "--file") && v) { cfg->file = v; i++; }
        else if (!strcmp(a, "--windowed")) cfg->windowed = true;
        else if (!strcmp(a, "--window-scale") && v) { cfg->window_scale = atof(v); i++; }
        else if (!strcmp(a, "--seconds") && v) { cfg->seconds = atof(v); i++; }
        else if (!strcmp(a, "--mode") && v) {
            if (!strcmp(v, "nearest")) cfg->sched = SCHED_NEAREST;
            else if (!strcmp(v, "own")) cfg->sched = SCHED_OWN;
            else return false;
            i++;
        } else if (!strcmp(a, "--decode") && v) {
            if (!strcmp(v, "d3d11va")) cfg->decode = DECODE_D3D11VA;
            else if (!strcmp(v, "sw")) cfg->decode = DECODE_SW;
            else return false;
            i++;
        }
        else if (!strcmp(a, "--preset") && v) { cfg->preset = v; i++; }
        else if (!strcmp(a, "--master") && v) { cfg->master = atoi(v); i++; }
        else if (!strcmp(a, "--no-lock")) cfg->lock_speed = false;
        else if (!strcmp(a, "--sync-render")) cfg->sync_render = true;
        else if (!strcmp(a, "--format") && v) {
            if (!strcmp(v, "rgb10a2")) cfg->rgb10 = true;
            else if (!strcmp(v, "bgra8")) cfg->rgb10 = false;
            else return false;
            i++;
        }
        else if (!strcmp(a, "--gap-mm") && v) { cfg->gap_mm = atof(v); i++; }
        else if (!strcmp(a, "--picture-height-mm") && v) { cfg->picture_height_mm = atof(v); i++; }
        else if (!strcmp(a, "--picture-center-mm") && v) { cfg->picture_center_mm = atof(v); i++; }
        else if (!strcmp(a, "--height-offset-mm") && v) { cfg->height_offset_mm = atof(v); i++; }
        else if (!strcmp(a, "--aim-mm") && v) { cfg->aim_mm = atof(v); i++; }
        else if (!strcmp(a, "--lag-ms") && v) {
            // DISPLAYn=MS, repeatable
            const char *val;
            char *end = NULL;
            if (cfg->n_lags >= MAX_MONITORS || !split_device(v, cfg->lags[cfg->n_lags].device, 31, &val))
                return false;
            double ms = strtod(val, &end);
            if (end == val || *end != '\0')
                return false;
            cfg->lags[cfg->n_lags].ms = ms;
            cfg->n_lags++;
            i++;
        }
        else if (!strcmp(a, "--adapter") && v) { cfg->adapter = v; i++; }
        else if (!strcmp(a, "--out") && v) { cfg->out_dir = v; i++; }
        else if (!strcmp(a, "--log-level") && v) { cfg->log_level = atoi(v); i++; }
        else if (!strcmp(a, "--probe")) cfg->probe_only = true;
        else if (!strcmp(a, "--refresh") && v) {
            const char *val;
            if (!split_device(v, cfg->refresh_device, 31, &val))
                return false;
            cfg->refresh_hz = atoi(val);
            if (cfg->refresh_hz <= 0)
                return false;
            i++;
        }
        else if (!strcmp(a, "--cues") && v) { cfg->cues_file = v; i++; }
        else if (!strcmp(a, "--control-script") && v) { cfg->control_script = v; i++; }
        else if (!strcmp(a, "--pipe") && v) { cfg->pipe_name = v; i++; }
        else if (!strcmp(a, "--no-hysteresis")) cfg->no_hysteresis = true;
        else if (!strcmp(a, "--no-reaim")) cfg->no_reaim = true;
        else if (!strcmp(a, "--eta-ms") && v) { cfg->eta_ms = atof(v); i++; }
        else if (!strcmp(a, "--restart-margin-ms") && v) { cfg->restart_margin_ms = atof(v); i++; }
        else if (!strcmp(a, "--headless") && v) { cfg->headless_hz = atoi(v); i++; }
        else if (!strcmp(a, "--headless-drift-ppm") && v) { cfg->headless_drift_ppm = atof(v); i++; }
        else if (!strcmp(a, "--audio-track") && v) { cfg->audio_track = atoi(v); i++; }
        else if (!strcmp(a, "--no-audio")) cfg->no_audio = true;
        else if (!strcmp(a, "--audio-null")) cfg->audio_null = true;
        else if (!strcmp(a, "--audio-volume") && v) { cfg->audio_volume = atof(v); i++; }
        else if (!strcmp(a, "--audio-offset-ms") && v) { cfg->audio_offset_ms = atof(v); i++; }
        else if (!strcmp(a, "--audio-null-drift-ppm") && v) { cfg->audio_null_drift_ppm = atof(v); i++; }
        else if (!strcmp(a, "--audio-null-latency-ms") && v) { cfg->audio_null_latency_ms = atof(v); i++; }
        else {
            // --frame-latency, --gpu-priority, --vram-reserve-mb (perf1-gpu-share.c), --ai-* (perf1-ai-sides.c), then the
            // picture path's options (perf1-picture-args.c)
            int r = gpu_share_arg(argc, argv, &i);
            if (r == 0)
                r = ai_sides_arg(argc, argv, &i);
            if (r < 0 || (r == 0 && picture_arg(argc, argv, &i) <= 0))
                return false;
        }
    }
    if (!cfg->probe_only && !cfg->file)
        return false;
    if (cfg->window_scale <= 0.05 || cfg->window_scale > 1.0 || cfg->seconds <= 0.0 || cfg->eta_ms < 0.0 ||
        cfg->restart_margin_ms < 0.0)
        return false;
    if (cfg->audio_track < -1 || cfg->audio_volume < 0.0 || cfg->audio_volume > 1.0 || fabs(cfg->audio_offset_ms) > 1000.0 ||
        fabs(cfg->audio_null_drift_ppm) > 5000.0 || cfg->audio_null_latency_ms < 0.0 || cfg->audio_null_latency_ms > 500.0) {
        fprintf(stderr, "perf1: --audio-track must be 0 or more, --audio-volume 0..1, --audio-offset-ms within 1000, "
                "--audio-null-drift-ppm within 5000, --audio-null-latency-ms 0..500\n");
        return false;
    }
    if (cfg->windowed && cfg->refresh_hz > 0) {
        fprintf(stderr, "perf1: --refresh is for full-screen trials; refusing to combine it with --windowed\n");
        return false;
    }
    if (cfg->headless_hz) {
        if (cfg->headless_hz != 96 && cfg->headless_hz != 72 && cfg->headless_hz != 100) {
            fprintf(stderr, "perf1: --headless takes the external's refresh rate: 96, 72 or 100\n");
            return false;
        }
        if (cfg->windowed || cfg->refresh_hz > 0 || cfg->probe_only) {
            fprintf(stderr, "perf1: --headless has no windows and no display: not with --windowed, --refresh or --probe\n");
            return false;
        }
        cfg->decode = DECODE_SW; // the dummy GPU has no decoder surfaces to share
    }
    return true;
}
