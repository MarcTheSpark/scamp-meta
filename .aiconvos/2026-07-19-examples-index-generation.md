# Examples index: generated from docstrings, not hand-maintained

*2026-07-19. Implemented (uncommitted in scamp repo, pending Marc's review).*

## The problem

`scamp/examples/INDEX.md` was hand-written (Jul 14) and required editing every time an
example was added — Marc explicitly didn't want that maintenance burden. It had also
already gone stale: its "Concatenated bundles" section documented `examples/_bundles/`,
which was the superseded first iteration of the tutor-bundle system (replaced Jul 15 by
`scamp_tutor/regenerate_bundles.sh`, which reads example sources directly and writes
bundles to `scamp_tutor/examples/`).

## The design

**Metadata lives in each example file; the index is generated.** Adding an example =
adding one file with a short docstring ending in a `Tags: comma, separated` line.

- `scamp/examples/regenerate_index.py` walks the example folders, parses each first
  docstring (summary + optional Tags), scans each file for public scamp API usage
  (module-level names + public methods of the main classes, via `dir()` at generation
  time), and writes `INDEX.md`: a Feature → examples table (inverted from the tags,
  Tutorial entries first), per-folder entries with summary/tags/API line, a compact
  `LowQuality/` listing, and an auto-generated non-Python companion-files section.
- INDEX.md stays **in the scamp repo, human-facing** (Marc was explicit about this) with
  a GENERATED banner. The tutor copy is made by `scamp_tutor/regenerate_bundles.sh`,
  which now runs the generator first, then `cp INDEX.md → examples_index.md`.
- Files without a `Tags:` line are listed but excluded from the feature table — that's
  the mechanism by which `LowQuality/` and support files (e.g. ScampCooking's
  `definitions.py`) stay out of feature lookup.
- The generator prints tags-used-only-once as a typo check (~30 singletons currently,
  reviewed and legitimate — unique features like "metric phase", "voice leading").

## What was done (2026-07-19)

- All 76 examples got short docstrings: Tutorial ones already had summaries and just
  gained Tags lines; ~35 others got new docstrings; 4 had wrong/stale docstrings
  replaced (notably `load_performance_and_export_midi.py`, whose docstring claimed it
  loaded a performance — it doesn't; renamed to `record_and_export_midi.py`).
- **Quality triage**: 9 scratch scripts moved (git mv) to `examples/LowQuality/`:
  bunchStuff, glissTest2, karlclock, karlclock_interactive, midi_channel_manager,
  repeated_notes, tempo_test, ticker_test, voice-leading_weird. Judgment calls:
  `qt_interactive.py` looked like junk from the top but is actually a real GUI demo
  (draggable rect controls live playback) — kept; `tempo_test.py` demos tempo-target
  chains but its tail is scratch ("bob" list, duplicated show calls) — moved.
  LowQuality is excluded from tutor bundles (regenerate_bundles.sh folder list) and
  from the feature table.
- Deleted the stale `examples/_bundles/` and `docs/tutor_project_instructions.md`
  (superseded by `scamp_tutor/instructions.txt`, which carries the pedagogy forward —
  verified before deleting). Removed the day-old `_bundles` gitignore stanza.
- All examples compile (`py_compile` sweep); tutor bundles + index regenerated.

## Conventions to remember

- Docstring format: optional `SCAMP Example: <title>` line (stripped by the generator —
  the filename conveys it), 1-2 line summary, blank line, `Tags: ...`. Keep summaries
  short (Marc: "not too much to read").
- Tag vocabulary is free-form but check the singleton warning when regenerating.
- `midi_export.mid` in AssortedHaphazard is a tracked-but-generated output that shows up
  in the companion-files section; harmless, but could be untracked someday.

## Update 2026-08-01: full reorg restored, still in-progress (deliberately NOT in 0.12.0)

The reorg grew well past the 2026-07-19 index pass into a full folder restructure:
`Demos/`, `marc/`, `Reconstructions/`, and parts of `AssortedHaphazard/` fan out into

- `Assorted/` (with subfolders `evolving_form/`, `osc_playback/`, `scamp_to_max/`),
- `Compositions/` (`LeafPoints/*.json`, `evanstein_*`, `reich_piano_phase`, `leaf_loops`),
- `LowQuality/`.

29 renames, 12 adds, 55 content edits, 1 delete (~97 paths). `midi_export.mid` — the
"someday" from the note above — is now deleted and gitignored.

**Mid-migration:** `AssortedHaphazard/` still coexists with the new dirs; not everything
has moved out yet. That's why the 55 `M` files still sit under the old path.

**State:** this work lived only in the `wip-snapshot` commit (`7b5c3b3`). On 2026-08-01 it
was restored into the scamp working tree — **staged, uncommitted** — and deliberately kept
out of the 0.12.0 release (cosmetic, not ready). It lands in a later release.

**Extraction method** (to redo if the tree gets reset): `git rm -r examples` *then*
`git checkout wip-snapshot -- examples .gitignore`. The `rm` first is essential — a plain
checkout writes the snapshot's paths but won't delete files it renamed/removed, leaving
stale copies of every moved example.

## Update 2026-08-05: reorg committed, tags normalized, docs Examples section added

The reorg finally landed (commits `Reorganize examples…`, then `Normalize example tags…`).
Final folders: `Tutorial`, `Assorted`, `ScampExtensions`, `Compositions`, `JunkDrawer`.
`JunkDrawer` replaces `LowQuality` as the un-indexed scratch bin (a `UNINDEXED` constant in
`regenerate_index.py`, no longer two hardcoded `"LowQuality"` checks).

- **Tag normalization pass.** Unified vocabulary where similar examples diverged:
  `gui` → `gui integration` (Marc's preference), `playback_implementations` →
  `playback implementations`, `scales` → `scale`, `generative`/`composition` →
  `algorithmic composition`. Also lined up the two indispensability examples (added
  `indispensability`/`barlow` to the barlicity piece) and fixed a copy-pasted
  `key_plane_example` summary + a mangled `23_special_notations` title line.

- **Docs Examples section** — new generator `scamp/docs/build_examples_docs.py`, sibling to
  `regenerate_index.py` and importing its `parse_docstring`/`FOLDERS`. Produces a docs page
  per *unit* and links them from `index.rst` under **Learning Resources** (first). See the
  docs-refresh note for the build-tooling side.
  - **Unit model.** A "unit" is either a standalone `.py` in a category folder, or a whole
    dedicated subfolder shown as one page (a captioned code box per script + one folder zip).
    Multiple `.py` in a folder → one page, NOT one page each — this is what Marc asked for
    (`evolving_form/`, `save_and_load_performance/`). Folder title/intro come from the script
    whose stem == folder name, else the title-cased folder name; tags are the union.
  - **Downloads.** Self-contained script → the raw `.py`; anything needing companions → a
    `.zip`. Companion detection for a root-level script: a non-`.py` sibling that shares its
    stem (`24_osc_to_supercollider.scd`) OR is named in the script source. That source-scan
    is what catches `leaf_loops.py` → `LeafPoints/` without hardcoding it; it deliberately
    skips sibling `.py` and other examples' folders so grab-bag folders never over-bundle.
    Served via Sphinx `:download:` (assets staged under `docs/examples/downloads/<slug>/`).
  - **Not tracked.** `docs/examples/` is gitignored like the API `.rst`s. Rebuild is opt-in
    (`build_docs.sh --rebuild-examples`) OR automatic when the folder is missing, so a fresh
    checkout still builds without committing generated pages. (Tracking was only ever forced
    by the opt-in; the auto-when-missing branch removes that need — Marc pushed on this.)
  - Collapsible tag groups use raw-HTML `<details>` (no sphinx-design in this theme).

- Three examples that were undocumented (Marc added docstrings): `multipreset_example`,
  `scale_example`, `leaf_loops` — now indexed and paged. `leaf_loops` stays a root-level
  `.py` beside `LeafPoints/` (not moved into its own folder) since the source-scan handles it.

## Update 2026-08-25: broad-tag consolidation applied (finally)

The Aug-6 "collapse granular tags into broader categories" plan (proposed then, never approved
before the session moved on) was implemented now. Went from ~87 tags / 37 singletons to **25
tags, one true unicorn** (`reconstruction`; `save and load` is a 2nd near-unicorn). Every
`Tags:` line across the examples + the five `about.txt` files was rewritten to the broad
vocabulary, then INDEX.md + media + docs regenerated.

- The broad categories (each ≳3 examples): basics, tempo and meter, clocks and coordination,
  quantization, transcription, randomness, envelopes, glissando, notation, engraving settings,
  note properties, text and spanners, pitch and spelling, scales, algorithmic composition,
  musical form, midi, osc and external tools, live interaction, gui integration, playback
  control, save and load, visualization, scamp_extensions, reconstruction.
- A few merges are lossy in odd ways (faithful to the proposal, worth a glance in review):
  `start_note`→live interaction (so `16_start_and_end_note` reads live interaction + note
  properties), `performance post-processing`→text and spanners. Easy to retune the map.
- The map lived in a throwaway script; if we retune, just re-derive from the `Subsumes` column
  of the Aug-6 proposal (session `4be3a0f8`) plus the three tags added since (octave line, ottava
  → text and spanners; max_voices_per_staff → note properties).
- Render picked up 3 examples that lacked media (`post_processing`, `voices`, `voices_with_ottava`);
  the 12 input/GUI examples stay skipped as before. Docs by-tag index now shows 25 groups.

### Follow-up same day (docs polish)

- **`reconstruction` tag dropped** into `algorithmic composition` (Marc: "nonsense"). Now 24 tags.
- **Sidebar by-tag.** `build_examples_docs.py` now generates a landing page per tag
  (`tag_<slug>.rst`, non-tutorial examples only) and lists them in a hidden toctree, so the
  RTD sidebar shows expandable tag groups instead of a flat dump. Works because **Sphinx 9.1
  made "referenced in multiple toctrees" an info, not a warning** (`toc`/`multiple_toc_parents`),
  so an example can sit under all its tags with a clean build. Tutorial pages are `:orphan:`
  (out of the sidebar, reached from the gallery's numbered list). Tag pages nest under
  "Examples"; with RTD's default `collapse_navigation:True` they expand on click, not in place
  (flip to False in `html_theme_options` for always-open toggles -- longer sidebar).
- **Video embeds** switched from the `padding-bottom:56.25%` wrapper hack to a bare
  `aspect-ratio:16/9` iframe. The hack rendered tall/narrow here because % padding resolves
  against the containing block, and custom.css sets `.wy-nav-content{max-width:none}` on wide
  screens, so height came out ~56% of the *window*, not the capped 640px.
