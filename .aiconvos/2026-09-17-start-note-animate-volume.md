# start_note: `fixed` + explicit `velocity`

Per-note `fixed` switch on `start_note`/`start_chord`, defaulting to fully fixed so a plain call plays at the
*given* volume; `fixed=False` frees the note to animate, and an optional explicit `velocity` decouples attack
from loudness. Proposed 2026-09-17, **implemented 2026-09-18** in `instruments.py` (plus passthrough in
scamp_extensions' `MultiPresetInstrument` / `MultiStaffInstrument`); examples 16, 25, 27, chords_example,
multipreset_example adapted. First built as three tiers (FULL / "velocity" / NONE), then **collapsed to a
boolean + explicit velocity 2026-09-21** — see "Final model" below.

## Final model: boolean `fixed` + explicit `velocity` (2026-09-21)

The middle `"velocity"` tier was dropped: it was exactly "velocity follows volume, but animatable," which is
redundant once `velocity=None` is defined to follow volume. Current semantics:

- `fixed=True` (or the instrument's `start_note_fixed`): velocity = start volume, expression at 100%, shares a
  MIDI channel, and takes no later changes.
- `fixed=False`: own channel; pitch, parameters, and volume are all animatable. With `velocity=None`, velocity
  still equals the start volume (an envelope's peak) and expression starts at 100%, so volume can only fall.
- explicit `velocity=v`: attack = v and `volume_ceiling` = 1, so the `volume` argument drives expression directly
  over its full range — "volume becomes expression," giving room to rise. This is the escape hatch for headroom
  and for libraries that take loudness from a CC rather than velocity.
- An Envelope argument or an explicit velocity always forces the note free; `fixed=True` alongside one warns and
  is ignored. `play_note` is unchanged: `_do_play_note` passes `fixed=False` for envelope/velocity notes, and the
  follows-volume default reproduces the old velocity-tier behavior, so no fixtures moved.

`note_info` stores `"fixed"` (bool) instead of `"fixed_tier"`; the `_Fixedness` enum and its `freest`/`interpret`
helpers are gone. Raising a follows-volume note's volume warns with `_RAISE_VOLUME_WARNING` and clamps to the
ceiling.

The three-tier sections below are kept for the reasoning, but the tier names no longer exist in the code.

## Follow-on: independent `velocity` (2026-09-18)

Added a `velocity` argument to `play_note`/`start_note`/`play_chord`/`start_chord`, **replacing `max_volume`**
(which was really the note-on velocity ceiling, just badly named). `velocity=None` (default) keeps today's
behavior (velocity follows volume). An explicit velocity decouples attack from loudness: the note plays at
that velocity and volume rides its own control unscaled.

Model / how it threads through:
- Two note-on numbers now live in `note_info`: `"velocity"` (the note-on velocity) and `"volume_ceiling"`
  (the divisor mapping volume→expression). Normally equal (velocity follows volume); with an explicit
  velocity, `velocity` = the given value and `volume_ceiling` = 1 (volume sent raw). The MIDI impl reads both.
- An explicit velocity forces the note to be unfixed (own channel, freely animatable volume). Combining it with
  `fixed=True` warns and lets velocity win. `_do_play_note` passes `fixed=False` when a velocity is given so it
  doesn't trip that warning. (Under the final boolean model, `fixed="velocity"` no longer exists.)
- The `volume_cc_num` (default 11 = expression) is which CC carries volume — already existed per-part; the CC7
  VST case is `new_part(volume_cc_num=7)`. No per-note CC.
- OSC note-on now sends `[note_id, pitch, volume, velocity]` (velocity must arrive *with* the note-on to shape
  the attack, unlike continuous params which stay separate messages). Positional receivers ignore the 4th
  value. The scamp_extensions SuperCollider layer subclasses the OSC impl without overriding `start_note`, so
  it inherits this; its synthdef could read the extra arg but that's left to the user.

## Follow-on: persist `velocity` through transcription + export (2026-09-21)

The original claim "velocity is playback-only, so it doesn't affect score or MIDI export" turned out to be a
gap, not a design: it meant `Performance.export_to_midi_file` wrote note-on = volume and `PerformanceNote.play`
dropped the explicit attack, so neither exported nor replayed audio matched live playback. Fixed by threading
it through the transcription pipeline:

- `start_note` records the raw arg as `note_info["explicit_velocity"]` (None unless the user passed one). The
  derived `"velocity"` is *not* the right signal because it equals `"volume_ceiling"` for every tier except an
  explicit velocity — and comparing the two fails when someone passes `velocity=1` (ceiling also 1).
- `Transcriber.register_note` passes it to `PerformancePart.new_note`, which stores it on `PerformanceNote.velocity`
  (new optional field, default None). It is serialized only when set, so existing JSON and the golden example
  fixtures are byte-identical; `duplicate`/`split_at` carry it for free via `_to_dict`/`_from_dict`.
- `PerformanceNote.play` passes `velocity` to `play_note`/`play_chord`, so `performance.play()` reproduces the
  attack (and the note comes back unfixed — round trip closes).
- `write_to_midi_file_track` mirrors `_MIDIPlaybackImplementation.start_note`: an explicit velocity marks the note
  variable (own channel), sets the expression ceiling to 1 (volume sent raw), and uses velocity for the note-on.
  Flows that don't set velocity keep their old output exactly.

**Score/quantization intentionally untouched for now** — velocity still does not survive into `Score` or
MusicXML, so `score.play()`/notation ignore it while `performance.play()`/MIDI export honor it. Deciding later
whether it needs a non-notated home that survives `to_score()`.

## Channel-sharing notes (ring_time)

`fixed` only feeds the manager via the `"fixed"` flag (`playback_implementations.py`): fixed notes add it (and may
share a channel), unfixed notes don't (own channel). This is correct — volume rides a channel-wide expression
stream, so any note whose volume isn't pinned to the channel state needs a private channel.

`MIDIChannelManager` has a baked-in `ring_time=0.5` (`_midi.py:234`) that `_get_free_channel` honors, but
`_get_best_channel_for_fixed_note` (the sharing path fixed notes use) does **not** consult it. That path
is safe-ish because a matched channel has identical bend/cc state and fixed notes always leave expression at 1, but
it can still retrigger a *ringing* (already-ended, not "active") note at the same pitch. This is pre-existing, not
introduced here, but the new fixed default routes more notes through it. Also note live playback measures `ring_time`
in seconds (`time.time`) while export measures it in beats (`time_func=lambda: t`) — a live/export divergence.

## The problem

To get real per-note velocity out of `start_note` today you have to build the whole instrument with
`note_on_and_off_only=True` — a constructor flag that also bans pitch bends and packs channels. Wanting
crisp velocity on one note shouldn't be an instrument-wide, all-or-nothing decision.

## The two axes

"Fixedness" bundles two independent choices, forced by MIDI:

- **Volume** — pin note-on velocity to the start volume (crisp attack, but can't swell *up*: expression
  only cuts down from velocity) vs. leave headroom (velocity at max → muddier velocity-timbre, volume
  floats freely).
- **Channel** — share a channel (must forbid pitch/CC changes) vs. own channel (bends free).

The meaningful combinations are three (headroom-volume + shared-channel is nonsense — animating volume
needs its own expression stream):

| tier | note-on velocity | channel | pitch / CC | volume |
|------|------------------|---------|-----------|--------|
| **full** (default) | = volume | shared | ✗ | ✗ |
| **velocity** | = volume | own | ✓ | down-only* |
| **none** | = max (headroom) | own | ✓ | ✓ |

\* open decision — see below.

## Why velocity fidelity hinges on `max_volume`

In `_MIDIPlaybackImplementation.start_note` (`playback_implementations.py:202`), `note_on` fires at
velocity `max_volume` (line 241) and expression CC only pulls it down (`volume / max_volume`, line 237).

- `play_note` passes `max_volume = volume.max_level()` for an Envelope, else the scalar `volume`
  (`instruments.py:568`) → scalar-volume notes fire at velocity == volume.
- `start_note` hard-defaults `max_volume=1` (`instruments.py:634`) → always full velocity.

So each tier is just a choice of `max_volume` (= volume, or headroom) plus whether the channel is shared
(`"fixed"` flag) — no new playback machinery.

## play_note already does this

The three tiers unify `start_note` and `play_note` rather than splitting them:

- nothing an Envelope → `max_volume = volume`, `"fixed"` flag (shared channel) = **full**.
- pitch / CC param an Envelope, scalar volume → `max_volume = volume`, own channel = **velocity**.
- volume an Envelope → `max_volume = peak`, own channel = **none**.

That is exactly what `_do_play_note` (`instruments.py:554`) computes today, so `play_note` output is
unchanged; it can pass `fixed="auto"` and land on the same behavior.

## Proposed API

```python
def start_note(self, pitch, volume, properties=None, clock=None,
               fixed="auto", max_volume=1, flags=None) -> NoteHandle
```

`fixed` accepts (three tiers, plus auto):

- **`"auto"` (default)** — resolved to a tier (see resolution below). A plain scalar
  `start_note(60, 0.5)` → fully fixed.
- **`True`** — fully fixed.
- **`"velocity"`** — velocity fixed (own channel, crisp velocity, free bends).
- **`False`** — not fixed (headroom velocity, everything animatable).

For an open-ended note that will be animated via later `change_*` calls, there's no envelope to infer
from, so you pass the tier explicitly. Changing something a tier forbids **warns** (naming the tier to
pass) and no-ops — warn-and-skip, since a shared-channel note can't retroactively bend without
corrupting its channel-mates. Exception: a **velocity**-fixed note takes *downward* volume changes fine
(expression ducking under the note-on ceiling); a target *above* the initial level warns ("can't raise a
velocity-fixed note above its initial volume; start it with `fixed=False`") and clamps to the ceiling.

### `"auto"` resolution and the instrument-wide default

Order the tiers by animation freedom: full (`True`) < `"velocity"` < none (`False`). Envelopes passed to
the call set a *floor* of required freedom:

- volume Envelope → floor none (needs headroom to swell up)
- pitch / CC-param Envelope, scalar volume → floor `"velocity"`
- no Envelope → floor full

`"auto"` resolves to **the freest of {this floor, the instrument's `start_note_fixed`}**
(`start_note_fixed` counts as full when unset). So the instrument default only ever *raises* freedom — it
never drops a note below what its envelopes require. A plain scalar note on a default-less instrument →
full. (`start_note_fixed=True` is thus a no-op vs. unset, since full is the floor — which is exactly why
`note_on_and_off_only` maps onto it.)

`start_note_fixed` is a new instrument attribute / constructor arg, `True` (default) / `False` /
`"velocity"`. It defaults to `True` rather than `None` because full sits at the floor, so `True` and
"unset" resolve identically; switch to `None` only if we later add a `Session`/`Ensemble`-level default
to inherit from. Its only real use is the opposite of the global default: an instrument whose
`start_note`s should default to animatable.

### `note_on_and_off_only` deprecation

Redundant now — a scalar `start_note` is fully fixed by default. Deprecated (warn + map to
`start_note_fixed=True`, not forwarded inward so a single user call warns once) on the public part
builders where it was documented and used: `new_part`/`new_midi_part` and `add_soundfont_playback`/
`add_streaming_midi_playback`. `DeprecationWarning` is silent by default in normal script runs, so it
only surfaces for devs/CI — the right audience. It also sheds a footgun: it used to force `play_note`'s
envelope notes fixed (breaking their animation); the new resolution keeps envelopes free. The impl never
serialized the flag (`_to_dict` omits it), so `start_note_fixed` — which *is* serialized on the
instrument — is a strict improvement.

The impl layer is **cleaned out entirely**, not just deprecated: `_MIDIPlaybackImplementation` (internal,
`_`-prefixed; its public subclasses are built via `add_*_playback`, not by hand with this flag) no longer
takes or stores `note_on_and_off_only`. `this_note_fixed` is just `"fixed" in flags`, the expression/cc
send is unconditional (it also resets a reused channel's expression to full for a fixed note), and the
three `raise`s in `change_*` are gone (the instrument-level tier guard intercepts changes to fixed notes
first). The tier system — the `"fixed"` flag + `max_volume` set by the instrument — fully covers what the
flag did.

## Implementation touch points

1. **`ScampInstrument.start_note` (`instruments.py:633`)** — replace `max_volume=1` default logic with a
   `fixed` resolver: resolve `"auto"` against envelope floors and `default_fixed` (above), then pick
   `max_volume` (volume vs. headroom) and whether to add the channel-sharing `"fixed"` flag. Record the
   resolved tier in `note_info`.
2. **`start_chord` (`instruments.py:714`)** — thread `fixed` through (line 752).
3. **`_do_play_note` (`instruments.py:537`)** — pass `fixed="auto"` (or the explicit tier it already
   computes) instead of hand-building the `"fixed"` flag; behavior unchanged.
4. **`change_note_parameter` (`instruments.py:780`)** — the current hard *raise* becomes a
   `logging.warning` + `return`, keyed to the note's tier: **full** forbids all changes; **velocity**
   allows pitch/CC and down-only volume (a target above the note-on level warns and clamps); **none**
   allows all.
5. **Constructors (`new_part` etc., `instruments.py:144+`; playback-impl `__init__`s)** — add
   `start_note_fixed`; deprecate `note_on_and_off_only` (warn + map to `start_note_fixed=True`).
6. **MIDI / OSC** — untouched; MIDI already reads `max_volume` as note-on velocity, and the guard in (4)
   stops forbidden changes before they reach any implementation.

## Breaking change

Flips the default: `start_note(...)` + later `change_pitch`/`change_volume` now warns and no-ops until
the caller passes the right `fixed` tier. Envelope-argument `start_note` calls keep working via `"auto"`.

- Changelog: **Changed** in `scamp/CHANGELOG.md` `[Unreleased]`, with the migration spelled out.
- Docstrings: `start_note`, `start_chord`, `NoteHandle.change_pitch`/`change_volume`; the gnarly
  `max_volume` docstring (`instruments.py:643–650`) shrinks once `fixed` fronts it.

## Open questions

- `logging.warning` (scamp convention) vs. `warnings.warn` for the tier-violation warnings; the
  `note_on_and_off_only` deprecation should be a real `DeprecationWarning`. Leaning `logging.warning` for
  the former.
- Whether `start_note_fixed` also belongs on `Session`/`Ensemble` as a broader default (as
  `default_spelling_policy` is). Probably later, if wanted.
