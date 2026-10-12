// Result files: a canvas read back from the GPU as raw RGBA16F (+ a small JSON header), and JSON text helpers.
#pragma once

#include <stdbool.h>
#include <stdio.h>

#include "pp-gpu.h"

// Creates the directory (and its parents). Returns false (with a message) on failure.
bool pp_mkdirs(const char *dir);

// Downloads `canvas` (RGBA16F) and writes <dir>/<name>.f16 (row 0 = top, half floats, R G B A) and <dir>/<name>.json
// (width, height, format "rgba16f" and meta_json: extra members as `"key":value,...` text without braces, or NULL).
bool pp_output_write_canvas(const char *dir, const char *name, const struct pp_gpu *g, pl_tex canvas, const char *meta_json);

// Writes `s` as a JSON string literal (with quotes) to f.
void pp_json_write_string(FILE *f, const char *s);
