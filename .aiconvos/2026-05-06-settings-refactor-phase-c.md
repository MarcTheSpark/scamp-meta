# Settings refactor — Phase C and the "auto" pattern

Roadmap items: Phase C #6 (dataclass migration) and the "auto"-style
settings section. Both done across two sessions on 2026-05-06 and
2026-05-07.

## Item 6: dataclass migration

Started by wrapping `_ScampSettings` subclasses with a custom
`@_settings_class` decorator that derived `factory_defaults` from dataclass
fields. Marc pushed back: the cached `factory_defaults` dict is redundant
since `dataclasses.fields(cls)` is already the source of truth. Dropped the
decorator and the dict; replaced with a tiny `_factory_default(cls, key)`
classmethod that reads from fields() on demand. Each subclass now uses
plain `@dataclass(repr=False, eq=False)` (we initially used `init=False`
plus a custom dict-driven `__init__`, then refactored again to use
auto-init + `__post_init__` — see below).

`__dataclass_fields__` vs `fields(cls)`: `__dataclass_fields__` is a dunder
but documented in the dataclasses module. We use `fields(cls)` where
iteration is needed (the public API) and `cls.__dataclass_fields__[key]`
for the by-name lookup in `_factory_default` (avoids an O(n) scan per
lookup).

## The auto/resolver pattern

Old design: settings with `default_audio_driver = "auto"` sentinel.
Callers (e.g. `SoundfontPlaybackImplementation.__init__`) had to remember
to call `auto_detect_audio_driver_if_needed()` *before* reading the
setting. Fragile — caused at least one bug where the dict was keyed by
"auto" instead of the real driver.

Three options on the table from the roadmap:

1. A `Resolvable` field type that lazily computes on first read.
2. Resolve all such settings at `import scamp` time / first `Session()`.
3. Standardize the explicit-probe model with a single `resolve_pending()`.

Picked option 1 (lazy, no scattered call sites). Implementation:

- Class-level `_resolvers = {field_name: resolver_fn}` registry on each
  settings subclass, plus `_persist_after_resolve` set for fields that
  should be saved to JSON after resolution.
- Resolvable fields declared `field(default_factory=lambda: None)`. **The
  `default_factory` form is load-bearing** — a plain `= None` default
  creates a class-level attribute equal to None, which masks `__getattr__`
  even after the instance attr is deleted. `default_factory` makes the
  decorator strip the class attribute entirely, so once we delete the
  instance attr, lookup misses and falls into `__getattr__`. Verified
  empirically; comment on the field declarations explains it.
- `__post_init__` (in `_ScampSettings`) deletes the instance attr for any
  resolver field whose value is None, "arming" the lazy mechanism.
- `__getattr__` looks the name up in `_resolvers`, runs the resolver,
  caches via `object.__setattr__`, optionally calls `make_persistent()`.
  Singular `__getattr__` (not `__getattribute__`) means hot-path reads of
  normal fields pay zero overhead — important because settings ARE read
  in tight loops (`resize_parameter_envelopes` 3x per `play_note`,
  quantization weights per beat, etc.). Confirmed via audit before
  committing to the design.

### Failure-mode design

Considered the case where the resolver returns None (couldn't find the
thing). The resolver caches None on the instance and persists None to
JSON. On the next process load, `__post_init__` sees the None and deletes
it — so the cached None is per-process, not permanent. If lilypond gets
installed later, the next process re-searches. JSON-persisted `null` is
never a permanent verdict; only a real resolved string sticks across
processes. Self-healing.

For mid-process invalidation (e.g. user uninstalls lilypond): the
`_invalidate_lilypond_dir_if_stale()` helper in `_dependencies.py` checks
`engraving_settings.__dict__.get("lilypond_dir")` (using `__dict__.get`
rather than the attribute access so it doesn't trigger the resolver
itself) and `del`s it if the cached binary is gone. Called from
`get_abjad()` to give abjad-using paths a fresh chance.

### Three settings migrated

- `default_audio_driver` — persisted (probing is seconds-slow)
- `lilypond_dir` — persisted (filesystem walk)
- `music_xml_open_command` — **not** persisted, deliberately. Resolution
  is just `platform.system()` and a string literal — microseconds. Marc
  added a `persist=True` kwarg to `set_music_xml_application` so users can
  opt in if they override the default.

Backward compat: `_from_dict` translates the legacy `"auto"` string to
None for resolver fields, so existing user JSON files migrate
transparently.

## __init__ vs auto-init + __post_init__

First refactor used `@dataclass(init=False, ...)` with a custom
dict-driven `__init__`. Marc asked whether we could let dataclass
generate `__init__` and use `__post_init__` instead. Refactored:

- `@dataclass(repr=False, eq=False)` (init now auto-generated).
- `__post_init__` only handles resolver-field cleanup.
- JSON load logic moved to `_from_dict(settings_dict, suppress_warnings)`
  — it filters extras, warns on missing, translates "auto"→None, then
  calls `cls(**filtered_kwargs)`.
- `factory_default()` collapsed to `return cls()`.

Wins: `PlaybackSettings(default_soundfont="x")` now works with full IDE
support, schema migration is in a clearly-named place, and we're using
the dataclass machinery as intended. The only public-API change was the
constructor signature `cls(some_dict)` → `cls._from_dict(some_dict)`, but
the only caller was `_from_dict` itself, so no external breakage.

## Tangential

- Created `.claude/settings.json` with read-only-search permission
  patterns (grep/rg/find/git status/diff/log/etc.). Most of these are
  already harness-auto-allowed — explicit entries are belt-and-suspenders
  against compound-pipeline cases that the validator scrutinizes more
  strictly.
- Renamed `show_music_xml_command_line` → `music_xml_open_command`.
  "command_line" as a noun was clunky; "open_command" reads naturally.
