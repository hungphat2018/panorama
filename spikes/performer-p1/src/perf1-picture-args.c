// Command-line options of the picture path (the rest of the command line is perf1-args.c).
#include "perf1-picture.h"

#include <math.h>

struct pic_cfg PC = {
    .surface = -1,
    .surround_glow = true,
    .kernel = "ewa_lanczossharp",
    .sub_stream = -1,
    .sub_monitor = -1,
    .dump_at_s = 0.0,
};

void picture_usage(void)
{
    fprintf(stderr,
        "  picture path:\n"
        "          [--room FILE] [--surface fold|curve] [--blend off|rolling] [--surround glow|black]\n"
        "          [--kernel NAME] [--chroma-filter NAME] [--sub FILE] [--sub-stream N] [--sub-monitor N] [--no-subs]\n"
        "          [--dump-canvas DIR [--dump-at SECONDS] [--dump-cut]]\n"
        "  --room FILE      the room (panorama.room/1): monitor sizes, places, gaps, seat and colour; without it the\n"
        "                   monitors are flat neighbours --gap-mm apart, as before\n"
        "  --surface        fold (the picture printed on the panels, gaps included) or curve (a cylinder seen from the\n"
        "                   seat; needs --room); default: the room file's, else fold\n"
        "  --blend rolling  a follower mixes the old and new film frame per row by the time its refresh straddles the\n"
        "                   master's switch (changes what you see; default off)\n"
        "  --surround       leftover area: glow from the picture's edges (default) or black; a cue's own setting wins\n"
        "  --kernel NAME    resample kernel: ewa_lanczossharp (default), ewa_lanczos, ewa_lanczos4sharpest, ewa_robidoux,\n"
        "                   ewa_catmull_rom, ewa_mitchell, ewa_hermite\n"
        "  --sub FILE       dialogue from an external .ass/.srt/.vtt (default: the film's own text track, if any)\n"
        "  --sub-monitor N  the monitor that shows dialogue when the cue sheet names none\n"
        "  --dump-canvas DIR  once per monitor, from the first refresh at or after film time --dump-at (default 0):\n"
        "                   write pass 1's canvas (DIR/<monitor>.f32/.json) and the finished frame (DIR/<monitor>-final.ppm);\n"
        "                   --dump-cut: with --blend rolling, wait for a refresh that mixes frames of two shots\n");
}

static const char *const k_options[] = { "--room", "--surface", "--blend", "--surround", "--kernel", "--chroma-filter",
                                         "--sub", "--sub-stream", "--sub-monitor", "--dump-canvas", "--dump-at" };

// A whole number in [0, 15] (a monitor or stream number).
static bool small_int(const char *v, int *out)
{
    char *end = NULL;
    long n = strtol(v, &end, 10);
    if (end == v || *end || n < 0 || n > 15)
        return false;
    *out = (int) n;
    return true;
}

int picture_arg(int argc, char **argv, int *i)
{
    const char *a = argv[*i];
    if (!strcmp(a, "--no-subs") || !strcmp(a, "--dump-cut")) {
        PC.no_subs |= !strcmp(a, "--no-subs");
        PC.dump_cut |= !strcmp(a, "--dump-cut");
        return 1;
    }
    bool known = false;
    for (size_t n = 0; n < sizeof(k_options) / sizeof(k_options[0]); n++)
        known = known || !strcmp(a, k_options[n]);
    if (!known)
        return 0;
    const char *v = *i + 1 < argc ? argv[*i + 1] : NULL;
    if (!v) {
        fprintf(stderr, "perf1: %s needs a value\n", a);
        return -1;
    }
    bool ok = true;
    if (!strcmp(a, "--room")) PC.room_file = v;
    else if (!strcmp(a, "--surface")) {
        ok = !strcmp(v, "fold") || !strcmp(v, "curve");
        PC.surface = !strcmp(v, "curve");
    } else if (!strcmp(a, "--blend")) {
        ok = !strcmp(v, "rolling") || !strcmp(v, "off");
        PC.blend_rolling = !strcmp(v, "rolling");
    } else if (!strcmp(a, "--surround")) {
        ok = !strcmp(v, "glow") || !strcmp(v, "black");
        PC.surround_glow = !strcmp(v, "glow");
    } else if (!strcmp(a, "--kernel")) {
        double support;
        ok = pic_kernel_weight(v, 0.0, NULL, &support);
        PC.kernel = v;
    } else if (!strcmp(a, "--chroma-filter")) PC.chroma_filter = v;
    else if (!strcmp(a, "--sub")) PC.sub_file = v;
    else if (!strcmp(a, "--sub-stream")) ok = small_int(v, &PC.sub_stream);
    else if (!strcmp(a, "--sub-monitor")) ok = small_int(v, &PC.sub_monitor);
    else if (!strcmp(a, "--dump-canvas")) PC.dump_dir = v;
    else { // --dump-at
        char *end = NULL;
        PC.dump_at_s = strtod(v, &end);
        ok = end != v && !*end && isfinite(PC.dump_at_s) && PC.dump_at_s >= 0;
    }
    if (!ok) {
        fprintf(stderr, "perf1: %s: bad value '%s'\n", a, v);
        return -1;
    }
    (*i)++;
    return 1;
}
