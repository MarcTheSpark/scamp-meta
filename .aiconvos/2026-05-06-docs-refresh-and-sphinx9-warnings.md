# Docs refresh, sourcehut→GitHub, Sphinx 9 warning cleanup

Worked through roadmap items #3 (sourcehut → GitHub) and #4 (installation
docs refresh) in one session, then chased the Sphinx 9 warnings that
surfaced during the doc rebuild. Notes here are the things worth caching
beyond what the diffs and commits already say.

## What gets regenerated vs. tracked

`scamp/.gitignore` excludes the entire generated-docs surface:

- `docs/<pkg>.rst` for `scamp`, `clockblocks`, `expenvelope`, `pymusicxml`
- `docs/scamp_extensions.*` (covers `scamp_extensions.composers.rst` and
  every per-subpackage stub file/dir)
- `docs/scamp/`, `docs/clockblocks/`, `docs/expenvelope/`, `docs/pymusicxml/`
  (the autosummary-generated module/class stub directories)

So when `makePackageRSTs.py` rewrites the top-level package files, none of
them show up in `git status` — and editing them by hand is pointless,
because `buildDocs` deletes everything that isn't `conf.py`,
`makePackageRSTs.py`, `_static/`, `_templates/`, `narrative/`, or
`index.rst` before each build.

If you want to keep edits, they need to live in the template
(`docs/_templates/autosummary/{base,class,module}.rst`), in the script,
or in `narrative/`. Anything else gets blown away.

## Sphinx 9 autosummary `import_cycle` warnings — what they actually mean

Sphinx 9 added a check: an autosummary entry under `.. currentmodule:: X`
shouldn't repeat `X.` in its own name (`X.foo` → just `foo`). Old code that
emitted fully-qualified names everywhere now produces 200+ warnings.

Two distinct sources in this repo:

1. **`makePackageRSTs.py`** — emitted submodule and subpackage names as
   `{package}.{module}` and API entries as
   `inspect.getmodule(obj).__name__ + "." + name`. Now it strips the
   current-package prefix where it matches; cross-package re-exports
   (e.g. `clockblocks.clock.Clock` listed in `scamp.rst`) keep their
   full path.

2. **`docs/_templates/autosummary/module.rst`** — the Classes and Functions
   blocks already used bare `{{ class }}`/`{{ function }}`, but the
   Attributes and Exceptions blocks emitted `~{{ fullname }}.{{ item }}`,
   which by definition matches the currentmodule. Switched both to bare
   `{{ item }}`. The class.rst template doesn't have this problem because
   `{{ name }}` there is the bare class name, not the full module path.

After both fixes, regenerate with `makePackageRSTs.py` and let Sphinx
recreate the stub directories; the warnings should be gone.

## Sneaky bug in `conf.py` abjad-version sync

`conf.py` rewrites the abjad version mention in `experienced_setup.rst` at
build time, using `re.sub(r'abjad==.*', ...)`. The greedy `.*` was harmless
when the only mention was a bare `pip install abjad==3.18` line, but the
moment we introduced an inline mention like
`This installs ``abjad==3.31`` (pinned…), …`, the regex ate everything from
`abjad==` to end of line — including the closing backticks and the rest of
the sentence. Result: an unterminated `` ``abjad==3.31` `` literal, which
broke RST parsing.

Fix: `r'abjad==[\d.]+'`. Lesson: tighten replace patterns when prose-like
content might appear after the match.

## macOS LilyPond install: status as of 2026-05

- LilyPond.org tarball still **not notarized**. Same Gatekeeper dance as
  always.
- **Homebrew** has a 2.26 bottle (`brew install lilypond`). This is now
  the recommended path on Mac — no Gatekeeper warnings because brew puts
  it in its own prefix and strips the quarantine bit.
- For users who insist on the manual tarball: the cleanest workaround is
  one terminal command rather than three dialogs:
  ```
  find /Applications -maxdepth 2 -iname "lilypond*" \
      -exec sudo xattr -dr com.apple.quarantine {} +
  ```
  `xattr -dr` recurses, so a single match on the outer folder strips the
  quarantine bit from every binary inside. The `-iname "lilypond*"` is
  case-insensitive and tolerates the version-suffixed folder names that
  the tarball currently produces (`lilypond-2.26.0/`). `-maxdepth 2` is
  a small forgiveness margin in case the user nested it inside a wrapper.

We left the original "click through Security & Privacy" walkthrough in
place as Option 2 for users who can't or won't open Terminal.

## `print_dependency_status()` is the supported diagnostic now

Both setup pages (`easy_setup.rst` and `experienced_setup.rst`) now lead
their testing snippet with `print_dependency_status()` before
`test_run.play()`. It prints a one-line-per-dep report with
`ok` / `warn` / `missing` states, including the resolved
`libfluidsynth` path so you can tell whether the bundled or system copy
got loaded. The companion `dependency_status()` returns the same data
as a list of `(name, state, detail)` tuples.

This replaces hand-rolled "why doesn't X work?" debugging dialogues.

## Things deferred

- A handful of `<unknown>:4: SyntaxWarning: invalid escape sequence '\ '`
  lines still appear during the Sphinx build and can't be reproduced by
  plain `import scamp` (or any direct import path I tried). They look
  like they fire from inside Sphinx 9's autosummary introspection
  synthesizing a string and `compile()`-ing it. Benign; chase only if it
  starts mattering.
- One `Envelope.from_segments` docstring in expenvelope was non-raw with
  a `\ s` rST glue marker; that was fixed (`r"""`). Other module/class
  docstrings in the same files were already raw.

## Docstring-coverage sweep (2026-07-11)

Audited every symbol that the docs actually render, and filled the gaps.
The audit script walks the same surface `makePackageRSTs.py` does (non-`_`
modules, recursively), then checks each public class / function / method /
property for its *own* `__doc__`. Worth re-running after any API addition.

Findings worth keeping:

- **Only ~50 of 223 raw hits were real.** ~170 were methods overriding a
  parent (`Chord.render` → `MusicXMLComponent.render`); `inspect.getdoc`
  inherits the parent docstring and autodoc renders it, so those are fine.
  An audit that checks `getattr(obj, "__doc__")` without distinguishing
  inherited docs will drown you in false positives.
- **`autoclass_content = 'class'`** (conf.py) means an `__init__` docstring
  is *never rendered*. `NonTraditionalKeySignature` and `StopBracket` each
  had a perfectly good `:param:` block on `__init__` and were showing up in
  the docs with no description at all. House style is docs on the class
  body (cf. `TraditionalKeySignature`, `StartBracket`); both were moved.
  Anything new that documents `__init__` instead of the class is invisible.
- **`Clock`'s five removed-in-1.0 tombstones** (`rouse_and_hold`,
  `release_from_suspension`, `time_in_master`, `wall_time_in_scheduler`,
  `synchronization_policy`) are not doc gaps — they always raise
  `AttributeError`. Documenting them would advertise dead API, so they're
  now marked `""":meta private:"""` and hidden.
- **`:meta private:` alone was not enough.** Autodoc honors it in the class
  *body*, but autosummary builds the Methods/Attributes *tables* from its
  own introspection and ignores it — the member survived as an empty table
  row. Fixed with a `reject_meta_private` Jinja filter (conf.py) applied in
  `_templates/autosummary/class.rst`. Note it has to be applied *inside*
  each `{% block %}`: a `{% set %}` in the enclosing template scope is not
  visible within a block, which silently no-ops.
- **`autosummary_generate_overwrite = False` — removed, and worth knowing
  why it was ever there.** It meant existing stubs were never rewritten, so
  editing a template had no effect until the stubs were deleted; a plain
  `sphinx-build` silently served stale output. Archaeology (commit
  `79917f7`, Feb 2020) shows it was added to protect a single
  *hand-maintained* stub, `scamp.playback_adjustments.PlaybackAdjustmentsDictionary.rst`,
  which also had a matching `-iname` exception in `buildDocs`'s delete line
  so it would survive the clean. That same commit deleted the hand-written
  stub and dropped the `buildDocs` exception — but left the flag behind.
  Its purpose was gone the moment it was committed, which is why the
  trailing comment read "Doesn't seem to work?": with `buildDocs` deleting
  every stub before Sphinx runs, there is never an existing file to
  overwrite, so `True` and `False` behave identically. Now removed; the
  default (`True`) regenerates stubs on every build, so the docs no longer
  depend on the delete step for correctness. `buildDocs`'s `find … -delete`
  is still worth keeping to clear orphaned stubs left by renamed/removed
  classes.
- `scamp_extensions/pitch/chord_builder.py` had no GPL header (every other
  source file has one). Added.

Private modules (`_midi`, `_soundfont_host`, `_engraving_translations`)
still have undocumented members. That's fine — `makePackageRSTs.py` skips
`_`-prefixed modules. The one exception is
`_soundfont_host.print_soundfont_presets`, which *is* re-exported into
scamp's `import *` API and so does appear in the docs; it got a docstring.

## Summary metadata

Author lookup in `scamp/__init__.py` switched from
`importlib.metadata.metadata('scamp')['Author']` to `'Author-email'`
(user's own change). Setuptools 77+ stopped populating `Author` separately
when the project uses the new SPDX-license `[project]` block, so the field
to read is `Author-email`. Worth knowing for any other introspection of
package metadata.

## Update 2026-08-05: buildDocs → scripts/build_docs.sh, output → scamp/docs_build

`buildDocs` moved to `scripts/build_docs.sh` (renamed with `.sh`) and now `cd`s to the
package root from its own location, so it runs from any CWD. HTML output moved from
`docs/build` to **`scamp/docs_build`** (out of the source tree — Marc asked why `docs/`
was so cluttered; the answer is that everything generates in place, and relocating the
built site is the one easy win). Updated together: `.gitignore` (`docs/build` → `docs_build`),
the workspace-root `uploadDocs.sh` rsync source, and the roadmap mention.

Gotchas:

- **`set -euo pipefail` + the `find … -delete` don't mix** without help. The delete keeps
  whitelisted non-empty dirs (`narrative/`, `_static/`, …); `find` then reports them as
  "Directory not empty" and exits non-zero, which `set -e` turns fatal. The old `buildDocs`
  survived only because it had no `set -e`. Fix: `|| true` on the find (the errors are
  expected). Also added `-path "*build_text/*"` to the whitelist so the tutor text bundle
  isn't half-clobbered (a pre-existing quirk — the old delete line ate its non-whitelisted
  files every run).
- **Examples pages are gitignored and rebuilt on demand.** `build_docs.sh` runs
  `build_examples_docs.py` only with `--rebuild-examples`, or automatically when
  `docs/examples/` is absent (fresh checkout). So the docs build works without committing
  generated example pages. See the examples-index note for the generator itself.
- `docs/build_examples_docs.py` is whitelisted from the find-delete (same as
  `makePackageRSTs.py`) so the build doesn't delete its own generator.
