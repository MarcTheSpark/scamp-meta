# Octave lines: MusicXML rendering model

The `octave_displacement` note property draws `<octave-shift>` brackets. Bugs found by eyeballing
MuseScore/Verovio against `examples/Assorted/{octava,voices_with_ottava}.py`; fixed 2026-08-25/26.

## 1. Don't displace the pitch (the big one)

Original code (score.py `NoteLike.to_music_xml`) subtracted `12 * octave_displacement` from the written
pitch, on the theory that `<pitch>` holds the *notated* pitch and `<octave-shift>` restores the sounding
octave. **That's the wrong model.** MuseScore and Verovio treat `<pitch>` as the **sounding** pitch and use
`<octave-shift>` to move the *display* (and draw the bracket). Pre-shifting double-counts → an 8va note
renders an octave too low.

Fix: always write the sounding pitch; emit the direction and let the renderer displace. The abjad/LilyPond
path already did this (`\ottava` + untouched pitch), so both backends are now consistent.

The `type="down"` (for 8va-alta / positive octaves) / `type="up"` mapping in pymusicxml was already correct
for the sounding-pitch model — only the scamp-side pitch subtraction was wrong.

## 2. Octave lines are placed as measure-level directions, not attached to notes

An octave line is **staff-wide**: after the sustain-timeline merge, displacement is one value per beat across
the whole staff, and a single line can cover notes in several voices (a shorter voice gets clobbered up/down
into the winning line). MusicXML writes a measure **one voice at a time** (all of voice 1, `<backup>`, all of
voice 2, ...), so a line anchored to notes gets its start/stop scattered across the voice serialization —
producing orphaned stops and never-closed starts (`down8 stop8 down15 stop15 stop15 up8 stop8 down15`).

Fix: emit octave lines the way **tempo marks** already are — as measure-level
`pymusicxml.Measure.directions_with_displacements`, positioned by beat, which pymusicxml lays out with
`<backup>`/`<forward>` independent of any voice.

- `StaffGroup._apply_octave_lines` returns per-staff runs `(start_beat, end_beat, displacement)` (keyed by
  staff index = lane // mvs); `from_quantized_performance_part` hands each run list to `Staff.octave_lines`.
- `Score.to_music_xml` has a pass (right after the tempo pass, same shape) that drops a `StartOctaveLine` at
  each run's start beat and a `StopOctaveLine` at its end beat into the right measure. A stop landing exactly
  on the closing barline belongs to that measure (offset == measure length).
- `NoteLike.to_music_xml` **skips** `Start/StopOctaveLine` spanners (they'd re-emit the broken note-anchored
  way).

The two backends deliberately differ, mirroring how tempo is handled: MusicXML serializes voices (needs
beat-positioned directions); LilyPond's parallel/moment model makes note-anchored `\ottava` correct as-is. So
`_apply_octave_lines` still tags the run's boundary notes with `_Start/_StopOctaveLine` spanners — those are
now read **only** by the abjad export.

Number pairing: `directions_with_displacements` is included in `Measure.iter_directions()`, so
`_validate_spanner_numbers` pairs the start/stop `number=` automatically. Consecutive runs on a staff never
overlap, so reusing number 1 is fine.

## Not fixed (out of scope / pre-existing)

`_validate_spanner_numbers` pairs start/stop by *input label* with FIFO pop; two lines overlapping in the same
part with the same input label would mispair. The merge collapses overlapping shifts on a staff into one line,
so this isn't hit.

## Verovio rendering — parked 2026-09-01 (low priority, not a bug in SCAMP)

The `octave_lines.py` output looks scrambled in **verovio** specifically. Root cause is verovio-side: it only
anchors `<octave-shift>` to a note via `@startid/@endid` and won't time-anchor a direction that rides the
note-less `<backup>`/`<forward>` stream we emit — so within-measure lines get dropped and cross-bar ones warn
+ misplace. The MusicXML is valid; **MuseScore renders it fine**. Related open verovio issue:
[#4393](https://github.com/rism-digital/verovio/issues/4393) (direction-only backup/forward streams). No
matching issue for our exact symptom — could file one with a minimal repro if we ever care.

**Dorico and MuseScore both render it fine** (checked 2026-09-01). So it's verovio-only; render these examples'
SVGs from MuseScore. Decision: leave the code as-is, not worth reworking.

If we ever revisit (we probably won't): the note-less stream can't be fixed cleanly. Note-anchoring hits the
2→1 voice-serialization scramble (that's *why* we moved to the stream). A dedicated hidden-rest carrier voice
works in verovio but costs a 5th voice — MuseScore caps at 4, so no. Least-bad path is anchor each line
endpoint to the containing voice-1 note/rest edge — BUT first the runs must be reconciled to *tile* instead of
overlap (they overlap today when an outer line ties across an inner stronger line from another voice, e.g.
runs `(0,4,1),(3,5,2),(4,8,1)`), else voice-1 snapping balloons the inner line. All of that is more work than
the feature is worth right now.
