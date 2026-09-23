# test_examples.py hang: natural-end master strands the cb2 scheduler singleton

*Topic started 2026-06-03 during cb2↔scamp integration. Symptom: `scamp/test/test_examples.py`
hung partway through, on a `condition.wait`; interrupting showed it parked in cb2. The example it
hung on wandered between runs (Marc saw `Tutorial/23_special_notations.py`, Claude saw ~`29_spanners`).*

## Root cause

cb2's scheduler is a **module-level singleton** (`scheduler._scheduler`, a daemon thread, reused by
`get_scheduler()` as long as it `is_alive()`). When a master clock's top-level code finishes
*without* `kill()`, there is no `_fork_wrapper` cleanup to notify, so the scheduler thread is left
parked forever on that dead master's `_scheduler_park_condition` (inside `_execute_event` →
`_wake_and_advance_to_next_wait_call`), waiting for a next `wait()` that never comes.

`kill()`'s docstring dismisses this — *"since it's a daemon thread and the process is exiting, this
doesn't matter."* That assumption breaks the instant a **second master is created in the same live
process**: the still-parked scheduler never fires the new master's initial wake, so
`Clock.__init__`'s `self._wait_event.wait()` (clock.py:157) blocks forever.

`test_examples.py` imports ~50 example scripts serially into one process, so it's the first place
serial masters happen. Reproduced minimally: two `Session()`s in a row with no `kill()` on the first
→ second hangs deterministically. `kill()` on the first → fine. Fast-forward is **irrelevant**
(early red herring — 23's test version uses `fast_forward_in_beats(inf)`, but plain real-time
sessions hang identically).

## Why it "wandered" (23 vs 29) and got ~50 deep before hanging

Almost every example script ends with `s.kill()` — releasing the scheduler for the next one — so the
suite sailed through them. The **lone exception was `Tutorial/29_spanners.py`**, which ended at
`performance = s.stop_transcribing()` with no kill. So the hang always landed on *whichever
session-example `os.walk` ordered immediately after 29_spanners* — filesystem-walk-order-dependent,
hence different across machines/runs. 29 itself ran fine (the example before it had killed).

`Test/clock_order.py` is a **legacy `clockblocks` test** (`from clockblocks import ...`), not cb2 —
its `c1.kill()/c2.kill()` are test logic (kill children, re-fork), and its master never touches cb2's
singleton scheduler. It was also the source of the ~100 `multiprocessing.pool` worker threads in the
fault dump (legacy clockblocks gives each master a 200-thread `ThreadPool`; cb2 doesn't). Red herring
for this bug — left untouched.

## Fix applied (harness-level — option "C")

The teardown is inherent to *running N scripts in one process*, not to any script, so it belongs in
the harness, not in the example files:

- `test_examples.py` `get_example_result()` now kills the active master in a `finally`
  (`_kill_active_session()`: `scamp.current_clock().master.kill()` + clear `__clock__`). Works because
  every example creates its `Session` on the import/main thread, so `current_clock()` resolves to it
  after `exec_module`. (Audited: no example uses `run_as_server`, none creates >1 `Session`.)
- Removed the now-redundant trailing `s.kill()` from all 27 session example scripts (left
  `clock_order.py` alone). This does **not** change any golden output — `test_results()` is built from
  `stop_transcribing()`, which runs before any kill.

Result: suite **completes, no hang**. (The remaining ~22 failing examples are the separate, expected
cb2-integration diffs — envelope float-precision and MIDI byte streams — not caused by this change;
they were visible in the very first fault dump.)

## The real bug deferred (option "B") — see cb2/PLAN.md

Killing the master harness-side only patches scamp's tests. The underlying cb2 question — *what is the
intended lifecycle when a master's owning thread just ends with a live clock?* — is now written up in
`cb2/PLAN.md` under "Known design questions" (extends the existing "Multiple master clocks" item).
Candidate fixes: (a) master-side cleanup hook (atexit / weakref finalizer) analogous to
`_fork_wrapper`; (b) new-master construction supersedes-and-kills any stale parked master; (c) Clock/
Session as a context manager. Only (a)/(b) fix the silent fall-off-the-end case without user code
changes. Ties into the Step-4 note's deferred "Step 5 cleanup wrapper" and `run_as_server`.
