# Clock position API: splitting the "Boilerplate TempoHistory" section

Follows `2026-07-07-scheduler-timing-policy-drift.md`, which reworked `Scheduler.projected_time()` to be
timing-policy-aware. Once `projected_time` became trustworthy, the *clock-level* accessors around it were
worth auditing.

## The section name had gone stale, and it was hiding a real seam

`Clock`'s "Boilerplate TempoHistory Functionality" section was accurately named when every method in it
forwarded to `tempo_history`. Step 2 of the cb2 rework broke that: `time()`/`beat()` stopped reading
`tempo_history` and started reading the **scheduler** (`scheduler_to_clock_time(scheduler.time())`),
explicitly *without* touching the committed pointer. So the section had silently become two unrelated
groups sharing a header:

- scheduler-derived position readout: `time`, `beat`, `time_in_master`, `wall_time_in_scheduler`, `status`
- genuine TempoHistory delegation: `beat_length`, `rate`, `tempo`, and the `absolute_*` trio

Split into **Clock Position** and **Tempo Properties**. The committed-vs-projected distinction now lives once
in the Clock Position header comment rather than being re-explained in each docstring.

## Two accessors removed

**`time_in_master()`** was literally `return self.master.time()`. The decisive argument wasn't redundancy but
that *being* a proxy, it had no way to forward `projected` — and its only caller was precisely an
out-of-clock-system reader that wanted it (below).

Keep in mind the name collision: **`TimeStamp.time_in_master` is a different, non-trivial thing** (it
converts a captured `scheduler_time` through the master's tempo history) and stays. `scamp/transcriber.py`
uses that one. Don't "clean it up" by analogy.

**`wall_time_in_scheduler()`** was `scheduler.wall_time() - self._start_time_in_scheduler` — a *wall*-second
count minus a *scheduler-ideal*-second count. Both are measured from the scheduler's start, so the subtraction
isn't nonsense, but what it returns is "this clock's wall lifetime **plus** whatever schedule lag had already
accumulated before the clock was born." Neither of the two quantities anyone would actually ask for. It only
ever looked right because ideal time tracks wall time closely.

Both are now raise-on-access stubs in the existing "Removed Legacy APIs" section.

## `_start_time_in_scheduler` was write-only; `parent_offset` is load-bearing

Worth having checked, because the two are assigned on the same line and look like siblings.

`_start_time_in_scheduler`: three writes, and exactly **one** read that isn't itself — inside
`wall_time_in_scheduler()`. Deleting that method orphaned the field entirely. Good riddance: its provisional
value at construction was `self.parent_offset + self.parent._start_time_in_scheduler`, which adds parent-
*beats* to scheduler-*seconds*. Dimensionally wrong, and only harmless because `_fork_wrapper` overwrote it
with `scheduler.time()` at the fire instant — meaning a clock whose fork wrapper never ran kept the bogus
value.

`parent_offset` stays. It's the term stitching a clock's local axis onto its parent's: added walking up in
`clock_to_scheduler_time`, subtracted walking down in `scheduler_to_clock_time`, and used by
`bring_up_to_date`. `test_fork.py::test_time_based_fork_parent_offset_survives_tempo_change` exists to guard
its finalization at the fire instant. On the master it's the offset into the scheduler's own axis, which is
what terminates the recursion.

## `Scheduler.lag()` is the quantity `wall_time_in_scheduler` was groping for

```python
def lag(self): return self.wall_time() - self.time()   # (now - _start_time) - _ideal_time
```

Real elapsed minus ideal elapsed = how far behind the absolute schedule we're running. Confirmation that this
is the right definition: `_reanchor_timing` sets `_start_time = now - _ideal_time` after fast-forwarding,
whose stated purpose is "reanchored to be exactly on time as far as absolute timing is concerned" — i.e. it
zeroes exactly this expression.

It is **scheduler-wide, not per-clock**, which is why no clock-level method could have expressed it honestly.
`Clock.status()` now prints `wall` (from `scheduler.wall_time()`) and `lag` side by side.

## `projected_time()` is not monotonic, and cannot cheaply be made so

Within one inter-event interval it's a clamped ramp, so it rises; the `min(progress, 1.0)` also guarantees it
never claims to have reached an event that hasn't fired. But the anchors defining the ramp all mutate, and
several mutations move it backward. Demonstrated from an outside thread against a master doing `wait(3)`:

```
   advancing:   0.500751 -> 0.701184   (sanity)
D  fast-fwd:    0.701198 -> 0.000000   backwards
C  past event:  1.001611 -> 0.000000   backwards
```

Both take an early return of `_ideal_time`. Other paths: the empty-queue free climb, later capped by a new
event; a live `timing_policy` retune; and an event with `t <= _ideal_time` firing, which advances
`_last_event_time` while `max()` pins `_ideal_time`, restarting the ramp from the same ideal with a later wall
anchor (observed as a repeatable ~24 ms drop at `wait(0.05)`).

**Rejected: a scheduler-global monotonic ratchet** (`_last_projected = max(_last_projected, new)`). It turns a
pure, lock-free read into shared mutable state — so an audio-thread read either takes a lock or races; reads
stop being idempotent, meaning a debug `print` perturbs behavior and tests go order-dependent; the ratchet is
scheduler-wide, so one consumer's read poisons another's and a `lag()`-style diagnostic gets lied to; and it
would *latch* any spurious forward spike, converting a transient into a permanent plateau followed by a freeze
until the schedule catches up. If a consumer needs monotonicity it should ratchet locally. Documented the
non-monotonicity on `Scheduler.projected_time()` instead.

**Also considered and dropped for now:** `_execute_event` writes `_ideal_time` (line ~604) and
`_last_event_time` (~608) as two unsynchronized stores, and `projected_time()` reads both without a lock, so a
reader landing between them pairs the new ideal time with the previous event's wall time, saturates `progress`,
and projects a whole interval into the future. Holding the pair in one tuple (`_event_anchor`) fixes it — an
atomic store means readers always see a matched pair — and a 1.7M-sample hammer confirmed the overshoot went
from present to zero. Reverted anyway: it's a real but narrow race, the residual backward steps (a different
cause, above) survive it, and it wasn't worth the churn mid-refactor. Worth revisiting on its own.

## Fallout: `time_in_master()`'s one caller, and a wrong turn worth recording

**First instinct was wrong.** `_timer_func` in `scamp/_soundfont_host.py` looked like an audio-thread callback,
so it got `projected=True`. It isn't. `timer_func` is called from `_record_as_well`, the decorator wrapping the
`Synth` MIDI methods (`noteon`, `cc`, …) — so it runs **on whichever thread issued the MIDI call**, which is
normally a clock thread inside a scheduled event.

That means the committed time was not merely adequate there, it was *better*: inside an event, `_ideal_time` is
exactly that event's scheduled time, so `current_sample` tracks the score and the rendered wav is immune to OS
jitter. Switching it to projected time would have injected wall-clock noise into the sample counts. This is why
it "always seemed to work fine."

### What `unsynced_time` actually was (traced, not guessed)

Never a clockblocks attribute. **scamp assigned it onto `clock.master` itself**, from `_ParameterChangeSegment`'s
raw unsynchronized animation thread — a hand-rolled monotonic (`max`) estimate accumulated by adding
`time_increment` per sleep:

```python
time_estimate = self.clock.master.time()
self.clock.master.unsynced_time = time_estimate
while ...:
    time.sleep(time_increment)
    time_estimate += time_increment
    self.clock.master.unsynced_time = max(time_estimate, self.clock.master.unsynced_time)
```

Introduced by `90e6cb8` ("Made wave recording handle pitch and volume bends smoothly", 2021-04-29). Its own TODO
states the problem exactly: *"Absolute_rate would be great, except that it doesn't update between synchronized
clock events."* — i.e. it was a private `projected_time` before there was one.

`fcfdcb2` ("Drive parameter animation with scheduled leaf actions") deleted the writer and left the reader
stranded; `479b375` dropped `fork_unsynchronized` outright. Hence the permanently-false `hasattr`.

### The predicate: two wrong turns, then measurement

`current_clock() is not None` is **not** "am I inside the clock system." It reads
`threading.current_thread().__clock__`, and **the scheduler thread never has one** (verified). Since
`NoteParameterAnimation.run` now emits its cc / pitch-bend as **leaf actions** (`_run_clock.schedule_action`),
which execute on the scheduler thread inside `_execute_event`, a `current_clock()`-only test would have routed
every gliss and dynamic envelope onto `projected_time()` — exactly where committed time is *exact*.

Measured, on a real recorded session with two forked parts, a gliss and a dynamic envelope — classifying every
`timer_func` call by calling thread:

| calling thread | calls |
|---|---|
| scheduler (leaf actions: cc / pitch_bend) | 700 |
| clock thread (noteon / noteoff) | 34 |
| anything else | **0** |

So the projected fallback was dead code. The OSC (`session.py:143`) and MIDI (`_midi.py:124`) listener wrappers
both run the user callback under `with clock.hold_scheduler():` *and* set
`threading.current_thread().__clock__ = clock`, so even those count as clock threads with the scheduler held
still. **`timer_func` is now simply `master.time()`** — which is what the code effectively did before, minus the
dead `hasattr`.

Keep in mind the general lesson: the wrapped methods (`sfload`, `program_select`, `noteon`, `noteoff`, `cc`,
`pitch_bend`) are *always* driven from inside the clock system. Committed time there is not a compromise, it is
the exact scheduled time, and the rendered wav is immune to OS jitter because of it.

Also fixed: `NoteParameterAnimation.run`'s docstring still described "a parallel, unsynchronized process
(`_animation_function`)". No such function exists. That stale docstring is what sent me down this path.

## Rejected: auto-selecting projected/committed by calling thread

Tempting, because a clock thread is synchronous with the scheduler and an outside reader isn't. But the
predicate is wrong at the edges — a clock thread from *another* family is an outsider too, `fork_unsynchronized`
workers sit in between, and the correlation inverts both ways (a clock may want `projected` to ask "how late am
I"; an outside thread often wants committed). The decisive case: `TimeStamp` captures `scheduler.time()` for
positions that must agree across clocks and reproduce run to run, and scamp's transcriber compares
`start_stamp.time_in_master == end_stamp.time_in_master` to find zero-length notes. A wall-interpolated default
would make captured positions depend on when a thread happened to be scheduled — jittery notated durations and
non-deterministic goldens under `CLOCKBLOCKS_TEST_COMPRESSION`. Beyond that, a function whose value depends on
its calling thread is untestable and unreadable at the call site, and `time()` is the library's most-used
accessor.

Resolved instead with explicit named accessors: `Clock.projected_time()` / `Clock.projected_beat()`, replacing
the `projected: bool` flag. Call sites now say which reading they mean.

## The real fix (Step 17): rouse committed time, don't sniff projected

Everything above treated the async-note problem as "pick projected vs committed per call." Marc pointed at the
right frame: the old `rouse_and_hold` idiom. A foreign callback used to (1) *rouse* the dormant clock so it
reoriented to now, (2) *hold* it while the callback ran, (3) release. The Step-2/8 rewrite kept the hold (the
execution lock, now `hold_scheduler`) but dropped the rouse — and the removal stub's justification is
subtly wrong: "lazy `beat()`/`time()` are live from any thread" means *not stale against the `tempo_history`
pointer*, **not** *advanced to the current wall moment*. Committed time between events is still the last event.

So the fix isn't to estimate the current time with projected at capture sites — it's to make committed time
*actually current* at the one boundary where a foreign thread reaches in. In the single-scheduler model the
rouse is trivial: **`Scheduler._rouse_to_now()`** sets `_ideal_time = projected_time()` and `_last_event_time =
now()`, i.e. injects a zero-duration event "now".

**Folded into the hold (Marc's call).** Rather than a separate `_quiescent_and_roused` wrapper on the foreign
branch, `Scheduler.while_quiescent` was renamed **`held()`** and rouses before yielding — so "held" means
*frozen at the current instant*, a coherent single concept (won't run events, won't advance committed, and
committed has been brought to now). The Clock-level `while_scheduler_quiescent` was renamed to match:
**`Clock.hold_scheduler()`** (`with clock.hold_scheduler():`), which now just returns `scheduler.held()`. The
rename touched clockblocks (def + docs + the two removed-API stubs) and eight scamp call sites (session ×6,
`_midi`, `transcriber`); scamp never referenced `Scheduler.held` directly.

The payoff of folding it into the primitive rather than the callback branch: the **external-thread tempo-change
path** (`_reschedule_after_tempo_change`, clock.py:180) also takes `held()`, and it *already* tried to apply
changes "from the current position" via `bring_up_to_date` — but that reads `self.beat()` (committed), so
between events it anchored at the **last committed beat**, not now. Its own docstring names the symptom
("retroactively reshape the segment the clock is currently napping through") without being able to fix it. The
rouse completes it: verified an external tempo change mid-wait now anchors at beat 0.501 (real now), not 0.0.
One primitive, two bugs.

**Third bug, found by simplifying that path.** `_reschedule_after_tempo_change` was `if current_clock() is None:
held()+tree else: tree`. That predicate is wrong: a tempo change driven from *another family's* clock thread
has `current_clock() is not None`, so it took the `else` (tree only) and **skipped `held()` on the target
scheduler** — mutating that family's tempo state while its scheduler could be firing. Collapsed the whole thing
to `with self.hold_scheduler(), self._tree_lock:` — `hold_scheduler` is a no-op *only* for a clock of this same
family, so cross-family correctly gets `held()`. This unifies on the one entry point and deletes the duplicated
(buggy) predicate. Test `test_tempo_change_from_other_family_takes_held` has teeth: it holds family A in a 0.3s
action and asserts the cross-family mutation doesn't return until A is idle; confirmed it *fails* on the old
predicate ("held() was skipped") and passes on the fix. A purely functional "did it reschedule" test does *not*
catch this — the reschedule fires under the tree lock either way; only the held()-blocking observation does.

Also (Marc's docstring edit, corrected): the lock-order note now reads `_execution_lock (via held) → _tree_lock`.
Tempting to add `_queue_change_condition` since `_rouse_to_now` now takes it, but that would mis-imply queue is
co-held with exec when tree is acquired — the rouse takes and *releases* queue before `held()` returns, so it
never coincides with tree. Left as a parenthetical, not part of the ordering.

Why this is strictly better than the projected-flag plan:
- **Nothing downstream changes.** `TimeStamp.now` and the recording timer keep reading committed. They're now
  correct for async notes for free, because committed *is* current inside the block. No flag threaded through
  clockblocks into scamp.
- **Monotonic.** `projected_time() ∈ [_ideal_time, next_event.t]`, so the bump is forward-only — committed
  never goes backward (the whole non-monotonicity worry evaporates for these consumers), and it never passes
  the next queued event, so nothing is skipped or mis-stamped when the run loop resumes.
- **Schedule preserved.** `_start_time` is left alone, so `_target_wall_time`'s `absolute_target` is unchanged
  and the next event still fires at its intended wall instant. Measured: `wait(2.0)` takes 1.994 s with a
  realistic 10-rouse/sec callback vs 1.999 s baseline.
- **Deterministic.** Genuine clock threads hit `nullcontext` and never rouse (they're already current). Only
  foreign entries reach `held()`: input callbacks and the external-thread tempo path. Synchronous scripts touch
  neither → goldens unchanged.

Safety rests on the rouse only touching `_ideal_time`/`_last_event_time` while `_execution_lock` is held, and on
a clock never being mid-turn during a hold (a clock turn runs *inside* `_execute_event` under that lock, so
`held()` blocks until it ends).

**One race the hold does *not* cover, found by asking "what runs outside the exec lock?"** The run loop's
STEP 1 reads `_ideal_time`+`_last_event_time` together (in `_target_wall_time`) under `_queue_change_condition`,
*not* the exec lock. Pre-rouse those fields had a single writer (the scheduler thread, in `_execute_event` /
`_reanchor`), so STEP 1's reads never raced. The rouse adds a *foreign* writer under the exec lock — different
lock from STEP 1's reads — so STEP 1 could read a torn pair (new `_ideal_time`, old `_last_event_time`) and
compute a briefly wrong wait. Benign (forward-only + clamped-to-head ⇒ no skip/reorder/drift; self-corrects next
pass) but real. Closed it: `_rouse_to_now` now writes under `_queue_change_condition` too. Lock order
`_execution_lock → _queue_change_condition` matches `_execute_event`; in the external-tempo path the queue lock
is acquired *and released* inside `held()`'s entry, before `_tree_lock`, so the global exec→tree→queue order is
never violated. Stress-checked: 863k concurrent rouses against a clock firing every 10 ms — no deadlock, committed
monotonic. Everything else outside the exec lock during a hold (queue mutations, `kill`, `set_fast_forward_goal`,
lock-free single-float `time()` reads) is pre-existing and benign.

Decision: rouse on **every** foreign `hold_scheduler` (matches old `rouse_and_hold`, cheap, keeps
committed honest for any foreign reader), and **keep** `projected_time()`/`projected_beat()` (still useful for a
true out-of-system poller that never grabs the scheduler — e.g. an audio meter).

### The non-monotonicity is smaller than Step 16 claimed

Traced the two "subtle" backward-step causes to one thing. Both are the gap between a **stale committed time**
and an **optimistic projection** snapping shut when you book an event below the current projection:
- `E ≤ committed`: fires immediately, `_ideal_time = max(committed, E) = committed` — doesn't drag committed
  back, we resume (Marc's intuition, confirmed). Projected only dips if committed was stale below it.
- `committed < E < projected`: `E` is overdue in *wall* terms, fires now, committed advances to `E`, projected
  corrects back to `E` (measured 0.50 → 0.30 → climbs). The free-climb had overshot a point where something
  needed to happen.
Booking *above* the projection is smooth (re-targets, no step). And since the rouse closes the stale/projected
gap, a reader entering via `hold_scheduler` sees neither step — only a passive poller does. Step 16's
docstring listed four equal causes; revised to say this.

## Verification

- clockblocks: 135/135 unittest (with rouse). scamp goldens: 30/30 (determinism preserved).
- Async note played 0.5 s into a 1 s wait: committed advances 0.0 → 0.65 on rouse, note transcribed at 0.65
  (its real play time) instead of 0.0.
- Synchronous back-to-back `beat()` reads in a forked function still share one beat; committed monotonic across
  371k foreign rouses (0 backward steps).
- `projected_beat()` leads `beat()` between events; `lag()` behaves; removed accessors raise actionable errors.

## Open

- `status()`'s output format changed (added `lag`). No golden depends on it today, but it's a printed API.
- The `_execute_event` torn read is still there (`_event_anchor` is the fix if we want it). Note the rouse now
  *reads* projected_time under `_execution_lock`, but the tear is between the run loop and lock-free readers, so
  it's unaffected either way.
- Step 16 is committed (two commits: clockblocks `232f40c`, scamp `43e8e16`). **Step 17 (rouse) is left
  uncommitted** at Marc's request, to review + fold into an amend.
- `cb2/` still carries the old copies of all of this. Untouched deliberately.

## Position accessors → properties (2026-07-15, for clockblocks 1.1)

Days after 1.0 shipped, Marc decided to fix the property/method asymmetry while no one has
adjusted yet. The principle adopted (and now stated in the Clock Position section comment):
**deterministic state is a property** — reading `clock.beat` twice between waits gives the same
answer — **while wall-clock samples that differ per call stay methods** (`projected_beat()`,
`projected_time()`, `wall_time()`). Converted: `Clock.beat/time/absolute_rate/absolute_tempo/
absolute_beat_length`, plus `Scheduler.time` and `TempoHistory.beat/time` (internals, no shim).

Back-compat shim: the Clock properties return `_CallableFloat`, a float subclass whose
`__call__` returns `float(self)` with a `DeprecationWarning` (`stacklevel=2`, so it's attributed
to the caller — visible in `__main__` scripts, suppressed by default filters inside libraries,
which is exactly the split we want for old scamp releases calling `master.time()` /
`clock.absolute_rate()` against new clockblocks). `__reduce__` pickles it as a plain float
(required extra `__new__` arg breaks default float-subclass pickling otherwise — caught by test).
Shim removal target: clockblocks 2.0. Tests: `tests/test_position_properties.py`.

Blast radius measured before doing it: scamp/src needed only 4 lines (1× `master.time()` in
`_soundfont_host.py`, 3× `absolute_rate()` in `instruments.py`) — Step 16/17 had already funneled
everything else through TimeStamp/scheduler. The bulk was ~110 mechanical call sites in examples
and tests, converted with a protected sed (`time.time()` shielded via placeholder; `wall_time()`/
`projected_*()` safe because the regex requires a literal dot before `beat`/`time`).
Verified: clockblocks 142/142 (was 136 + 6 new), scamp goldens 30/30 unchanged (the respelling is
behavior-neutral), no shim warnings triggered anywhere in either repo.

Also: scamp pyproject now `clockblocks>=1.1.0,<2` — the `<2` cap from the 2026-05-22 versioning
decision had never been applied; without it a future clockblocks 2.0 would break floating installs
(the 0.9.5 scenario again; remedy then would be: release fixed metadata + *yank* the uncapped one).

**Nothing shipped yet.** Release-day checklist:
1. Bump clockblocks version to 1.1.0 (rename `[Unreleased]` in its CHANGELOG), release clockblocks.
2. scamp release whenever convenient (its CHANGELOG `[Unreleased]` entry is written); until then,
   released scamp 0.10.0 + clockblocks 1.1 works — the shim warning it triggers is invisible under
   default filters.
3. Only after BOTH are released: update scamp_tutor (regenerate doc + example bundles, flip the
   "s.beat() is a method — call it" line in stumblingBlocks.txt to the property spelling). The
   tutor documents the released library, so it was deliberately left untouched now.
4. Old CHANGELOG 1.0 sections were left historically accurate (still show `clock.master.time()`
   etc.); the 1.1 entry carries the new spelling.

## Module-level `get_beat()` / `get_time()` (2026-07-25)

Marc asked whether `current_clock().beat` deserved a shorthand, floating `beat()`/`time()` or
`local_beat()`/`master_beat()` as candidate spellings. Settled on **`get_beat()` / `get_time()`**,
placed in a new "Context-inferring position readers" section of `utilities.py`.

The deciding argument was that the answer already existed: Step 13 of the 1.0 rework added
`get_tempo`/`get_rate`/`get_beat_length` as module-level readers over `_current_clock_or_raise`,
precisely because the underlying `tempo`/`rate`/`beat_length` are properties with no module-level
form. Position is a property in exactly the same way (since 1.1, above), so its absence from that
family was a gap, not a deliberate omission. No new concept, no new naming convention.

**Rejected `beat()` / `time()`.** Two concrete failures, not taste:
- `time` shadows the stdlib `time` module under `from scamp import *`, which is the tutorial idiom.
  `clockblocks/examples/nested_fork.py` does `import time` next to `current_clock().time` and would
  have broken.
- It fights the 1.1 property migration head-on. `_CallableFloat` emits a DeprecationWarning telling
  users to drop the parens from `clock.beat()`, while a module-level `beat()` would require them —
  the same word, callable in one place and not the other.

**Rejected `local_beat()` / `master_beat()`.** "Local" isn't the library's vocabulary (it says
*current clock* throughout). And `master_beat()` is `Clock.time_in_master()`, removed in 1.1 for
being a bare proxy that couldn't forward the projected/committed distinction — re-adding it at
module level reintroduces exactly that defect. `current_clock().master.beat` stays the spelling, and
reaching for the master *should* read as the more deliberate act.

Two implementation points worth keeping:
- They return **plain floats** (`float(...)` over the property). A new function has no old method
  spelling to support, so it must not propagate the `_CallableFloat` shim — otherwise the shim
  outlives its 2.0 removal target by leaking into APIs that never needed it. Guarded by
  `test_get_beat_and_get_time_return_plain_floats`, which also errors on any DeprecationWarning.
- **No `get_projected_beat()`/`get_projected_time()`.** The projected readings exist for callers
  *outside* the clock family; these functions require an active clock by definition, and from inside
  a clock thread committed time is exact. The asymmetry is principled and is stated in the section
  comment so no one "completes" the set later.

Verified: clockblocks 150/150, scamp goldens 30/30. Docs needed no edit — `scamp/docs/*.rst` are
gitignored and regenerated by `makePackageRSTs.py` from the exports.

### Folded into 1.1.0 by rewriting history (2026-07-26)

First pass shipped this as a *new* version on top of 1.1.1, which meant bumping scamp to
`clockblocks>=1.2.0`. Marc pointed out the premise was wrong: **1.1.0 and 1.1.1 were never
published.** Confirmed three ways before touching anything — PyPI's latest clockblocks is `1.0.0`
(scamp's is `0.10.0`), `origin/main` sits at `c34a132` = v1.0.0 with the 1.1.x commits local-only
(`ahead 5`), and `git ls-remote --tags origin` stops at v1.0.0. So the rewrite rewrote nothing anyone
had ever fetched, and the `>=1.2.0` pin was never needed — 1.1.0's existing floor already covers it.

**Decision: collapse to a single 1.1.0.** Two release commits and two tags for versions no user ever
saw is precisely the history that doesn't make sense; the `extract_absolute_tempo_envelope` fix is now
just another bullet in 1.1.0's Fixed section. Resulting clockblocks history:

```
c34a132 (origin/main, v1.0.0)   ← published, untouched
  ├ Fix scheduler start-time anchoring
  ├ Make position accessors read-only properties
  ├ Fix extract_absolute_tempo_envelope hang     (was 1.1.1)
  ├ Add module-level get_beat() and get_time()
  └ Release 1.1.0                    (tag v1.1.0, dated 2026-07-26)
```

scamp got the same treatment: a `Re-export clockblocks' get_beat() and get_time()` commit inserted
before `Release 0.11.0`, both changelog bullets under 0.11.0's Added, and v0.11.0 retagged. Its pin
stayed `clockblocks>=1.1.0,<2` — unchanged from what 0.11.0 already declared.

**Mechanics worth remembering, because scamp made this awkward.** scamp's working tree held ~83
entries of unrelated WIP (the examples reorganization, `INDEX.md`, staged renames and a staged
deletion), and the commit to amend was two commits below `main`. A rebase needs a clean tree, and
`reset --hard` would have destroyed that WIP. Interactive rebase is also unavailable in this harness.
Solution: do the surgery in a throwaway `git worktree add --detach` at the pre-release commit, replay
`Release 0.11.0` + the two later commits there with `cherry-pick`, then move the branch with
`git update-ref refs/heads/main <new> <old>` (the second arg is a compare-and-swap guard) and finally
`git checkout HEAD -- CHANGELOG.md pyproject.toml src/scamp/__init__.py` to sync *only* the three
rewritten files in the primary tree. The main working tree is never checked out or reset, so the WIP
is untouched — verified by diffing `git status --porcelain` before and after, which showed exactly
those three paths dropping out and nothing else.

Two smaller traps hit along the way:
- Cherry-picking a commit whose changelog entry sat under `[Unreleased]` conflicts once the release
  headings move. Resolved by hand-merging the bullet into the surviving `### Fixed` list rather than
  taking either side; `git diff <original> HEAD` then confirmed source and tests were byte-identical
  and only CHANGELOG/pyproject differed.
- `clockblocks.__version__` comes from `importlib.metadata`, i.e. the *installed* dist-info, so it
  kept reporting `1.1.1` after the pyproject went back to 1.1.0. `uv sync` at the workspace root
  rebuilds the editable installs and fixes it. Check this after any version rewrite — the source says
  one thing and the import says another until you re-sync.

Backup branches `clockblocks/backup/pre-1.1-collapse` and `scamp/backup/pre-0.11-amend` still point at
the pre-rewrite tips; delete them once the releases are out. Release order is unchanged from the 1.1
checklist above: clockblocks first, then scamp, then scamp_tutor.
