# Compound-beat tuplet crash (AssignabilityError / "does not resolve to single note type")

Found while tracking down a crash in `scamp/examples/Assorted/evolving_form/evolving_form.py`
(`s.stop_transcribing().to_score(bar_line_locations=bar_lines, max_divisor=14).show()`).

## Symptom
```
abjad.exceptions.AssignabilityError: not assignable duration: Duration(numerator=1, denominator=24).
```
The MusicXML path fails identically: `ValueError: Duration length of 1/6 does not resolve to
single note type.` So the bug is **not** abjad-specific — the `Score` model itself already holds an
invalid `Tuplet`. Both `_abjad_facade.create_note` and `pymusicxml` merely surface it.

## Root cause (confirmed)
Longstanding (division-point logic dates to 2019 commit 3af3687), not a modernization regression.

Trigger: a **compound-length beat** (1.5 quarters — the second beat of 5/8, the beats of 3/8, etc.,
which arise here because `do_bar_line` can emit non-integer bar lengths → odd-over-8 meters) quantized
with a **divisor that produces a genuine tuplet** whose divisions don't line up with the beat's natural
metric hierarchy.

Mechanism, for a 1.5-beat divided by 5 (`score.py:_process_and_convert_beat`):
- `Tuplet.from_length_and_divisor(1.5, 5)` → **5:3** tuplet (5 eighths in the space of 3), `divlen=0.5`,
  dilation 5/3.
- The divisor is then multiplied (`divisor *= numerator(length/divisor)`) 5 → **15**, giving an atomic
  `written_division_length = 1.5/15 · 5/3 = 1/6` quarter. Each tuplet-eighth = 3 atomic units.
- `_get_beat_division_hierarchy(1.5, 15)` groups the 15-grid as **"3*5" (3 groups of 5)** — because 3 is
  the natural factor of the 1.5 beat length, it's forced to the front. Strong-beat grid points land at
  0, 5, 10 — **not** at the tuplet's division boundaries 0, 3, 6, 9, 12.
- So a note exactly one tuplet-eighth long (atomic units 0→3) has an endpoint (3) that sits on no grid
  line. `_get_division_points_for_note(0, 3, …)` can't reach a grid point and falls back to
  `_length_to_undotted_constituents(3) = [2, 1]`, splitting it into **2 + 1 atomic units = 1/3 + 1/6
  quarter** — neither is an assignable duration → crash.

The hierarchy *should* be "5*3" (5 groups of 3) to match the 5:3 tuplet, which would put a grid point at
3 and notate the note as a single eighth.

3-smooth divisors (2,3,4,6,8,9,12) are fine: they either return `None` from
`from_length_and_divisor` (duple denom in compound time → dotted rhythm, no tuplet — this is why div 8
survives) or their divisions happen to align. Divisors with a prime factor ≥5 (5,7,10,11,…) crash.

## Minimal reproduction (no soundfont/MIDI/OSC needed)
```python
from scamp import Session
s = Session(); s.fast_forward()
inst = s.new_silent_part('t')
s.start_transcribing()
inst.play_note(60, 0.8, 1.0, blocking=False); s.wait(1.0)   # fill beat 1 of the 5/8 bar
for _ in range(5):                                            # clean quintuplet across the 1.5 beat
    inst.play_note(60, 0.8, 0.3, blocking=False); s.wait(0.3)
s.stop_transcribing().to_score(bar_line_locations=[2.5], max_divisor=14).to_music_xml()
# -> ValueError: Duration length of 1/3 does not resolve to single note type.
```
Divisor sweep on a 1.5 beat: 5,7,10,11 FAIL; 2,3,4,6,8,9,12 OK.

## Root cause is deeper than the hierarchy: the grid multiply is spurious for tuplets
The `divisor *= numerator(beat_length/divisor)` line in `_process_and_convert_beat` refines the atomic
grid so the *grain* is an undotted note. That's only needed when there's **no tuplet** (e.g. 1.5 into 8 →
3/16 grain → ×3 → 1/16). Inside a genuine tuplet the **dilation factor already makes the grain a plain
note** (verified: every tuplet on a 1.5 beat has grain = beat_length/normal_divisions = 1/2^n quarter).
So multiplying there is redundant — and it's exactly the divisors that multiply by >1 (5,7,10,11,13,14)
that crashed; the ×1 case (9) never did.

## Fix (chosen: skip the multiply for tuplets)
```python
if tuplet is None:
    divisor *= Fraction(beat_quantization.length / divisor).limit_denominator().numerator
```
Nothing else changes: the hierarchy goes back to plain `_get_beat_division_hierarchy` for all cases. For
a genuine tuplet the grid is just the divisor (5 slots, not 15), so a slot's endpoint is always a grid
point and the un-notatable split can't happen.

### Considered and rejected: the hierarchy fix
First attempt kept the ×3 grid but added `_get_tuplet_division_hierarchy` to regroup 15 as "5*3" instead
of "3*5". It works and touched only the crashing path, but it's more code and it keeps a redundant 3×
finer grid. Marc preferred the simpler multiply-skip. The two are **not** output-identical: on a note
spanning several tuplet slots, multiply-skip yields a single **dotted** note while the hierarchy fix
yields **tied** notes. Side-by-side PDFs (a rhythm-only quintuplet study) confirmed multiply-skip is the
cleaner engraving — see `.claudeConvos/tuplet-fix-pdfs/` (`A_hierarchy.pdf`, `B_multiply-skip.pdf`,
`builder.py`).

### Note on the golden suite
No example test exercises a compound-beat tuplet (probed: `10_multi_tempo` etc. hit zero), so the suite
gives no signal here and needs no `-s` regen for this change. The 3 failures currently showing
(`bananaphone`, `11_record_on_clock`, `10_multi_tempo`) are a **separate** in-progress effort on XML
tempo spanners, unrelated to this fix.

Verified: minimal repro OK; full `evolving_form.py` exports via both MusicXML and abjad.
