# Looping tempo envelope read the old cycle's tempo at the loop seam

*2026-08-01. Found via the `notation_issues.py` repro while restoring SC-2. Shipped in clockblocks 1.2.0.*

## Symptom

With a looping `TempoEnvelope` (`apply_tempo_envelope(..., loop=True)`), a note landing
*exactly* on the loop point read the **previous** cycle's final tempo instead of the new
cycle's. In `notation_issues.py` (levels 160/100/130/70 as instantaneous jumps, period 4
beats), the note at beat 4.0 came out at the tempo-70 pitch — a spurious 5th note in the
low group — before catching up at 4.25.

## Root cause

`get_rate()` → `TempoHistory.rate` → `beat_length` → `beat_length_at(self._beat)` →
`value_at(beat, from_left=False)`. The `from_left=False` reads the segment to the *right*
of the current beat — which is correct and is exactly why an *internal* jump reads the
post-jump tempo (beat 1.0 already returned the new plateau).

The loop, though, only materializes the next cycle lazily. The auto-extend guard in
`tempo_envelope.py` (`time_at_beat` / `beat_at_time`) was:

```python
while self.follow_func_or_envelope_loop.current_end_beat < beat:   # and current_end_time < t
```

At the seam, `current_end_beat == beat` (e.g. 4), so `4 < 4` is False → the next cycle
wasn't appended yet → the envelope simply *ended* at beat 4 with the old value, and the
read-from-the-right clamped to that. Internal jumps worked because their right-hand segment
already existed; only the loop re-append lagged by one query.

## Fix

`<` → `<=` in both guards. Now a query landing exactly on the seam extends the loop first,
so the zero-length seam jump (old→new tempo) is in place and the right-side read returns the
new cycle. Consistent with how internal discontinuities already behaved.

**Why `<=` can't over-extend:** each extension bumps `current_end_beat`/`current_end_time`
by a full period, so `8 <= 4` is False after one step — it materializes exactly one extra
cycle and stops. No infinite loop, no unbounded growth. Verified: repro clean, clockblocks
suite 178 pass / 5 skip, scamp examples 30/30.

## Related

- Repro preserved at `scamp/bugs/resolved/notation_issues.py`. Its docstring also documents
  the *other* bug that file surfaced: duplicate metronome marks at beat 0, fixed separately
  in scamp `score.py` by deduping coincident tempo key points
  (`sorted(set([0.0] + local_extrema(...)))`). That one is a notation-stage bug, unrelated
  to this clock-timing one — they just happened to share a repro.
- Shipped in clockblocks 1.2.0 (commit "Materialize the next loop cycle at the seam, not
  just past it"); scamp 0.12.0 carries the score.py dedupe.
