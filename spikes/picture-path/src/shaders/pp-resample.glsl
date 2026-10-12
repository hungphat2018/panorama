// Pass 1 of structure S2: one monitor pixel -> its source position (fold or curve, with a per-row slide), one elliptical
// weighted average (EWA) resample of the prepared frame(s), optional per-row blend of two frames, premultiplied coverage
// in alpha. The same maths, in double precision, is pp_geometry.py / pp_resample.py (the reference).
//
// Uniforms (vec4), set by pp-s2.c:
//   u_mon   = (W, H, w_mm, h_mm)               monitor pixels and active area
//   u_cen   = (cx, cy, cz, fold_u_left)        monitor centre in the seat frame; fold: u of the panel's left edge
//   u_ex    = (ex.xyz, model)                  panel right axis; model 0 = fold, 1 = curve
//   u_ey    = (ey.xyz, curvature)              panel up axis; cylinder curvature 1/R (0 = flat plane at the seat distance)
//   u_pic   = (u_left, v_top, width, height)   the picture on the surface, mm
//   u_crop  = (x0, y0, x1, y1)                 source crop, source pixels
//   u_slide = (vu, vv, t_ref_ms, t_top_ms)     camera slide mm/s and the row timing reference
//   u_misc  = (seat_distance, scan_ms, kernel_support, debug_map)
//   u_blend = (wa, wb, enabled, sigmoid)       weight of frame B in row py: clamp(wa + wb * py, 0, 1); sigmoid: the frames
//                                              are in the sigmoid domain (upscaling), results are mapped back to linear
//   u_jfold = (j00, j01, j10, j11)             fold: the constant Jacobian, exact from the host
//   u_filt  = (antiring strength, kernel first zero in kernel units, 0, 0)
// Samplers: u_src_a, u_src_b (prepared frames; u_src_b exists only when PP_BLEND is defined), u_lut (kernel weights over
// [0, support], 1 x N, R32F). libplacebo refuses one texture bound twice, hence no dummy u_src_b without a blend.

// atan2(y, x) for x > 0, accurate to about 1e-8: the GPU's own atan is good to only about 1e-5 rad (a 0.04 source pixel error
// at 700 mm), too coarse for lines that must meet across a seam. Two half-angle steps bring the argument under 0.2, then a
// short series: angle = 4 * atan(tan(angle / 4)).
float pp_atan2(float y, float x)
{
    float rho = sqrt(x * x + y * y);
    float s1 = y / (rho + x);                               // tan(angle / 2)
    float s2 = s1 / (1.0 + sqrt(1.0 + s1 * s1));            // tan(angle / 4)
    float s = s2 * s2;
    return 4.0 * s2 * (1.0 - s * (1.0 / 3.0 - s * (1.0 / 5.0 - s * (1.0 / 7.0 - s / 9.0))));
}

// Returns (source position with the slide applied, source position without it): the first drives the sampling, the second
// the picture's coverage (the picture rectangle on the surface does not slide).
vec4 pp_map(vec2 pix)
{
    float lx = (pix.x / u_mon.x - 0.5) * u_mon.z;
    float ly = (0.5 - pix.y / u_mon.y) * u_mon.w;           // monitor side of the vertical flip: pixel row -> mm up
    vec2 uv;
    if (u_ex.w < 0.5) {
        uv = vec2(u_cen.w + lx + 0.5 * u_mon.z, u_cen.y + ly);
    } else {
        vec3 p = u_cen.xyz + lx * u_ex.xyz + ly * u_ey.xyz;
        vec3 d = normalize(p);
        float dist = u_misc.x, curv = u_ey.w;
        if (abs(curv) < 1e-12) {
            uv = (dist / d.z) * d.xy;
        } else {
            float r = 1.0 / curv, za = dist - r;
            float a = d.x * d.x + d.z * d.z, b = d.z * za, c = za * za - r * r;
            float t = (b + sqrt(b * b - a * c)) / a;
            vec3 h = t * d;
            uv = vec2(r * pp_atan2(h.x, h.z - za), h.y);
        }
    }
    float t_ms = u_slide.w + pix.y / u_mon.y * u_misc.y;     // light time of this row (ADR 0025)
    vec2 uvs = uv - u_slide.xy * ((t_ms - u_slide.z) * 0.001);
    // picture side of the vertical flip: surface v is up, source rows go down
    vec2 k = vec2((u_crop.z - u_crop.x) / u_pic.z, (u_crop.w - u_crop.y) / u_pic.w);
    return vec4(u_crop.x + (uvs.x - u_pic.x) * k.x, u_crop.y + (u_pic.y - uvs.y) * k.y,
                u_crop.x + (uv.x - u_pic.x) * k.x, u_crop.y + (u_pic.y - uv.y) * k.y);
}

float pp_kernel(float q)
{
    int n = textureSize(u_lut, 0).x;
    float f = q / u_misc.z * float(n - 1);
    int i = int(f);
    float w0 = texelFetch(u_lut, ivec2(i, 0), 0).x;
    float w1 = texelFetch(u_lut, ivec2(min(i + 1, n - 1), 0), 0).x;
    return mix(w0, w1, f - float(i));
}

// EWA of `src` around source position c with Jacobian j = (j00, j01, j10, j11). The footprint matrix J J^T gets its
// eigenvalues clamped to >= 1 (never narrower than one source pixel); distance in kernel units is sqrt(d^T A'^-1 d).
vec3 pp_ewa(sampler2D src, vec2 c, vec4 j)
{
    float a = j.x * j.x + j.y * j.y;
    float b = j.x * j.z + j.y * j.w;
    float cc = j.z * j.z + j.w * j.w;
    float m = 0.5 * (a + cc);
    float dl = sqrt(0.25 * (a - cc) * (a - cc) + b * b);
    float l1 = max(m + dl, 1.0), l2 = max(m - dl, 1.0);
    float cos2 = 1.0, sin2 = 0.0;
    if (dl > 1e-12) {
        cos2 = 0.5 * (a - cc) / dl;
        sin2 = b / dl;
    }
    float mm = 0.5 * (l1 + l2), dd = 0.5 * (l1 - l2);
    float a00 = mm + dd * cos2, a01 = dd * sin2, a11 = mm - dd * cos2;
    float det = l1 * l2;
    float w00 = a11 / det, w01 = -a01 / det, w11 = a00 / det;
    float support = u_misc.z;
    float hx = support * sqrt(a00), hy = support * sqrt(a11);
    ivec2 sz = textureSize(src, 0);
    int x0 = int(ceil(c.x - hx - 0.5)), x1 = int(floor(c.x + hx - 0.5));
    int y0 = int(ceil(c.y - hy - 0.5)), y1 = int(floor(c.y + hy - 0.5));
    float s2 = support * support;
    vec3 acc = vec3(0.0);
    float wsum = 0.0;
    // Anti-ringing as libplacebo's polar sampler does it: the taps inside the kernel's first zero vote, with weight
    // w * (1 - c + 0.1)^32 for the minimum and w * (c + 0.1)^32 for the maximum, so the result is clamped to a soft range
    // of its nearest neighbours; the clamp is mixed in by the strength u_filt.x.
    const bool use_ar = u_filt.x > 0.0;
    float r0 = u_filt.y, r02 = r0 * r0;
    vec3 lo_acc = vec3(0.0), hi_acc = vec3(0.0), lo_w = vec3(0.0), hi_w = vec3(0.0);
    for (int y = y0; y <= y1; y++) {
        float dy = float(y) + 0.5 - c.y;
        int yy = clamp(y, 0, sz.y - 1);
        for (int x = x0; x <= x1; x++) {
            float dx = float(x) + 0.5 - c.x;
            float q2 = w00 * dx * dx + 2.0 * w01 * dx * dy + w11 * dy * dy;
            if (q2 < s2) {
                float w = pp_kernel(sqrt(q2));
                vec3 t = texelFetch(src, ivec2(clamp(x, 0, sz.x - 1), yy), 0).rgb;
                acc += w * t;
                wsum += w;
                if (use_ar && q2 <= r02) {
                    vec3 a1 = (vec3(1.0) - t) + 0.10, a2 = t + 0.10;
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
        vec3 lo = vec3(1.0) - lo_acc / lo_w, hi = hi_acc / hi_w;
        vec3 cl = clamp(col, lo, hi);
        cl = mix(cl, 0.5 * (lo + hi), greaterThan(lo, hi));
        col = mix(col, cl, u_filt.x);
    }
    return col;
}

vec3 pp_decode(vec3 v)
{
    return u_blend.w > 0.5 ? pp_unsigmoid(v) : v;
}

vec4 pp_main()
{
    vec2 pix = gl_FragCoord.xy;                               // (column + 0.5, row + 0.5)
    vec4 m0 = pp_map(pix);
    vec2 c = m0.xy, c0 = m0.zw;
    vec4 j;
    if (u_ex.w < 0.5) {
        j = u_jfold;
    } else {
        vec2 xp = pp_map(pix + vec2(0.5, 0.0)).xy, xm = pp_map(pix - vec2(0.5, 0.0)).xy;
        vec2 yp = pp_map(pix + vec2(0.0, 0.5)).xy, ym = pp_map(pix - vec2(0.0, 0.5)).xy;
        j = vec4(xp.x - xm.x, yp.x - ym.x, xp.y - xm.y, yp.y - ym.y);
    }
    if (u_misc.w > 0.5)
        return vec4(c.x, c.y, j.x, j.w);                       // debug: the map itself, into a float32 canvas
    float gx = length(j.xy), gy = length(j.zw);
    float cov = clamp(0.5 + (c0.x - u_crop.x) / gx, 0.0, 1.0) * clamp(0.5 + (u_crop.z - c0.x) / gx, 0.0, 1.0)
              * clamp(0.5 + (c0.y - u_crop.y) / gy, 0.0, 1.0) * clamp(0.5 + (u_crop.w - c0.y) / gy, 0.0, 1.0);
    if (cov <= 0.0)
        return vec4(0.0);

    bool ident = all(lessThan(abs(j - vec4(1.0, 0.0, 0.0, 1.0)), vec4(1e-3)))
              && all(lessThan(abs(fract(c) - vec2(0.5)), vec2(1e-3)));
    float w = u_blend.z > 0.5 ? clamp(u_blend.x + u_blend.y * pix.y, 0.0, 1.0) : 0.0;
    vec3 ca = vec3(0.0), cb = vec3(0.0);
    if (w < 1.0)
        ca = pp_decode(ident ? texelFetch(u_src_a, ivec2(floor(c)), 0).rgb : pp_ewa(u_src_a, c, j));
#ifdef PP_BLEND
    if (w > 0.0)
        cb = pp_decode(ident ? texelFetch(u_src_b, ivec2(floor(c)), 0).rgb : pp_ewa(u_src_b, c, j));
#endif
    vec3 col = (1.0 - w) * ca + w * cb;
    return vec4(col * cov, cov);
}
