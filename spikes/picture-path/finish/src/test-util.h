// Small helpers for the finish test driver: wall clock, statistics, PNG and CSV output, texture read-back.
#pragma once

#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "finish-device.h"
#include "finish-passes.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define TU_ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

// Test bookkeeping: every check prints one line, the program exits non-zero if any failed.
void tu_check(bool ok, const char *fmt, ...);
int tu_failures(void);

double tu_now_ms(void); // monotonic, QueryPerformanceCounter

// Percentiles of a sample set (the array is sorted in place). p in [0,100].
void tu_sort(double *v, int n);
double tu_percentile(const double *sorted, int n, double p);

// 8-bit RGB(A) PNG. `rgba` is w*h*4 bytes (alpha ignored when channels == 3 is requested via tu_png_write_rgb).
bool tu_png_write_rgba8(const char *path, const uint8_t *rgba, int w, int h);

// Half-float helpers for canvases read back as "rgba16f" (the format hands the host 32-bit floats).
// Reads a whole texture into a malloc'd float array (w*h*4); the caller frees.
float *tu_read_f32(const struct fp_env *env, pl_tex tex, int w, int h);
// Reads an 8-bit rgba/bgra texture into a malloc'd RGBA byte array (swizzled to RGBA).
uint8_t *tu_read_u8(const struct fp_env *env, pl_tex tex, int w, int h);

// Linear-light float image -> sRGB 8-bit PNG (for looking at canvases; clamps and encodes).
bool tu_png_write_linear(const char *path, const float *rgba, int w, int h, float exposure);

// Creates the formats the tests use.
pl_tex tu_canvas(const struct fp_env *env, int w, int h, const char *what);       // rgba16f, sampleable+renderable
pl_tex tu_target8(const struct fp_env *env, int w, int h, bool storable, const char *what); // rgba8 render target

// Makes a directory (and parents) if missing.
void tu_mkdir_p(const char *path);
