// The room file and where each monitor sits, see perf1-picture.h. Without --room the monitors are the flat neighbours
// perf1 always had (sizes and positions from the displays, --gap-mm apart, tops per --height-offset-mm).
#include "perf1-picture.h"

static struct room g_room;
static bool g_have_room;
static int g_ridx[MAX_MONITORS];            // room monitor index of each monitor slot
static struct room_geom g_geom[MAX_MONITORS];

const struct room *picture_room(void)
{
    return g_have_room ? &g_room : NULL;
}

const struct room_geom *picture_geom(int slot)
{
    return &g_geom[slot];
}

// Slots ordered left to right by desktop position: the order the room lists its monitors in.
static void order_by_position(int order[MAX_MONITORS])
{
    for (int i = 0; i < A.nmon; i++)
        order[i] = i;
    for (int i = 1; i < A.nmon; i++)
        for (int k = i; k > 0 && A.mon[order[k]].rect.left < A.mon[order[k - 1]].rect.left; k--) {
            int t = order[k];
            order[k] = order[k - 1];
            order[k - 1] = t;
        }
}

bool picture_load_room(void)
{
    if (!PC.room_file) {
        if (PC.surface == 1) {
            fprintf(stderr, "perf1: --surface curve needs the seat and the monitors' poses: give --room\n");
            return false;
        }
        for (int i = 0; i < A.nmon; i++)
            g_ridx[i] = -1;
        return true;
    }
    char err[300] = "";
    if (!room_load(&g_room, PC.room_file, err, sizeof(err))) {
        fprintf(stderr, "perf1: %s\n", err);
        return false;
    }
    if (g_room.nmon != A.nmon) {
        fprintf(stderr, "perf1: the room %s lists %d monitors, %d are connected\n", PC.room_file, g_room.nmon, A.nmon);
        return false;
    }
    int order[MAX_MONITORS];
    order_by_position(order);
    for (int r = 0; r < A.nmon; r++) {
        struct monitor *m = &A.mon[order[r]];
        const struct room_monitor *rm = &g_room.mon[r];
        int w = (int) (m->rect.right - m->rect.left), h = (int) (m->rect.bottom - m->rect.top);
        if (w != rm->w || h != rm->h) {
            fprintf(stderr, "perf1: the room's monitor %d (%s) is %dx%d, %s is %dx%d (monitors are matched left to right)\n", r,
                    rm->id, rm->w, rm->h, m->name, w, h);
            return false;
        }
        g_ridx[m->slot] = r;
        m->w_mm = rm->w_mm;
        m->h_mm = rm->h_mm;
        m->left_mm = rm->left_mm;
        m->row.height_mm = rm->h_mm; // a virtual display's row clock was made before the room was read
    }
    g_have_room = true;
    bool curve = PC.surface >= 0 ? PC.surface == 1 : g_room.curve;
    printf("room %s from %s: %d monitors, seat %.0f mm, surface %s\n", g_room.name, PC.room_file, g_room.nmon, g_room.seat_distance,
           curve ? "curve" : "fold");
    return true;
}

double picture_top_mm(int slot, bool follower)
{
    if (g_have_room)
        return room_top_mm(&g_room, g_ridx[slot], g_ridx[A.master]);
    return follower ? A.cfg.height_offset_mm : 0.0;
}

bool picture_geometry(void)
{
    bool curve = g_have_room && (PC.surface >= 0 ? PC.surface == 1 : g_room.curve);
    for (int i = 0; i < A.nmon; i++) {
        const struct monitor *m = &A.mon[i];
        if (curve)
            room_geom_curve(&g_geom[i], &g_room, g_ridx[i], m->sc_w, m->sc_h, g_ridx[A.master]);
        else
            room_geom_fold(&g_geom[i], m->sc_w, m->sc_h, m->w_mm, m->h_mm, m->left_mm, m->row.top_mm);
    }
    return true;
}

// The colour block of a monitor: the room's, or neutral (Windows owns the gamut).
const struct room_color *picture_color(int slot)
{
    static const struct room_color neutral = { .acm = true, .gain = { 1, 1, 1 }, .white = { 1, 1, 1 }, .power = 2.2 };
    return g_have_room ? &g_room.mon[g_ridx[slot]].color : &neutral;
}

// Where the gaze axis and eye level are on the surface (the room's frame is measured from them).
bool picture_surface_origin(double *u_axis, double *v_eye)
{
    if (!g_have_room)
        return false;
    *u_axis = g_room.u_axis;
    *v_eye = room_eye_v(&g_room, g_ridx[A.master]);
    return true;
}

// The name a monitor's files carry: the room's id for it, else its display name.
const char *picture_monitor_name(int slot)
{
    return g_have_room ? g_room.mon[g_ridx[slot]].id : A.mon[slot].name;
}
