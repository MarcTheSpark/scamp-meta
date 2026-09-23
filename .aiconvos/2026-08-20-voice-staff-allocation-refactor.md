# Voice / staff allocation refactor

Prompted by the octave-line work: octave shifts are staff-wide, but the current pipeline
assigns staves *per measure*, after octave processing, so it can't reason about staff
membership when it needs to. The deeper issue is that voice→rendered-voice→staff assignment
is unstable: a musical line can jump rendered-voice number or staff between measures for no
musical reason. Fixing allocation is a prerequisite for finishing octave lines. Octave work
is parked in `../octave-feature.patch` (re-applies against 862aeab; its `score.py` hunks will
need manual reintegration once this refactor lands).

## Current system (score.py, StaffGroup)

`from_quantized_performance_part`:
1. `_separate_voices_into_fragments(part)` — for each source voice (dict keyed by name:
   "1"/"2"… numbered, or arbitrary names), walk notes and split into **fragments** wherever a
   **rest coincides with a measure break** (rest exactly at a barline). Each fragment →
   `_construct_voice_fragment` → per-measure note lists, tagged as:
   - `_NumberedVoiceFragment(voice_num, start_measure, measures_with_quantizations)` if the
     source voice name is an int, or
   - `_NamedVoiceFragment(average_pitch, start_measure, measures_with_quantizations)` otherwise.
2. `_create_measure_voice_grid(fragments)` builds `grid[measure][lane] = (notes, quant) | None`:
   - **numbered** fragments → placed at a FIXED lane = `voice_num - 1` (never compacted).
   - **named** fragments → sorted by (start_measure, -pitch, -len), then greedily placed in the
     lowest lane free across the fragment's whole measure range (interval-graph coloring).
3. `_from_measure_voice_grid(grid)` → `num_staffs = ceil(max lanes in any measure /
   max_voices_per_part)`; per measure, chunk lanes into groups of `max_voices_per_part`;
   **staff of a lane = lane // max_voices_per_part** (stable, since a fragment keeps one lane).

### Problems
- **Split rule too fine.** A rest at a barline splits a phrase into fragments; independently
  colored fragments then land in different lanes/staves → a single line jumps around. The user
  wants a line to move only at a *true* break.
- **Named source-voice identity is lost.** Only average_pitch survives, so a named voice's later
  fragment won't return to its earlier lane even when free.
- **Octave/staff ordering.** Staff membership only exists after step 3, but octave processing
  (staff-wide displacement + conflict detection) runs in step 1.

## Proposed system (Marc's description)

Everything is known up front (quantization + measure sizes), so do allocation globally:

1. **Split each source voice into pieces** at any **completely silent measure** (a measure in
   which the voice has no sounding note). A piece = maximal run of consecutive active measures.
   Coarser than today's rest-at-barline rule → phrases stay whole; a piece can only ever change
   lane/staff at a real gap.
2. **Allocate pieces to rendered-voice lanes** by walking the part start→finish and giving each
   piece the first lane not occupied during its measure span, opening a new lane when all are
   occupied (greedy interval coloring — same idea already used for named voices, now for all).
3. **Lanes → staves** by `lane // max_voices_per_part`, as today.

Keeping the `grid[measure][lane]` contract identical means `_from_measure_voice_grid` and
everything downstream are untouched; the change is confined to the split rule + allocation.

### Stability refinement
When a source voice has several pieces (separated by gaps), prefer to reuse the lane it last
occupied if free, so it only moves when genuinely forced. Only named-voice pitch / numbered-voice
number is used for tie-breaking when several pieces compete for lanes at the same moment.

## Decisions (settled with Marc, 2026-08-20)
- **Numbered voice = "put me in this exact lane."** lane = number-1; staff = (number-1) //
  max_voices_per_part. So voice 5 = first lane of staff 2, voice 6 = second, etc. (= today's
  fixed-lane behavior, now the stated contract).
- **Named voices → lowest available lane**, greedy, no stability memory. Consistency beyond that
  is what numbered voices are for. (Kills the piece-stability question.)
- **Overflow from a self-overlapping numbered voice** should become a named voice and flow into
  available space (Marc's own proposal, and already the intent of quantization's "1_2" renaming).

## What's actually already correct (surprise finding)
The allocation Marc described is *already implemented* and already global/up-front: numbered ->
fixed lane, named -> greedy lowest-free, staff = lane // max_voices_per_part (stable per fragment).
So the instability is NOT the allocation. Two real causes:

1. **Split rule too eager.** `_separate_voices_into_fragments` splits at any rest touching a
   barline, so one line becomes several fragments that color into different lanes/staves. Fix:
   split only at a **fully silent measure** (compute per-measure activity; pieces = runs of active
   measures).
2. **`int("1_2") == 12` bug.** `_construct_voice_fragment` classifies with `int(voice_name)`, but
   Python reads underscores as digit separators, so the overflow voice "1_2" becomes *numbered 12*
   (pinned to lane 11 / staff 2) instead of a named voice. Fix: classify with `voice_name.isdigit()`.

## Implementation (this session)
- (A) rewrite split rule to fully-silent-measure in `_separate_voices_into_fragments`.
- (B) classify numbered voices with `.isdigit()` in `_construct_voice_fragment`.
- `_create_measure_voice_grid` / `_from_measure_voice_grid` unchanged (grid contract preserved).

## Test impact
Golden example outputs for multi-voice pieces will likely change (that's the point). Baseline is
31/31 green at 862aeab. Regenerate with `test_examples.py -s` and review diffs as improvements
vs regressions.

## Outcome (2026-08-20)
Implemented (A) + (B) in score.py. Only ONE golden example changed: Tutorial/24_osc_to_supercollider
(blocking=False overlaps + max_voices_per_part=1 -- the exact target scenario). Verified:
- **Note conservation**: all 13 source onsets preserved through the new split (nothing dropped).
- **Clear improvement**: OLD had the busy line jump staff 2 (m1-3) -> staff 1 (m4-5) and the sparse
  line split across staves 1 and 0; NEW keeps the busy line on staff 1 for all of m0-5 and the
  sparse line on staff 2 for m1-3. Each line stays on one staff, per the goal. Every measure's
  pitch multiset is identical old->new.
Regenerated 24's reference JSON (uncommitted); suite back to 31/31. Nothing committed.
Still parked: ../octave-feature.patch -- reapply AFTER this lands; its score.py hunks will need
manual reintegration against the rewritten _separate_voices_into_fragments.

## Two-pass lane allocation (2026-08-20)
The single `(start_measure, -pitch, -len)` sort conflated two orthogonal priorities that live on
different axes: which *staff* a fragment lands in (wants time -- earlier music on top) vs its *lane
within a shared staff* (wants pitch -- higher voice = lower lane = stems up). Pitch won everywhere,
so shared staves were right (ex16) but separate staves put the first-entering line below the top
(seed-0 / ex24). They only conflict *within* a staff; across staves lane parity is irrelevant.

Rewrote `_create_measure_voice_grid` as two passes:
- Pass 1 (staff by time): first-fit interval coloring of named fragments into unit lanes, seeded
  with numbered voices at their fixed lanes, sorted by (start_measure, entry_beat, -avg_pitch);
  staff = lane // mvp. First-fit by entry time is optimal, so staff count stays minimal, and
  grouping by // mvp guarantees <= mvp overlap within a staff (what pass 2 needs to fit).
- Pass 2 (lane by pitch within staff): per staff, numbered voices keep their fixed slot; named
  voices, pitch-sorted, take the lowest free local lane (per-measure routing around numbered ones).
  `entry_beat` = earliest note's start_beat, derived from measures_with_quantizations (no struct
  change). `assert local_lane < mvp` encodes the pass-1 feasibility guarantee.

Outcome: only ex24 changed (target scenario, mvp=1) -- earliest-entering line now on the top staff,
25-note multiset conserved. ex16 byte-identical (shared staff, pass 2 reproduces old pitch order).
Synthetic check: numbered voice 6 pins to lane 5 (staff1 slot1), named hi/mid/lo flow into staff0
lanes 0/1/2 by pitch. Suite 31/31, ex24 reference regenerated. Nothing committed.

## Meaningful voices vs artifacts (2026-08-20)
Follow-up problem (random_walk_fragments, seed 4, mvp=2): a long "spine" fragment got bumped to
voice 2 by a brief higher fragment early in its span and, since a fragment holds ONE lane for its
whole life, stayed voice 2 for ~10 measures -- so later lower fragments sat in voice 1 beneath it
(pitch inversion). Root insight (Marc): those voices aren't meaningful -- they're all the greedy
`_separate_into_non_overlapping_voices` decomposition of one self-overlapping `_unspecified_` soup,
unlike a voice the user deliberately named.

So classify each quantized voice by how deliberate it is (`_classify_voice`), returning tier +
whether it may split by measure:
- tier 1 numbered ("1"): keep whole, pinned to a fixed lane.
- tier 2 named ("melody"): keep whole (deliberate phrase, only breaks at a silent measure).
- tier 3 overage ("melody_2", "1_2"): split by measure -- a notation artifact.
- tier 4 unspecified ("_unspecified_" AND its "_..._2" overage): split by measure.
Overage is detected structurally: a trailing _<k> whose base is also a voice in the part (the
quantization naming pattern), not bare name-parsing.

Split rule for artifacts (tiers 3-4): break at every barline a note does NOT sound across, so
measures re-slot freely; the tie exception (a note crossing the barline) keeps tied measures in one
fragment so ties don't span lanes. Deliberate voices (tiers 1-2) keep the old silent-measure rule.

Priority = plan A: tier leads pass 2 (lane within staff) only -- `sorted(..., key=(tier, -pitch,
-len))` -- so a named voice takes voice 1 over artifacts sharing its staff. Pass 1 (staff by time)
is untouched, so staff count stays minimal. (Plan B -- tier also steering staff -- deferred.)

Also: quantization now warns when a user named/numbered voice self-overlaps and must be split
(quantization.py, `logging.warning`, once per source voice; silent for `_unspecified_`).

Fix confirmed: seed-4 m8 now voice1=[69,79,84]/voice2=[63,70] (pitch-correct). Suite: only
bananaphone changed (unspecified overage at mvp=1) -- 39-note multiset conserved, no ties broken,
staff count stable; reference regenerated. ex16 + ex24 byte-identical. Warning verified to fire for
named "melody"/numbered "1" and stay silent for unspecified. Suite 31/31. Nothing committed.

## Committed + per-measure pitch reorder flag (2026-08-21/22)

### Committed to `scamp` main
- **089c7e5** "Assign voice/staff layout up front for stabler multi-voice notation" -- the whole
  up-front allocation refinement: two-pass `_create_measure_voice_grid` (staff by entry time, then
  lane within staff by priority > length > pitch), voice classification (`_classify_voice`:
  priority 1 named / 2 overage / 3 unspecified, None = numbered), artifact measure-splitting, the
  self-overlap warning, and the length-first base sort. score.py, quantization.py, CHANGELOG.md, +
  regenerated bananaphone.json & 24_osc_to_supercollider.json. (During this commit, caught a bug from
  a rename: pass 2 was sorting `named_fragments` instead of the loop's `staff_fragments`, which
  crashed bananaphone via `assert local_lane < mvp`. Always re-run the suite before committing.)
- **6d2d50a** roadmap item "Separate overlapping voices before quantizing".

### Uncommitted: `engraving_settings.pitch_order_voices_within_measure` (default True)
New flag + `_pitch_order_voices_within_measures(measure_grid, mvp)` + count-aware voice map, added to
settings.py and score.py. When on, per staff per measure: if >1 active voice AND none ties across a
barline, sort by pitch and reassign voice slots via the count-aware map so upper voices are stem-up
(top=voiceOne, bottom=voiceTwo, inner=voiceThree/Four). The tie-safe skip is what keeps it correct --
a reordered measure has no pinned tied lanes, so the count-aware map applies cleanly. Marc then
refactored the map to a dict `voice_pitch_order_by_count = {1:(1,), 2:(1,2), 3:(1,3,2), 4:(1,3,4,2)}`.
Verified: seed-4 m8 now pitch-ordered; tied-run measures keep base order; 3-voice -> v1/v3/v2; flag
off disables; suite 31/31 with zero golden changes (base sort already orders the suite's cases).

## Next up -- 5 tasks Marc queued (investigate-then-implement after a compact)
1. **Validate the `voice_pitch_order_by_count` dict refactor.** Values are correct (count -> voice
   numbers, descending pitch). CAUTION: the dict holds 1-based VOICE NUMBERS; placement needs local
   lane = voice_number - 1 (place at `staff_start + voice_num - 1`). The old helper returned 0-based
   lanes. Verify the current `_pitch_order_voices_within_measures` (score.py ~1595, where it zips
   `active` with `voice_pitch_order_by_count[len(active)]` into a var it calls `local_lane`) subtracts
   1 -- otherwise it's off by one.
2. **Numbered voices must never be reordered (guaranteed pin).** Currently the reorder collects ALL
   non-None cells in a staff-measure, including numbered voices -> can move them off their pinned
   lane. Grid cells don't carry priority, so pass numbered-pinned info in. Simplest fix (matches the
   tie-skip): skip reordering a staff-measure that contains any numbered-pinned lane. Alternative:
   keep numbered lanes fixed and reorder only the others (fiddlier with the count-aware map).
3. **Validate max_voices 1-4.** PARTLY DONE: Marc added a check in `_migrate_settings_dict`
   (settings.py ~727: warns + falls back to default if not int 1-4) -- but that's JSON-load only.
   Runtime assignment (`engraving_settings.max_voices_per_part = 7`) is NOT validated. Need
   `__setattr__` validation (on EngravingSettings or _ScampSettings) or a validate-at-use guard in
   score building. `_voice_names` has only 4 entries, so >4 would IndexError.
4. **Rename `max_voices_per_part` -> `max_voices_per_staff`, back-compat by copying the old key on
   startup.** Usages to update: settings.py (field, docstring, the validation at ~727), score.py
   (~1466, 1506, 1521, 1602, 1609, 1618, 1620-1621), examples (JunkDrawer/bananaphone,
   Assorted/random_walk_fragments, Tutorial/24 osc example), test/example_tests
   (Tutorial/24_osc_to_supercollider.py, AssortedHaphazard/bananaphone.py). Migration: add a rename
   in `_migrate_settings_dict` (map old key -> new). Consider a runtime alias/property so
   `engraving_settings.max_voices_per_part = ...` still works (deprecation), since examples/users use
   it. Update the CHANGELOG (Changed) when done.
5. **Apply ottava** -- reintegrate `../octave-feature.patch` (16KB, workspace root). Its score.py
   hunks predate this whole allocation rewrite, so they need MANUAL reintegration against the current
   `_separate_voices_into_fragments` / `_create_measure_voice_grid` (the octave `_apply_octave_lines`
   step runs pre-staff-assignment). The non-score.py hunks (CHANGELOG, __init__, _parsing,
   note_properties, spanners, examples/Assorted/octava.py) should apply more cleanly. Note:
   note_properties `octave_displacement` merger fix may already be relevant.

Working tree also has unrelated pre-existing changes NOT part of any of this: post_processing.py
(staged), JunkDrawer/bunchStuff.py (M), Assorted/random_walk_fragments.py (untracked demo).

## Session 2026-08-22: tasks 1-4 done, task 5 planned

### Tasks 1 & 2 -- committed (eb688a3 "Add per-measure pitch ordering of voices within a staff")
- **Task 1 (off-by-one) WAS a real crash.** `voice_pitch_order_by_count` holds 1-based voice numbers, but the
  placement did `measure_row[staff_start + local_lane]` with the voice number as a 0-based offset -> IndexError
  (reproduced with random_walk_fragments seed 4, mvp=2). Fixed: `staff_start + voice_number - 1`, and renamed the
  loop var to `voice_number` with a comment. The suite passing "with zero golden changes" earlier had masked it
  because the golden cases happen not to hit a reorder that overflows; the demo does.
- **Task 2 (numbered pin) implemented.** The reorder now receives a `numbered_cells` set of (measure_num, lane)
  pairs (built in `_create_measure_voice_grid` from the numbered fragments' spans) and skips any staff-measure that
  contains one -- same shape as the existing tie-skip. Verified: a numbered voice "2" pitched ABOVE a named voice
  sharing its staff stays pinned at voice2 (would otherwise be promoted to voice1 by pitch). Suite 31/31, no golden
  changes.
- This commit is what "carried the uncommitted flag over the finish line": settings flag + reorder function, now
  correct. CHANGELOG "laid out more stably" entry extended to describe the per-measure reorder + the off switch.

### COMMITTED 2026-08-22 (two commits on top of eb688a3)
- **35f5658** "Refine per-measure voice reordering around pinned voices" -- the reorder rewrite below
  (anchor pins, pack free voices into lowest free lanes by pitch). score.py only.
- **e623628** "Rename max_voices_per_part to max_voices_per_staff" -- the task-4 rename PLUS the internal
  `mvp` -> `mvs` locals, deprecated property alias, JSON `_renamed_fields` migration, 1-4 validation kept.
  score.py, settings.py, CHANGELOG, examples, example_tests. (Committed separately from the reorder via a
  captured patch + `git apply --cached`, since both lived in score.py at once.) NB: doing the earlier
  `git reset --soft` left `examples/Assorted/post_processing.py` UNTRACKED again (it had been staged by Marc,
  pre-existing/unrelated) -- it was not re-added; re-stage if wanted. scamp_extensions/supercollider_example.py
  still carries the rename uncommitted in its own repo (works via the alias).

### Reorder refinement -- anchor pins, reorder the rest (now committed as 35f5658)
eb688a3 skipped a whole staff-measure if ANY voice was numbered or tied. Marc: instead keep the pinned
voices (numbered / tied-across-barline) fixed and reorder only the FREE voices among the FREE lanes by pitch.
Key simplification found while doing it: `voice_pitch_order_by_count` was just encoding the visual top-to-bottom
order of voice numbers on a staff (v1 top, then v3, v4, v2 bottom = the tuple `(1,3,4,2)`). So it collapses to
`voice_visual_order.index(voice_number)` as a lane-rank key. When nothing is pinned this reproduces the old
per-count mapping EXACTLY (proven for counts 2/3/4), so no golden change.

Then Marc pushed further: the first cut only PERMUTED free voices among the lanes they already occupied, so a
free voice stuck in a high lane with a lower lane open never moved (`len(free_lanes)<=1` also skipped a lone
drifted voice). Fixed by thinking in terms of the canonical block: the staff's voices belong in its lowest
the lowest lanes the pins DON'T hold: `available = [lane in staff_lanes if not pinned]`, take the lowest
`len(free_cells)` of them, order those by visual rank, drop cells in by pitch. So a free voice drifted to a high
lane compacts down, and a pin anywhere (even a numbered voice pinned to a high lane) is simply skipped over --
no special-case bail. (An earlier cut used a `target_lanes = range(staff_start, staff_start+len(occupied))`
block model that had to skip when a pin sat outside the block; the "lowest non-pinned lanes" framing removes that
sizing assumption and the skip.) Verified with an 8-case synthetic-grid unit test (all-free / numbered-pin / tie
/ lone-drifted / gappy-free / pin-then-free / high-pin-free-already-low / high-pin-free-drifted) and suite 31/31
(no golden changes -- the suite's allocator
output doesn't drift free voices, but the logic now handles it). Behavioral change beyond eb688a3 -- uncommitted.

Also dropped a vestigial empty-cell guard (`if any(not notes_in(cell)...)`) from the reorder: a fragment only
ever encloses ACTIVE measures (the split loop closes a fragment before any silent measure), and every active
measure gets a note piece in `_construct_voice_fragment`, so a non-None cell always has >=1 note. The nearby
`ties_across_barline` already indexes `notes[0]`/`notes[-1]` with no guard, so the code already assumed this.
(Marc also renamed the reorder's `measure_row` -> `measure_voices_column`.)

### Task 3 -- already done (no work)
`EngravingSettings._validate_attribute` (settings.py) ALREADY validates max_voices to int 1-4, and because
`_ScampSettings.__setattr__` routes through `_validate_attribute`, this covers BOTH runtime assignment and JSON
load (dataclass `__init__` assigns via `__setattr__`). My earlier note that it was "JSON-load only in
_migrate_settings_dict" was wrong. Verified at runtime: 7/0/2.5/"x" -> warn + fall back to 4; 3 -> 3.

### Task 4 -- implemented, STAGED not committed
Renamed `max_voices_per_part` -> `max_voices_per_staff` everywhere (settings.py field/docstring/validation, score.py,
bundled examples, example_tests, gitignored docs .rst). Back-compat:
- **JSON migration**: added a generic `_renamed_fields` class dict on `_ScampSettings` (default `{}`), honored in
  `_migrate_settings_dict` (maps old key -> new, sets rewrite_file). EngravingSettings sets
  `_renamed_fields = {"max_voices_per_part": "max_voices_per_staff"}`. Old-key JSON migrates on load and the file is
  rewritten with the new key.
- **Runtime alias**: a `max_voices_per_part` property (getter+setter) on EngravingSettings that warns
  DeprecationWarning (stacklevel=2, matching clockblocks convention) and proxies to `max_voices_per_staff`. Works
  through the custom `__setattr__` (object.__setattr__ honors the data descriptor; value re-validates under the new
  key). Verified: new name validates; alias read/write warns and works; alias set 9 -> falls back to 4; `_to_dict`
  writes only the new key. Suite 31/31.
- CHANGELOG Changed entry added.
- **Was committed as 8453c18 then SOFT-RESET** (git reset --soft HEAD~1): re-read the ask -- "commit that work"
  attached only to tasks 1&2; tasks 3&4 were "address... implementing if it makes sense" with no commit directive
  (matching the session's commit-A-then-implement-B pattern). So 3&4 sit staged, awaiting an explicit go to commit.

### Task 5 -- octave lines: PREP ONLY (design decision for Marc)
The parked `../octave-feature.patch` does NOT just add octave lines -- it REPLACES the design already shipped in
6aa3e5f. Two mutually exclusive UX models:
- **Model A (committed, 6aa3e5f, Unreleased):** PUBLIC `StartOctaveLine(octaves=1)`/`StopOctaveLine()` spanners the
  user attaches to notes by hand; user also lowers pitch manually. Renders (MusicXML `<octave-shift>` + LilyPond
  `\ottava`).
- **Model B (the patch):** an `"8va"`/`"8vb"`/`"15ma"`... note-property STRING. The pipeline coalesces runs of equal
  displacement into internal (now `_`-private) spanners, drags time-overlapping sibling voices so the whole staff
  shifts, and auto-subtracts `12*displacement` from the WRITTEN MusicXML pitch (LilyPond leaves pitch to `\ottava`).
  Much better UX; the patch's `octave_lines.py` rewrite shows it (`play_note(pitch, 0.5, 0.25, "8va")`).

Model B was written to supersede Model A. Since A is only Unreleased (no shipped API to break), switching to B before
release is clean. **Recommendation: adopt Model B.** Reintegration when green-lit:
- Clean-ish: `_parsing.py` (octave grammar + `_octave_string_to_displacement` + `visit_octaves`),
  `note_properties.py` (add `octave_displacement` field, default 0, chord_merger_critical, merger `p1 if p2==0 else p2`),
  `spanners.py` (rename public octave classes -> `_`-private), `__init__.py` (drop the public octave exports),
  `examples/Assorted/octava.py` (replace with the property-driven version).
- MANUAL: `score.py` -- add imports (`_StartOctaveLine,_StopOctaveLine`, `groupby`), add module fns
  `_apply_octave_lines` / `_overlapping_displacement`, and fold "deepcopy ALL voices up front + call
  `_apply_octave_lines(copied_voices)` + iterate" into the CURRENT `_separate_voices_into_fragments` (which now
  deepcopies per-voice at ~1411 with classify/active/tied logic). Plus the `octave_shift = 12*octave_displacement`
  subtraction hunks in `NoteLike.to_music_xml` (chord / single / two gliss grace-note paths) -- localized, should
  reintegrate with minor context fixes.
- CHANGELOG: REWORD the existing Added octave entry (it currently advertises the public spanner classes) to describe
  the note-property syntax instead.
- BUGS to fix in the patch as-is: `_apply_octave_lines` uses `run[1]` for the stop spanner -- must be `run[-1]`
  (IndexError on a 1-note run, wrong note otherwise). And `octave_lines.py` passes `ottava_mark` that can be `None` inside
  a property list (`["voice:2","staccato",None]`) -- confirm the parser tolerates None or filter it.
- Interaction check: octave displacement only affects WRITTEN pitch at MusicXML export; `average_pitch()` uses
  sounding pitch, so the eb688a3 pitch-order reorder is unaffected. Good.

### Task 5 -- octave lines: APPLIED 2026-08-22 (Model B), NOT yet committed
Reintegrated `../octave-feature.patch` onto e623628. Clean hunks applied via `git apply --include=...`:
`_parsing.py`, `note_properties.py`, `spanners.py`, `__init__.py`, `examples/Assorted/octava.py`. Hand-reintegrated
into the moved `score.py` (imports `_StartOctaveLine/_StopOctaveLine` + `groupby`; module fns `_apply_octave_lines`
/`_overlapping_displacement`; folded "deepcopy ALL voices up front + `_apply_octave_lines(copied_voices)` + iterate"
into the current `_separate_voices_into_fragments`, dropping its per-voice deepcopy; the four `octave_shift =
12*octave_displacement` subtractions in `NoteLike.to_music_xml`).
- Bug 1 (`run[1]`->`run[-1]`): fixed in the reintegrated `_apply_octave_lines`.
- Bug 2 (None in property list): non-issue -- `NoteProperties.interpret` tolerates bare `None` and `None` inside a
  list (verified), so octava.py's `[..., None]` is fine as written.
- CHANGELOG: reworded the Added octave entry to the note-property syntax; dropped `"loco"` (grammar doesn't
  implement it) and updated the `max_voices_per_part` reference to `max_voices_per_staff`. Grammar marks:
  8va/8vb/15ma/15mb/15va/15vb/22ma/22mb.
- Verified: golden suite 31/31; end-to-end MusicXML shows one coalesced `<octave-shift type="down" size="8">` with
  written pitches an octave below sounding; octava.py runs (2 parts) with show() suppressed. All changed files
  byte-compile; no circular import from score->spanners.
- NOT committed (no request to). Working tree also carries pre-existing/unrelated: bunchStuff.py (M),
  post_processing.py + random_walk_fragments.py (untracked).

### Task 5b -- octave lines made staff-aware, 2026-08-22 (NOT committed)
The applied Model B ran `_apply_octave_lines` inside `_separate_voices_into_fragments`, BEFORE staves exist, so it
was part-wide (dragged overlapping siblings across the whole part, even onto other staves) and per-voice (could
draw two overlapping brackets on one staff; groupby-on-note-list could span a rest). Reworked so the octave pass is
staff-aware:
- Split `_create_measure_voice_grid` into `_place_fragments(fragments) -> placements` (the two-pass lane/staff
  assignment) and a slimmed `_create_measure_voice_grid(placements, num_measures)` (grid build + reorder).
  `numbered_cells` now derived from placements (lane == voice_num for numbered).
- `from_quantized_performance_part` runs: separate -> place -> `_apply_octave_lines(placements, mvs)` -> grid.
  Staff membership is `lane // mvs`, stable at that point (the later within-staff reorder never moves across staves).
- `_separate_voices_into_fragments` reverted to per-voice deepcopy (no more copy-all-up-front); octave pass now
  mutates the post-barline-split note pieces that actually render.
- New `_apply_octave_lines`: per staff, merge marked notes into time spans (contiguous+same disp = one line;
  contiguous+different disp e.g. 8va->15ma = new line; genuine time OVERLAP of different disp = conflict, warn,
  keep larger jump); displace every note a span covers (staff-wide), warning per span for subsumed unmarked notes;
  one bracket per span, start on first marked note, stop on latest-ending marked note. Removed `_overlapping_
  displacement` and the `groupby` import.
- Verified: suite 31/31. MusicXML: single-voice unchanged; two 8va voices merge to ONE bracket; unmarked sibling
  under an 8va is displaced + warned; 8va->15ma transition = two brackets, no warn. Cross-voice endpoints (start in
  v1, stop in v2) render valid in BOTH backends -- MusicXML start/stop share number="1"; LilyPond emits sequential
  `\ottava 1`/`\ottava 0` on the shared staff. octava.py runs clean (no spurious warnings).
- Conflict path (true simultaneous 8va vs 15ma) is order-sensitive and can yield two abutting brackets of the
  larger shift; deemed acceptable for an un-notatable input that already warns.

### Task 5c -- octave lines reworked to a timeline/merge model, 2026-08-23 (NOT committed)
Replaced the interval-sweep `_apply_octave_lines` with Marc's timeline design (cleaner, and fixes a real bug where
the interval version left un-marked notes at true pitch under a staff-wide `\ottava`). Per staff:
1. Build each voice's (lane's) requested octave spans -- `groupby` the lane's notes by displacement, one
   (start, end, disp) per nonzero run (rest inside a run is spanned). groupby is legit here: a lane's notes are
   ordered and non-overlapping.
2. Displace each note by the largest overlapping span at its ONSET (ties -> positive). Octave shifts key off
   noteheads, so onset is the sample point. A note held across a barline is one note: a tied continuation
   (`ends_tie`) inherits the shift decided at its true onset rather than re-sampling (avoids a tied note changing
   octave mid-note).
3. Warn once per staff with a count of notes pulled off their own shift (subsumes the old "unmarked note under
   another voice's line" warning -- it's just the disp==0 case of clobbering).
4. Draw one bracket per run of consecutive same-shift notes (staff notes in onset order); start on the run's first
   note, stop on the note with the latest end. Endpoints may be cross-voice; the timeline guarantees the run is a
   uniformly-shifted region, which is exactly what makes a staff-wide bracket correct there.
Key Q resolved (Marc's concern): start/stop do NOT need to be in the same voice. Verified in both backends --
MusicXML pairs start/stop by number=; LilyPond `\ottava` is a Staff-context command so sequential on/off across
voices is fine. Often they land same-voice anyway when one voice spans the run.
Straddle example (v1 15ma b0-2, v2 one held 8va note b2-5): tie-propagation makes the whole held note 15ma -> ONE
15ma bracket (matches intent). Give v2 real onsets in the 8va region instead and it draws 15ma then 8va.
Verified: suite 31/31; single-voice unchanged; concurrent-same -> one bracket no warn; 8va->15ma transition -> two
brackets; 8va over a rest -> one bracket (interval version wrongly split this); forced cross-voice handoff valid in
both backends; octava.py clean. `groupby` import restored (used for the per-lane spans + the bracket runs).
Naming (Marc): in `_apply_octave_lines` the post-placement buckets are staves/voices, not lanes -- outer dict
`voices_by_staff`, loop var `staff`, inner `voice_notes`; `lane` kept only at the placements boundary.

### Convention check: octave line ending mid-tie (2026-08-23)
Q: a note held past where its octave line ends -- does the second half revert to written pitch? Convention: NO.
Octave transposition is fixed at the notehead/onset; a tie is one sounding event, so it keeps the register it was
struck in. Gould (Behind Bars) ends the ottava at the LAST NOTEHEAD it covers and calls "continuing octave signs
for the value of a long note" incorrect; LilyPond docs say changing ottavation during a tie is "not well-defined"
(suggest a slur). So our tie-propagation (continuation inherits the onset's shift) is right.
This surfaced a real bug: the bracket STOP was attached to `max(run, key=end_beat)` -- the longest-held note --
which across voices can be a note whose ONSET precedes the run start (e.g. v1 whole note [0,4) under an 8va while
v2 plays quarters within it), making start and stop land on the same note = malformed bracket. Fixed: `run` is
sorted by (start,end), so stop on `run[-1]` (the last notehead), which is also exactly Gould's "end at last
notehead". Suite still 31/31 (single-voice runs unaffected: run[-1] == longest there).

Follow-on (Marc): the COVERAGE span was still duration-based (`run[-1].end_beat`, half-open), which over-covered --
a note in another voice struck AFTER the last shifted notehead but while a held note still rings would be wrongly
clobbered. Made coverage onset-based to match the convention: span = (first_onset, last_onset) and the test is the
CLOSED interval first_onset <= note.onset <= last_onset. Closed upper bound is what makes a note struck *with* the
last notehead count while one struck *after* it doesn't -- and it lets a single-note run (first==last) still cover
itself. (Bracket end and coverage end are now both the last notehead; they were briefly inconsistent.)
Single-note 8va convention (Gould/LilyPond/MusicXML): a lone note commonly just gets "8va"/"8" text, but both
`<octave-shift>` and `\ottava` draw a short bracket by default, which is what we emit (start+stop on the same
note, valid in both backends) -- no special-casing needed.

### Bug: gap-spanning coverage clobbered other voices (octava.py, 2026-08-23)
octava.py fired 3 bogus clobber warnings though no two notes overlap. Cause: the per-run span rode over the run's
gaps. The piano's unspecified voice had sparse 8vb notes (onsets 3,7,7.5,11.5) that groupby merged into ONE run ->
span covering [3, 11.5], which swallowed voice 2's PLAIN notes sitting in the gaps (4.5,5.5,8.5). Both the
closed-interval and the original duration span had this flaw -- any gap-spanning coverage does.
Fix: coverage is now NOTEHEAD SIMULTANEITY, not a span. A note is displaced by the largest shift requested at its
OWN onset (`beats_equal`), ties -> positive; a barline-split continuation (`ends_tie`) requests nothing and
inherits instead. This satisfies every case: octava.py -> 0 clobbers (no plain note shares an onset with an 8vb
notehead); note struck DURING a held lone 8va -> not clobbered (different onset); note struck WITH it -> clobbered;
straddle held note -> pulled to the bigger simultaneous shift then inherited by its continuation. "8va over a rest"
still works because the BRACKET pass groups assigned notes and rides over true rests -- but a plain note in a gap
has a different displacement, so it breaks the run and is never swallowed. `groupby` now used only for the bracket
runs. Verified: suite 31/31; battery (single / over-rest / simultaneous-warn / during-held-no-warn / straddle) all
correct; octava.py silent.

### Bug: onset-only coverage split a bracket mid-line (voices.py, 2026-08-24)
Pure notehead-simultaneity had the opposite failure. voices.py (4 forked voices on one staff, one 8vb) drew the
8vb as TWO touching brackets with no gap between. Cause: a natural note attacked at 3.5 -- inside a *held* 8vb note
(attacked 3.25, sustaining to 3.625, tie-split at 3.5). 3.5 is not an 8vb *onset*, only a point inside the sound,
so simultaneity didn't pull it; it stayed at disp 0 and broke the bracket run in the groupby. The user pinned it:
the splitter falls *on* the shift, not in a gap.
Fix (final): coverage keys off RUNS OF SHIFTED NOTEHEADS, not onsets and not sustain intervals. Sort the shifted
noteheads (real attacks; `ends_tie` continuations excluded -- they're not noteheads); build runs where a run is a
maximal same-shift stretch with no gap in the shifted sound (next notehead starts before the run's running
`run_sound_end`). A run is `(first_onset, last_onset, disp)`; a note is pulled if its onset lies in the CLOSED span
`[first, last]` (`in_run`), taking the max-abs shift among covering runs. `ends_tie` still inherits `previous`.
Why the intermediate sustain-interval attempt was wrong (caught by Marc's half-open question): it covered a note
during a HELD FINAL shifted note, extending the bracket past the last notehead -- but Gould ends the line at the
last notehead. The run model ends at `last_onset`, so: note during held lone 8va -> run `[0,0]`, not covered (as
the earlier simultaneity model had it); note between two noteheads of a run -> covered (fixes voices.py); sparse
notes with real rests -> separate runs, gap notes uncovered (keeps octava.py silent). Half-open vs closed only
ever mattered for the note starting exactly at a boundary: the run's `[first,last]` is closed on both onsets, which
is right (a note struck on the first or last notehead is under the line); a note after the last notehead but during
its sustain is outside `[first,last]`, correctly natural. Verified: held-final -> single-note bracket, beat-2 note
untouched; voices.py -> ONE bracket, 3 interior notes pulled; octava.py -> 0 clobbers; suite 31/31.

### Reversal: sustain-based timeline model (2026-08-24)
The notehead-run model (above) was reverted at Marc's call. In voices.py he put a SHORT 8vb voice (dilation 0.5)
under a LONG 8va voice (dilation 2) on one staff. Ending each run at its last notehead made the short 8vb notes
poke through between the long 8va's noteheads -- a mess of tiny brackets. Marc: the long 8va should just sustain
throughout and swallow the shorter notes; he doesn't care that this contradicts Gould's last-notehead rule.
So we went back to his original timeline idea: each shifted note holds its shift over its WHOLE DURATION
(`shift_spans` = `(start, end, disp)` per shifted note, half-open `covers`); voices merge by max-abs, positive
breaking a tie. A note's onset falling in any span is pulled to the winning shift. "Break at rests" is automatic:
each note only covers its own duration, so gaps between a voice's notes are uncovered (this is what keeps octava.py
at 0 clobbers -- foreign notes in the sparse 8vb voice's gaps aren't caught). The bracket pass (groupby over
assigned notes) is unchanged and rides over rests. Result: voices.py -> ONE 8va bracket, all 8vb notes absorbed to
+1 (20 pulled); octava.py -> still 0 clobbers; suite 31/31. Prior notehead-run version stashed in
`octave-notehead-run-stash.md`. NB this is the sustain-interval version from earlier the same day, minus the
half-open-boundary hand-wringing -- the "held final note pulls a note during its tail" behavior is now WANTED.
