# scamp_tutor stumbling blocks file

**Context:** The AI tutor (scamp_tutor/) was making SCAMP mistakes: assumed
play_note is non-blocking (blocking=True is the default), and created a drum
part with `s.new_part("drums")` (the kits are named "Standard"/"Power"/
"TR 808"/"Jazz"/"Orchestra" in bank 128). Marc's running list of observed AI
failures is in `scamp_tutor/aisux.txt`.

**What we did (2026-07-15):** Created `scamp_tutor/stumblingBlocks.txt` — a
category-organized list of non-obvious API facts for the tutor AI, covering
blocking/timing, play_note argument traps (list = gliss not chord; `length`
not `duration`), preset name matching, envelopes, and notation.

**How the preset trap list was produced (rerun if Merlin.sf2 or the matcher
changes):** replicated `get_best_preset_match_for_name` from
`scamp/src/scamp/_soundfont_host.py` (same `_preset_name_substitutions` +
`get_average_square_correlation`) against preset names parsed straight out of
Merlin.sf2's `phdr` chunk (sf2utils wasn't installed; a 20-line struct parse
of the phdr records works fine: 38-byte records, 20-byte name, then
uint16 preset, uint16 bank). Ran ~100 common instrument-name queries and
recorded which ones silently matched a wrong preset (e.g. "bass" → Brass,
"snare" → Seashore, "acoustic guitar" → Acoustic Bass, "upright bass" →
Brightness) and which fell below the 1.0 score threshold and fell back to
piano ("percussion", "kick", "clap", "rhodes", "brush").

**Other findings worth remembering:**
- Current clockblocks fork() does NOT inject the child clock as the forked
  function's first argument (legacy behavior, still shown in old tutorial
  videos) — the tutor should teach `current_clock()` instead.
- `set_tempo_target(100, 9)` (bare number) is rejected now; needs
  `Moment.after_beats(9)`. Another legacy-training-data trap.
- Merlin.sf2 has no bass clarinet, contrabassoon, or brush kit — nearby
  substitutes match without warning.

**Wiring (done same day):** instructions.txt now has a REQUIRED READING
paragraph pointing at stumblingBlocks.txt (read up front, not fetch-on-demand)
and a new "BE BRIEF" bullet at the top of HOW TO TEACH (Marc found the tutor
wordy). index.html's "What's here" list also mentions it. The regeneration
scripts don't need changes — stumblingBlocks.txt is a standalone file in
scamp_tutor/, not generated from any bundle source. Note the file must be
uploaded to scamp.marcevanstein.com/aitutor/ along with the rest, and
aisux.txt's "fix robots.txt" item is server-side (can't be done from this
repo).
