# Verovio clef misplacement: use one `<divisions>` for the whole score

Fixed 2026-09-27 (pymusicxml). Found via `examples/Time & clocks/Advanced/metric_phase.py`: in verovio,
the piano's clef changes rendered partway through a measure instead of at the barline.

## Root cause (verovio, not us)

pymusicxml used to compute `<divisions>` (units per quarter) independently for each measure of each part. When
divisions is **not uniform**, verovio mis-anchors a mid-system clef change, drawing the small change-clef
partway through the *previous* measure. The MusicXML is valid — MuseScore renders it fine. Checked against
verovio 6.2.1.

Two independent ways to make divisions non-uniform, both trigger it (established by bisection):
- **across parts** in the same measure (a dense part at `divisions=24` beside a sparse one at `divisions=1`);
- **across measures** within one part (metric_phase m1 is `divisions=8`, m2 is `divisions=24`).

The second was the one that actually bit metric_phase, and the first (per-column) fix missed it. Note density
is a red herring; it only matters because dense vs sparse content *leads to* different divisions.

Mechanism inside verovio not confirmed from its source — the symptom (a start-of-measure clef anchored to a
too-early, fractional timestamp landing in the prior measure) is consistent with a divisions-scaling desync in
its timestamp math, but that's a hypothesis. Upstream issue filed with a minimal repro (link once opened).
Different verovio quirk from the octave-shift one in `2026-08-25-octave-line-musicxml.md`.

## The fix

`Score._unified_divisions()` returns the LCM of every measure's need across the whole score, and that one value
is handed to every measure via a `render(divisions=...)` parameter (Measure/Part/PartGroup/Score). Uniform
divisions everywhere covers both triggers.
- `Measure._needed_divisions()` — a measure's own minimal exact grid (extracted from `render`).
- Passed as an argument, **not** stashed state. An earlier draft set a `_forced_divisions` attribute on measures
  and cleared it in a `finally`; an argument is cleaner because `Part.render` deepcopies and the value rides into
  the copy naturally.

Effect: one exact grid for the score. Integers get bigger; rhythms unchanged (verified: every duration scales by
an exact integer factor). Largest divisions across all example goldens is 840.

## Why global, and why no cap

This is what real exporters do — MuseScore emits a single `divisions=27720` for a score with 5-, 7-, 9- and
11-tuplets and doesn't blink. Non-uniform divisions is the unusual, undertested path in verovio precisely because
the convention is to declare divisions once (it carries over between measures when omitted).

Rejected an early cap on the LCM (coprime-tuplet blowup fears):
- MusicXML has **no** max on divisions — `positive-divisions` is `xs:decimal` restricted only to `> 0` (W3C 4.0
  reference). The one number the spec mentions is a *soft* `16383` ceiling "if maximum compatibility with Standard
  MIDI 1.0 files is important" — a MusicXML→SMF converter concern.
- That MIDI note doesn't touch us: scamp writes MIDI directly with `midiutil` (`Performance.export_to_midi_file`,
  ~960 ticks/quarter), never from the MusicXML divisions. The two export paths are independent.
- Capping couldn't stay exact anyway (the shared value must be a multiple of each measure's own grid, so clamping
  would round notes). Not worth it; real LCMs stay small.
