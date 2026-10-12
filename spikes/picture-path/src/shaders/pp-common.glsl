// Shared by the picture-path passes: libplacebo's sigmoid transform (center 0.75, slope 6.5, as pl_shader_sigmoidize and
// pl_shader_unsigmoidize define it). The sigmoid domain maps [0, 1] to [0, 1]; a value outside the range is clamped.
const float PP_SIG_C = 0.75;
const float PP_SIG_S = 6.5;

vec3 pp_sigmoid(vec3 x)
{
    float offset = 1.0 / (1.0 + exp(PP_SIG_S * PP_SIG_C));
    float scale = 1.0 / (1.0 + exp(PP_SIG_S * (PP_SIG_C - 1.0))) - offset;
    vec3 sig = clamp(x, 0.0, 1.0) * scale + offset;
    return (log(sig / (1.0 - sig)) - log(offset / (1.0 - offset))) / PP_SIG_S;
}

vec3 pp_unsigmoid(vec3 y)
{
    float offset = 1.0 / (1.0 + exp(PP_SIG_S * PP_SIG_C));
    float scale = 1.0 / (1.0 + exp(PP_SIG_S * (PP_SIG_C - 1.0))) - offset;
    float bias = log(offset / (1.0 - offset));
    y = clamp(y, 0.0, 1.0);
    return (vec3(1.0) / (vec3(1.0) + exp(-(y * PP_SIG_S + bias))) - 1.0 / (1.0 + exp(-bias))) / scale;
}

// The sRGB electro-optical transfer function (IEC 61966-2-1), the exact piecewise form.
vec3 pp_srgb_to_linear(vec3 c)
{
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), greaterThan(c, vec3(0.04045)));
}
