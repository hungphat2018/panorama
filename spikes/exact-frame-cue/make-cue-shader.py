"""Write the mpv user shader for the exact-frame cue spike from truth.json.

The shader paints a full-height stripe whose gray level is the cue it applied to
the frame it is rendering. With use_pts=1 it looks the cue up from PTS, which
mpv's gpu-next fills in for the frame being drawn (the cue travels with the
frame). With use_pts=0 it shows manual_level, which a script sets from outside
while playing (the out-of-band control arm).

The cue table is an rg32f texture (start seconds, level), not GLSL const arrays:
on D3D11 a few thousand const array entries become indexable temp registers and
fail to compile (X4505, limit 4096), and libplacebo then drops the pass. Cue
starts sit halfway between the last frame of one shot and the first frame of the
next, so float rounding of PTS can never land on the wrong side of a cut.

Hot-swap trials (see swap-planner.py) add:
--capacity N      fixed-size table. Every version of the shader then has identical
                  GLSL and differs only in texture data, so libplacebo reuses the
                  compiled pass when a new version is loaded mid-playback. Unused
                  entries start at 1e30 (never reached); the last texel holds the
                  table version, which the OUTPUT hook paints as black/white bars.
                  The version is also in the OUTPUT pass description ("cue table vN"),
                  which mpv reports through its vo-passes property.
--version N       table version stored in that last texel.
--version-define  compile the version into the GLSL instead (a naive generator):
                  every version then needs a new shader compile.
--main-stripe     a second hook at the MAIN stage (source pixels, before scaling)
                  paints the cue it looked up as 2 black/white bars, to test
                  cue-dependent work before the OUTPUT stage.

Usage: python make-cue-shader.py TRUTH_JSON OUT_GLSL [--cues-out CUES_TXT] [--pad-cues N]
                                 [--capacity N [--version N] [--version-define]] [--main-stripe]
--cues-out writes the same cue list as text for the control-arm script.
--pad-cues appends dummy cues after the end of the video to test a film-sized table.
"""
import argparse
import json
import math
import struct
from pathlib import Path

MAX_TEXTURE_WIDTH = 16384  # D3D11 feature level 11 limit for a one-row table
UNUSED_START = 1.0e30      # start time of padding entries: later than any PTS
# Hot-swap trial bars; the capture strip must reach SWAP_STRIP_W screen pixels.
# Version bars: a fixed white-black pair (proves the shader ran on this image), then the
# table version mod 16, so the plain video behind them can never read as a version.
VERSION_BARS = {"x0": 656, "width": 24, "pitch": 32, "bits": 6}   # screen pixels, OUTPUT hook
VERSION_SYNC = 2                                                    # top two bars: 1, 0
MAIN_BARS = {"x0": 432, "width": 16, "pitch": 24, "bits": 2}       # source pixels, MAIN hook
SWAP_STRIP_W = 960

PARAMS = """//!PARAM PTS
//!DESC PTS of the frame being rendered, filled in by mpv gpu-next
//!TYPE DYNAMIC float
0.0

//!PARAM manual_level
//!DESC cue level set from outside while playing (use_pts=0)
//!TYPE DYNAMIC int
//!MINIMUM 0
//!MAXIMUM 3
0

//!PARAM use_pts
//!DESC 1: cue looked up from PTS, 0: cue taken from manual_level
//!TYPE DEFINE
1
"""

TEXTURE = """//!TEXTURE CUES
//!SIZE {count} 1
//!FORMAT rg32f
//!FILTER NEAREST
//!BORDER CLAMP
{texture_hex}
"""

LOOKUP = """#define CUE_COUNT {count}
#define CUE_STEPS {steps}

// Level of the last cue whose start is <= t (binary search over the CUES texture;
// a TEXTURE block is bound under its own name, with no _raw macro).
int cue_level(float t) {{
    int lo = 0;
    int hi = CUE_COUNT - 1;
    for (int step = 0; step < CUE_STEPS; step++) {{
        if (lo >= hi)
            break;
        int mid = (lo + hi + 1) >> 1;
        if (texelFetch(CUES, ivec2(mid, 0), 0).x <= t)
            lo = mid;
        else
            hi = mid - 1;
    }}
    return int(texelFetch(CUES, ivec2(lo, 0), 0).y + 0.5);
}}
"""

# Black/white bars: bit b of value, most significant first, as a full-height bar.
BARS = """    for (int b = 0; b < {bits}; b++) {{
        float bar_x0 = {x0:.1f} + {pitch:.1f} * float(b);
        if (px.x >= bar_x0 && px.x < bar_x0 + {width:.1f})
            return vec4(vec3(float((({value}) >> ({bits} - 1 - b)) & 1)), 1.0);
    }}
"""

MAIN_HOOK = """//!HOOK MAIN
//!BIND HOOKED
//!BIND CUES
//!DESC exact-frame cue spike: cue level baked into the frame before scaling

{lookup}
// Runs per source frame before scaling and frame mixing, unlike the OUTPUT hook.
vec4 hook() {{
    vec2 px = HOOKED_pos * HOOKED_size;
{bars}    return HOOKED_texOff(0);
}}
"""

OUTPUT_HOOK = """//!HOOK OUTPUT
//!BIND HOOKED
//!BIND CUES
//!DESC exact-frame cue spike: paint the applied cue level{desc_tag}

{lookup}{version_code}
vec4 hook() {{
    vec2 px = HOOKED_pos * HOOKED_size;
{bars}    if (px.x < {x0:.1f} || px.x >= {x1:.1f})
        return HOOKED_texOff(0);
#if use_pts
    int level = cue_level(PTS);
#else
    int level = manual_level;
#endif
    return vec4(vec3({gray0} + {gray_step} * float(level)), 1.0);
}}
"""

VERSION_FROM_TEXEL = """
// Version of this cue table, kept in the table's last texel (data, not code).
int table_version() {
    return int(texelFetch(CUES, ivec2(CUE_COUNT - 1, 0), 0).y + 0.5);
}
"""

VERSION_FROM_DEFINE = """
// Version of this cue table, compiled into the code (changes the GLSL every version).
#define TABLE_VERSION {version}
int table_version() {{
    return TABLE_VERSION;
}}
"""


def cue_list(truth):
    """(start_seconds, level) per shot, on mpv's timeline (file start rebased to 0)."""
    pts, rebase = truth["frame_pts"], truth["start_time"]
    cues = []
    for start, level in zip(truth["shot_starts"], truth["shot_levels"]):
        t = -1.0e6 if start == 0 else (pts[start - 1] + pts[start]) / 2 - rebase
        cues.append((t, level))
    return cues


def shader_text(cues, layout, source, capacity=None, version=0, version_define=False, main_stripe=False):
    """The complete shader file for this cue list. With capacity set, the GLSL depends only
    on capacity, main_stripe and (with version_define) version, never on the cues."""
    entries = list(cues)
    if capacity:
        if len(entries) > capacity - 1:
            raise ValueError(f"{len(entries)} cues do not fit a {capacity}-entry table")
        entries += [(UNUSED_START, 0)] * (capacity - 1 - len(entries)) + [(UNUSED_START, version)]
    if len(entries) > MAX_TEXTURE_WIDTH:
        raise ValueError(f"{len(entries)} cues exceed a one-row texture ({MAX_TEXTURE_WIDTH})")

    count = len(entries)
    texture = b"".join(struct.pack("<ff", t, level) for t, level in entries).hex()
    lookup = LOOKUP.format(count=count, steps=math.ceil(math.log2(max(count, 2))) + 1)
    version_code, version_bars, desc_tag = "", "", ""
    if capacity:
        # The pass description is not part of the GLSL, and mpv's vo-passes property reports
        # it for every rendered frame: the planner reads back which table mpv is drawing.
        desc_tag = f" (cue table v{version})"
        version_code = VERSION_FROM_DEFINE.format(version=version) if version_define else VERSION_FROM_TEXEL
        version_bars = BARS.format(value=f"({VERSION_SYNC} << 4) | (table_version() & 15)", **VERSION_BARS)
    grays = layout["levels_gray"]
    parts = [f"// Generated by make-cue-shader.py from {source}. Do not edit by hand.\n", PARAMS,
             TEXTURE.format(count=count, texture_hex="\n".join(texture[i:i + 128] for i in range(0, len(texture), 128)))]
    if main_stripe:
        parts.append(MAIN_HOOK.format(lookup=lookup, bars=BARS.format(value="cue_level(PTS)", **MAIN_BARS)))
    parts.append(OUTPUT_HOOK.format(
        lookup=lookup, version_code=version_code, bars=version_bars, desc_tag=desc_tag,
        x0=layout["probe_screen_x"][0], x1=layout["probe_screen_x"][1],
        gray0=grays[0], gray_step=round(grays[1] - grays[0], 6),
    ))
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("truth", type=Path)
    parser.add_argument("out_glsl", type=Path)
    parser.add_argument("--cues-out", type=Path)
    parser.add_argument("--pad-cues", type=int, default=0)
    parser.add_argument("--capacity", type=int)
    parser.add_argument("--version", type=int, default=0)
    parser.add_argument("--version-define", action="store_true")
    parser.add_argument("--main-stripe", action="store_true")
    args = parser.parse_args()

    truth = json.loads(args.truth.read_text())
    cues = cue_list(truth)
    if args.cues_out:
        args.cues_out.write_text("".join(f"{t:.6f} {level}\n" for t, level in cues))
    end = truth["frame_pts"][-1] + 10
    cues += [(end + i, i % 4) for i in range(args.pad_cues)]
    try:
        text = shader_text(cues, truth["layout"], args.truth.name, args.capacity, args.version,
                           args.version_define, args.main_stripe)
    except ValueError as error:
        raise SystemExit(str(error))
    args.out_glsl.write_text(text)
    print(f"{args.out_glsl}: {len(cues)} cues" + (f" in a {args.capacity}-entry table" if args.capacity else ""))


if __name__ == "__main__":
    main()
