# cb2 Step 14 — precise event timing (perf_counter basis + guard-band spin)

Prototyped 2026-06-08. PLAN.md Step 14 written up first; this captures the findings from the prototype + benchmark.

## What was built

- **14a** — switched the scheduler's timing basis from `time.time()` (CLOCK_REALTIME — NTP-slewable, can step backwards) to `time.perf_counter()` everywhere (`_start_time`, `now`, `_last_wake_time`, `wall_time()`). Same clock the spin uses, so sleep + bookkeeping share one monotonic domain.
- **14b** — master-only `precise_timing` flag (default off) + `spin_guard_duration` (default 500µs). In STEP 1c: coarse-wait on the condition to `deadline - spin_guard_duration`, `continue`, converge, then **release the lock** and busy-spin `while perf_counter() < spin_deadline`. `spin_deadline = now + wait_duration` (= the intended wake time). Lock released during spin so queue mutators run freely; the line-365 head-recheck in `_execute_event` keeps it correct.

## Benchmark findings (this Linux box, no RT priority)

Measured |fire_time − scheduled_time| over 60 events 20ms apart.

| policy | coarse | precise (500µs guard) |
|---|---|---|
| absolute (`timing_policy=0`) | median 98µs / max 221µs | **median 11µs / max 27µs** |
| default (`timing_policy=0.98`, ~relative) | median 2250µs / max 3400µs | median 283µs / max 421µs |

Key takeaways:

1. **The spin itself is essentially perfect** — instrumented spin-*exit* overshoot was median **0.3µs**, max 0.7µs. The spin nails its deadline.
2. **The residual under default policy is relative-timing accumulation — but that's the WRONG metric for relative mode.** This table measured |fire − *absolute* target|. Under relative timing each target is anchored to the previous *actual* wake, so the tiny per-spin residuals (all slightly late) accumulate into the absolute number (60 × ~5µs ≈ 283µs). But absolute drift is exactly what relative mode chooses to give up; measuring it under-credits the spin.
   - **Correction (the "pairs best with absolute" claim was wrong).** A follow-up benchmark measuring *inter-event intervals* under relative policy: coarse median 112µs / max 297µs → **precise median 16µs / max 281µs** (~7×), and it shrank absolute drift too (4830→724µs over 80 events). So `w_i = w_{i-1} + Δt_i + jitter_i` ⇒ interval error = `jitter_i`, which the spin kills. **precise_timing helps under any timing_policy** — it tightens whatever that policy optimizes: the duration of each individual wait under relative, the absolute schedule under absolute. Docs/docstrings corrected accordingly.
3. **The spin floor is OS preemption latency, not zero.** A bigger guard didn't help (it slightly hurt) — proof the limit isn't coarse-wait overshoot. Even a pure spin lands ~10µs+ because the spinning thread gets time-sliced near the deadline. To go lower needs `SCHED_FIFO` / CPU isolation (the PLAN's deferred RT-scheduling note). 500µs default guard is fine — it only needs to exceed coarse-wait jitter, and the spin-exit precision doesn't depend on it.

## Bug found & fixed (adjacent) — worse than just docs

`timing_policy` convention is **0 = absolute, 1 = relative** — confirmed three ways: the formula
(`tp*relative + (1-tp)*absolute`), the original clockblocks (`clock.py:123`), and cb2's own tests
(`test_timing_policy_relative`→1.0, `test_timing_policy_absolute`→0.0). cb2 had it inverted in two places:

- `Scheduler.__init__` docstring said "0 → Relative, 1 → Absolute" — just wrong docs.
- **`clock.py` was a functional bug**: `use_absolute_timing_policy()` set `1.0` (= relative!) and
  `use_relative_timing_policy()` set `0.0` (= absolute!). The two convenience methods did the *opposite*
  of their names. Plus the `timing_policy` property docstring + the ValueError message were inverted.

All fixed. No test pinned the (wrong) `use_*` values, so the behavior change is safe. This inversion is
also what made the *first* benchmark misleading (`timing_policy=1.0` thinking it was absolute → ~620µs);
corrected to `0.0` the real 11µs emerged.

## ClockFamilyOptions refactor (the construction-API question)

Decided: the advanced family-level knobs don't belong as individual `Clock`/`Session` constructor args
(five master-only knobs almost no one touches, crowding `initial_tempo`). They're really *family*
settings the Clock only proxies. So:

- New frozen `ClockFamilyOptions` dataclass: `timing_policy`, `precise_timing`, `spin_guard_duration`,
  `pool_size`, `prewarm_pool`, with `__post_init__` validation. Canonical defaults live here (mirror
  `Scheduler`'s arg defaults).
- **Naming + home.** First built as `SchedulerOptions` in `scheduler.py`, but that was a misnomer: the
  bundle also carries the fork *thread-pool* knobs (`pool_size`/`prewarm_pool`), a master/family concern not
  the scheduler's — and `scheduler.py` only *defined* the class, never used it (`Scheduler.__init__` takes
  the individual args). Considered splitting into `SchedulerOptions` (timing) + a pool bundle, but rejected
  it: two constructor args re-clutter what the bundle was meant to declutter, and it leaks an internal seam
  (scheduler vs pool) the caller doesn't care about — there's one door (`Clock`) and one thing happening
  ("configure my master's family"). So: **one flat bundle named for the family, moved to `clock.py`** (where
  the constructor and the pool live; no circular import since the scheduler doesn't reference it). Kwarg
  renamed `scheduler_options` → `clock_family_options` to match.
- `Clock.__init__` slimmed to `name, parent, initial_rate/tempo/beat_length, clock_family_options=None`.
  Removed the old `timing_policy` / `pool_size` / `prewarm_pool` kwargs. `clock_family_options` on a child
  raises `NotMasterClockError`.
- Live knobs also adjustable post-construction via master-only properties: `timing_policy` (existing),
  plus new `precise_timing` / `spin_guard_duration`. `pool_size`/`prewarm_pool` stay bundle-only (pool built once).
- Confirmed **zero** SCAMP examples/tutorials/scamp_extensions pass `timing_policy` to a constructor (the
  only refs are autodoc `.rst`), so demoting it from a top-level kwarg to property + bundle is free.
- Migrated `test_thread_pool.py` constructions to `ClockFamilyOptions(...)`. All 95 tests still pass.

## Constraint for Step 11 (mock-time tests)

`tests/mock_time.py` compresses time by `@patch("time.time", ...)` + `@patch("threading.Event", ...)`. The scheduler now reads **`time.perf_counter`** and waits on a **`Condition`**, neither of which those patches touch. So mock compression no longer reaches the scheduler. Nothing breaks today only because `test_clock.py`'s one mock test `break`s early with its assertions commented out. When Step 11 builds real mock-based scheduler timing tests, mock_time must patch `time.perf_counter` (and the condition/its timeout), not `time.time`.

## Status

**Committed: 9115fe2** (Step 14 writeup was 8effa3b). Includes the perf_counter switch, precise_timing +
spin_guard_duration with the branch-on-mode STEP 1c, the ClockFamilyOptions bundle (in clock.py),
Clock-level `precise_timing`/`spin_guard_duration` master-only properties, the timing_policy inversion fix,
and `from __future__ import annotations` in clock.py (needed once the signature went to PEP 604 `Clock | None`).
All 95 tests pass. Default is precise_timing=off.

Still open: the Step-11 mock-time fix above (patch `perf_counter`/the condition, not `time.time`), and real
tests for precise timing (spin engages only inside the guard band; queue-change wakes us during the coarse
phase; deadline hit within tolerance).
