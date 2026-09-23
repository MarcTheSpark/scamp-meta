# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

This directory is a workspace containing five sibling Python packages developed together. They are independent on PyPI but `scamp` depends on the other three core libraries, so changes often span more than one package.

- `clockblocks/` — musical-time / coordinated-clock library (`Clock`, `TempoEnvelope`, `wait`, `fork`). Standalone package. Version 1.0 is a ground-up rewrite around a background `Scheduler`; the old 0.x implementation lives on the `0.6.x` branch of the same repo.
- `expenvelope/` — piecewise exponential curve library (`Envelope`, `EnvelopeSegment`) used for dynamics, tempi, and continuous parameters. Standalone package.
- `pymusicxml/` — hierarchical MusicXML export library. Standalone package.
- `scamp/` — the main framework. Re-exports symbols from the three packages above and adds `Session`, `Ensemble`, `ScampInstrument`, `Performance`, `Score`, quantization, soundfont/MIDI/OSC playback.
- `scamp_extensions/` — optional extensions built on top of `scamp` (scales, pitch-class sets, additional I/O conveniences).

The workspace also contains `scamp_tutor/` — not a Python package, but the instructions and fetch-on-demand doc bundles for an AI-based SCAMP tutor hosted at scamp.marcevanstein.com/aitutor/.

When working in `scamp/`, remember that `Clock`, `Envelope`, etc. are imported from `clockblocks` / `expenvelope` — if behavior seems wrong, the bug may live in the sibling package, not in scamp itself.

## Build / install

Each package is a standalone distribution. All five use `pyproject.toml` and a `src/` layout (modernized 2026-05-02). `scamp` requires Python ≥ 3.12. The workspace root has a `pyproject.toml` defining a **uv workspace** over the five packages — `uv sync` at the root installs them all in editable mode against each other, which is the normal development setup. Individual packages can still be pip-installed standalone:

```
cd scamp && pip install -e .          # editable install
cd scamp && pip install -e .[all]     # adds abjad, python-rtmidi, pynput
```

Native FluidSynth libs live in `scamp/src/scamp/_thirdparty/{linux_libs,mac_libs,windows_libs}/` but are **gitignored** — they're populated at build time by the per-platform scripts in `scamp/scripts/wheel_building/before_build_*.sh`. For editable installs on Linux, `apt install fluidsynth` gives you the system library and scamp will use it (`try_system_fluidsynth_first`). Built wheels bundle their own copy via `auditwheel repair` (Linux), `delocate-wheel` (macOS), or DLL drop-in (Windows).

Wheel building uses **cibuildwheel** orchestrated by `scamp/.github/workflows/build-wheels.yml`, triggered by pushing a `v*` tag (or via `workflow_dispatch`). Config lives in `scamp/pyproject.toml` under `[tool.cibuildwheel.*]`. Linux uses the `quay.io/pypa/manylinux_2_34_*` images. The workflow uploads wheels + sdist as run artifacts; it does **not** publish — download them and `twine upload` by hand.

The intel-mac (x86_64) wheel is the one exception, built outside CI because GH's macos-13 runner is deprecated. **It does not need a Mac**: run `scamp/scripts/wheel_building/build_macos_12_wheel.sh` on Linux. It builds the wheel, retags it `macosx_12_0_x86_64`, and uses `scripts/wheel_building/inject_mac_dylibs.py` to lay in pre-delocated dylibs from `scripts/wheel_building/intel-mac-dylibs.tar.gz`, fixing up the RECORD hashes. Those dylibs were frozen from one real Intel-Mac cibuildwheel run and stay valid for future releases as long as their relative paths inside the wheel don't move. The stash is tracked in **Git LFS**, so a fresh clone (with LFS) has it; `inject_mac_dylibs.py`'s docstring documents how to regenerate it on an Intel Mac if the wheel's internal paths ever move.

## Tests

The only automated tests live in `scamp/test/test_examples.py`. They are golden-output tests: each `.py` file under `scamp/test/example_tests/{AssortedHaphazard,Test,Tutorial}/` is executed and its captured output is diffed against a stored reference.

```
cd scamp/test
python3 test_examples.py              # run all examples, report diffs
python3 test_examples.py -s           # SAVE_NEW: regenerate reference outputs
```

There is no pytest config and no per-test selection flag — to run a subset, edit the `examples` list or temporarily move files out of `example_tests/`. When intentional output changes happen (e.g. quantization tweaks), regenerate references with `-s` and review the diffs in version control before committing.

`clockblocks/` has its own pytest suite in `clockblocks/tests/` (`cd clockblocks && python3 -m pytest tests`), written during the 1.0 rewrite; it's largely machine-generated and meant for pinpointing regressions. `expenvelope/`, `pymusicxml/`, and `scamp_extensions/` have no test suites; correctness for them is exercised indirectly through scamp's example tests.

## Architecture notes (scamp)

The data flow for a typical scamp piece is **Session → Performance → Score**:

1. `Session` (subclasses `Clock`) is the top-level object. It owns an `Ensemble` of `ScampInstrument`s and a `Transcriber`. User code calls `instrument.play_note(...)` while `wait(...)` advances musical time.
2. Each `ScampInstrument` holds a list of `PlaybackImplementation`s (`SoundfontPlaybackImplementation`, `MIDIStreamPlaybackImplementation`, `OSCPlaybackImplementation`) — a single note can fan out to multiple backends. Microtonality is handled by allocating MIDI channels and emitting pitchbend automatically.
3. The `Transcriber` records every played note as a `PerformanceNote` into a `Performance` (organized into `PerformancePart`s). A `Performance` is the unquantized, "as-played" representation.
4. `Performance.to_score(...)` runs quantization (`quantization.py`, `_metric_structure.py`) against a `QuantizationScheme` and produces a `Score` (`score.py`: `StaffGroup`/`Staff`/`Measure`/`Voice`/`Tuplet`/`NoteLike`). The `Score` is the notation-ready model.
5. `Score` exports to MusicXML via `pymusicxml`, or to LilyPond via abjad (`_abjad_facade.py`, optional dependency, version-pinned to `abjad==3.31`).

Cross-cutting concerns:
- **Time** is driven by `clockblocks.Clock`. `Session` is itself a `Clock`; `fork(...)` spawns child clocks for polyphonic / multi-tempo layers that stay coordinated under the master.
- **Continuous parameters** (gliss, dynamic envelopes, arbitrary playback params) are `expenvelope.Envelope`s passed into `play_note`.
- **Settings** are global singletons in `settings.py`: `playback_settings`, `quantization_settings`, `engraving_settings`. Changing them affects all subsequent operations in the process.
- **Note metadata** (articulations, noteheads, spelling, text, spanners) is carried through the pipeline via `NoteProperties` (`note_properties.py`) and applied at the score stage by `score.py` / `spanners.py` / `text.py` / `spelling.py`.
- **Playback adjustments** (`playback_adjustments.py`) let users intercept and modify playback parameters per-note without affecting notation.

Files prefixed with `_` (`_midi.py`, `_soundfont_host.py`, `_parsing.py`, `_metric_structure.py`, `_abjad_facade.py`, `_dependencies.py`, `_engraving_translations.py`, `_animation.py`) are internal; the public API surface is whatever `src/scamp/__init__.py` re-exports.

## Conventions

Contribution conventions (spelling, GPL headers, commit/comment style, "don't commit unless asked") live in `workflow.md`. A `UserPromptSubmit` hook in `.claude/settings.json` injects that file's contents on every prompt, so it stays in context rather than needing a manual re-read.

## Changelogs

Each of the five packages has a `CHANGELOG.md` at its repo root ([Keep a Changelog](https://keepachangelog.com) format, newest first, with an `## [Unreleased]` heading on top). It ships in the sdist via `MANIFEST.in`, and is the canonical release notes — the GitHub release body is a copy, since a release body doesn't exist on the sourcehut mirror.

**When committing a change with user-facing implications, add it to that package's `## [Unreleased]` section** under the appropriate heading (Added / Changed / Deprecated / Removed / Fixed / Security). Cutting a release then means renaming `[Unreleased]` to the version and date, not reconstructing history from `git log`.

A changelog is not a commit log: it's written for someone deciding whether upgrading will break them. So it's curated and lossy on purpose — summarize the user-visible effect, not the implementation, and skip refactors, tests, and docs churn that nobody downstream can observe. Breaking changes are the highest-value entries; give them the replacement API, not just the removal.

## Roadmap

A roadmap file is found at `scamp/roadmap/Roadmap.md`, with old roadmaps stored in `scamp/roadmap/archive`.

## Working notes (`.aiconvos/`)

The workspace root has an `.aiconvos/` directory holding Markdown notes about ongoing work, organized **by topic, not by session or commit**. Filenames are `YYYY-MM-DD-short-topic.md` (date is when the topic was first written down). These capture *why* and *considered alternatives* — context that doesn't fit in commit messages. Marc may say things like "recall our conversation about wheel paths" — when that happens, `ls .aiconvos/`, find the matching filename(s), read them.

A topic can span multiple commits and multiple sessions. When you find an existing file whose topic matches what we're working on, **extend it** rather than starting a new one. Start a new file only for a genuinely new topic.

Making a commit together is a *prompt* — not a trigger — to ask:
- Does the change have user-facing implications? If so, add it to that package's `## [Unreleased]` changelog section (see **Changelogs** above).
- Does CLAUDE.md need updating? Re-read it, fix anything the change made stale. It should usually stay the same length or shrink — recent-commit logs belong in `git log`, not here.
- Is there context from this work worth caching in `.aiconvos/`? (Rejected alternatives, surprising findings, open questions, "we tried X and it didn't work because Y".) If so, append to the relevant topic file or create a new one.

Other moments that warrant a convo update without any commit: untangling a confusing system, discovering a non-obvious constraint, deciding to defer something for later, or any time Marc says "let's note that down."

The folder is tracked in the workspace superproject (public), so write for an audience beyond this session — no secrets, no candid remarks about people.
