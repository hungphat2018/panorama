// Control of a running perf1: load a cue sheet version, seek, pause, resume, status, stop. Two doors, one command
// executor: a script file for tests and a named pipe for another program (the planner).
//
// A command is a JSON object:
//   {"cmd":"load","file":"cues-v3.json"}     validate the sheet against the video and publish it as the newest version
//   {"cmd":"seek","t":55.5}                  film time in seconds on the rebased timeline (first video frame = 0)
//   {"cmd":"pause"}   {"cmd":"resume"}
//   {"cmd":"status"}                         -> {"ok":true,"state":"playing","epoch":3,"playhead":123.4,"version":5,...}
//   {"cmd":"stop"}                           end the run (the logs are written)
// Every command answers one JSON object: {"ok":true,...} or {"ok":false,"error":"..."}.
//
// Named pipe (--pipe NAME): \\.\pipe\NAME, message mode, one JSON command per message, one reply per command. Only the
// user that started perf1 can write to it (the default security of a named pipe); remote clients are refused.
//
// Script (--control-script FILE): JSON lines; blank lines and lines starting with # are ignored. Each line is a command
// plus when to run it, either
//   "at": 12.0       when the picture on the master reaches this film time (seconds), or
//   "after": 0.25    this many seconds of wall time after the previous line ran (the first: after the film started).
// Lines run in order, one at a time. A "load" line is read and validated when the script is loaded, so a typo fails the
// run before it starts, and publishing at its moment takes microseconds.
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "perf1-cue-sheet.h"
#include "perf1-engine.h"

struct control;

// src_w x src_h: the visible size of the video frames (a sheet's crops must fit it). on_stop is called for "stop".
struct control *control_create(struct engine *e, struct cue_store *store, double src_w, double src_h, void (*on_stop)(void));
bool control_load_script(struct control *c, const char *path, char *err, size_t err_size);
bool control_start_pipe(struct control *c, const char *pipe_name);
// The film's first frame is up: the first "after" counts from here.
void control_film_started(struct control *c, double now);
// Render thread, every loop: runs the script lines that are due.
void control_poll(struct control *c, double now);
void control_destroy(struct control *c);

// Runs one command (used by the pipe and the script, public for tests). The reply is JSON text in `reply`.
void control_execute_text(struct control *c, const char *text, size_t len, double now, char *reply, size_t reply_size);
