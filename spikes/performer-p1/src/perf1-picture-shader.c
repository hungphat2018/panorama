// The shader text of the resample pass (pass 1 of the picture path), ported from the picture-path harness (pp-resample.glsl
// and pp-common.glsl) with the surface in the performer's own coordinates (u right, v down, millimetres), a slide that
// varies by row, one elliptical weighted average per output pixel and an optional per-row mix of two prepared frames, each
// seen through its own shot's view of the picture. The same maths in double precision is perf1-picture-map.c.
//
// Each GLSL() block is stringified by the preprocessor, so the text has no line comments of its own that survive and no
// preprocessor lines; the notes are here instead.
//
// Uniforms (all vec4), set by perf1-picture-pass1.c:
//   u_mon   = (W, H, w_mm, h_mm)               output pixels and the active area they cover
//   u_cen   = (cx, cy, cz, left_mm)            curve: monitor centre in the seat frame; fold: u of the panel's left edge
//   u_ex    = (ex.xyz, model)                  panel right axis; model 0 = fold, 1 = curve
//   u_ey    = (ey.xyz, curvature)              panel up axis; cylinder curvature 1 / R (0 = the flat plane at the seat distance)
//   u_srf   = (top_mm, u_axis, v_eye, seat)    fold: v of the panel's top edge; curve: surface u of the gaze axis, v of eye level
//   u_pic, u_crop, u_slide, u_jfold            frame A's view of the picture; u_pic_b, u_crop_b, u_slide_b, u_jfold_b: frame B's
//                                              (they differ when the mixed frames belong to different shots)
//     u_pic   = (u0, v0, u1, v1)               the cropped picture on the surface, mm
//     u_crop  = (x0, y0, x1, y1)               source crop, source pixels
//     u_slide = (du0, dv0, ddu, ddv)           camera slide: the picture sits displaced by u_slide.xy + u_slide.zw * row
//     u_jfold = (j00, j01, j10, j11)           fold: the constant Jacobian, exact from the host
//   u_blend = (wa, wb, enabled, sigmoid_a)     share of frame B in row y: clamp(wa + wb * y, 0, 1); sigmoid_a: frame A is in
//                                              libplacebo's sigmoid domain (the picture is magnified), mapped back to linear
//   u_filt  = (antiring strength, kernel first zero in kernel units, kernel support, sigmoid_b)
// Samplers: u_src_a, u_src_b (prepared frames; u_src_b is a 1 x 1 dummy when nothing blends: libplacebo refuses one
// texture bound twice), u_lut (kernel weights over [0, support], 1 x N, R32F).
#include "perf1-picture-shader.h"

#define GLSL(...) #__VA_ARGS__

const char pic_glsl_common[] = GLSL(
    const float PIC_SIG_C = 0.75;
    const float PIC_SIG_S = 6.5;

    vec3 pic_unsigmoid(vec3 y)
    {
        float offset = 1.0 / (1.0 + exp(PIC_SIG_S * PIC_SIG_C));
        float scale = 1.0 / (1.0 + exp(PIC_SIG_S * (PIC_SIG_C - 1.0))) - offset;
        float bias = log(offset / (1.0 - offset));
        y = clamp(y, 0.0, 1.0);
        return (vec3(1.0) / (vec3(1.0) + exp(-(y * PIC_SIG_S + bias))) - 1.0 / (1.0 + exp(-bias))) / scale;
    }

    vec3 pic_sigmoid(vec3 x)
    {
        float offset = 1.0 / (1.0 + exp(PIC_SIG_S * PIC_SIG_C));
        float scale = 1.0 / (1.0 + exp(PIC_SIG_S * (PIC_SIG_C - 1.0))) - offset;
        vec3 sig = clamp(x, 0.0, 1.0) * scale + offset;
        return (log(sig / (1.0 - sig)) - log(offset / (1.0 - offset))) / PIC_SIG_S;
    }
);

const char pic_glsl_resample[] = GLSL(
    // atan2(y, x) for x > 0 to about 1e-8: the GPU's own atan is good to only about 1e-5 rad (0.04 source pixel at 700 mm),
    // too coarse for lines that must meet across a seam. Two half-angle steps bring the argument under 0.2, then a series.
    float pic_atan2(float y, float x)
    {
        float rho = sqrt(x * x + y * y);
        float s1 = y / (rho + x);
        float s2 = s1 / (1.0 + sqrt(1.0 + s1 * s1));
        float s = s2 * s2;
        return 4.0 * s2 * (1.0 - s * (1.0 / 3.0 - s * (1.0 / 5.0 - s * (1.0 / 7.0 - s / 9.0))));
    }

    // Output pixel -> surface position (mm, u right, v down).
    vec2 pic_surface(vec2 pix)
    {
        if (u_ex.w < 0.5)
            return vec2(u_cen.w + pix.x / u_mon.x * u_mon.z, u_srf.x + pix.y / u_mon.y * u_mon.w);
        float lx = (pix.x / u_mon.x - 0.5) * u_mon.z;
        float ly = (0.5 - pix.y / u_mon.y) * u_mon.w;
        vec3 d = normalize(u_cen.xyz + lx * u_ex.xyz + ly * u_ey.xyz);
        float dist = u_srf.w;
        float curv = u_ey.w;
        vec2 uv;
        if (abs(curv) < 1e-12) {
            uv = (dist / d.z) * d.xy;
        } else {
            float r = 1.0 / curv;
            float za = dist - r;
            float a = d.x * d.x + d.z * d.z;
            float b = d.z * za;
            float c = za * za - r * r;
            float t = (b + sqrt(b * b - a * c)) / a;
            vec3 h = t * d;
            uv = vec2(r * pic_atan2(h.x, h.z - za), h.y);
        }
        return vec2(u_srf.y + uv.x, u_srf.z - uv.y);
    }

    // Returns (source position with the slide applied, source position without it) for the surface position uv of an output
    // pixel in row `row`: the first drives the sampling, the second the picture's coverage (the picture's rectangle on the
    // surface does not slide).
    vec4 pic_map(vec2 uv, float row, vec4 pic, vec4 crop, vec4 slide)
    {
        vec2 uvs = uv - (slide.xy + slide.zw * row);
        vec2 k = (crop.zw - crop.xy) / (pic.zw - pic.xy);
        return vec4(crop.xy + (uvs - pic.xy) * k, crop.xy + (uv - pic.xy) * k);
    }

    float pic_kernel(float q)
    {
        int n = textureSize(u_lut, 0).x;
        float f = q / u_filt.z * float(n - 1);
        int i = int(f);
        float w0 = texelFetch(u_lut, ivec2(i, 0), 0).x;
        float w1 = texelFetch(u_lut, ivec2(min(i + 1, n - 1), 0), 0).x;
        return mix(w0, w1, f - float(i));
    }

    // Taps beyond the crop take the crop's edge pixel: the black bars the crop removes never bleed into the picture.
    vec3 pic_fetch(sampler2D src, ivec2 p, vec4 crop)
    {
        ivec2 lo = ivec2(floor(crop.xy + 1e-3));
        ivec2 hi = ivec2(ceil(crop.zw - 1e-3)) - 1;
        ivec2 sz = textureSize(src, 0);
        return texelFetch(src, clamp(p, max(lo, ivec2(0)), min(hi, sz - 1)), 0).rgb;
    }

    // EWA of src around source position c with Jacobian j = (j00, j01, j10, j11). The footprint matrix J J^T gets its
    // eigenvalues clamped to >= 1 (never narrower than one source pixel); distance in kernel units is sqrt(d^T A'^-1 d).
    vec3 pic_ewa(sampler2D src, vec2 c, vec4 j, vec4 crop)
    {
        float a = j.x * j.x + j.y * j.y;
        float b = j.x * j.z + j.y * j.w;
        float cc = j.z * j.z + j.w * j.w;
        float m = 0.5 * (a + cc);
        float dl = sqrt(0.25 * (a - cc) * (a - cc) + b * b);
        float l1 = max(m + dl, 1.0);
        float l2 = max(m - dl, 1.0);
        float cos2 = 1.0;
        float sin2 = 0.0;
        if (dl > 1e-12) {
            cos2 = 0.5 * (a - cc) / dl;
            sin2 = b / dl;
        }
        float mm = 0.5 * (l1 + l2);
        float dd = 0.5 * (l1 - l2);
        float a00 = mm + dd * cos2;
        float a01 = dd * sin2;
        float a11 = mm - dd * cos2;
        float det = l1 * l2;
        float w00 = a11 / det;
        float w01 = -a01 / det;
        float w11 = a00 / det;
        float support = u_filt.z;
        float hx = support * sqrt(a00);
        float hy = support * sqrt(a11);
        int x0 = int(ceil(c.x - hx - 0.5));
        int x1 = int(floor(c.x + hx - 0.5));
        int y0 = int(ceil(c.y - hy - 0.5));
        int y1 = int(floor(c.y + hy - 0.5));
        float s2 = support * support;
        vec3 acc = vec3(0.0);
        float wsum = 0.0;
        // Anti-ringing as libplacebo's polar sampler does it: the taps inside the kernel's first zero vote, with weight
        // w * (1 - c + 0.1)^32 for the minimum and w * (c + 0.1)^32 for the maximum, so the result is clamped to a soft
        // range of its nearest neighbours; the clamp is mixed in by the strength u_filt.x.
        bool use_ar = u_filt.x > 0.0;
        float r02 = u_filt.y * u_filt.y;
        vec3 lo_acc = vec3(0.0);
        vec3 hi_acc = vec3(0.0);
        vec3 lo_w = vec3(0.0);
        vec3 hi_w = vec3(0.0);
        for (int y = y0; y <= y1; y++) {
            float dy = float(y) + 0.5 - c.y;
            for (int x = x0; x <= x1; x++) {
                float dx = float(x) + 0.5 - c.x;
                float q2 = w00 * dx * dx + 2.0 * w01 * dx * dy + w11 * dy * dy;
                if (q2 < s2) {
                    float w = pic_kernel(sqrt(q2));
                    vec3 t = pic_fetch(src, ivec2(x, y), crop);
                    acc += w * t;
                    wsum += w;
                    if (use_ar && q2 <= r02) {
                        vec3 a1 = (vec3(1.0) - t) + 0.10;
                        vec3 a2 = t + 0.10;
                        a1 *= a1; a1 *= a1; a1 *= a1; a1 *= a1; a1 *= a1;
                        a2 *= a2; a2 *= a2; a2 *= a2; a2 *= a2; a2 *= a2;
                        a1 *= w; a2 *= w;
                        lo_acc += a1 * (vec3(1.0) - t); lo_w += a1;
                        hi_acc += a2 * t; hi_w += a2;
                    }
                }
            }
        }
        vec3 col = acc / wsum;
        if (use_ar) {
            vec3 lo = vec3(1.0) - lo_acc / lo_w;
            vec3 hi = hi_acc / hi_w;
            vec3 cl = clamp(col, lo, hi);
            cl = mix(cl, 0.5 * (lo + hi), greaterThan(lo, hi));
            col = mix(col, cl, u_filt.x);
        }
        return col;
    }

    // One frame through one view: the output pixel's colour premultiplied by the picture's coverage, coverage in alpha.
    // uv: the pixel's surface position; sig: src holds the sigmoid domain.
    vec4 pic_one(sampler2D src, vec2 pix, vec2 uv, vec4 pic, vec4 crop, vec4 slide, vec4 jfold, float sig)
    {
        vec4 m0 = pic_map(uv, pix.y, pic, crop, slide);
        vec2 c = m0.xy;
        vec2 c0 = m0.zw;
        vec4 j;
        if (u_ex.w < 0.5) {
            j = jfold;
        } else {
            vec2 xp = pic_map(pic_surface(pix + vec2(0.5, 0.0)), pix.y, pic, crop, slide).xy;
            vec2 xm = pic_map(pic_surface(pix - vec2(0.5, 0.0)), pix.y, pic, crop, slide).xy;
            vec2 yp = pic_map(pic_surface(pix + vec2(0.0, 0.5)), pix.y + 0.5, pic, crop, slide).xy;
            vec2 ym = pic_map(pic_surface(pix - vec2(0.0, 0.5)), pix.y - 0.5, pic, crop, slide).xy;
            j = vec4(xp.x - xm.x, yp.x - ym.x, xp.y - xm.y, yp.y - ym.y);
        }
        float gx = length(j.xy);
        float gy = length(j.zw);
        float cov = clamp(0.5 + (c0.x - crop.x) / gx, 0.0, 1.0) * clamp(0.5 + (crop.z - c0.x) / gx, 0.0, 1.0)
                  * clamp(0.5 + (c0.y - crop.y) / gy, 0.0, 1.0) * clamp(0.5 + (crop.w - c0.y) / gy, 0.0, 1.0);
        if (cov <= 0.0)
            return vec4(0.0);
        bool ident = all(lessThan(abs(j - vec4(1.0, 0.0, 0.0, 1.0)), vec4(1e-3)))
                  && all(lessThan(abs(fract(c) - vec2(0.5)), vec2(1e-3)));
        vec3 col = ident ? pic_fetch(src, ivec2(floor(c)), crop) : pic_ewa(src, c, j, crop);
        if (sig > 0.5)
            col = pic_unsigmoid(col);
        return vec4(col * cov, cov);
    }

    vec4 pic_main()
    {
        vec2 pix = gl_FragCoord.xy;
        vec2 uv = pic_surface(pix);
        float w = u_blend.z > 0.5 ? clamp(u_blend.x + u_blend.y * pix.y, 0.0, 1.0) : 0.0;
        vec4 a = vec4(0.0);
        vec4 b = vec4(0.0);
        if (w < 1.0)
            a = pic_one(u_src_a, pix, uv, u_pic, u_crop, u_slide, u_jfold, u_blend.w);
        if (w > 0.0)
            b = pic_one(u_src_b, pix, uv, u_pic_b, u_crop_b, u_slide_b, u_jfold_b, u_filt.w);
        return (1.0 - w) * a + w * b;
    }
);
