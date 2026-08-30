# The dev VM's wall clock steps backwards ~2.7 s every ~30 s

Measured 2026-08-30 on the development machine (Windows 11 + Docker Desktop /
WSL2), during the hardening sweep's investigation of roadmap row 195. This
directory exists so the measurement and its reproducer survive: the original
artefacts lived under `.superpowers/`, which is gitignored, and a roadmap row
citing a gitignored path documents nothing.

**What was measured:** Postgres `clock_timestamp()` and the test container's own
`time.time()`, sampled back to back at ~100 Hz for 120 s. Both clocks retreat,
by the same amount, at the same sample.

**The finding:** the VM wall clock that sources *both* processes steps backwards
by a composite **~2.705 s** roughly every **~30 s** of real time. It is not a
Postgres artefact, not an asyncpg artefact, and no Postgres time function
escapes it — `transaction_timestamp()`, `now()` and `clock_timestamp()` all read
the same stepping clock. It is Docker Desktop / WSL2 time sync repeatedly
correcting a forward-drifting guest clock.

**The rule this imposes on the test suite:** no test may assert an ordering, or
a sub-3-second tolerance, between two wall-clock readings taken at different
instants — database clock or host clock. Both fail spuriously when a step lands
in the interval. `time.perf_counter()` is monotonic and is unaffected; so is any
assertion made *within a single transaction*, because `transaction_timestamp()`
is fixed at transaction start and cannot move underneath the reader.

This is a developer-environment fact, not a known CI fact. A Linux CI runner may
well have a monotonic clock. The suite is written to survive both.

## The measurement

`.superpowers/run-p-hard1-t3-clockprobe.log`, in full — 120 s of sampling,
11 646 samples, 8 backwards events (4 composite steps):

```
BACKWARDS sample=2162  db_delta=-0.018330s host_delta=-0.018360s db=2026-08-30T11:09:13.976105+00:00
BACKWARDS sample=2166  db_delta=-2.687504s host_delta=-2.687493s db=2026-08-30T11:09:11.321814+00:00
BACKWARDS sample=4835  db_delta=-0.018389s host_delta=-0.018457s db=2026-08-30T11:09:41.248268+00:00
BACKWARDS sample=4839  db_delta=-2.687721s host_delta=-2.687680s db=2026-08-30T11:09:38.593539+00:00
BACKWARDS sample=7499  db_delta=-0.020446s host_delta=-0.020445s db=2026-08-30T11:10:08.550603+00:00
BACKWARDS sample=7503  db_delta=-2.686325s host_delta=-2.686385s db=2026-08-30T11:10:05.896997+00:00
BACKWARDS sample=10174 db_delta=-0.019202s host_delta=-0.019178s db=2026-08-30T11:10:35.822084+00:00
BACKWARDS sample=10178 db_delta=-2.687776s host_delta=-2.687809s db=2026-08-30T11:10:33.166495+00:00
samples=11646 backwards_steps=8
```

Read as four step *events*, each a small retreat (~19 ms) followed 4 samples
(~33 ms) later by the large one (~2.687 s):

| quantity | value | derivation |
|---|---|---|
| composite step magnitude | **−2.705 s** | 0.018 + 2.687 |
| step duration | ~33 ms | 4 samples at ~100 Hz — atomic for practical purposes |
| cadence, *apparent* time | 27.28 s | 11:09:13.98 → 11:09:41.25 → 11:10:08.55 → 11:10:35.82 |
| cadence, **real** time | **~30 s** | 27.28 + 2.705 — apparent time is short by exactly one step per cycle |
| DB/host agreement | < 100 µs on all 8 events | `db_delta` vs `host_delta` — one shared VM clock, measured rather than assumed |

The real-time cadence is the load-bearing number: a test's exposure is
`interval / 30 s`, where `interval` is the gap between the two wall-clock
readings it compares — **not** the step's magnitude, which only sets the
*condition* (a step inverts an ordering whenever |step| > interval, satisfied
here for any interval below 2.7 s).

## Exposure of the assertions this was found through

| site | interval | exposure per execution |
|---|---|---|
| `tests/test_pipeline.py` row-195 assertion (fixed in `9503df0`) | 1.12 s | ~3.7 % |
| `tests/test_facts_gather.py:294`, `:328` (fixed) | ~0.1 s | ~0.3 % each |
| `tests/test_item_facts.py:69`, `tests/test_queue.py:119` 1 s tolerances (fixed) | sub-ms, tolerance 1 s < the 2.7 s step | in principle, whenever a step lands in the gap |
| `tests/test_queue.py:295` (fixed) — `reclaim_stale()` commits, so the later `func.now()` read is a new transaction | ~2 ms (commit + round trip) | ~0.007 % |

Six assertions across four files, all now restructured to be immune rather than
merely improbable to fail.

Two sites were checked and deliberately **not** touched, because the step is
backwards-only and the direction runs the other way: `tests/test_queue.py:92`
(`job.run_after > db_now + timedelta(seconds=5)`) and `tests/test_routes.py:131`
(`job.run_after > db_now + timedelta(seconds=20)`) both compare `run_after`
against a *later* `db_now` reading with several seconds of slack — a backwards
step only makes `db_now` smaller, which makes both assertions easier to satisfy,
never harder. Recorded here so a future sweep does not re-derive it.

Two independent corroborations of the same 2.705 s step, from data this probe
did not produce:

- The row-195 reproduction's three-clock diagnostic showed
  `transaction_timestamp()` retreating **1.581795 s** across the test's 1.1 s
  sleep. Real elapsed = −1.5818 + 2.705 = **1.1232 s** — the 1.1 s sleep plus
  ~23 ms of overhead. The `clock_timestamp()` pair closes the same way
  (−1.5708 + 2.705 = 1.134 s).
- The 2026-08-29 sighting of the same test read **−0.92 s**. Real elapsed =
  −0.92 + 2.705 = **1.785 s** — the same step across an interval stretched
  0.66 s wider by full-suite load, which is why the flake looked
  full-suite-specific. Suite length cannot change a test's per-execution
  probability; it can and does widen the test's own inter-write interval.

Extrapolating the probe's 27.28 s apparent cadence back three cycles from its
first event (11:09:13.98) lands at **11:07:52.17** — within ~30 ms of the
failing write's transaction. The failing write and a predicted step event
coincide.

## The reproducer

Save the script below to `<repo>/clockprobe.py`, then run it inside the test
container, which supplies `AUTOPOSTER_MAINTENANCE_DATABASE_URL` (the script was
actually run from `.superpowers/p-hard1-195-clockprobe.py`, a gitignored path;
the command below is the equivalent invocation for the version checked in here):

```bash
docker compose -p <project> -f docker-compose.yml -f <isolated-db overlay> \
  run --rm test python /app/clockprobe.py
```

```python
"""Row 195 branch-B corroboration: does the DB's wall clock step backwards?

Samples clock_timestamp() from Postgres and time.time() from the test
container, back to back, as fast as the round trip allows, and prints every
pair where either clock went BACKWARDS between consecutive samples.
"""
import asyncio, os, time
import asyncpg

URL = os.environ["AUTOPOSTER_MAINTENANCE_DATABASE_URL"]

async def main():
    conn = await asyncpg.connect(URL)
    prev_db = prev_host = None
    steps = 0
    samples = 0
    deadline = time.time() + 120
    while time.time() < deadline:
        host = time.time()
        db = await conn.fetchval("select clock_timestamp()")
        samples += 1
        if prev_db is not None:
            ddb = (db - prev_db).total_seconds()
            dhost = host - prev_host
            if ddb < 0 or dhost < 0:
                steps += 1
                print("BACKWARDS sample=%d db_delta=%+.6fs host_delta=%+.6fs db=%s"
                      % (samples, ddb, dhost, db.isoformat()), flush=True)
        prev_db, prev_host = db, host
        await asyncio.sleep(0.01)
    print("samples=%d backwards_steps=%d" % (samples, steps), flush=True)
    await conn.close()

asyncio.run(main())
```

The load-bearing line is `if ddb < 0 or dhost < 0`: it prints **both** deltas
whenever **either** clock retreats, so no backwards event in either clock can be
missed, and lockstep is measured rather than assumed.

Two known imprecisions, neither material: the host reading is taken *before* the
DB round trip, so the two deltas cover slightly offset intervals (the sub-100 µs
agreement absorbs that); and `deadline = time.time() + 120` is itself
non-monotonic, so the "120 s" run covered ~130.8 s of real time (4 steps ×
2.705 s) — which if anything understates the event rate.

## For an operator on this machine

Optional and not a repo change: the step is characteristic of Docker Desktop /
WSL2 time sync. Restarting WSL (`wsl --shutdown`) or the WSL2 time-sync setting
is where to look. The suite does not depend on anyone doing this — that is the
point of the rule above.
