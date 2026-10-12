// GLSL of the resample pass (see perf1-picture-shader.c for the uniforms it expects).
#pragma once

extern const char pic_glsl_common[];   // the sigmoid transform pair
extern const char pic_glsl_resample[]; // pic_main(): one output pixel
