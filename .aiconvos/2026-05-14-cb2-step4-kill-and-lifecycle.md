# cb2 Step 4: kill, clock lifecycle, error types (2026-05-14)

Step 4 of the cb2 redesign — clock termination and the state machine around it.
This was a thorny one because the threading/notify protocol has subtle invariants
that aren't visible from any single function. Committed as `a9418f1` on cb2/master,
plus `8622e7b` for the PLAN updates. Companion scamp commit `b6b728f` on main.

## Three-state lifecycle (PENDING / ALIVE / DEAD)

Original plan was a two-state `_killed` flag, mirroring legacy clockblocks. We
ended up with three states because forked clocks have a non-trivial "before _process
runs" window:

- **PENDING**: forked, `start_delay` not yet elapsed; the new thread hasn't been
  created yet. `_start_new_clock` is the scheduler event that will eventually launch
  the thread and flip the clock to ALIVE.
- **ALIVE**: `_process` is running (or about to run) the user function.
- **DEAD**: either killed, or user function returned and `_process` cleanup ran.

Why PENDING matters: `fork()` and `wait()` should both reject calls on
PENDING clocks. A PENDING clock has no thread, so wait() from it doesn't make sense
(would have to come from a foreign thread, which is its own bug). A PENDING clock
being forked from is "pretty unlikely to matter either way" — but blocking it makes
the rule consistent: only ALIVE clocks can wait/fork. Cheap to enforce, easy to
explain.

The master starts ALIVE in `__init__` (no fork wrapper). Sub-clocks start PENDING
and flip to ALIVE at the top of `_process` once `start_delay` has elapsed.

## Error types

Three new error classes (plus a shared `ClockblocksError` base):

- **ClockKilledError** — raised from inside `wait()` STEP 4a when kill() wakes
  a parked clock. Caught by `_process` to unwind the user function cleanly.
- **DeadClockError** — raised at the entry check of `wait()` / `fork()` when the
  clock isn't ALIVE. Two realistic triggers:
  1. A non-clock thread calls kill() on a clock while its user code is running
     between waits. User code continues to its next wait, which then raises.
  2. Someone holds a reference to a dead clock and tries to fork from it
     after the fact.
- **WrongThreadError** — raised when wait() is called from a thread that isn't
  the clock's owner. Replaces the old "cross-thread wait silently works" behavior.

Important detail Marc caught: cascading kill from a clock-thread doesn't
generate DeadClockError, because the scheduler serializes user code — the
killer is the only thing running, so all victims are necessarily parked in
wait() and get ClockKilledError via STEP 4a. DeadClockError only fires when
kill() comes from *outside* the clock system.

## The notify protocol — why kill() does the work

Most of the session was untangling the condition-variable dance.

**The setup:** every clock has `_wait_event` (the clock parks on this inside
wait()) and `_scheduler_park_condition` (the scheduler parks on this in
`_wake_and_advance_to_next_wait_call`). The protocol:

1. Scheduler runs `_wake_and_advance`: sets `_wait_event` (releases clock),
   parks on `_scheduler_park_condition`.
2. Clock wakes, runs user code.
3. Next wait(): clock clears `_wait_event`, notifies `_scheduler_park_condition`
   (releases scheduler), parks on `_wait_event`.

When a clock is killed mid-wait, three threads care about each other:
the kill thread, the victim clock thread, and the scheduler thread parked on
the victim's `_scheduler_park_condition`. Initially we placed notifies in three
sites (kill, wait()'s STEP 4a, _process cleanup). Walking through it carefully:

- The notify in `_process` cleanup is needed for the **natural fork exit** path
  (user function returned without an error). Without it the scheduler sits
  forever waiting for a next wait that will never come — this was actually a
  *pre-existing bug* we discovered while running multi-fork tests.
- The notify in `kill()` STEP 3 is needed because if a victim's user code is
  running between waits at kill time, the scheduler is parked on its
  `_scheduler_park_condition` *right now*. Without an explicit notify in
  kill(), the sub-clock's `_process` cleanup would eventually do it — but only
  after user code reaches its next wait and raises DeadClockError, which could
  be arbitrarily long. For the **master**, there's no `_process` wrapper, so
  without kill()'s notify the scheduler would never be released at all
  (critical once `run_as_server` lands — the scheduler is a module-level
  singleton, so a stuck park would break any future master).
- The notify in wait()'s STEP 4a turned out to be redundant: STEP 4a is only
  reachable when state is DEAD, which only happens via kill(), which already
  notified in STEP 3. Walked the race carefully (the victim could wake and
  reach STEP 4a *before* kill() does its notify, but kill() still notifies
  immediately after — both paths leave the scheduler released). Removed it.

End state: **two** notify sites — kill() STEP 3 and _process cleanup. Each is
load-bearing for a specific path:

The removed STEP 4a notify, preserved here in case we ever revisit the decision
— the comment was hand-crafted and captures the original reasoning before we
added the kill()-side notify:

```python
if self._state is ClockState.DEAD:
    # if this clock is dead, the scheduler shouldn't be waiting on it anymore.
    # This notify_all is almost never needed: a forked clock releases the scheduler as
    # part of its cleanup when the forked function ends or is killed. On the master,
    # though, there's no such cleanup wrapper, so without this the scheduler would stay
    # parked on _scheduler_park_condition — and since the scheduler is a module-level
    # singleton, that would also break any future master clock created in this process.
    with self._scheduler_park_condition:
        self._scheduler_park_condition.notify_all()
    raise ClockKilledError()
```

That comment's reasoning was correct at the time, but became redundant once
kill() STEP 3 was added. If we ever remove kill()'s notify (e.g. as part of a
refactor that consolidates state-flip and event-removal), the STEP 4a notify
would need to come back — at minimum for the master case it describes.

| Path                        | Who notifies scheduler |
|-----------------------------|------------------------|
| Sub-clock natural exit      | _process cleanup       |
| Sub-clock killed (any thread) | kill() STEP 3        |
| Master killed               | kill() STEP 3          |
| Master natural exit         | (no-op; daemon dies)   |

## Eager parent detach in kill()

Originally kill() only flipped state and woke victims; the parent's
`_children` list was cleaned up later by each victim's `_process` cleanup.
Marc pushed for eager detach in kill() and the reason crystallized:

- For a victim killed **while parked in wait**: _process cleanup runs soon
  after ClockKilledError → fine.
- For a victim killed **between waits**: _process cleanup is delayed until the
  next wait raises DeadClockError → "could be arbitrarily long."
- For a **PENDING victim** killed during start_delay: `_start_new_clock` sees
  DEAD and returns without launching the thread → `_process` *never runs*.
  Without eager detach, the parent's _children list keeps a dead reference
  forever.

The PENDING case is the one that forced the change — there's literally no
other code path that will clean up that child reference.

Implementation: merged eager detach into the existing `for c in victims` loop
that does wake+notify, so we still have just two loops in kill() (one for
state flip + event removal inside `scheduler.held()`, one for
wake+notify+detach outside). Each victim detaches from its own parent, so
descendants get cleaned by their own `_process` cleanup (or by their own loop
iteration if they're killed in the same cascade) — the eager-detach in this
loop covers all victims, not just self.

## Cross-thread wait() is now forbidden

Added `WrongThreadError` at wait()'s entry check. This affects scamp:
`play_note` and friends accept a `clock=` kwarg that, when combined with
`blocking=True`, would call `clock.wait(...)` from a foreign thread. That
pattern is now illegal under cb2. Added a Roadmap note to scamp to audit
whether the `clock=` kwarg has any legitimate use case left (committed
separately as `b6b728f`).

`fork()` is *not* thread-restricted — it doesn't block, just schedules an
event. Marc considered restricting it for consistency, then decided against:
the new clock's `_process` sets its thread's `__clock__` correctly regardless
of who called fork().

## Pre-existing bugs caught along the way

- **set_id() bug** (fixed earlier in commit `ad02392`): the original
  `set_id()` was reading `next(self._child_counter)` instead of
  `next(parent._child_counter)`. Siblings got identical clock_ids. Inlined
  into `__init__` since there's no real reason for it to be a method.
- **Scheduler hang on normal fork exit**: when a forked function returned
  without ever calling wait(), the scheduler was left parked on the new
  clock's `_scheduler_park_condition` forever. Fixed by adding the
  notify_all in `_process` cleanup.

## PLAN.md restructure

- Step 4 marked done.
- Added Step 4.5 — legibility pass on wait/fork/kill (factor out helpers
  like `_resolve_start_delay`, possibly mirror wait()'s STEP comments in
  fork()/kill()). Explicit warning to not over-factor.
- Added "Possible 1.5 features" section at the bottom for things that aren't
  required for 1.0 parity but feel natural given the redesign:
  - **ScheduledMoment** + schedule_at by time (originally proposed as
    Step 8.5; moved here because it's a nice-to-have, not core).
  - **Externally-driven scheduler clock** — drive the clock tree from a DAW
    or external transport instead of wall-clock sleep. Should be a
    scheduler-side change only.

## Tests

`tests/test_kill.py` (11 tests) covers: kill on alive child, cascade from
master, kill on PENDING, normal fork exit doesn't hang subsequent waits,
sibling clock_ids are distinct (regression), state transitions, entry checks
on wait/fork against non-ALIVE, WrongThreadError from foreign thread.

`tests/test_fork.py` (4 tests) covers: basic fork, scheduled fork with
numeric beat, **tempo-change-reschedules-pending-fork** (regression — already
worked via the existing `_reschedule_self_and_descendants` mechanism;
"suggests good design that this issue is already covered").

Both files use setUp/tearDown to kill the module-level scheduler singleton
between tests, since a previously-parked scheduler would prevent the next
master's initial wake from firing.
---

## 2026-07-26 — Unkilled master hangs interpreter exit

Surfaced from `scamp/examples/marc/barlicity.py`: a Qt app whose `closeEvent`
had `session.kill()` commented out. Closing the window ended the Qt event loop
but nothing touched SCAMP, and the process then hung while spraying
`RuntimeError: cannot schedule new futures after shutdown` — with music still
audibly playing.

### The mechanism (worth remembering, it's counterintuitive)

CPython's `Py_FinalizeEx` order is:

1. `threading._shutdown()` — runs `threading._register_atexit` callbacks
   (LIFO), *then* joins all non-daemon threads.
2. plain `atexit` callbacks.
3. interpreter finalization, where daemon threads are finally killed.

`ThreadPoolExecutor` workers have been non-daemon since 3.9 and are joined by
`concurrent.futures.thread._python_exit`, registered in stage 1. Our forked
clock functions usually loop forever, so that join never returns → stuck in
stage 1 forever.

The trap: **the scheduler being a daemon thread does not help.** Daemon threads
die in stage 3, which is never reached. So the scheduler keeps ticking normally
throughout the hang, and every fork it fires hits the pool that `_python_exit`
already flagged as shut down → the error spam, one per note. Stage 2 never runs
either, which is why scamp's `atexit.register(end_all_notes)` never fired and
notes were never released.

### Fixes

- `_live_master_clocks` (a `WeakSet`) + `_kill_live_masters_at_exit`, registered
  via `threading._register_atexit`. Must be stage 1, not plain `atexit` —
  `atexit` runs *after* the join, i.e. after the hang. Ordering works out
  because `from concurrent.futures import ThreadPoolExecutor` at the top of
  clock.py already registered `_python_exit`, and stage-1 callbacks are LIFO, so
  ours runs first: kill the masters, forked functions raise `ClockKilledError`,
  `_fork_wrapper` catches, workers unwind, join completes.
  `threading._register_atexit` is private, but it's what concurrent.futures
  itself uses; there's an `atexit` fallback behind a `hasattr` guard.
- `_start_new_clock` now detaches the child if `_run_in_pool` raises. The child
  is appended to `_children` at fork time but was only ever detached inside
  `_fork_wrapper`, so a launch failure left it attached forever. Visible in the
  original traceback as the growing
  `Clock('PIANO_CLOCK')[DO_PLAY_NOTE, DO_PLAY_NOTE, ...]` repr. Independent of
  shutdown — any `submit` failure would also have stalled
  `wait_for_children_to_finish()`. The detach happens *outside* the
  `with child._scheduler_park_condition` block on purpose, to preserve kill()'s
  lock order (`_tree_lock` → park condition) rather than inverting it.

### Deliberately not done

Explicit `kill()` is still the documented right thing; the hook only downgrades
forgetting from "hangs the process" to "leaks until exit". Considered and
rejected: making the master auto-kill when its owning thread ends (no clean
hook for it, and a master legitimately outlives the thread that made it in the
`run_as_server` case).

Verified with a repro (master + forever-looping forks that fork children, main
thread falls off the end): hangs with the hook neutered, exits 0 with it.
150 clockblocks tests and 30/30 scamp example tests pass.

### 2026-07-27 follow-on — kill() releases the owning thread's `__clock__` tag

Marc's reframing, which settled a question the exit-hook work had left open.
The two errors mean different things:

- **`DeadClockError`** = you hold a *reference* to a clock and it has died.
  (`c = fork(...)`; the function ends; you call `c.wait()`.)
- **`NoActiveClockError`** = you're using the *implicit* clock — module-level
  `wait()`/`fork()`/`get_beat()` — and there isn't one on this thread.

Under that split, killing a master should clear its thread's `__clock__` tag,
because afterwards there genuinely is no active clock there.

**Precedent already in the code:** `run_as_server()` does exactly this — it
hands ownership to the background thread and sets the caller's tag to `None`,
and its docstring says "the calling thread relinquishes ownership (its
`current_clock()` becomes None)". `kill()` was the inconsistent one.

**This supersedes the weakref/tombstone idea** discussed just before. I had
argued the strong tag was "load-bearing" because `DeadClockError` after a kill
depends on `current_clock()` still finding the corpse — but that assumed the
old semantics were correct, which was the question. Clearing the tag gets the
GC benefit deterministically, no weakref: verified that a killed master is now
fully collectable (both with and without forks, once the pool worker has
unwound — the earlier "still pinned" reading was just unwind timing, not a
leak). An unkilled master is still uncollectable by design, since its scheduler
is live; that's what the atexit hook is for.

**Scope: master-only.** Considered clearing tags for every victim in the kill
cascade, and rejected it. A killed-but-still-awake child's next module-level
`wait()` would raise `NoActiveClockError`, but `_fork_wrapper` catches only
`(ClockKilledError, DeadClockError)` — so it would escape to
`_threadpool_error_callback` and log instead of unwinding quietly. Children
already get untagged deterministically in `_fork_wrapper`'s `finally`, so eager
clearing buys nothing there and costs that catch clause. The master is the case
worth fixing: its tag otherwise persists for the life of the thread.

**`_tagged_thread` bookkeeping.** `kill()` is explicitly safe from any thread,
so `threading.current_thread()` inside it is the *killer's* thread, not the
victim's. The clock therefore records which thread it tagged, set both in
`__init__` and in `run_as_server` (assigned before `start()`, so a kill arriving
mid-handover still finds the right thread). `kill()` clears it only if that
thread's tag is *still this clock*, so an out-of-order kill can't steal the tag
from a newer master on the same thread — covered by a test.

**Compatibility turned out to be a non-issue.** Every pre-existing
`DeadClockError` assertion goes through an explicit reference
(`test_kill.py:64,69,76`, `test_schedule_action.py:119`), so none changed.
scamp is unaffected too: `_do_play_note` captures `clock = current_clock()`
*before* waiting and then calls `clock.wait()`, so its
`except (ClockKilledError, DeadClockError)` unwind still fires; and
`_resolve_clock` has a fallback chain that never yields `None` (cleared tag →
ensemble-as-Session → fresh `Clock()`), so there's no `AttributeError` hazard.

Checked the one corner the analysis flagged: `hold_scheduler()` (clock.py:441)
reads the tag, so on the now-untagged owning thread it takes the
`scheduler.held()` branch against a dead scheduler. Probed it — enters and exits
cleanly, no hang.

Not done: improving `NoActiveClockError`'s message when the thread's clock was
killed (rather than never having had one). Cheap to add later via a marker left
on the thread; deliberately skipped as polish.

**Follow-up: the polish, and the broad `except`.** `NoActiveClockError` now
carries a reason. `kill()` and `run_as_server()` each leave a
`__no_clock_reason__` note on the thread as they release it, and
`utilities._no_active_clock_error()` appends it — all six raise sites in
utilities.py route through that one helper. Crucially the note is a **plain
string, never the clock**: stashing a reference there would undo the
collectability the tag-clearing just bought. Re-verified GC after adding it.

The `run_as_server` variant is the more useful of the two, since it points at
the actual fix ("fork on the returned clock object directly rather than via the
module-level helpers") for a mistake the docstring already warned about.

On `except Exception: pass` in `_kill_live_masters_at_exit` — PyCharm's
complaint is really about the silent `pass`, not the breadth. Breadth is right:
`kill()` touches the scheduler, tree locks and thread pool, and one master
failing must not abort the loop and strand the others, which is exactly the hang
the hook exists to prevent. Narrowing to `RuntimeError` would be false
precision. `BaseException` would be wrong — KeyboardInterrupt/SystemExit during
shutdown should propagate. So: kept `Exception`, replaced `pass` with
`logging.exception(...)`, which both silences the inspection and makes a real
bug in `kill()` visible. Safe at this point in shutdown because we run in stage 1
(`threading._shutdown`), before logging's own atexit teardown in stage 2.

---

## 2026-07-28: two holes in `_fork_wrapper`'s cleanup path

Both came in via `../scamp/bugs/resolved/hanging_fork.py` — a clarinet note that never got
cut off and a process that wouldn't exit. Neither was a scamp bug.

**1. A clock that outlives its own function severed its subtree.** The lifecycle
note at the top of clock.py enumerated four termination paths but never said what
happens to a clock's *own* children when its function returns. The answer was:
nothing — `_fork_wrapper` detached the clock from its parent and marked it DEAD
while its children were still running. Those children stayed ALIVE with wakeups
queued, but were now unreachable from the master:

- `master.descendants()` didn't list them, so `kill()`'s victim walk skipped them
- an ancestor's `wait_for_children_to_finish()` returned as soon as the *direct*
  child detached, i.e. early
- at exit, `_kill_live_masters_at_exit` killed the master and its scheduler, and
  the orphans' non-daemon pool workers parked forever → interpreter hang

In the scamp repro the note-off was one of those stranded wakeups, hence the
infinite clarinet.

First fix attempted was **wait-on-end** (the clock parks in
`wait_for_children_to_finish()` before detaching). Marc rejected it in favor of
**kill-on-end plus a warning**, which is what shipped. Worth recording how that
argument resolved, because both directions have real evidence behind them:

- Against kill-on-end: scamp implements `play_note(..., blocking=False)` as a
  forked `DO_PLAY_NOTE` clock, so killing children truncates any non-blocking
  note that outlasts its function (measured: a 4.0-beat note recorded as 0.5).
- Against wait-on-end at the *master* level: `fork(random_notes); wait(2)` with
  an infinite fork would never terminate. `wait(2)` is the composer saying how
  long the piece is.
- Decisive point: the truncation is only unacceptable when it's *silent*. A
  warning makes kill-on-end self-documenting and covers both confusions at once
  — the blocking=False note and the "forks at end of script do nothing" case.

Considered and rejected: **re-parenting orphans to the grandparent**. Tempo
nesting and `parent_offset` are defined relative to the actual parent, so
re-parenting would silently change the children's timing — the whole point of
the hierarchy.

**A non-obvious consequence: kill() is only a signal.** The victim's thread
still has to wake, raise `ClockKilledError` and run its cleanup — and in scamp
that cleanup is where `note_handle.end()` runs, stamping the note's end with
`TimeStamp.now(clock)`. If the terminating parent returns before that happens,
it releases the scheduler, the master races on, and the note gets recorded
ending at some arbitrary later beat. This showed up as a flaky 1-in-5 failure
(0.5 vs 6.0 beats) under `fast_forward`, where beats fly past. Fixed with a
`Clock._unwound` Event set as the very last act of `_fork_wrapper`;
`_terminate_unfinished_children` waits on it (bounded by `_UNWIND_TIMEOUT`)
before returning. PENDING children are excluded — they never ran
`_fork_wrapper`, so their event is never set.

Also worth knowing: `master.beat`-stamped side effects of *any* `kill()` have
this same asynchrony. We only need the join on the natural wind-down paths,
because those are the ones where musical time keeps going afterward.

**Follow-up (2026-07-29): the join moved into `kill()` itself.** The paragraph
above turned out to understate the problem. A user calling `some_fork.kill()`
while the master keeps running hits exactly the same race — measured 6 late out
of 10 under fast-forward, cleanup stamped at beat 4.5 for a kill at beat 0.5. So
`kill()` now waits, and `_terminate_unfinished_children` just calls it and
inherits the behavior (its own wait loop is gone).

Supporting evidence that the async default was the wrong one: scamp had
*already* hand-worked around it, independently. `_ParameterChangeSegment.abort_if_running`
stamps `end_time_stamp` **before** calling `self._run_clock.kill()`, precisely
because it can't trust the victim to stamp itself in time. Two independent
workarounds for one asynchrony.

Three classes of victim can't be joined and are skipped:

- **The calling thread's own inheritance line.** `victims` always includes
  `self`, so `current_clock().kill()` and `s.kill()` would wait on the very
  thread doing the waiting. Computed with `iterate_inheritance()`.
- **Masters.** `_unwound` is set only by `_fork_wrapper`, which a master never
  runs — so waiting on one burns the whole timeout. `s.kill()` at the end of a
  script is the most common kill there is; missing this filter would be a 5s
  stall on nearly every SCAMP program (and every test `tearDown`).
- **PENDING clocks**, which have no thread yet.

The ALIVE test has to be captured *before* kill flips everyone to DEAD, and the
wait has to happen *outside* `_tree_lock` — the unwinding clock takes that lock
to detach itself.

**Rejected concern, recorded so it isn't re-litigated:** I initially flagged a
deadlock risk where a victim's cleanup calls `scheduler.held()` while the killer
holds `_execution_lock`. It doesn't hold up. `held()` is never called from scamp
at all, and the only route to it — `hold_scheduler()` — already short-circuits
to `nullcontext()` when the calling thread is inside the clock system, which an
unwinding victim still is (`__clock__` is cleared at the very end of
`_fork_wrapper`, after the user function *and* the cleanup).

The one path worth watching is scamp's `abort_if_running`, which now joins while
holding `_note_info_lock`. Stress-tested with 480 overlapping animation aborts:
0.34s, no stalls. Fine, but it's the place a future deadlock would show up.

**`terminate_forked_children()` — giving the warning a second answer.** The warning
originally pointed only at `wait_for_children_to_finish()`, which reads as though
waiting is the *correct* resolution and cutting off is a mistake. It isn't:
plenty of pieces genuinely want a layer to stop when its parent does. So there
are now two symmetric ways to answer it, and the warning names both. Since it
fires only when children are *still* unfinished at wind-down, calling
`terminate_forked_children()` silences it structurally rather than by filtering —
there's nothing left to report.

Mirrors `wait_for_children_to_finish` all the way through: `Clock` method,
module-level helper in `utilities.py` acting on `current_clock()`, re-exported
from both `clockblocks` and `scamp`. `_terminate_unfinished_children` now
delegates to it, so there's a single implementation of "kill the children."

Named for the verb the user typed (`fork`) rather than for the internal model
("child clocks") — someone reading the warning after writing `fork(part_a)`
connects `terminate_forked_children` without first having to learn that forks
*are* child clocks. Costs a little visual parallelism with
`wait_for_children_to_finish`, judged worth it.

**`logging.warning`, not `warnings.warn` — and why the reverse was wrong.** The
message started life as a `warnings.warn` with a dedicated
`UnfinishedChildrenWarning` category. Removed. `warnings.warn` *can raise* under
`-W error` / `simplefilter("error")`, and that turned out to cost real
machinery:

- Warning before terminating let the raise skip the termination, reproducing the
  original orphaned-subtree bug exactly: grandchild not terminated, subtree
  detached, unreachable by `kill()`, non-daemon pool thread parked at beat
  10000, interpreter hung. Needed a terminate-then-warn ordering constraint plus
  a regression test to hold it in place.
- `threading._shutdown` does not guard its atexit calls, so an escaping warning
  aborted `_kill_live_masters_at_exit` and **no** master got killed at all
  (measured: two live masters, `masters actually killed: []`). Needed its own
  `try/except Exception` with a paragraph explaining itself.

All of that bought type-based filtering nobody asked for, plus `assertWarns` in
tests — which `assertLogs`/`assertNoLogs` do just as well. With `logging.warning`
the raise can't happen, so the ordering constraint, its regression test, and the
extra atexit guard all went away (`_terminate_unfinished_children` now sits
inside the same try as `clock.kill()`).

**That try/except is now gone too.** Marc asked the obvious follow-up: with the
warning no longer able to raise, where can `kill()` throw? Answer: nowhere
identifiable. `scheduler.kill()` is two lines, `remove_events` is pure Python,
`_pool.shutdown(wait=False)` doesn't block or raise, and `_await_unwind` only
waits on events. I argued for keeping it on generic atexit-fan-out grounds and
measured the difference (`scratchpad/atexit_guard.py`: two masters, the first
one's `kill()` monkeypatched to raise, the second holding a fork parked at beat
10000):

- guard in place → `ERROR:root:Error killing Clock('A')` with full traceback, then
  B winds down normally, `B alive at true-exit: ClockState.DEAD`.
- guard removed → `Exception ignored in: <module 'threading'>`, and
  `B alive at true-exit: ClockState.ALIVE` — B stranded, its fork never
  terminated, because an escaping exception aborts the remainder of
  `threading._shutdown`, including `concurrent.futures`' own worker-join hook.

Note what did *not* happen: **both exited 0.** The earlier comment claimed the
guard prevents the hang this hook exists for, and that's an overstatement — with
the join step skipped there's nothing left to block finalization.

Two further overstatements Marc caught, which shrink the justification to one
line:

- "protects other people's shutdown code" is backwards. `threading._shutdown`
  pops LIFO, and `concurrent.futures`' `_python_exit` registered when `clock.py`
  imported `ThreadPoolExecutor` (see the comment above the `_register_atexit`
  call) — so we pop *first* and the stdlib hook after. A downstream library
  registers after clockblocks is imported, so its hook pops *before* ours: if it
  raises, we're the ones skipped, and our guard can't help.
- "buries the cause" is false. `Exception ignored in: <module 'threading'>` wraps
  a complete traceback naming `clock.py` and the raising frame; the GUARD=0 run
  shows it. The only loss is stderr instead of logging handlers.

That left the guard's entire value as "master 2 still gets killed when master 1's
wind-down throws," in the single-master case that is ~everything. Marc's closing
argument killed it outright, and it's the sharpest one: **the failure mode is
self-cancelling.** This hook exists to prevent a hang, and the hang comes from
`concurrent.futures._python_exit` joining non-daemon pool workers parked in
`wait()`. An exception escaping our hook abandons the rest of
`threading._shutdown` — *including that join*. The same abort that skips our
cleanup removes the only thing that could have hung, which is why both measured
runs exited 0.

Nor is anything audible lost: scamp's consequential cleanups
(`instruments.py:390` `end_all_notes`, `_cleanup_port_connections`,
`stop_recording`) are all plain `atexit`, i.e. stage 2, which still runs after a
stage-1 abort — the GUARD=0 run printed its stage-2 line. What the guard bought
was tidying a dying master's `_children` list microseconds before the objects
became garbage.

**Deleted.** The loop is now four lines with no error handling. Worth remembering
if a future change makes the hook do something with real consequences, at which
point the calculus changes — but "we're shutting down and it doesn't hang" is the
right test to re-apply then, rather than restoring the guard reflexively.

The `curve_shape is ignored when align_to is a fixed point` warning moved to
`logging.warning` in the same pass — Marc confirms its `warnings.warn` was never
a considered choice, and it degrades gracefully (ignores the argument and carries
on), so it belonged in `logging` all along. scamp's 33 `logging.warning` calls
are the house style.

**The one `warnings.warn` left in clockblocks is the right one:** the
`DeprecationWarning` on the callable-position-property shim (`clock.py:272`,
`clock.beat()` → `clock.beat`). Deprecations are the canonical `warnings` use
case — `-W error::DeprecationWarning` in someone's CI is exactly how you want a
removal-in-2.0 to surface, and `pytest`/tooling keys off the category. Two tests
depend on that (`test_position_properties.py`, `test_module_api.py:217`).

Also fixed in passing: `_await_unwind`'s timeout branch called `logger.warning`,
but `clock.py` never defines a `logger` — a latent `NameError` on a path no test
covers. Now `logging.warning`, matching `logging.exception` elsewhere in the
file.

Scamp customizes the message via `Clock.description`, set on the
internal `DO_PLAY_NOTE`, `PARAM_ANIMATION*` and `PEDAL_CHANGE` forks. Note that
`pedal_change` already forked on the *master* precisely so the re-press would
survive its parent ending — the codebase had already adopted "fork on master"
as the fire-and-forget idiom, which is a nice independent confirmation that
kill-on-end matches existing intent.

**2. An erroring forked function froze the entire family.** Steps 3–5 of
`_fork_wrapper` (detach, release scheduler, done_callback) sat in the `try`, not
the `finally`, so an unhandled user exception skipped all three. The scheduler
stayed parked on the dead clock's `_scheduler_park_condition` and *every* clock
in the family stopped advancing — silently, since the traceback that did print
looked like it only concerned the one fork. Found while reading the block for
bug 1, not reported. Moved 3–5 into a `finally`, with a nested `finally` keeping
the step-6 untagging last so it survives a raising `done_callback`.

Worth remembering that this class of bug is invisible to the test suite unless a
test asserts *the master keeps advancing*; asserting only on the forked clock's
own state passes either way.

## 2026-07-29 — `done_callback` ran after the scheduler was released

Marc spotted this reading `_fork_wrapper`: a naturally-ending sub-clock released
the scheduler (old step 4) and *then* ran `done_callback` (old step 5), so the
callback executed with the family free to advance. Same class as the note-end
timestamp bug, in the one place we hadn't looked.

Measured with `scratchpad/done_callback_time2.py` (child ends at beat 1, master
then fast-forwards through `wait(20)`, callback records `master.beat`):

| callback work | before | after |
|---|---|---|
| none | 1.0 | 1.0 |
| 2 ms | **20.0** | 1.0 |
| 50 ms | **20.0** | 1.0 |

The zero-work column is the trap: it reads correctly only because the callback
beats the just-notified scheduler thread to the CPU. 2ms is enough to lose the
race, and then it loses it *completely* — 19 beats, not a small drift, because
the master is fast-forwarding.

Fix: swap the two steps, with the release in a `finally` around the callback.
That ordering is load-bearing in both directions — the callback must precede the
release to see the right time, and the release must be guaranteed or a raising
callback leaves the scheduler parked on a dead clock and freezes the family
(exactly the erroring-fork bug from earlier in this file). Both directions now
have a test: `test_done_callback_runs_at_the_moment_the_clock_ended` and
`test_raising_done_callback_still_releases_the_scheduler`.

### The killed path needed the same treatment

I first shipped this covering only the natural end, on the grounds that a killed
clock has its `_scheduler_park_condition` notified inside `kill()` already. Marc
rejected that: a killed clock should observe `done_callback` at the moment of the
kill too. He also had the fix — kill()'s notify is idempotent noise except on the
master, so gate it on `is_master()`.

That's right, and the comment defending the sub-clock notify was wrong. It
claimed the notify avoids an "arbitrarily long" delay when the victim's code is
between waits, since `_fork_wrapper`'s cleanup then has to wait for the code to
reach its next `wait()`. But the scheduler is parked on that clock while its code
runs *whether or not it was killed* — killing can't interrupt running user code.
So the notify's only effect was to let the family advance concurrently with the
doomed clock's teardown, i.e. the race. And `ALIVE` implies `_fork_wrapper` is
running (that's what sets `ALIVE`), so a sub-clock victim is always guaranteed to
reach its own release; only a master, with no wrapper, cannot.

The notify actually only *does* anything when the scheduler is parked on the
victim, which is narrower than it looks: not when a parent's
`_terminate_unfinished_children` kills a parked child (the scheduler is parked on
the parent), but when the victim's own code is running. Two ways in, both
measured with `scratchpad/killed_done_callback.py`, both reading **20.0** before
and **1.0** after:

- `self` — the clock calls `current_clock().kill()` on itself.
- `foreign` — a non-clock thread kills it mid-computation between waits.

Tests: `test_done_callback_of_a_self_killed_clock_runs_at_the_moment_of_the_kill`
and `test_done_callback_of_a_clock_killed_from_a_foreign_thread`.

Known cost: a foreign thread killing a clock stuck in a long non-yielding
computation now stalls the family until that computation returns, where before the
family carried on. That's the same stall you'd get without killing it at all, and
`_await_unwind`'s 5s bound already depended on the victim yielding, so it isn't a
new hang — but it is a behavior change worth remembering.

## `to_await` exclusion narrowed to the acting clock only (2026-07-30)

`kill()` originally excluded the acting clock's whole inheritance line (self +
ancestors) from the unwind wait, with the justification "we ARE that thread." That
reasoning is only literally true for **self** — an ancestor runs on its own thread.
The only victim on the calling thread is the acting clock itself, so the filter is
now just `c is not current_clock()` (plus the master and PENDING/ALIVE checks).
Ancestors and descendants are all on other threads and are waited for.

Proven safe: narrowing to self-only passed the full kill+fork suite and a
hand-built grandparent scenario with no deadlock. It's deadlock-free because
`kill()` detaches every victim from its parent (STEP 3) *before* `_await_unwind`
(STEP 4), so a killed ancestor's own `_terminate_unfinished_children` finds no
children and never loops back to wait on us. **This safety depends on
detach-before-await ordering** — if that ever changes, revisit.

Test: `test_killing_a_non_master_ancestor_waits_for_its_unwind` (verified it fails
under the old full-inheritance exclusion).

### DEFERRED TO CB-3 — the synchronicity payoff is masked in CB-2
The point of waiting for a killed ancestor is that its cleanup should stamp at the
kill beat, not later. That only fully works once CB-3 gates the sub-clock scheduler
notify on `is_master()`. Measured with `scratchpad/ancestor_sync.py`
(master fast-forward → P → C, C kills P, P cleanup slow):

- CB-2 as-is (self-only, unconditional notify): P stamps at beat **50** — the
  notify releases the scheduler regardless of the wait.
- self-only **+** CB-3 notify-gating: P stamps at beat **1**. Correct.

So in CB-3, alongside gating the notify, add a beat-accuracy ancestor test (assert
the ancestor's cleanup stamps at the kill beat, not a run-away fast-forward beat).
The CB-2 test above only covers the *blocking* behavior (kill waits), which is all
that's observable until the notify is gated.
