# Stashed: notehead-run version of `_apply_octave_lines`

Saved 2026-08-24 before switching to the sustain-based timeline model (Marc preferred a long
octave line to sustain through its whole duration and absorb shorter shifted notes underneath,
even where that contradicts Gould's "line ends at the last notehead"). This version instead ends
each run at its last notehead, which fragments a long 8va when shorter differently-shifted notes
poke in between its noteheads. Kept here in case we want to compare or revert.

```python
        for staff in voices_by_staff.values():
            for voice_notes in staff.values():
                voice_notes.sort(key=lambda n: n.start_beat)

            # group the shifted noteheads into runs -- a run is a maximal contiguous stretch of same-shift
            # noteheads (each starting before the run's sound has ended). an octave line is drawn over a run from
            # its first notehead to its last, so a note whose onset falls in that span sits under the line. a run
            # ends at its last notehead, not the end of a note held past it -- so a note during a held final shifted
            # note is left alone, while one between two noteheads of a run is caught. (tie-split continuations aren't
            # real noteheads, so they don't define runs; they inherit their shift below.)
            shifted = sorted((note for voice_notes in staff.values() for note in voice_notes
                              if note.properties.octave_displacement != 0 and not note.properties.ends_tie),
                             key=lambda n: n.start_beat)
            runs = []  # (first_onset, last_onset, displacement)
            run_sound_end = None  # running end of the current run's sound, for spotting a gap to the next notehead
            for note in shifted:
                d = note.properties.octave_displacement
                if runs and runs[-1][2] == d and not beat_is_after(note.start_beat, run_sound_end):
                    runs[-1] = (runs[-1][0], note.start_beat, d)
                    run_sound_end = max(run_sound_end, note.end_beat)
                else:
                    runs.append((note.start_beat, note.start_beat, d))
                    run_sound_end = note.end_beat

            def in_run(first, last, beat):  # closed onset-span: [first notehead, last notehead]
                return not beat_is_after(first, beat) and not beat_is_before(last, beat)

            staff_notes = []
            clobbered = 0
            for voice_notes in staff.values():
                previous = 0
                for note in voice_notes:  # sorted by start_beat above
                    if note.properties.ends_tie:
                        winner = previous
                    else:
                        active = [d for first, last, d in runs if in_run(first, last, note.start_beat)]
                        winner = max(active, key=lambda d: (abs(d), d)) if active else 0
                        if winner != note.properties.octave_displacement:
                            clobbered += 1
                    note.properties.octave_displacement = winner
                    previous = winner
                    staff_notes.append(note)
            if clobbered:
                logging.warning("{} note(s) on a staff fall under an octave line from another voice and have been "
                                "displaced along with it.".format(clobbered))

            staff_notes.sort(key=lambda n: (n.start_beat, n.end_beat))
            for displacement, group in groupby(staff_notes, key=lambda n: n.properties.octave_displacement):
                if displacement != 0:
                    run = list(group)
                    run[0].properties.spanners.append(_StartOctaveLine(octaves=displacement))
                    run[-1].properties.spanners.append(_StopOctaveLine())
```
