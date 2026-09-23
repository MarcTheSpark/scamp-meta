# extract_absolute_tempo_envelope hangs on tempo-function followers

*2026-07-21. Fixed in clockblocks (uncommitted, on top of v1.1.0).*

## Symptom

`multipleTempos.py` (OldTutorialExamples): `stop_transcribing(perf).quantized().to_score().show()`
hung forever when the performance was recorded on the **clarinet clock**
(`apply_tempo_function(lambda t: 60 + 30*sin(t), duration_units="time")`), while the
**flute clock** (`apply_tempo_envelope(..., loop=True)`) worked. Ctrl-C traceback landed in
`_extend_follow_function` → `TempoEnvelope.from_function`.

## Root cause: a receding horizon, introduced by the 1.0 architecture move

`Clock.extract_absolute_tempo_envelope()` (used by scamp when building a Score from a child
clock's perspective) deepcopies the tempo histories and steps them forward:

```python
while any(th.beat < th.length() for th in tempo_histories): ...advance...
```

This loop is **unchanged from 0.6.x** — what changed is where follow-extension lives. In
0.6.x, `advance()` just integrated over the frozen envelope (extension happened separately,
in the clock's wait machinery), so a deepcopied history was inert and the loop terminated at
its fixed `length()`. In 1.0, extension moved *into* `TempoHistory.time_at_beat`/`beat_at_time`
(auto-extend on demand) — so every `advance()` near the end grew `th.length()` by another
`extension_increment`, and the loop chased the horizon forever (repro reached beat ~6000
before being killed; each iteration pays a full `from_function` segment-fit, so it's also
quadratically slow, not just unbounded).

Notes:
- The extension **bookkeeping is correct** (verified: `current_end_beat` tracks `length()`
  exactly) — first suspicion, wrong.
- The envelope-loop (flute) case does terminate pre-fix. Initial theory — that it only works
  when loop length is commensurate with the 0.05 sampling step — was **disproven** by a test
  with loop length 2.97, which passed pre-fix. Only the function-follower path hangs.

## Fix (as shipped — note the variant change)

In `extract_absolute_tempo_envelope`, right after the deepcopies, freeze each copy. Two
variants existed:

- What was originally written/tested: `th.follow_func_or_envelope_loop = None` — extraction
  covers whatever the clock happened to have *materialized* (includes follower lookahead;
  stops short if a clock outran its finite envelope).
- **What Marc changed it to before committing (56887c5), and what shipped in v1.1.1:**
  `th.stop_follow_function_or_envelope_loop()` — additionally truncates/`extend_to`s the copy
  at its committed beat, so extraction covers **exactly the span the clock actually
  traversed**. Semantically better: no dependence on incidental lookahead, and finite-target
  clocks that keep playing past their envelope get the flat tail included.

Beware: 56887c5's commit *message* still argues against the method-call variant ("would
truncate away materialized lookahead") — it describes the rejected alternative, not the code.

Golden fallout (2026-07-22): `Tutorial/11_record_on_clock` — extracted envelope now completes
the ritardando to exactly tempo 50 and covers the full 8.5 recorded beats (old: stopped at
the envelope's materialized end, beat 7, mid-glide at 49.97). Regenerated the golden;
MusicXML structurally identical except one `<forward>` fill duration (final tempo-mark
anchor). Verified the truncate variant against the full clockblocks suite (147/147) — that
run hadn't happened pre-commit, since the variant swap came during review.

Regression tests: `clockblocks/tests/test_extract_tempo_envelope.py` (daemon-thread timeout
guard so a regression fails instead of hanging the suite; verified to fail pre-fix).
Verified: repro terminates, full multipleTempos scenario builds scores on both clocks,
suite 147/147 at 10x compression.

## Performance measurements (Marc asked whether extensions slow down over time)

They don't. Benchmarked extending a sine tempo function to 30k beats in 3k-beat chunks:
~3.0s per chunk, flat, from 6,666 segments up to 66,632. Per-extension cost is constant
(dominated by `from_function` curve-fitting a fixed window); segment lookup is bisection.
The original hang was constant-cost iterations x unbounded count — the earlier "quadratically
slow" characterization in conversation was wrong.

Long-running function-following clocks: per-wait CPU stays flat (committed pointer sits near
the envelope's end -> 0.06ms short integrations even at 66k segments); memory grows forever
(~2.2 segments/beat for the sine case, tens of MB per million beats) — same in 0.6.x,
inherent to TempoHistory keeping full history for TimeStamps.

**Quirk found while benchmarking**: `Envelope.integrate_interval` does
`self.segments[self._get_index_of_segment_at(t1):]` (envelope.py:830) — the slice copies the
tail of the list, so short integrations near the *start* of a 66k-segment history cost 1.74ms
vs 0.06ms near the end. Only matters for integrate-from-origin operations against long
histories (old TimeStamps, go_to_beat(0), the extraction sweep). Cheap future fix in
expenvelope: iterate by index instead of slicing.
