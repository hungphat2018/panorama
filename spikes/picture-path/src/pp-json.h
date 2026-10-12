// A small strict JSON reader for the room and scenario files: objects, arrays, numbers, strings (ASCII escapes),
// booleans and null. No writer: the harness writes its own result files by hand.
#pragma once

#include <stdbool.h>
#include <stddef.h>

enum pp_json_type { PP_J_NULL, PP_J_BOOL, PP_J_NUM, PP_J_STR, PP_J_ARR, PP_J_OBJ };

struct pp_json {
    enum pp_json_type type;
    double num;
    bool boolean;
    char *str;
    int n;                      // elements of an array or members of an object
    struct pp_json **items;     // array elements / object member values
    char **keys;                // object member names
};

// Parses `text`; on error returns NULL and writes a message with the byte offset to err.
struct pp_json *pp_json_parse(const char *text, char *err, size_t errsz);
void pp_json_free(struct pp_json *j);

// Member of an object, or NULL if j is not an object or has no such key.
const struct pp_json *pp_json_get(const struct pp_json *obj, const char *key);
// Element i of an array, or NULL.
const struct pp_json *pp_json_at(const struct pp_json *arr, int i);

// Typed readers with defaults; they never fail, a missing or mistyped value gives the default.
double pp_json_num(const struct pp_json *j, double dflt);
const char *pp_json_str(const struct pp_json *j, const char *dflt);
bool pp_json_bool(const struct pp_json *j, bool dflt);
