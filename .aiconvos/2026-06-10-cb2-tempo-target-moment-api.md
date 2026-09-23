# cb2 tempo-target API: Moments, mixed axes, looping

Topic: the `Clock.set_*_target(s)` / `apply_tempo_envelope` surface in cb2 (clockblocks 1.0).
Builds on the Step 10.5 work in PLAN.md.

## Where we landed

- `set_*_target(target, when, curve_shape=0, truncate=True)` — `when` is a `ResolvableMoment`
  (a `Moment` or a `MetricPhaseTarget`). No `duration`/`duration_units`/`metric_phase_target`; the
  Moment carries its own beats-vs-time axis. Bare numbers rejected (`allow_number=False`).
- `set_*_targets(targets, whens, curve_shapes=None, truncate=True)` — **no `loop`**. Builds the
  curve left-to-right via `_apply_targets`, and **may freely mix beats- and time-axis whens in one
  call**.
- `apply_tempo_envelope(envelope, truncate=True, loop=False)` — the home for looping. Thin wrapper
  over `TempoHistory.append_envelope`.
- `MetricPhaseTarget` gained `min_duration`: `resolve()` returns the nearest match ≥ `now +
  min_duration` (default 0 = old behavior).

## Why mixed axes actually work (the key insight)

Originally `_whens_to_durations` raised if the whens didn't share an axis, because you can't *order*
a beat-value against a time-value up front without the curve. Marc's idea: build left-to-right —
each segment resolves against the curve built so far, so the beat↔time mapping you need always
exists by the time you need it.

It turned out to be nearly free because `TempoEnvelope._add_segment` **already** does the whole job
per segment: it treats `duration` as cumulative-from-now *in its own `duration_units`*, recomputes
how far the curve already extends each call, and **already raises `ValueError` if a target doesn't
extend beyond the last one** (the backwards-guard, in both beats and time domains — the time branch
inverts the integral exactly like a singular time-axis `set_tempo_target`). So `_apply_targets` is
just: resolve each `when` against the live clock → dispatch to the singular `TempoHistory` setter
with that segment's own axis, `truncate` only on the first. We re-raise `_add_segment`'s ValueError
with the offending index.

`tempo_modification` (the TempoEnvelope decorator) only clears two caches, so calling the singular
setter N times in a loop is safe/cheap. The clock-level `@_reschedule_after_tempo_change` fires once
(on the plural method), not per segment.

## Decisions / rejected alternatives

- **whens resolve against *now*, not the previous segment's end.** `after_beats(5)` = beat now+5,
  cumulative-from-now (matches TempoHistory's plural `durations` semantics). One clean invariant for
  the whole list. Consequence: a non-final `MetricPhaseTarget` means "next match *from now*", which
  can land before a prior segment → ValueError. The escape hatch is exactly `min_duration` (that's
  *why* we added it). Considered resolving phase targets against the cursor instead — rejected
  because it breaks the single invariant for marginal intuitiveness.

- **Looping pulled out of `set_*_targets` entirely.** A looped multi-segment call with mixed/time
  intent would have to silently rebase to a beats-domain loop (once solved, segments *are*
  beats-domain) — awkward and surprising. A `TempoEnvelope` is unambiguously beats→tempo (under the
  hood a beat-length curve), so `apply_tempo_envelope(env, loop=True)` is the honest place for it and
  sidesteps the axis question. `apply_*_function` keeps its own `loop` (it's a different mechanism —
  function-following with `domain_start/end` + a `duration_units` axis selector).

- **`min_duration` semantics:** inclusive (a match exactly at `now + min_duration` counts), validated
  ≥ 0. Implemented by shifting `resolve()`'s search center from `now` to `now + min_duration` — the
  "nearest future match" machinery was already there.

## Lower layers left untouched

`TempoHistory`/`TempoEnvelope` keep their numeric-duration `set_*_target(s)` (still accept
`metric_phase_target`, single `duration_units`, `loop`). cb2's clock no longer calls the plural
TempoHistory setters — only the singular ones, in a loop — but they remain valid public low-level
API. Don't "simplify" them to match the clock surface; that's deliberate separation.

## Tests

- `test_moment.py`: `min_duration` pushes match forward (0→0, +4→4, +5→8), repr + negative
  validation.
- `test_module_api.py`: mixed beats/time 3-segment curve (asserts the time-axis middle segment
  consumed exactly 5 s via `integrate_interval`), backwards-when names index `#1`, metric-phase +
  `min_duration`, looping `apply_tempo_envelope` set/stop, off-thread raise for all 17 forwarders.

105 tests pass.

---

## `align_to` — solving curvature to land on a phase/coordinate (2026-06-12)

Reintroduced the capability we'd dropped: pin one endpoint axis with `when`, constrain the *other*
(free) axis with `align_to`, and let the **curvature be solved** instead of given. Headline use:
`set_tempo_target(130, Moment.after_time(20), align_to=MetricPhaseTarget(0, divisor=4))` =
"accelerate to 130 over 20 s, curvature chosen so it lands on a downbeat."

### The mental model that drove the design

A segment's endpoint has 3 linked quantities — end_beat, end_time, curvature (one integral equation →
2 DOF). `when` pins one; then either `curve_shape` pins curvature (free axis falls out) **or**
`align_to` pins the free axis (curvature falls out). So `curve_shape` and `align_to` are the two ways
to spend the last DOF.

- **Fixed `align_to` (plain Moment, e.g. `at_beat(40)`)**: both coords pinned → curvature *determined*
  → `curve_shape` is meaningless. If explicitly set alongside, we `warnings.warn` and discard it.
- **Phase `align_to` (MetricPhaseTarget)**: the free coord is a *set* of candidates, so many
  (landing, curvature) pairs work → `curve_shape` is a live **seed** (it picks which matching phase is
  nearest, then curvature is re-solved to hit it). No warning.

### Single vs group (why we staged it)

- **Single segment**: 1 curvature unknown, unique solve. This is what landed 2026-06-12.
- **Group** (a run of segments, `align_to` only at the end → bend the whole run to collectively land):
  N unknowns, 1 constraint → underdetermined → needs a **distribution policy**. That policy already
  exists and is decided: `_adjust_segments_time_duration` (hold the run's beats, push total time to the
  goal, distribute the delta weighted by each segment's `get_integral_range` wiggle room, feasibility
  check + rollback). The complexity that made the old code "suuuper complicated" was *not* this core —
  it was the stateful inline grouping inside the monolithic `set_beat_length_targets`. The new
  left-to-right builder dissolves that. **Group is the next step, not yet implemented.**

### Axis handling — `MetricPhaseTarget.units` now optional

`align_to`'s axis is always the *free* axis (opposite `when`), never user data. So:
- A plain `Moment` carries an intrinsic axis → validated (same-axis as `when` → `ValueError`).
- A `MetricPhaseTarget` is axis-agnostic in spirit → its `units` now defaults to **`None` = infer**.
  In the `when`/`resolve` role `None` means beats (musical default); in the `align_to` role it means
  the free axis. An *explicit* `units` that conflicts with the free axis → `ValueError`.

### What landed 2026-06-12 (single-segment only)

- `MetricPhaseTarget.units` → optional (`None`=infer); `resolve()` treats None as beats.
- `TempoHistory`: `_add_segment`'s `metric_phase_target` → `alignment_target` (None | MetricPhaseTarget
  on free axis | fixed free-axis number). Named `alignment_target` (not `align`) so it (a) doesn't read
  as a boolean flag and (b) stays visually distinct from the clock-level `align_to`, which takes a
  ResolvableMoment rather than a resolved free-axis coordinate. Two single-segment solvers generalized to candidate-coordinate form
  (`_solve_segment_end_time` / `_solve_segment_end_beat`). `set_beat_length_target` snapshots
  `self.segments` and **restores on any failure** (alignment ValueErrors are now atomic — the old code
  silently `logging.warning`'d and left the curve unaligned). `set_rate/tempo_target` forward `align`.
- `Clock.set_*_target(..., curve_shape=None, align_to=None)` via new `_resolve_align_to` helper
  (resolves when+align, validates axis, warns on fixed+curve_shape, normalizes curve_shape None→0).
  Module forwarders updated.
- Dead `TempoHistory.set_*_targets` (plural) left in place for now — a MetricPhaseTarget passed
  positionally as `align` reproduces the old behavior, so they still function; they'll be removed when
  the group rewrite replaces them.

### Group / plural `align_to` (Step 15, done 2026-06-14)

- `Clock.set_*_targets(..., align_to=None)`: single ResolvableMoment = align whole call as one run;
  per-segment list with `None`s = a non-None entry closes/bends the run since the last alignment.
  Grouping driven from `_apply_targets` (builds segments incrementally with `align_to=None`, accumulates
  run indices + when-axes, validates single-axis on close, resolves the target on the run's free axis,
  calls `_align_run`). Whole build atomic via a call-level `deepcopy` snapshot.
- **Key decision: a new `TempoHistory._align_run(run_segments, alignment_target, free_axis)` that operates
  on a *known segment slice* (the tail of `self.segments`), NOT the ported `adjust_*_at_beat/time`
  family.** Those located the affected segments by inverting an absolute beat/time coordinate, and the
  BEATS-free pair (`adjust_beat_at_time`/`adjust_metric_phase_at_time`) called a nonexistent
  `get_beat_wait_from_time_wait` (dead since the port). Because `_apply_targets` already knows exactly
  which segments form the run, `_align_run` needs no time→beat inversion — sidestepping the missing method
  entirely. TIME-free (run pins beats) holds beats + pushes time via `_adjust_segments_time_duration`;
  BEATS-free (run pins time) proportionally stretches beats onto each candidate end-beat then re-solves
  curvature to preserve the run's total time (restoring between candidate attempts).
- Run-length-1 is just `_align_run` over a one-element slice (equivalent to the singular
  `_solve_segment_end_*`), so no special-casing — except the fixed-align curve_shape warning, which
  `_resolve_align_to` issues only when `warn_on_fixed_curve_shape=(run_len==1)`.
- **Helper responsibilities (refactored 2026-06-14):** split the old bundled `_resolve_align` into two
  honestly-named, single-responsibility helpers, both shared by the singular setters and `_apply_targets`:
  `_resolve_when(when) -> (duration, pinned_axis)` (the pinned axis is a *product* of resolving `when` — a
  `units=None` MetricPhaseTarget infers beats — not a field readable off the input) and
  `_resolve_align_to(align_to, pinned_axis, curve_shape, warn_on_fixed_curve_shape=True) ->
  (alignment_target, curve_shape)` (derives the free axis = `pinned_axis.opposite`, validates/infers the
  axis, normalizes/discards curve_shape). Added `DurationUnits.opposite` to kill the repeated free-axis
  ternary. The group path ignores `_resolve_align_to`'s returned curve_shape (seeds already baked into the
  built segments; `_align_run` re-solves curvature). `_resolve_when` reads "now" live each call — no
  batched now_beat/now_time needed even in the plural loop, because `beat()`/`time()` derive from the
  scheduler's committed `_ideal_time`, which only advances on event execution, and the scheduler is
  parked (owning-thread call) or quiescent (external call) for the whole tempo-setting call, so the
  reference can't drift between iterations (verified).
- **Cleanup:** deleted the dead monolithic `TempoHistory.set_*_targets` plural block, all four
  `adjust_*_at_*` methods, `MetricPhaseTarget.interpret` (orphaned tuple-coercion), and the now-unused
  `Tuple`/`logging` imports. Kept `_adjust_segments_time_duration` (used by `_align_run`).

115 tests pass.
