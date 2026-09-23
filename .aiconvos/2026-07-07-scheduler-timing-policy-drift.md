# Scheduler wall-clock drift: timing_policy as a per-wait clamp

Distinct from `2026-06-17-cb2-golden-timing-drift.md` (float precision, ~1e-12, in *beat reconstruction*).
This is **wall-clock** drift in the scheduler's wait computation — milliseconds, accumulating without bound.

## Symptom

`clockblocks/tests/fork_t2.py` (master at tempo 30→60, child forked at `initial_rate=2`) printed a `wall`
that crept steadily past `time`: `.503, .503, .504, .504, .505, …`, growing forever. Roughly 0.2–1 ms per
beat. Two prior commits had each tried to fix drift and neither had.

## Two bugs, stacked

**Bug 1 (fixed in `b1fbd12`).** `_last_wake_time` was captured *after* `event.action()` ran, so every
relative wait measured its baseline from callback-return rather than from when the event actually fired.
Callback runtime folded into the wait baseline. Necessary fix, but not sufficient.

**Bug 2 (the real one).** `9269ff6` redefined `timing_policy` as a per-wait clamp, but expressed the band as
**durations measured from the current `now`**:

```python
relative_wait_dur = last_wake + (next_event.t - ideal_time) - now
lo = timing_policy * relative_wait_dur     # compression floor
wait_duration = min(hi, max(lo, absolute_wait_dur))
```

In coarse mode (the default, `precise_timing=False`) the run loop does **not** wait once and fire. STEP 1c
does `if wait_duration > 0: wait(timeout); continue` — so `_compute_wait_duration` is re-entered *many times
per event*, each pass on the shrinking remaining wait. `relative_wait_dur` shrinks toward 0, and the floor
`lo = 0.98 * relative_wait_dur` shrinks with it, chasing the remainder down. The event ends up firing when
`relative_wait_dur <= 0` — i.e. exactly `nominal_gap` after the *previous firing*. **Pure relative timing.**
OS wake jitter baked into `_last_wake_time` every beat, accumulating forever.

### The sharp way to see it (worth keeping)

It isn't "the OS wait returned early." **The catch-up sets the timeout short on purpose, then the re-clamp
sleeps the shaved-off time right back.** Walk one behind-schedule beat, behind by δ:

- `absolute_wait_dur ≈ gap − δ`, `relative_wait_dur ≈ gap`. Since `gap − δ > 0.98·gap`, the clamp picks
  `wait = gap − δ`. Good — we deliberately shorten the wait to claw back δ.
- That wait returns *on time*, after `gap − δ`. But the relative deadline is at `gap`, so we've landed δ
  short of it. Recompute: `relative_wait_dur = δ > 0`, `absolute_wait_dur ≈ 0`, floor `lo = 0.98·δ > 0`.
  So it **re-sleeps that δ** instead of firing at the absolute target it had just reached.

An honest on-time return from a catch-up wait is *guaranteed* to land before the relative deadline — that's
what "catch up" means — and the next pass reads "before the relative deadline" as "wait more." Geometric
re-sleeps converge back to `last_event + gap`. The correction only survived on beats where OS jitter happened
to overshoot *past* the relative deadline (tripping the `relative_wait_dur <= 0` guard). Hence a *creep*
rather than an explosion.

Ratchet detail: on a beat that is exactly on schedule, no compression is even attempted
(`absolute == relative == gap`), so that beat's overshoot pushes you δ behind with zero correction — and the
*next* beat's catch-up gets swallowed by the cascade above.

## Fix

Frame everything as **fixed target instants**, independent of `now`; subtract `now` once at the end.

```python
ideal_wait_duration = next_event.t - self._ideal_time     # no `now` in it -> stable across re-waits
absolute_target = self._start_time + next_event.t
lo = self._last_event_time + ideal_wait_duration * self.timing_policy
hi = self._last_event_time + ideal_wait_duration / self.timing_policy   # inf if policy == 0
target = min(hi, max(lo, absolute_target))
wait_duration = target - now
```

Coarse re-waits now all count down to the *same* instant instead of a receding `0.98 × (whatever's left)`.
Algebraically identical on the first pass (`now ≈ _last_event_time`), so single-shot precise-timing behavior
is unchanged; the only behavioral difference is exactly the one we wanted.

Also renamed `_last_wake_time` → `_last_event_time` (it's the instant the last event *fired*, not a wake).

**The old `relative_wait_dur <= 0` guard does not survive as `ideal_wait_duration <= 0`.** They aren't the
same condition — the old one folded in `now` and so caught "callback overran its own wait"; the new one is
purely the scheduled gap. That's fine: an overrunning callback needs no special case, because its `target`
lands in the past and `wait_duration` goes negative, so the run loop fires it immediately. The remaining
guard covers only a genuinely degenerate *schedule* (event booked at/before ideal time), where the band
would be trivial or inverted (`hi < lo` once `gap < 0`).

## Result

`fork_t2` wall now tracks time to sub-ms with no accumulation over 24 s (beat 23: `wall=24.500`,
`time=24.500`). Occasional 1 ms jitter blips self-correct on the very next beat (visible as a 0.499 interval
right after a 1.001).

## Residual: master runs a flat +1 ms, and that's fundamental

Every master beat coincides with a child event (master period 1.0 s = two 0.5 s child beats). At a shared
instant the heap tiebreaks on priority: `clock.py` sets `_priority = (-len(clock_id), clock_id)`, so **deeper
clocks sort first**. The child fires dead-on; the scheduler then parks in `_execute_event` for the child's
wakeup handshake + user callback (~1 ms); only then does the run loop reach the master, already past due, so
it fires immediately — 1 ms late, every time.

Two consequences worth remembering:
- **`precise_timing` will not help.** The master is already overdue when the child's callback returns; there
  is nothing to spin toward. This is callback serialization, fundamental to one scheduler thread servicing
  coincident events.
- It does **not** accumulate: the next master target is still computed off the absolute grid, so it stays a
  flat 1 ms rather than growing.

## Cross-reference: this bug is already in a benchmark table

`2026-06-08-cb2-precise-timing-step14.md` records, for coarse mode, `timing_policy=0` → median 98 µs error
but `timing_policy=0.98` → **median 2250 µs**, and reads that as "0.98 is ~relative, so it drifts." That
2250 µs *was this bug* — 0.98 was collapsing to fully relative, not behaving as a 2 %-per-wait catch-up.
**Untested prediction:** re-running that benchmark now should bring the 0.98 coarse row close to the
absolute row. Worth doing before anyone cites that table again.

## Test fallout

Four tests failed on the committed code; the fix repaired one (`test_timing_policy_half`, which had been
passing/failing on numeric coincidence). The remaining three had gone stale at `b1fbd12`.

Both `test_clock.py` and `test_scheduler.py` computed `expected` from the **old linear blend**
(`relative*policy + absolute*(1-policy)`). Note that **blend and clamp agree exactly at `policy=0`** (both
reduce to pure absolute) — which is why the two `absolute` tests kept passing and masked the staleness.
`test_clock` additionally assumed the relative wait restarts *after* the callback, the very thing `b1fbd12`
removed.

**Trap.** Simply correcting `test_clock`'s expected numbers would have produced a *vacuous* test: with its
scenario (`work=0.25` < `wait=0.3`) nothing ever falls behind, the policy never engages, and all three
policies yield identical times `[0.3, 0.6, 0.9, 1.2]` — it would pass under any policy. The scenario needs a
genuine **overrun** (`work > wait`). Changed to `waits=[0.5]×5, work=[0, 0.9, 0, 0, 0]`, giving

| policy | expected wake times | behavior |
|---|---|---|
| 0.0 | `0.5, 1.0, 1.9, 2.0, 2.5` | snaps back to grid immediately |
| 0.5 | `0.5, 1.0, 1.9, 2.15, 2.5` | compresses to the 0.25 s floor, rejoins grid by step 4 |
| 1.0 | `0.5, 1.0, 1.9, 2.4, 2.9` | never recovers; permanently 0.4 s behind |

`test_scheduler`'s scenario already had an overrun (0.4 s action vs 0.15 s gap), so only its expected values
changed: policies separate at `event_3` (`0.6 / 0.725 / 0.85`).

Before writing either, the clamp semantics were re-derived as a pure-arithmetic model and checked against the
real clock and scheduler across all three policies — agreement < 2 ms. That model is the thing to rebuild if
these numbers ever need re-deriving.

### Judgment calls

- **Literals, not a model reimplemented in the test.** A test that recomputes `_compute_wait_duration` is
  tautological — it silently tracks any future change to the algorithm. Literals + a derivation comment force
  the semantics to be stated and reviewed. Cost: the comment has to stay honest.
- **Rejected: shrinking the new scenario for runtime.** (`0.3` waits / `0.55` work) tightens the
  policy separations to ~0.1 against the 0.08 tolerance. Kept the slower scenario; +4.4 s on a ~58 s suite.
- The `EXPECTED` comment explicitly records *why* the overrun is load-bearing, so a later "simplification"
  doesn't quietly re-vacuum the test.

## Verification state

- clockblocks: **135/135** unittest, and **9/9** under `CLOCKBLOCKS_TEST_COMPRESSION=10` for the two touched
  modules (the new separations survive the widened tolerance).
- scamp golden examples: **29/30**. The single failure is `Test/clock_order.py`, and it is **pre-existing and
  unrelated** — verified by re-running against the committed clockblocks. The only diff is the first two
  entries, `'A, 0'`/`'B, 0'` (golden, int) vs `'A, 0.0'`/`'B, 0.0'` (now, float): a `c.time()` repr change at
  beat 0 predating this work. All 200 beat values and their interleaving match exactly — good evidence the
  timing rewrite doesn't perturb scamp output. That golden wants regenerating with `-s`, separately.
