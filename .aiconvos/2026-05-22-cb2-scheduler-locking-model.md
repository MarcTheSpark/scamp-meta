# cb2 scheduler/clock locking model

*Topic started 2026-05-22. Commits: `566ea54` (code), `fec332a` (PLAN). Corresponds to PLAN.md Step 8, redone.*

## Why this happened

Started as a question about why `kill()` wrapped its heap surgery in `scheduler.held()`. Investigating
that gate turned up several problems with the Step-8 "unconditional `held()`" design:

- `held()` was a coarse `threading.Event` gate — **non-reentrant and stompable**. Two concurrent
  holders (a tempo change racing a kill, or two tempo changes) clobbered each other: the first to
  exit called `_release()` and un-paused the scheduler while the second still thought it was held.
- It was **level-checked at the top of the run loop**, so it could not preempt an action already in
  flight. So it didn't actually prevent the scheduler from waking a clock (whose `wait()` cleanup
  mutates `tempo_history`) during a tempo rewrite — the exact race it was supposed to stop.
- Separately, the run loop had a **lost-wakeup**: `_get_next_event` (peek) and `_wait_for` (timed
  wait) were two separate `_queue_change_condition` acquisitions. A `reschedule` landing in the gap
  notified into an empty room, and the loop then slept the stale, longer duration → event fires late.
- `fork()` and `kill()` shared no lock on the clock tree → a fork concurrent with a kill could leave
  an orphaned child the kill didn't see.

## The model we landed on

Three primitives, each with one job (block comment lives in `Scheduler.__init__`):

- **`_queue_change_condition`** — guards the heap + signals changes. Run loop holds it across
  peek+compute+wait as one critical section (kills the lost-wakeup). Plain (non-reentrant) `Lock`
  on purpose: all critical sections are flat, so accidental reentrancy should deadlock loudly.
- **`_execution_lock`** — held by the run loop for the *full duration* of each action's execution,
  so "held" == "an action is running". Exposed as `while_quiescent()` for external mutators.
- **`_clock_tree_lock`** (per family, on master, via `Clock._tree_lock`) — serializes
  fork/kill/tempo on the tree.

Lock order: **`_execution_lock` → `_tree_lock` → `_queue_change_condition`.** The run loop never
holds the queue condition while taking `_execution_lock` (releases it before executing). The decorator
takes `_execution_lock` (via `while_quiescent`) before `_tree_lock`, and that order is load-bearing
(see below). The scheduler itself never touches `_tree_lock`, so it can't be a node in a tree-lock
cycle.

`_reschedule_after_tempo_change` gates on `current_clock()`: external thread → `while_quiescent()` +
`_tree_lock`; clock thread → `_tree_lock` only (the scheduler is already frozen on that clock, so
nothing is awake to race, and calling `while_quiescent()` from it would self-deadlock).

## Key insight (the thing to remember)

`_execute_event` does **not** run-and-return — for a clock wakeup it parks on the clock's
`_scheduler_park_condition` and blocks until that clock's thread reaches its next `wait()`. So it's a
cross-thread handoff. **You must not hold across that handoff any lock the woken clock's thread needs.**
That's why:
- holding `_queue_change_condition` across `_execute_event` deadlocks (the woken clock needs the queue
  lock to schedule its next wakeup, and it's a *different* thread, so an RLock wouldn't help —
  reentrancy is per-thread);
- but holding `_execution_lock` across it is fine — the awake clock never needs `_execution_lock`,
  only external mutators do.

And the decorator's `_execution_lock`-before-`_tree_lock` order: `while_quiescent()` can block a long
time (until the running clock yields), and that running clock may itself need `_tree_lock` (to fork,
kill, or finish/detach). If we grabbed `_tree_lock` first and then blocked in `while_quiescent()`,
we'd hold `_tree_lock` while waiting on the scheduler, the scheduler would be waiting on the running
clock, and that clock would be waiting for `_tree_lock` → deadlock. Taking `_execution_lock` first
means we hold nothing while waiting for quiescence.

## Rejected alternatives

- **Merge the two scheduler primitives into one.** Possible (a flag guarded by the queue lock), but
  they protect different things (heap freshness vs. "an action is running") and a Condition's
  edge-triggered notify can't model the sticky "is anything executing" state cleanly. Kept separate.
- **Hold `_queue_change_condition` across `_execute_event`, RLock so the woken clock can mutate the
  queue.** Deadlocks — the woken clock is a *different* thread; RLock only forgives the holding
  thread. (See key insight.)
- **Keep `held()` but move it earlier / cover more of `kill()`.** Doesn't help — a level-checked gate
  can't preempt an in-flight execute, and the real fork/kill atomicity needs a tree lock anyway. The
  hold in kill turned out to be unnecessary: kill is correct from `_tree_lock` + "every victim
  observes DEAD" + the park-lock discipline (a window-fired victim wakeup just degrades to the
  documented killed-while-awake path).
- **Split the code into finer commits (clock vs scheduler).** The two are bidirectionally coupled
  (scheduler removes `held()` the clock used; clock uses `while_quiescent()` the scheduler adds), so a
  correct split would need a fabricated intermediate (`held()` + `_tree_lock` in the decorator) that
  never actually ran. Committed the code as one unit + a separate PLAN commit.

## Naming notes

- `QueueEvent` kept as-is (not `QueuedAction`): mirrors stdlib `sched.Event`, which has the same
  `{time, priority, action}` shape; the `.action` field cleanly names the callable. `QueuedAction`
  would force `.action` → something to avoid `action.action`.
- `_new_event` → `_queue_change_condition` (it's a Condition, not a boolean; fires on
  remove/reschedule/kill too, not just new events).

## Quiescence-gate latency measured, cleared for 1.0 (2026-07-04)

While chasing "jerky" mouse-driven playback (SCAMP `27_mouse_input.py`), briefly instrumented
`Session.register_mouse_listener`'s `on_move_wrapper` to time (a) event inter-arrival and (b) how long
`while_scheduler_quiescent()` blocked before acquiring `_execution_lock`. Results:

- **X11 login:** events a steady ~3 ms apart; `quiescent_acquire` = **0.0 ms every time**.
- **Wayland/XWayland:** events arrive in *clumps* (bursts at 0.2–0.4 ms separated by 40–200 ms idle) —
  this is XWayland motion **coalescing**, i.e. the perceived jerkiness is pure input transport, wholly
  upstream of cb2. `quiescent_acquire` stayed **0.0 ms when idle** and only went nonzero (**0.6–6.1 ms,
  never higher**) once note glides were active — the predicted mild compounding (each queued move
  schedules 0.1 s glides that hold `_execution_lock`). The gate even *de-clumps* slightly (0.2 ms
  bursts stretch to 1–4 ms under load).

Conclusion: the quiescence model is healthy — bounded (≤ ~6 ms here), free when idle, no unbounded
holds — so **no clock-side reason blocks cb2 → clockblocks 1.0**. Caveat worth a docstring line: acquire
latency scales with however long user code runs *between* waits (bounded by "time to next `wait()`"),
so a heavy callback would raise it — inherent, not a defect. Debug instrumentation was reverted.

## Waiting inside a held scheduler now raises, not deadlocks (2026-08-04)

*Commits: clockblocks `f5bee65`, scamp `2cd110c`. (`while_quiescent()` above is now `held()` /
`Clock.hold_scheduler()`; `_execution_lock` unchanged.)*

Surfaced via scamp_extensions' `key_plane_example.py`: one note played on keypress, then the whole thing
hung — no note-off, no further keys. The keyboard/MIDI/OSC/HID listener wrappers run the user callback
under `hold_scheduler()` **and** tag the thread `__clock__ = session`. So a blocking `play_note()` (which
waits internally) called `session.wait()`, which *passed* its own-thread check (because `__clock__` was
the session) and then parked waiting for the run loop to fire its wake-up — but the run loop can't take
`_execution_lock` until the hold releases, and the hold won't release until the wait returns. Textbook
deadlock, and the exact "cross-thread handoff under a held lock" hazard this file's key-insight warns
about, just approached from the callback side.

Fix: `held()` records `_held_by_thread = current_thread()` (cleared on exit); `Clock._wait` raises the
new `SchedulerHeldError` when `scheduler._held_by_thread is current_thread()`.

Decisions:
- **Raise, not warn.** A warning + guaranteed hang is the worst of both. Raising *prevents* the hang: the
  exception unwinds out of the callback, exits the hold (releasing `_execution_lock`, clearing
  `_held_by_thread`), and the scheduler lives on — verified a later `fork` still runs. Can't hit valid
  code: `hold_scheduler()` no-ops when the caller already belongs to the family, so the flag is only ever
  the current thread on a genuinely foreign thread holding via `held()`, where any wait deadlocks.
- **Track the thread, not a bool.** `_execution_lock.locked()` is `True` during *every* normal action the
  run loop executes (forked clocks wait inside actions all the time — fine). Only "*this* thread holds via
  `held()`" is the deadlock, and `threading.Lock` doesn't expose its owner, so we store it ourselves.
- **No save/restore of `_held_by_thread`.** `held()` can't nest on one thread (non-reentrant lock +
  `hold_scheduler()` self-no-op), so it's always `None` on entry — plain set/clear.
- **Only `wait` gates it.** Heavy compute / `time.sleep` in a callback eventually return and
  release the scheduler; only `wait` (incl. blocking `play_note`) is the unrecoverable case.
- **No downstream refs in clockblocks** (workflow.md rule): the `SchedulerHeldError` message says
  "Schedule the timed work with fork() instead" — the `play_note(blocking=False)` framing lives only in
  scamp's changelog/error path. First draft mentioned `blocking=False`; stripped it.

Also, tangentially: `run_as_server()` was superfluous in that example (a script ending in blocking
`.start()` → `wait_forever()` already parks the main thread) and its docstring didn't warn that it
detaches the calling thread's clock — clarified. It's genuinely useful only for interactive/REPL driving.
