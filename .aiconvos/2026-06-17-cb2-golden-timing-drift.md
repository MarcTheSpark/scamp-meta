# cb2 → scamp golden-output timing drift (~1e-12, accumulating)

Context: Step 10 SCAMP integration. After pointing scamp's `__init__.py` at cb2 and migrating the
test scripts to Moment-based tempo args, `scamp/test/test_examples.py` shows widespread tiny diffs in
recorded `start_beat` / `length` / Envelope segment durations. Marc asked to track down the source
before regenerating goldens.

**Not caused by the Moment migration.** The diffs appear in pieces with zero tempo calls — e.g.
`AssortedHaphazard/chords_example.py` runs at constant rate 1, yet `change_pitch(..., (1/3,1/3,1/3))`
records as `(0.33333333333303017, 0.3333333333327271, ...)` instead of exact thirds.

## Causal chain (verified)

1. **Design change (cb2 Step 7).** cb2's `TimeStamp` stores *only* `scheduler_time` + the master and
   reconstructs per-clock beats lazily. Its own docstring (`time_stamp.py:43-44`) says it "needs no
   cached per-clock beat snapshots (as we did in the original clockblocks)." The **original clockblocks
   captured the committed beat directly**, so an exact value (1/3) stayed exact.
2. **The transcriber reconstructs every beat from timestamps.** Note start/end and *every*
   parameter-change segment start/end go `transcriber._resolve_time_stamp` → `TimeStamp.beat_in_clock`
   → `Clock.scheduler_to_clock_time(..., "beats")` (`clock.py:1050`).
3. **That ends in an iterative inverse.** `scheduler_to_clock_time` → `tempo_history.beat_at_time(t)`,
   which inverts the tempo integral via `get_upper_integration_bound(..., max_error=1e-12)`
   (`tempo_envelope.py:530-532`). That solver (`expenvelope/.../envelope.py:821-858`) is a **bisection**
   that stops once the bracket width ≤ `max_error` — so each reconstructed beat carries up to ~1e-12.
4. **Differences + running sums amplify.** Durations are differences of two reconstructed beats; absolute
   `start_beat` is a running sum of prior lengths, so the drift *grows* across a piece (oboe example:
   ~1e-13 at beat 0 → ~1e-11 by beat 7). Constant-tempo pieces drift too: the inversion runs regardless
   of rate — a rate-1 clock still bisects instead of using the exact linear inverse.

The `0` → `0.0` MusicXML `<alter>` diffs are unrelated cosmetic float-formatting churn from the redesign.

## Decision

The drift is ≤1e-11 — musically inaudible, rounded away by quantization. It's exactly the "minor output
diffs (timing precision)" the PLAN's Step 10 anticipated. Plan: **regenerate goldens** (`-s`) and review.

## Possible precision fix (deferred — its own step)

Analytic inverse for constant-`beat_length` segments. `time_at_beat` already has the forward simplified
path (linear integral); the *inverse* (`beat_at_time`) currently always root-finds, even when each
segment's integrand is constant and the inverse is exactly linear (`beat = beat0 + (t - t0)/beat_length`).
Short-circuiting constant segments makes all constant-tempo reconstruction exact (killing drift for the
majority of pieces) and skips the bisection on the hot path. Exponential segments still need the solver
but could get a tighter `max_error`. More work, touches the hot path — not done here.

Considered and rejected: just tightening `max_error` (1e-12 → 1e-14) globally. Shrinks but doesn't
eliminate the noise, won't make thirds exact, and changing the expenvelope default affects everything.

## Outcome — analytic inverse implemented (2026-06-17)

Built into **expenvelope**, not cb2 (benefits the whole stack, right home):
- `EnvelopeSegment.get_t_at_integral(t1, desired_area, max_error)` — per-segment inverse. Constant and
  linear segments solved analytically (√-rationalized quadratic, no cancellation, degrades to the
  constant case); exponential via guarded Newton (g(τ)=A·τ+B·e^(Sτ), g'=level≥0, monotonic → unique).
  Chose **Newton over Lambert-W** to avoid a scipy dependency / complex-branch handling.
- `Envelope.get_upper_integration_bound` rewritten to walk segments, subtracting closed-form areas until
  the remainder lands in one segment, then invert that segment. Before/after the envelope = flat
  (start/end level), matching `integrate_interval`. **Bug found in review:** the flat-tail return must
  measure from `t` (= end_time after a full walk, or t1 if t1 already started past the end), not from
  `end_time()` — otherwise t1-beyond-end cases returned a point *before* t1.

Verified: round-trip `integrate_interval(t1, result)` vs requested area — constant/linear ~1e-15 (exact),
exponential ~1e-12 (the requested max_error). Thirds on a constant envelope come back **bit-exact**. All
139 cb2 tests pass (the inverse is on cb2's `beat_at_time` hot path).

Golden-test impact (drift 1e-11 → 1e-13/1e-15): **16 scripts now match Performance/Score/MusicXML/LilyPond
exactly** and fail *only* on the binary MIDIFile; 6 tempo-curve scripts (bananaphone, double_score,
9_multi_part, 11_record_on_clock, 24_osc, 29_spanners) keep ~1e-13 Performance noise (bananaphone larger:
curved 60→300 tempo → exponential-Newton floor + accumulation).

## Secondary finding: MIDIFile-only diffs trace to `Performance.tempo_envelope`

`Performance.__repr__` (performance.py:1318) serializes only `self.parts`, **not** `self.tempo_envelope`.
So the "Performance" golden can pass while the MIDI bytes differ, because `export_to_midi_file` places
events using `tempo_envelope`, which is built by **sampling** in `Clock.extract_absolute_tempo_envelope`
(step_size 0.1, tolerance 0.001) — inherently not bit-stable across the engine change. This is a separate,
sampled-approximation source, not the integral-inverse drift. Goldens get regenerated regardless; if
bit-stability of constant-tempo MIDI matters, the master-clock branch of `extract_absolute_tempo_envelope`
(returns `tempo_history.as_tempo_envelope()` directly) is worth checking for exactness as a follow-up.
