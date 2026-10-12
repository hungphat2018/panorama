// A small strict JSON parser (RFC 8259) for cue sheets and control messages. No dependencies beyond libc.
//
// The tree is plain heap data: json_parse returns the root, json_free releases it. Strings are decoded
// (escapes, surrogate pairs) into NUL-terminated UTF-8. Duplicate object keys, trailing commas, comments,
// NaN and leading zeros are errors, because a hand-written cue sheet with such a slip should fail loudly.
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>

enum json_type { JSON_NULL, JSON_BOOL, JSON_NUMBER, JSON_STRING, JSON_ARRAY, JSON_OBJECT };

struct json_value {
    enum json_type type;
    double num;                 // JSON_NUMBER; for JSON_BOOL 0 or 1
    char *str;                  // JSON_STRING
    size_t n;                   // JSON_ARRAY / JSON_OBJECT: number of elements / members
    struct json_value *items;   // elements, or the member values of an object
    char **keys;                // JSON_OBJECT: member names, parallel to items
};

// Parses text[0..len). Returns the root, or NULL with a message ("line L, column C: ...") in err.
struct json_value *json_parse(const char *text, size_t len, char *err, size_t err_size);

// Frees the tree below *v and the root itself.
void json_free(struct json_value *v);

// Member of an object by name, or NULL (also when v is not an object).
const struct json_value *json_member(const struct json_value *obj, const char *key);

// A deep copy of v (the copy must be released with json_free).
struct json_value *json_clone(const struct json_value *v);

// Convenience for validation code: is v a number holding an exact integer in [lo, hi]?
bool json_is_int_in(const struct json_value *v, double lo, double hi);

// Writes s as a JSON string literal (with the quotes) to f.
void json_write_string(FILE *f, const char *s);
