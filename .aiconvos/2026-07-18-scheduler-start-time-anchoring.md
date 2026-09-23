# Scheduler start-time anchoring: setup time before the first wait

Question (Marc, 2026-07-18): the old 0.6.x clockblocks didn't start counting time until the first
`wait()` call. Should 1.0 do the same?

## What the old code did

On the `0.6.x` branch, `Clock._start_time` is `None` at construction and is lazily set to
`time.time()` at the top of `wait()` (old clock.py:1095) and `wait_for_children_to_finish()`
(old clock.py:1304) — i.e. the wall-clock anchor for absolute timing is planted the first time the
clock actually *blocks*. Everything between `Clock()` and the first wait — loading soundfonts,
creating instruments, typing in a REPL — was free: the first wait was always a full wait.

## What 1.0 does (and why it's a problem)

`Clock.__init__` starts the scheduler thread immediately (clock.py:289), and `Scheduler.run()`
anchors `_start_time = now()` at thread start (scheduler.py:413). So setup time counts against the
absolute schedule. Verified empirically (Clock, default options, 2 s sleep between construction and
first wait):

```
first wait(1.0)  took 0.000s   # event already past due -> fires instantly
second wait(1.0) took 0.980s   # timing_policy=0.98 compresses every wait by 2%
```

With the default `timing_policy=0.98`, absorbing 2 s of setup lag takes ~100 s of music running 2%
fast — a subtle, long-lasting rush rather than an obvious burst. With policy near 0 it's an instant
burst of events at the start. Either way, `s = Session()` (soundfont load) + instrument setup means
every scamp piece starts behind schedule.

Note the clock's *beat* position is not affected — beats only advance through waits — it's purely
the wall anchor `_start_time` that goes stale. That's why the fix is confined to the scheduler.

## Recommendation (corrected 2026-07-19): anchor on the first event that requires *waiting*

First, a fact that the original version of this note got wrong: `Scheduler.time` is **not**
wall-derived — it's `_ideal_time`, event-quantized (scheduler.py:204-212), bumped to each event's
scheduled t as it executes (`_execute_event`, scheduler.py:660). It does not advance between
events. The mismatch introduced by setup time is purely between `_ideal_time` and the *wall*
anchor `_start_time` — i.e. artificial `lag()`.

A naive "anchor at STEP 1b when `_start_time is None`" does **nothing** (Marc caught this):
`Clock.__init__` immediately schedules an initial-wake event at `scheduler.time` (= `_ideal_time`
= 0) for the wakeup handshake (clock.py:328), so the queue is non-empty within microseconds of
thread start and the anchor would land at construction time anyway.

The correct rule distinguishes *immediate* events from events that require waiting:

- `run()` stops setting `_start_time` / `_last_event_time` (leave both None;
  `projected_time` already handles `_last_event_time is None`, scheduler.py:234, and
  `wall_time()`/`lag()` already guard `_start_time` None).
- In the run loop, while `_start_time is None`: if `next_event.t <= _ideal_time` (an immediate
  event — the master's initial wake at t=0, any child's initial wake forked during setup, also
  t=0 since pre-anchor `_ideal_time` never moves), fire it with `wait_duration = 0` and **don't**
  plant the anchor.
- The first event with `t > _ideal_time` — i.e. the first event anyone actually has to wait for,
  which is exactly "the first wait" in 0.6.x terms — plants the anchor via `_reanchor_timing(now)`
  (`_start_time = now - _ideal_time`, `_last_event_time = now`; scheduler.py:557) before its wait
  is computed. An event at t=1.0 then targets `now + 1.0`: a full wait.

Trace of the scamp case: `Session()` → initial wake t=0 fires, no anchor → 2s of soundfont
loading → `wait(1)` schedules event at t=1 > 0 → anchor plants now → full 1s wait. Works equally
if a forked child's wait, rather than the master's, is the first real wait.

Implementation wrinkles to handle: the debug trace `_log_event_timing` reads `_start_time`
unguarded (scheduler.py:625); fast-forward entered pre-anchor would hit `_reanchor_timing` via
`_end_fast_forward_if_active` (fine — it just plants the anchor — but verify). An explicit
`schedule_action(t=future)` during setup plants the anchor at scheduling — defensible: asking to
wait for an absolute time is what starts the clock.

## Retracted (2026-07-19): the "idle gaps re-stale the anchor" concern

The original note worried that after the first anchor, an idle/REPL gap between waits recreates
the problem and might warrant re-anchoring on every empty→non-empty queue transition. Marc's
pushback is correct and the concern is withdrawn: once anchored, a gap between waits is just
wall-clock **lag**, mechanically identical to a callback overrunning its wait, and absorbing it
(or not) at the rate set by `timing_policy` is that policy's *documented job*, not staleness.
0.6.x behaved the same way once started — its absolute target was `_start_time + self.time() + dt`
(old clock.py:1022), also past-due after a gap, with the same policy-clamped catch-up (and a
"running behind" warning). The lazy anchor was only ever about the *first* wait, where nothing
has happened yet and there is no schedule to be behind. If REPL users ever want "forgive the gap,
resume from here," that's an explicit-resync feature (fast-forward's `_reanchor_timing` is the
primitive), not something the scheduler should infer.

## Status

Implemented 2026-07-19 in `scheduler.py` exactly as described above (run() no longer anchors;
pre-anchor immediate events fire with zero wait and skip the `_last_event_time` update so
`projected_time` doesn't climb during setup; first event with `t > _ideal_time` plants the anchor
via `_reanchor_timing`). Regression tests in `tests/test_lazy_anchor.py`. Verified: clockblocks
suite 145/145 (and new tests at 10x compression), scamp golden examples 30/30, manual repro shows
first wait now runs full-length after 2s of setup.

## Related

- `2026-07-07-scheduler-timing-policy-drift.md` — how the policy clamp interacts with being behind
  schedule; that mechanism is what turns a stale anchor into a 2%-fast rush.
