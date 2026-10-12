// Prints the CPU map of a scenario at a grid of pixels so tests/test-c-geometry.py can compare it with the numpy
// reference, and runs the C-side round-trip checks of the vertical convention.
//   bin/geometry-test.exe scenarios/fold-t1.json
#include <math.h>
#include <stdio.h>

#include "pp-scenario.h"
#include "pp-util.h"

static int check_round_trip(void)
{
    int bad = 0;
    for (int i = 0; i < 2000; i++) {
        double row = 1600.0 * (i * 0.6180339887 - floor(i * 0.6180339887));
        double y = pp_row_to_local_y(row, 1600, 215.0);
        double back = pp_local_y_to_row(y, 1600, 215.0);
        if (fabs(back - row) > 1e-9)
            bad++;
    }
    if (fabs(pp_row_to_local_y(0, 1600, 215.0) - 107.5) > 1e-12 || fabs(pp_row_to_local_y(1600, 1600, 215.0) + 107.5) > 1e-12)
        bad++;
    printf("{\"test\":\"vertical-round-trip\",\"bad\":%d}\n", bad);
    return bad;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: geometry-test scenario.json\n");
        return 2;
    }
    struct pp_scenario s;
    char err[256];
    if (!pp_scenario_load(&s, argv[1], err, sizeof(err))) {
        fprintf(stderr, "geometry-test: %s\n", err);
        return 2;
    }
    int bad = check_round_trip();
    struct pp_picture pic;
    pp_scenario_picture(&s, &pic);
    for (int k = 0; k < s.room.nmon; k++) {
        const struct pp_monitor *m = &s.room.mon[k];
        static const double fx[] = { 0.0, 0.25, 0.5, 0.9, 1.0 };
        for (int a = 0; a < 5; a++) {
            for (int b = 0; b < 5; b++) {
                double px = fx[a] * m->w, py = fx[b] * m->h;
                if (a == 0)
                    px += 0.5;
                if (b == 0)
                    py += 0.5;
                double sx, sy, j[4];
                pp_source_jacobian(&s.room, k, &pic, &s.surface, s.has_slide ? &s.slide : NULL, px, py, &sx, &sy, j);
                printf("{\"monitor\":\"%s\",\"px\":%.9g,\"py\":%.9g,\"sx\":%.12g,\"sy\":%.12g,\"j\":[%.12g,%.12g,%.12g,%.12g]}\n",
                       m->id, px, py, sx, sy, j[0], j[1], j[2], j[3]);
            }
        }
        printf("{\"monitor\":\"%s\",\"upscales\":%d}\n", m->id, pp_monitor_upscales(&s.room, k, &pic, &s.surface));
    }
    return bad ? 1 : 0;
}
