# Code review: phase 3 live painter, sets-v2 join check, identity refs

Read-only. Nothing executed beyond `ruff check --no-cache` (0.6.9) and `py_compile` of 10 files into a temp dir. No self-tests, GPU, worker or player.
File versions: `identity_encoder.py`, `painter_jobs.py` and `set-atlas-cuts-check.py` 05:18; `live-worker.py` 05:10; `worker_sets.py` 04:51; `painter.py` 04:40; `set_atlas.py` 03:07; `painter-service.py` 01:50.

## Scope

- Reviewed: the functions named in the brief, in `live/set_atlas.py`, `live/worker_sets.py`, `live/painter.py`, `live/painter_jobs.py`, `live/identity_encoder.py` and `live-worker.py:108-142`. About 1,560 lines.
- Context read:
  - `mosaic_t1.py`, `side_cache.py`, `worker_state.py`;
  - `painter-service.py` and `painter_core.py` (encode, paint, slot check, schedules);
  - `analyse_v0.py` (`Analyser.run`, `_start_frame`);
  - `identity-refs.py` (the clusters.json writer);
  - the four callers.
- Scout: traced the shared state across the worker's threads:
  - the analyser (`register` and `verify`);
  - the painter (`pick` and `_paint`);
  - the identity encoder;
  - the main loop (heap);
  - the warm start.

## Overall assessment

- The geometry is right. `agreement()`, `plate_band_agreement()`, `_joinable()` and the plate check all use the texel-centre convention and compose their maps in the right direction (details under "Verified correct").
- The defects are in the hand-off between the set registry and the painter:
  - The sets-v2 plate check does not see the plate of a set that was alone when it was painted, which is almost every set.
  - The check is skipped for a plate that is being painted while the check runs.
  - Plate keys are not validated when the worker starts warm from the cache.
- Thread robustness has two gaps:
  - The identity encoder holds its lock while it stops a 9 GB process.
  - The paint thread has no guard: one exception ends all painting for the run.
- Findings: no Critical, 4 High, 6 Medium, 7 Low.

## Critical

None.

## High

### H1. The plate check and the plate-cover rule never see an alone set's plate

**Cause**
- `painter_jobs.py:253-254` registers a plate only `if not alone`.
- The warm start adds cached plates to the state without telling the registry (`live-worker.py:290-296`).
- So a set that was alone when painted keeps `plate_box` and `plate_rkey` at None.

**What happens when a later shot f matches that set**
- `_joinable` skips the cover rule, because `s.plate_box is None` (`set_atlas.py:255`).
- `_plate_check` sees no rkey and returns None, so no band check runs (`worker_sets.py:194-197`).
- The join commits. `publish_analysis` → `_join` makes f share the set's version list (`worker_state.py:153-161`).
- f then shows a plate that was painted without its pixels and was never checked against its picture. The plate may not even cover f's canvas.

**How often**
- Reuse is about 1.01, so nearly every set is alone when it is painted.
- Every join after that first paint is affected: at startup and after seeks, and for cut-backs to scenes that were painted earlier.

**The acceptance check cannot see it**
- `set-atlas-cuts-check.py:186` and `:69` only consider sets that have a `plate_rkey`.
- Both the by-eye sheets and today's check therefore skip these joins.
- A re-watch heals the set: with 2 members, `plate_key` finds no registered plate and the painter repaints. The first showing is still wrong, and that is what the phase's acceptance check is about.

**Fix**
1. Register every plate. Call `sets.plate_painted(sid, atlas, rkey)` whether or not the set is alone.
2. In the warm start, after `state.add_canvas(sid, TIER_PLATE, ...)`, call `reg.set_plate(sid, <box from the meta>, rkey)`.
3. `plate_key` for an alone set keeps phase 2's key unless the registry holds one. This also fixes M1.

### H2. Race: a plate painted while `verify` runs is never band-checked; the commit re-checks only geometry

**Timeline** (set S = {k}, new shot f matched to k):
1. Painter: `atlas(S)` reads members {k} (`painter_jobs.py:211`), then paints for about 5 s.
2. Analyser: `verify(f)` → `_plate_check` → `plate_of(S)` returns None, because `set_plate` has not run yet. No band check runs (`worker_sets.py:194-197`). The mosaics agree.
3. Analyser: the commit under the lock calls `_joinable(locked=True)`, sees `plate_box` is None, and f joins (`set_atlas.py:232-239`).
4. Painter: `alone` is evaluated after the paint (`painter_jobs.py:240`) and is now False:
   - `rkey = key_hash(akey_{k}, recipe)`;
   - `plate_painted` registers k's extent and saves sets.json (`:253-254`).
   - f now shares a plate that was painted on the atlas from before the join. The plate was never checked against f's picture and may not cover f's canvas.

**Variants and persistence**
- If `set_plate` lands between `verify` and the commit, the re-check applies the cover rule but still not the band check.
- The result persists across runs. On a re-watch, `plate_key` returns this rkey (`worker_sets.py:306-307`), and nothing compares it with the 2-member atlas key (M2).

**When it happens and why it matters**
- Likely at startup and after every seek: the idle painter paints the first feasible set while the analyser is still registering that scene's cut-backs.
- It contradicts `_join_check`'s own rule, "what cannot be judged does not join": a plate in progress cannot be judged.

**Fix** (optimistic concurrency)
1. Give `ShotSet` a `plate_gen` counter and a `painting` flag.
2. `_paint` calls `reg.begin_paint(sid)` before it reads the atlas; this increments `plate_gen` and sets `painting`.
3. `set_plate` increments `plate_gen` and clears `painting`.
4. `register` captures `plate_gen` under the lock before `verify`. At the commit, if `painting` is set or `plate_gen` changed, it re-verifies or skips the join.
5. Add an invariant check in `_paint`: if `self.sets.atlas_key(sid) != akey` after the paint, do not register or serve the plate for the changed set.

### H3. The identity encoder stops its 9 GB service while holding its condition lock, and the paint thread waits for it

**The lock**
- `identity_encoder.py:89-92`: `_stop_service()` runs inside `with self.cv:`.
- The stop sends `/quit` (5 s timeout), waits up to 30 s, terminates, then waits up to 30 more seconds (`:155-168`). That is up to about 65 s.
- Normally the wait is however long a 9 GB torch process takes to exit (not measured).

**Who waits**
- `painter_jobs.py:222` and `:226` call `prefetch()` and `ready()` before every plate. Both take `self.cv` (`:65`, `:76`).
- So the plate starts late by the length of the stop. The X5 feasibility test (estimate × 1.2) was already decided before the wait.
- This happens whenever the queue drains, which is routine: after every read when nothing else is queued.
- `PainterJobs.stop()` also takes the lock, so the worker's shutdown (`live-worker.py:387`) waits the same time.

**A hung encoder makes it worse**
- An `/encode` timeout (600 s, `:122`) leaves the hung service running. `_start_service` returns early while `proc.poll() is None` (`:136-137`), so the hung process is reused.
- The service is single-threaded (`HTTPServer`, `painter-service.py:204`), so every later read times out after 600 s.
- The drain-time stop then waits 5 + 30 + 30 s while holding the lock.

**Fix**
- Decide under the lock and act outside it: when the queue is empty and a service is up, release the lock, call `_stop_service()`, and loop.
- On an `/encode` timeout, kill the service instead of reusing it.

### H4. One exception in `_paint`, outside the paint call itself, ends all painting for the run

**Cause**
- `painter_jobs.py:200`: `self._paint(info)` has no guard.
- `_paint`'s `try` has only a `finally` (`:210-236`), and the bookkeeping after the paint (`:237-263`) has no guard at all.

**New ways it can raise (tonight's code)**
- The identity lookups parse a JSON file written by another tool:
  - `int(e.get("first", -1))` raises TypeError on a null (`:66`);
  - `pick["path"]` raises KeyError if the key is missing (`:97`);
  - a document that is not a dict raises AttributeError.

**Existing ways, likely on Windows**
- `save_canvas` → `os.replace` (`side_cache.py:77`, `:91`).
- `plate_painted` → `save_registry` → `os.replace` (`set_atlas.py:396`).
- Both raise PermissionError while another process has the target open: `set-atlas-cuts-check.py` reading sets.json, an antivirus scan or the indexer.

**Effect**
- No more plates for the rest of the run.
- `painter_state` stays "idle" (set in the `finally`) and the beat still says "running".
- The traceback goes to stderr only, never to worker.log.

**Fix**
- Guard the call in `run()`:

  ```python
  try:
      self._paint(info)
  except Exception as e:
      self.failed += 1
      self.log(f"paint: set {info.set_id} failed: {e!r}")   # plus the traceback
  ```

- Wrap the identity block on its own, so that a failure there means "paint without identity".

## Medium

### M1. Identity plates of alone sets are never found on a warm start, so they are repainted every run

- `painter_jobs.py:241-244`: the key is `key_hash(<phase-2 key>, <crop png name>)`, and the plate is not registered (H1).
- The warm start looks up `plate_key` (`live-worker.py:290`), which returns phase 2's key for an alone set (`worker_sets.py:303-305`). That never matches.
- So every run repaints the same identity plate under the same key: wasted GPU time, and a re-watch is not served from the cache.
- The reverse also holds: with `--identity-refs`, a cached plate without identity always wins and is never upgraded.
  - `ready()` puts the plate's crop at the front of the queue, but the plate itself is painted without it.
  - `tried` blocks a second paint in the same run.
- Fix: with H1's registration in place, `plate_key` returns the registry's key whenever there is one.

### M2. Plates of multi-shot sets are served across recipe changes

- `worker_sets.py:306-307`: `plate_key(set_id, recipe)` ignores `recipe` when the set has several shots. `live-worker.py:290-296` serves whatever key sets.json holds.
- An alone set is repainted when the recipe changes, because its key includes the recipe. A multi-shot set keeps its old plate:
  - tonight's turbo6 → turbo5 change;
  - later, a sides-only LoRA.
- The canvas meta already records `recipe` and `atlas_key` (`painter_jobs.py:248`).
- Fix: accept the stored plate only when `meta["recipe"] == recipe` and `meta["atlas_key"] == atlas_key(sid)`. Otherwise treat the set as having no plate.

### M3. A set picked while a newly joined member's mosaic is still being built gets no plate this run

- `live-worker.py:199-208`: the join is visible in `reg.members` before `set_mosaic` runs.
- `atlas_key` returns None while any member has no mosaic key (`worker_sets.py:235-236`).
- `_paint` then logs "mosaic not in the cache" and returns, but the set is already in `tried` (`painter_jobs.py:206-214`).
- The window is the mosaic build plus its PNG save, roughly 0.5-3 s (an estimate) per join. The painter picks again right after every plate.
- Fix: when `atlas is None`, call `tried.discard(sid)` and set `plate_tried = False`, because the condition is transient. Alternatively, `pick()` skips sets whose `atlas_key` is None.

### M4. `Painter.paint` does not recover from a timeout or a failed identity paint

- After the 120 s timeout, the hung service stays: `alive()` is still True (`painter_jobs.py:191`).
- The service is single-threaded, so each later `/paint` queues behind the hung one and also times out after 120 s (`painter.py:183-186`).
- An identity paint that fails, for example on a bad conditioning (the slot-count ValueError comes back as a 500), costs the set its plate for the run. Nothing retries it without identity.
- Fix: on a timeout, stop and restart the service; on an identity failure, retry once with `identity=None`.

### M5. Identity plates are larger than the warm-up plate (not tested at the 7 GB cap)

- The warm-up (`painter-service.py:80-84`, `PainterConfig.warmup` at `painter.py:84`):
  - one 1856x576 canvas (about 8.35k image tokens, reference included);
  - one reference;
  - 20 context tokens.
- An identity plate is about 8.7-8.9k image tokens: a canvas of about 1 MP, its reference, and a crop latent of up to 0.15 MP. Its context also gains the crop's vision tokens, and the K/V cache grows with them.
- The identity test ran with `--vram-cap-gb 10` and no warm-up (`painter-identity-test.py:103`).
- So the live combination (7 GB cap, reserved pool, a reference image) has never run.
- Risk: the allocator grows while the film plays (cudaMalloc forces a device-wide sync, which E1's rule forbids), or the cap throws an out-of-memory error and the plate fails (with no fallback, M4).
- Check: warm up with an identity-shaped request when `identity_refs` is on. Alternatively, log `res["vram"]["device_allocs"]` and `alloc_retries` for each identity plate and confirm they do not grow.

### M6. The CPU encoder's load next to the player is unbounded and unmeasured

- The service loads 9 GB and runs torch on every core. Neither `painter_core` nor `painter-service` caps the threads; the only throttle is below-normal priority.
- The first plate queues 24 crops for read-ahead, about 10 minutes of CPU (`painter_jobs.py:29`, `:222`).
- Every drained queue stops the service, so each later single request reloads the 9 GB.
- No E1 cell has run with the encoder reading, and the plan's "no pagefile growth" criterion is at risk on the laptop.
- Fix:
  - cap the threads: `OMP_NUM_THREADS` / `MKL_NUM_THREADS` in the `Popen` environment, or `torch.set_num_threads` when `--te-device cpu`;
  - keep the service up for N seconds after the queue drains;
  - run one E1 cell while the encoder reads.

## Low

- **L1. `verify` has no exception guard** (`worker_sets.py:168-188`).
  - An exception aborts the shot's `on_shot`.
  - With a content model, the analyser then retries the same span forever for any shot of 1 s or longer (`analyse_v0.py:345-348` → `_start_frame` at `:312-319`).
  - The trigger is unlikely: for example a corrupt mosaic JSON, since `load_mosaic` does not catch JSON errors (`side_cache.py:213-214`).
  - Fix: catch the exception and return False ("cannot be judged").
- **L2. The mosaic is built twice** for every shot with an accepted match: in `verify` (`worker_sets.py:176`) and again in `on_shot` (`live-worker.py:205`). That is seconds of CPU per long pan on the analyser thread. Fix: hand the mosaic from `verify` back to `on_shot`.
- **L3. `agreement` compares f only with the matched member** (`worker_sets.py:177`).
  - The atlas gives the key shot's pixels priority, so in a set of 3 or more shots f's seam can continue a member f was never compared with.
  - Fix: compare f with the set's current atlas, through t.
- **L4. `edge_person` also counts boxes entirely outside the crop** (`worker_sets.py:70`).
  - `bx0 <= x0 + m` is true for any box to the left of the crop.
  - The effect is conservative: fewer joins.
  - Fix: count a box only when it straddles the edge band.
- **L5. Version strings miss parameters.**
  - `SET_VERSION` is hand-written (`set_atlas.py:45`).
    - It omits `AGREE_MIN_CELLS`, `PLATE_COVER`, `MAX_ZOOM` and `MIN_HIST`.
    - It also omits `EDGE_SHARE` and `SEAM_BAND` (`worker_sets.py:24-27`).
  - The conditioning cache key is the crop's content alone (`identity_encoder.py:37-40`). A new `te`, `INSTRUCTION`, `REF_PIXELS` or grey-canvas size reuses old conditionings, and the plate key does not change either.
  - Fix: build the version strings from the constants, and key conditionings by (content, te, instruction, ref size).
- **L6. clusters.json and its crops can disagree while phase 4 rewrites them.**
  - `identity-refs.py` deletes the `c<k>/` folders (`:297`) and rewrites the crops non-atomically before it replaces clusters.json (`:332`).
  - In that window the worker's loaded document points at renumbered crops (another character) or at half-written files.
  - Fix: skip crops newer than the document (`painter_jobs.py:96-99`).
  - Also, with `limit` 1, `_crops` returns nothing when the top cluster's files are missing, instead of trying the next cluster.
- **L7. Two smaller encoder risks.**
  - `grey-canvas.png` is written non-atomically (`identity_encoder.py:118-120`). A torn file would break every later read.
  - An `http.client.HTTPException` (which is not an OSError) in `_stop_service` kills the encoder thread silently: the run loop guards only `_read`.

## Verified correct (risk calibration)

- **`agreement()`**:
  - `to_world` maps a's texel to its centre, x0 + (i + 0.5)·s.
  - `to_b` maps a world point to b's texel index, (x − x0)/s − 0.5.
  - `mp` = to_b ∘ M⁻¹ ∘ to_world, used with `WARP_INVERSE_MAP` (destination → source). Correct.
  - Coverage `>= 255` after bilinear sampling means all four neighbours are covered.
- **`verify`'s map**: `best["M"]` comes from `match(a, f)`, which runs `estimateAffinePartial2D(src=f.pts, dst=a.pts)`. It maps f's world to a's, which is the right direction.
- **The plate check's map**: `compose(T_a, M)` maps f → a → the set's world, the same as `_joinable`.
- **`plate_band_agreement`**: builds its bands from texel centres in f's world, which is its first frame's source px (`analyse_v0.py:9`). Correct.
- **The encoder queue**:
  - the head stays in place while it is being read;
  - `ready()` inserts at index 1 while busy;
  - no crop is queued twice.
- **Atomic writes**: the conditioning and the PNG are written through a temporary file and `os.replace`.
- **Lock order**: always `state.lock` then `reg.lock`; no inversion found.
- **Callers do not break**:
  - `live-sets-selftest`: `register` still returns a 3-tuple and `verify` is optional.
  - `set-atlas-cuts-check`: the signatures it uses match.
  - `e1-painter-load`: the `PainterConfig` fields it sets and `_call` both exist.
  - `painter-bench`: passes its own service arguments.
- **Service support**: `turbo5` is in `SCHEDULES`; the service accepts `--attn-chunk` and `--te-device`.

## Metrics

- Lint: ruff, 0 issues on 9 files. Compile: `py_compile` 10/10 OK.
- Tests: none cover `agreement`, `plate_band_agreement`, `edge_person`, `register` with `verify`, `IdentityEncoder` or `IdentityRefs`. The self-tests were not run, per the brief.
- Type coverage: n/a (partial type hints).

## Recommended actions

1. H4: a three-line guard.
2. H3: stop the service outside the lock.
3. H1: register every plate.
4. H2: the paint generation counter and `painting` flag.
5. M2 and M1: validate `plate_key`.
6. M3.
7. M4 and M5 before identity is turned on by default.

Plan follow-up: the "10 cuts by eye" check for the set atlas is still pending. Re-run it after H1 and H2 are fixed; until then `set-atlas-cuts-check.py` cannot see the affected joins.

## Unresolved questions

1. H2: should a join into a set that is being painted be refused, or deferred until the plate lands and then band-checked?
2. H1: was keeping alone-set plates out of the registry deliberate? Registering them keeps phase 2's keys, so old caches still serve.
3. What does perf1 show where a member's canvas extends beyond its set's plate: the glow at alpha 0, or a clamp? This decides how visible the cover-rule bypass is.
4. Will phase 4's `identity-refs.py` run while a worker with `--identity-refs` is live (L6)?
5. With `--identity-refs`, should a cached plate without identity be repainted once its crop's conditioning exists (M1)?
