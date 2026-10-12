// Small helpers shared by every picture-path source file: fatal errors, allocation, wall clock, percentiles.
#pragma once

#include <math.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define PP_ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))

// Prints "picture-path: ..." to stderr and exits with status 2: used for bad input and impossible states.
static inline void pp_die(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fputs("picture-path: ", stderr);
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
    exit(2);
}

static inline void *pp_xcalloc(size_t n, size_t size)
{
    void *p = calloc(n ? n : 1, size ? size : 1);
    if (!p)
        pp_die("out of memory (%zu x %zu bytes)", n, size);
    return p;
}

static inline void *pp_xrealloc(void *old, size_t size)
{
    void *p = realloc(old, size ? size : 1);
    if (!p)
        pp_die("out of memory (%zu bytes)", size);
    return p;
}

static inline char *pp_xstrdup(const char *s)
{
    size_t n = strlen(s) + 1;
    char *p = pp_xcalloc(n, 1);
    memcpy(p, s, n);
    return p;
}

// Reads a whole file into a NUL-terminated heap buffer; dies if it cannot. *size_out excludes the terminator.
static inline char *pp_read_file(const char *path, size_t *size_out)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        pp_die("cannot open %s", path);
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n < 0)
        pp_die("cannot size %s", path);
    char *buf = pp_xcalloc((size_t) n + 1, 1);
    if (n && fread(buf, 1, (size_t) n, f) != (size_t) n)
        pp_die("cannot read %s", path);
    fclose(f);
    if (size_out)
        *size_out = (size_t) n;
    return buf;
}

double pp_now_ms(void); // monotonic wall clock (QueryPerformanceCounter), milliseconds

// Sorts `v` in place (ascending) and returns the percentile p in [0, 100] by linear interpolation. n >= 1.
double pp_percentile_sorted(double *v, int n, double p);
void pp_sort_doubles(double *v, int n);
